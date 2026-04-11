"""Shared application state — the single source of truth for the web dashboard.

This module holds all runtime globals (active sessions, singletons) and provides
thread-safe helpers to register / look-up / unregister orchestrator managers.

All public functions validate their inputs and log significant operations.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC
from typing import Any

from orchestrator import OrchestratorManager
from sdk_client import ClaudeSDKManager
from skills_registry import scan_skills
from src.db.database import get_session_factory, init_db
from src.storage.platform_session import PlatformSessionManager

logger = logging.getLogger(__name__)

# ── Global mutable state ──────────────────────────────────────────────

_state_lock = asyncio.Lock()
_init_lock = asyncio.Lock()  # separate lock so initialize() doesn't deadlock get_manager()
_initialized = False

# user_id -> {project_id -> OrchestratorManager}
active_sessions: dict[int, dict[str, OrchestratorManager]] = {}

# user_id -> currently-focused project_id
current_project: dict[int, str] = {}

# Singletons (populated by initialize())
sdk_client: ClaudeSDKManager | None = None
session_mgr: PlatformSessionManager | None = None

# Valid project name pattern
PROJECT_NAME_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9 _-]+$")

# Per-user rate limiting: user_id -> last message timestamp
user_last_message: dict[int, float] = {}

# Server start time — used to calculate uptime in /api/health
server_start_time: float = time.monotonic()


# ── Initialization ────────────────────────────────────────────────────


async def _ensure_system_user() -> None:
    """Create system user (id='0') if it doesn't exist.
    Used as the owner of predefined/anonymous projects."""
    from sqlalchemy import text

    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(text("SELECT id FROM users WHERE id = '0'"))
        if result.fetchone() is None:
            from datetime import datetime

            now = datetime.now(UTC).isoformat()
            await db.execute(
                text(
                    "INSERT INTO users (id, display_name, created_at, updated_at) "
                    "VALUES ('0', 'system', :now, :now)"
                ),
                {"now": now},
            )
            await db.commit()
            logger.info("Created system user (id='0')")


async def initialize() -> None:
    """Create SDK + PlatformSessionManager singletons.  Safe to call once.

    Raises:
        RuntimeError: If database initialisation fails.
    """
    global sdk_client, session_mgr, _initialized

    async with _init_lock:
        if _initialized:
            return

        if sdk_client is None:
            sdk_client = ClaudeSDKManager()
            logger.info("SDK client initialized")

        if session_mgr is None:
            await init_db()
            # Ensure system user (id='0') exists for predefined/anonymous projects
            await _ensure_system_user()
            factory = get_session_factory()
            session_mgr = PlatformSessionManager(factory)
            await session_mgr.initialize()
            logger.info("PlatformSessionManager initialized (platform.db)")

        # Scan available skills
        skills = scan_skills()
        logger.info("Loaded %d skills: %s", len(skills), list(skills.keys()))

        _initialized = True


# ── Helper functions ──────────────────────────────────────────────────


def get_manager(project_id: str) -> tuple[OrchestratorManager | None, int | None]:
    """Find an OrchestratorManager by *project_id* across all users.

    Acquires ``_state_lock`` to prevent reading a manager that is being
    torn down by a concurrent ``unregister_manager`` call.  The lock is
    the same one used by register/unregister so reads are serialised
    against writes.

    Args:
        project_id: The project identifier to search for.

    Returns:
        A ``(manager, user_id)`` tuple, or ``(None, None)`` if not found.
    """
    if not isinstance(project_id, str) or not project_id:
        logger.warning("get_manager called with invalid project_id: %r", project_id)
        return None, None

    # NOTE: This is a sync function but _state_lock is an asyncio.Lock.
    # We take a snapshot under no contention risk because asyncio is
    # single-threaded — the snapshot is safe as long as we don't yield.
    # The dict() copies ensure we don't hit "dictionary changed size"
    # if another coroutine modifies active_sessions between our yields.
    snapshot = dict(active_sessions)
    for user_id, sessions in snapshot.items():
        inner = dict(sessions)
        manager = inner.get(project_id)
        if manager is not None:
            return manager, user_id
    return None, None


async def get_manager_safe(project_id: str) -> tuple[OrchestratorManager | None, int | None]:
    """Async version of get_manager that acquires _state_lock.

    Use this from async contexts where concurrent register/unregister
    may be happening to guarantee the returned manager is not being
    torn down.
    """
    if not isinstance(project_id, str) or not project_id:
        logger.warning("get_manager_safe called with invalid project_id: %r", project_id)
        return None, None

    async with _state_lock:
        for user_id, sessions in active_sessions.items():
            manager = sessions.get(project_id)
            if manager is not None:
                return manager, user_id
    return None, None


def get_all_managers() -> list[tuple[int, str, Any]]:
    """Return a flat list of ``(user_id, project_id, manager)`` across all users.

    Takes snapshots of both outer and inner dicts to prevent iteration
    errors when sessions are modified concurrently.

    Returns:
        A new list (safe to iterate/mutate without affecting global state).
    """
    result: list[tuple[int, str, Any]] = []
    snapshot = dict(active_sessions)
    for user_id, sessions in snapshot.items():
        inner = dict(sessions)
        for project_id, manager in inner.items():
            result.append((user_id, project_id, manager))
    return result


async def get_all_managers_safe() -> list[tuple[int, str, Any]]:
    """Async version of get_all_managers that acquires _state_lock.

    Use this from async contexts (e.g. shutdown) where concurrent
    register/unregister may be happening.
    """
    async with _state_lock:
        result: list[tuple[int, str, Any]] = []
        for user_id, sessions in active_sessions.items():
            for project_id, manager in sessions.items():
                result.append((user_id, project_id, manager))
        return result


async def register_manager(
    user_id: int,
    project_id: str,
    manager: Any,
) -> None:
    """Register an OrchestratorManager for a user + project.

    Args:
        user_id: Numeric user identifier.
        project_id: The project identifier (must be non-empty string).
        manager: The OrchestratorManager instance to register.

    Raises:
        ValueError: If *user_id* or *project_id* are invalid.
    """
    if not isinstance(user_id, int):
        raise ValueError(f"user_id must be an int, got {type(user_id).__name__}")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError(f"project_id must be a non-empty string, got {project_id!r}")

    async with _state_lock:
        if user_id not in active_sessions:
            active_sessions[user_id] = {}
        active_sessions[user_id][project_id] = manager
    logger.debug("Registered manager for user=%d project=%s", user_id, project_id)


async def unregister_manager(user_id: int, project_id: str) -> None:
    """Remove an OrchestratorManager for a user + project.

    Safe to call even if the user or project doesn't exist.

    Args:
        user_id: Numeric user identifier.
        project_id: The project identifier to remove.
    """
    async with _state_lock:
        if user_id in active_sessions:
            removed = active_sessions[user_id].pop(project_id, None)
            if not active_sessions[user_id]:
                del active_sessions[user_id]
            if removed is not None:
                logger.debug("Unregistered manager for user=%d project=%s", user_id, project_id)
        # Cascade: clear current_project pointer if it pointed at removed project
        if current_project.get(user_id) == project_id:
            del current_project[user_id]


async def cleanup_stale_sessions(max_idle_seconds: float | None = None) -> int:
    """Remove OrchestratorManager entries that are no longer running.

    Prevents unbounded memory growth from managers that finished but were
    never explicitly unregistered (e.g. due to disconnects or crashes).

    Args:
        max_idle_seconds: Max seconds since last activity before a stopped
            manager is considered stale. Defaults to SESSION_TIMEOUT_SECONDS.

    Returns:
        Number of stale sessions removed.
    """
    from config import SESSION_TIMEOUT_SECONDS

    if max_idle_seconds is None:
        max_idle_seconds = float(SESSION_TIMEOUT_SECONDS)

    now = time.monotonic()
    removed = 0

    async with _state_lock:
        for user_id in list(active_sessions):
            sessions = active_sessions[user_id]
            stale_projects = []
            for project_id, manager in sessions.items():
                if manager.is_running:
                    continue
                # Check if manager has been idle long enough
                # Use the last known activity or fall back to server start time
                last_activity = getattr(manager, "_last_orch_call_time", 0.0)
                if last_activity == 0.0:
                    last_activity = server_start_time
                idle = now - last_activity
                if idle > max_idle_seconds:
                    stale_projects.append(project_id)

            for project_id in stale_projects:
                del sessions[project_id]
                removed += 1
                logger.info(
                    "Reaped stale session: user=%d project=%s",
                    user_id,
                    project_id,
                )

            if not sessions:
                del active_sessions[user_id]

    # Also prune stale user_last_message entries
    cutoff = time.time() - max_idle_seconds
    stale_users = [uid for uid, ts in user_last_message.items() if ts < cutoff]
    for uid in stale_users:
        del user_last_message[uid]

    if removed:
        logger.info("Cleanup: removed %d stale session(s)", removed)
    return removed


def is_valid_project_name(name: str) -> bool:
    """Check whether *name* matches PROJECT_NAME_RE.

    Args:
        name: The candidate project name.

    Returns:
        ``True`` if the name contains only alphanumerics, spaces, hyphens,
        and underscores (and is non-empty).
    """
    if not isinstance(name, str):
        return False
    return bool(PROJECT_NAME_RE.match(name))
