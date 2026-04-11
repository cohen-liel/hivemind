"""Plugin infrastructure for Hivemind custom agent roles.

PluginBase defines the contract every plugin must satisfy.
PluginRegistry discovers, loads, enables/disables, and hot-reloads plugins
from the ``plugins/`` directory without restarting the server.

Usage::

    registry = PluginRegistry()
    registry.discover()
    registry.start_hot_reload()   # background thread watches plugins/

    plugin = registry.get("documentation_writer")
    registry.disable("documentation_writer")
    registry.enable("documentation_writer")

    registry.stop_hot_reload()    # clean shutdown
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"

# Role names must be safe identifiers: lowercase letters, digits, underscores, 1-64 chars.
_ROLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


# ---------------------------------------------------------------------------
# PluginBase — the contract every plugin must satisfy
# ---------------------------------------------------------------------------


class PluginBase(ABC):
    """Abstract base class for all Hivemind custom agent-role plugins.

    Subclasses must set all four class-level attributes and may override
    ``build_prompt`` to inject dynamic context into ``system_prompt``.
    """

    # --- Required class-level declarations ----------------------------------

    @property
    @abstractmethod
    def role_name(self) -> str:
        """Unique identifier for this agent role (e.g. 'documentation_writer')."""

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Full system prompt injected when this role is invoked."""

    @property
    @abstractmethod
    def file_scope_patterns(self) -> list[str]:
        """Glob patterns that limit which files this agent may read/write.

        Examples: ``["**/*.py"]``, ``["docs/**/*.md", "README.md"]``
        An empty list means *no restriction*.
        """

    @property
    @abstractmethod
    def is_writer(self) -> bool:
        """True if this agent produces file writes; False for read-only agents."""

    # --- Optional hooks -----------------------------------------------------

    def build_prompt(self, context: dict[str, Any] | None = None) -> str:
        """Return the system prompt, optionally injecting dynamic *context*.

        Override this to interpolate task context into ``system_prompt``.
        The default implementation returns ``self.system_prompt`` unchanged.
        """
        return self.system_prompt

    def on_load(self) -> None:  # noqa: B027
        """Called once after the plugin class is successfully loaded."""

    def on_unload(self) -> None:  # noqa: B027
        """Called before the plugin is removed during hot-reload."""


# ---------------------------------------------------------------------------
# PluginMetadata — internal record stored in the registry
# ---------------------------------------------------------------------------


@dataclass
class PluginMetadata:
    """Runtime record for a loaded plugin."""

    role_name: str
    plugin_class: type[PluginBase]
    source_file: Path
    enabled: bool = True
    instance: PluginBase = field(default=None, init=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.instance = self.plugin_class()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_name": self.role_name,
            "source_file": str(self.source_file),
            "enabled": self.enabled,
            "is_writer": self.instance.is_writer,
            "file_scope_patterns": self.instance.file_scope_patterns,
            "system_prompt_preview": self.instance.system_prompt[:120],
        }


# ---------------------------------------------------------------------------
# PluginRegistry — discovery, lifecycle, hot-reload
# ---------------------------------------------------------------------------


class PluginRegistry:
    """Discovers and manages Hivemind custom-role plugins.

    Plugins are ``.py`` files inside the ``plugins/`` directory that define
    exactly one concrete subclass of :class:`PluginBase`.

    Thread safety: ``_lock`` guards all mutations so that the hot-reload
    thread and API handler threads can coexist without races.
    """

    def __init__(self, plugins_dir: Path | None = None) -> None:
        self._dir: Path = plugins_dir or _PLUGINS_DIR
        self._plugins: dict[str, PluginMetadata] = {}
        self._lock = threading.RLock()
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> list[str]:
        """Scan ``plugins/`` and load every valid plugin file.

        Returns the list of role names that were successfully loaded.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        loaded: list[str] = []
        for py_file in sorted(self._dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue  # skip __init__.py and private helpers
            role_name = self._load_file(py_file)
            if role_name:
                loaded.append(role_name)
        logger.info("PluginRegistry: discovered %d plugin(s): %s", len(loaded), loaded)
        return loaded

    def _load_file(self, path: Path) -> str | None:
        """Import *path*, find the PluginBase subclass, register it.

        Returns the role_name on success, None on failure.

        Security: resolves the canonical path and verifies it lives inside
        ``plugins_dir`` before executing — prevents symlink traversal attacks.
        """
        # --- SECURITY: validate canonical path before executing ----------------
        try:
            resolved = path.resolve(strict=True)  # raises OSError if not found
            plugins_resolved = self._dir.resolve()
            resolved.relative_to(plugins_resolved)  # raises ValueError if outside
        except (ValueError, OSError) as exc:
            logger.error(
                "PluginRegistry: SECURITY — refusing to load '%s': "
                "file resolves outside plugins directory or does not exist (%s)",
                path,
                exc,
            )
            return None
        # Use the resolved (non-symlink) path for all subsequent I/O
        path = resolved
        # -----------------------------------------------------------------------

        module_name = f"hivemind_plugins.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                logger.warning("PluginRegistry: cannot create spec for %s", path)
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            logger.exception("PluginRegistry: error importing %s", path)
            return None

        # Find all concrete PluginBase subclasses defined in this module
        candidates = [
            cls
            for _, cls in inspect.getmembers(module, inspect.isclass)
            if issubclass(cls, PluginBase) and cls is not PluginBase and not inspect.isabstract(cls)
        ]

        if not candidates:
            logger.warning("PluginRegistry: no PluginBase subclass found in %s", path)
            return None
        if len(candidates) > 1:
            logger.warning(
                "PluginRegistry: multiple PluginBase subclasses in %s — using first: %s",
                path,
                candidates[0].__name__,
            )

        cls = candidates[0]
        try:
            raw_role_name = cls().role_name  # instantiate briefly to get role_name
            # --- SECURITY: validate role_name format ---------------------------
            if not _ROLE_NAME_RE.match(raw_role_name):
                logger.error(
                    "PluginRegistry: SECURITY — rejecting plugin '%s': "
                    "role_name '%s' is invalid (must match [a-z][a-z0-9_]{0,63})",
                    path.name,
                    raw_role_name,
                )
                return None
            # -------------------------------------------------------------------
            meta = PluginMetadata(
                role_name=raw_role_name,
                plugin_class=cls,
                source_file=path,
            )
            meta.instance.on_load()
        except Exception:
            logger.exception("PluginRegistry: error initialising plugin from %s", path)
            return None

        with self._lock:
            old = self._plugins.get(meta.role_name)
            if old is not None:
                try:
                    old.instance.on_unload()
                except Exception:
                    logger.debug("PluginRegistry: on_unload error for %s (ignored)", meta.role_name)
            # Preserve enabled state across reloads
            if old is not None:
                meta.enabled = old.enabled
            self._plugins[meta.role_name] = meta

        logger.info("PluginRegistry: loaded plugin '%s' from %s", meta.role_name, path.name)
        return meta.role_name

    def _unload_file(self, path: Path) -> None:
        """Remove any plugin whose source file matches *path*."""
        with self._lock:
            to_remove = [rn for rn, m in self._plugins.items() if m.source_file == path]
            for rn in to_remove:
                try:
                    self._plugins[rn].instance.on_unload()
                except Exception:
                    pass
                del self._plugins[rn]
                logger.info("PluginRegistry: unloaded plugin '%s'", rn)

    # ------------------------------------------------------------------
    # Enable / Disable
    # ------------------------------------------------------------------

    def enable(self, role_name: str) -> bool:
        """Enable a previously disabled plugin. Returns True if found."""
        with self._lock:
            meta = self._plugins.get(role_name)
            if meta is None:
                return False
            meta.enabled = True
        logger.info("PluginRegistry: enabled '%s'", role_name)
        return True

    def disable(self, role_name: str) -> bool:
        """Disable a plugin so it is excluded from agent dispatch. Returns True if found."""
        with self._lock:
            meta = self._plugins.get(role_name)
            if meta is None:
                return False
            meta.enabled = False
        logger.info("PluginRegistry: disabled '%s'", role_name)
        return True

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, role_name: str) -> PluginBase | None:
        """Return the live plugin instance, or None if not found / disabled."""
        with self._lock:
            meta = self._plugins.get(role_name)
        if meta is None or not meta.enabled:
            return None
        return meta.instance

    def get_metadata(self, role_name: str) -> PluginMetadata | None:
        """Return raw metadata (regardless of enabled state)."""
        with self._lock:
            return self._plugins.get(role_name)

    def list_all(self) -> list[dict[str, Any]]:
        """Return serialisable info for all registered plugins."""
        with self._lock:
            return [m.to_dict() for m in self._plugins.values()]

    def list_enabled(self) -> list[PluginBase]:
        """Return all enabled plugin instances."""
        with self._lock:
            return [m.instance for m in self._plugins.values() if m.enabled]

    def role_names(self) -> list[str]:
        """All registered role names (enabled and disabled)."""
        with self._lock:
            return list(self._plugins.keys())

    # ------------------------------------------------------------------
    # Hot-reload via watchfiles
    # ------------------------------------------------------------------

    def start_hot_reload(self) -> None:
        """Start a background thread that watches ``plugins/`` for changes."""
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            logger.debug("PluginRegistry: hot-reload already running")
            return
        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            name="plugin-hot-reload",
            daemon=True,
        )
        self._watcher_thread.start()
        logger.info("PluginRegistry: hot-reload watcher started (watching %s)", self._dir)

    def stop_hot_reload(self) -> None:
        """Signal the watcher thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=5)
            self._watcher_thread = None
        logger.info("PluginRegistry: hot-reload watcher stopped")

    def _watch_loop(self) -> None:
        """Blocking loop (runs in a daemon thread) — uses watchfiles.watch()."""
        try:
            from watchfiles import watch  # type: ignore[import-untyped]
        except ImportError:
            logger.error(
                "PluginRegistry: 'watchfiles' not installed — hot-reload disabled. "
                "Run: pip install watchfiles"
            )
            return

        self._dir.mkdir(parents=True, exist_ok=True)

        try:
            for changes in watch(
                str(self._dir),
                stop_event=self._stop_event,
                yield_on_timeout=True,
            ):
                if self._stop_event.is_set():
                    break
                if not changes:
                    continue  # timeout ping, no actual changes

                # Group changed paths
                modified: set[Path] = set()
                deleted: set[Path] = set()

                for change_type, raw_path in changes:
                    path = Path(raw_path)
                    if path.suffix != ".py" or path.name.startswith("_"):
                        continue
                    # watchfiles Change enum: 1=added, 2=modified, 3=deleted
                    if change_type.name in ("added", "modified"):
                        modified.add(path)
                    elif change_type.name == "deleted":
                        deleted.add(path)

                for path in deleted:
                    logger.info("PluginRegistry: file removed %s — unloading", path.name)
                    self._unload_file(path)

                for path in modified:
                    logger.info("PluginRegistry: file changed %s — reloading", path.name)
                    self._load_file(path)

        except Exception:
            logger.exception("PluginRegistry: watcher loop crashed")


# ---------------------------------------------------------------------------
# Module-level singleton convenience
# ---------------------------------------------------------------------------

#: Global singleton — import and use directly in server startup.
registry = PluginRegistry()
