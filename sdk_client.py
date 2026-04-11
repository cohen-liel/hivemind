from __future__ import annotations

import asyncio
import collections
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


def _ensure_sdk_can_spawn():
    """Unset CLAUDECODE so the SDK can spawn claude subprocesses.

    Without this, the SDK refuses with:
        'cannot launch inside another Claude Code session'
    This is intentional and documented — not a side-effect of import.

    Also remove ANTHROPIC_API_KEY: this project authenticates via
    ``claude login`` (OAuth), not API keys.  If a stale key is present
    in the shell environment it causes Claude CLI to attempt key-based
    auth and fail with "Invalid API key".
    """
    os.environ.pop("CLAUDECODE", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)  # OAuth only — no API key auth


_ensure_sdk_can_spawn()


from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from config import (
    CLAUDE_CLI_PATH,
    SDK_MAX_RETRIES,
    SEMAPHORE_ACQUIRE_TIMEOUT,
    SUBPROCESS_SHORT_TIMEOUT,
    get_agent_timeout,
)

logger = logging.getLogger(__name__)

# ── Global Active PID Registry ──────────────────────────────────────────────
# Tracks Claude subprocess PIDs that belong to currently-active queries.
# When a query starts, it snapshots PIDs before and after spawning, then
# registers the new PIDs. When a query ends (success/error/cancel), it
# unregisters its PIDs. The orphan cleanup only kills PIDs that are NOT
# in this registry — preventing one query's cleanup from killing another
# query's legitimate subprocess.
_active_query_pids: set[int] = set()
_active_query_pids_lock = threading.Lock()


def _register_active_pids(pids: set[int]) -> None:
    """Register PIDs as belonging to active queries (thread-safe)."""
    with _active_query_pids_lock:
        _active_query_pids.update(pids)
        if pids:
            logger.debug(
                f"Registered active PIDs: {pids} (total active: {len(_active_query_pids)})"
            )


def _unregister_active_pids(pids: set[int]) -> None:
    """Unregister PIDs when their query completes (thread-safe)."""
    with _active_query_pids_lock:
        _active_query_pids.difference_update(pids)
        if pids:
            logger.debug(f"Unregistered PIDs: {pids} (total active: {len(_active_query_pids)})")


def _get_active_pids() -> set[int]:
    """Get a snapshot of all currently active query PIDs (thread-safe)."""
    with _active_query_pids_lock:
        return set(_active_query_pids)


def _kill_specific_pids(pids: set[int], grace_period: float = 3.0) -> int:
    """Kill specific PIDs by sending SIGTERM then SIGKILL after grace_period.

    Used to kill known subprocess PIDs that should be terminated.
    Returns the number of processes killed.
    """
    if not pids:
        return 0
    killed = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Sent SIGTERM to Claude PID {pid}")
            killed += 1
        except ProcessLookupError:
            logger.debug("SIGTERM: PID %d already gone", pid)
        except PermissionError:
            logger.warning(f"No permission to kill PID {pid}")
    if killed == 0:
        return 0
    time.sleep(grace_period)
    for pid in pids:
        try:
            os.kill(pid, 0)  # Check if still alive
            os.kill(pid, signal.SIGKILL)
            logger.warning(f"Sent SIGKILL to stubborn Claude PID {pid}")
        except ProcessLookupError:
            logger.debug("SIGKILL: PID %d already terminated", pid)
        except PermissionError:
            logger.debug("SIGKILL: no permission to signal PID %d (sandboxed?)", pid)
    return killed


async def _async_kill_specific_pids(pids: set[int], grace_period: float = 3.0) -> int:
    """Async wrapper — runs _kill_specific_pids in a thread to avoid blocking the event loop."""
    return await asyncio.to_thread(_kill_specific_pids, pids, grace_period)


def kill_spawned_claudes(pids_before: set[int], grace_period: float = 3.0) -> int:
    """Kill Claude CLI processes that were spawned after *pids_before* snapshot.

    This is the nuclear option — used when `gen.aclose()` fails to terminate
    the subprocess (e.g. anyio cancel-scope RuntimeError suppressed during
    cleanup).  We find any `claude` processes whose PID is NOT in the
    *pids_before* set and send SIGTERM, then SIGKILL after *grace_period*.

    Returns the number of processes killed.
    """
    killed = 0
    try:
        # Snapshot current claude processes
        result = subprocess.run(
            ["pgrep", "-f", "claude.*--output-format"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_SHORT_TIMEOUT,
        )
        if result.returncode != 0:
            return 0  # No matching processes

        current_pids = set()
        for line in result.stdout.strip().splitlines():
            try:
                current_pids.add(int(line.strip()))
            except ValueError:
                continue

        orphans = current_pids - pids_before
        if not orphans:
            return 0

        # CRITICAL: Exclude PIDs that belong to other active queries.
        # Without this, parallel queries kill each other's subprocesses.
        active = _get_active_pids()
        true_orphans = orphans - active
        if orphans != true_orphans:
            logger.info(
                f"Orphan cleanup: {len(orphans)} new PIDs found, but {len(orphans - true_orphans)} "
                f"belong to other active queries — skipping those. "
                f"True orphans: {true_orphans}, Active query PIDs: {active & orphans}"
            )
        orphans = true_orphans
        if not orphans:
            return 0

        logger.warning(f"Found {len(orphans)} orphan Claude process(es): {orphans}")

        # SIGTERM first (polite)
        for pid in orphans:
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"Sent SIGTERM to orphan Claude PID {pid}")
                killed += 1
            except ProcessLookupError:
                logger.debug("SIGTERM orphan: PID %d already gone", pid)
            except PermissionError:
                logger.warning(f"No permission to kill orphan PID {pid}")
        # Wait briefly, then SIGKILL any survivors.
        # NOTE: This is a sync function intentionally — it's called from
        # finally blocks and the watchdog.  The sleep is acceptable here
        # because it only runs when killing orphans (rare, post-timeout).
        time.sleep(grace_period)

        for pid in orphans:
            try:
                os.kill(pid, 0)  # Check if still alive
                os.kill(pid, signal.SIGKILL)
                logger.warning(f"Sent SIGKILL to stubborn Claude PID {pid}")
            except ProcessLookupError:
                logger.debug("SIGKILL orphan: PID %d already terminated", pid)
            except PermissionError:
                logger.debug("SIGKILL orphan: no permission to signal PID %d (sandboxed?)", pid)

    except Exception as e:
        logger.debug(f"Error during orphan process cleanup: {e}")

    return killed


# Backward-compatible alias (referenced as _kill_orphan_claude_processes in some call sites)
_kill_orphan_claude_processes = kill_spawned_claudes


async def _async_kill_orphan_claude_processes(
    pids_before: set[int], grace_period: float = 3.0
) -> int:
    """Async wrapper — runs kill_spawned_claudes in a thread to avoid blocking the event loop."""
    return await asyncio.to_thread(kill_spawned_claudes, pids_before, grace_period)


def _snapshot_claude_pids() -> set[int]:
    """Return the set of PIDs for currently running Claude CLI processes."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude.*--output-format"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_SHORT_TIMEOUT,
        )
        if result.returncode != 0:
            return set()
        pids = set()
        for line in result.stdout.strip().splitlines():
            try:
                pids.add(int(line.strip()))
            except ValueError:
                continue
        return pids
    except Exception as e:
        logger.debug("_snapshot_claude_pids failed: %s", e)
        return set()


# Use the Claude CLI binary. Resolution order:
#   1. CLAUDE_CLI_PATH env var / config.py (explicit non-"claude" path)
#   2. SDK-bundled binary (always works, no sandbox issues)
#   3. Native macOS binary at /usr/local/bin/claude_code/claude
#   4. shutil.which("claude") — finds it on $PATH (works in Docker + Linux)
#   5. Fallback to /usr/local/bin/claude
import shutil as _shutil
import subprocess as _subprocess


def _cli_works(path: str) -> bool:
    """Quick check: can this CLI actually run?"""
    try:
        r = _subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_SHORT_TIMEOUT * 2,
            env={**os.environ, "CLAUDECODE": ""},
        )
        return r.returncode == 0
    except Exception as e:
        logger.debug("_cli_works(%s) failed: %s", path, e)
        return False


# Find the SDK-bundled CLI
_BUNDLED_CLI = ""
try:
    import claude_agent_sdk as _sdk

    _candidate = os.path.join(os.path.dirname(_sdk.__file__), "_bundled", "claude")
    if os.path.isfile(_candidate) and os.access(_candidate, os.X_OK):
        _BUNDLED_CLI = _candidate
except (ImportError, AttributeError, OSError) as _sdk_disc_err:
    logger.debug("SDK package discovery failed (will use alternative CLI path): %s", _sdk_disc_err)

_NATIVE_BINARY = "/usr/local/bin/claude_code/claude"

if CLAUDE_CLI_PATH != "claude":
    # Explicitly configured — use as-is
    SYSTEM_CLI_PATH = CLAUDE_CLI_PATH
    logger.info(f"Using configured Claude CLI: {SYSTEM_CLI_PATH}")
elif _BUNDLED_CLI and _cli_works(_BUNDLED_CLI):
    # SDK-bundled binary — always works, no sandbox issues
    SYSTEM_CLI_PATH = _BUNDLED_CLI
    logger.info(f"Using SDK-bundled Claude CLI: {SYSTEM_CLI_PATH}")
elif (
    os.path.isfile(_NATIVE_BINARY)
    and os.access(_NATIVE_BINARY, os.X_OK)
    and _cli_works(_NATIVE_BINARY)
):
    # macOS native binary — only if it actually works (not a restricted sandbox version)
    SYSTEM_CLI_PATH = _NATIVE_BINARY
    logger.info(f"Using native Claude binary: {_NATIVE_BINARY}")
else:
    # Find on PATH (Docker, Linux, standard installs)
    SYSTEM_CLI_PATH = _shutil.which("claude") or "/usr/local/bin/claude"
    logger.info(f"Using Claude CLI from PATH: {SYSTEM_CLI_PATH}")


# ============================================================
# Error Classification
# ============================================================


class ErrorCategory(Enum):
    """Classification of SDK errors for retry/handling decisions."""

    TRANSIENT = "transient"  # Network blips, timeouts — safe to retry
    RATE_LIMIT = "rate_limit"  # API rate limit — retry with backoff
    SESSION = "session"  # Stale/invalid session — retry without session
    AUTH = "auth"  # API key / auth — permanent, don't retry
    BUDGET = "budget"  # Budget exhausted — permanent, don't retry
    PERMANENT = "permanent"  # Other permanent errors — don't retry
    UNKNOWN = "unknown"  # Unclassified — retry once cautiously


def classify_error(error_message: str) -> ErrorCategory:
    """Classify an error message to determine retry strategy.

    Returns an ErrorCategory that guides the retry logic:
    - TRANSIENT: safe to retry immediately or with short delay
    - RATE_LIMIT: retry with exponential backoff
    - SESSION: retry without session_id
    - AUTH/BUDGET/PERMANENT: don't retry
    """
    if not error_message:
        return ErrorCategory.UNKNOWN

    lower = error_message.lower()

    # Timeout errors — transient
    if any(kw in lower for kw in ("timeout", "timed out", "deadline exceeded")):
        return ErrorCategory.TRANSIENT

    # Connection errors — transient
    if any(
        kw in lower
        for kw in (
            "connection",
            "connect",
            "network",
            "dns",
            "econnrefused",
            "econnreset",
            "broken pipe",
            "eof",
            "socket",
            "unavailable",
            "502",
            "503",
            "504",
        )
    ):
        return ErrorCategory.TRANSIENT

    # Rate limiting
    if any(
        kw in lower for kw in ("rate limit", "rate_limit", "429", "too many requests", "throttl")
    ):
        return ErrorCategory.RATE_LIMIT

    # Session/resume errors
    if any(kw in lower for kw in ("session", "resume", "invalid session", "expired session")):
        return ErrorCategory.SESSION

    # Authentication errors — permanent
    if any(
        kw in lower
        for kw in (
            "authentication",
            "unauthorized",
            "401",
            "403",
            "forbidden",
            "permission denied",
            "not logged in",
            "login required",
            "invalid api key",
            "api key",
        )
    ):
        return ErrorCategory.AUTH

    # Budget errors — permanent
    if any(kw in lower for kw in ("budget", "spending limit", "insufficient funds", "quota")):
        return ErrorCategory.BUDGET

    # Process spawn errors — transient (CLI binary might be busy)
    if any(kw in lower for kw in ("process", "spawn", "enoent", "exited with")):
        # Exit code 71 = macOS sandbox restriction — permanent, not transient
        if "exit code 71" in lower or "exit code: 71" in lower:
            return ErrorCategory.PERMANENT
        return ErrorCategory.TRANSIENT

    # Content/validation errors — permanent
    if any(kw in lower for kw in ("invalid", "malformed", "bad request", "400")):
        return ErrorCategory.PERMANENT

    return ErrorCategory.UNKNOWN


# ============================================================
# Error Reason Templates
# ============================================================

# Maps each ErrorCategory to a (human_reason, suggested_action) tuple.
# human_reason: concise user-facing description (ARCH-04: standardized to English).
# suggested_action: what the user or system should do next.

ERROR_REASON_TEMPLATES: dict[ErrorCategory, tuple[str, str]] = {
    ErrorCategory.TRANSIENT: (
        "Temporary network error — retrying automatically",
        "The system will retry automatically. If the issue persists, check your network connection.",
    ),
    ErrorCategory.RATE_LIMIT: (
        "Rate limited — waiting before retry",
        "Too many requests sent. The system is waiting before retrying. No action needed.",
    ),
    ErrorCategory.SESSION: (
        "Session expired — reconnecting",
        "Your session has expired or become invalid. The system will start a new session automatically.",
    ),
    ErrorCategory.AUTH: (
        "Authentication error — check claude login",
        "Run 'claude login' in your terminal to re-authenticate, then try again.",
    ),
    ErrorCategory.BUDGET: (
        "Budget exceeded",
        "The budget for this operation has been exhausted. Increase the budget in settings or wait for the next billing cycle.",
    ),
    ErrorCategory.PERMANENT: (
        "Permanent error — cannot retry",
        "This error cannot be resolved by retrying. Check the error details and fix the underlying issue.",
    ),
    ErrorCategory.UNKNOWN: (
        "Unknown error",
        "An unexpected error occurred. The system may retry once. If it persists, check the logs for details.",
    ),
}


def get_error_reason(category: ErrorCategory) -> tuple[str, str]:
    """Return (human_reason, suggested_action) for an ErrorCategory.

    Uses the ERROR_REASON_TEMPLATES mapping. Falls back to UNKNOWN
    if the category is somehow not in the map.
    """
    return ERROR_REASON_TEMPLATES.get(
        category,
        ERROR_REASON_TEMPLATES[ErrorCategory.UNKNOWN],
    )


def classify_and_enrich_error(error_message: str) -> tuple[ErrorCategory, str, str]:
    """Classify an error and return enriched (category, human_reason, suggested_action).

    Combines classify_error() with get_error_reason() for convenience.
    Use this when constructing SDKResponse objects with error context.

    Returns:
        (ErrorCategory, human_reason, suggested_action) tuple.
    """
    category = classify_error(error_message)
    human_reason, suggested_action = get_error_reason(category)
    return category, human_reason, suggested_action


def _make_project_guard(project_dir: str):
    """Return a ``can_use_tool`` callback that blocks file-tool access outside *project_dir*.

    This is the technical enforcement layer — it intercepts every Read / Write /
    Edit / Glob / Grep call at the SDK level and denies any path that resolves
    outside the project directory.  The system-prompt boundary header is the
    first line of defence; this callback is the hard wall that cannot be bypassed
    regardless of what the agent decides.
    """
    from pathlib import Path

    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

    project_resolved = str(Path(project_dir).resolve())

    # Tool → parameter that holds the file/directory path
    _PATH_PARAMS: dict[str, list[str]] = {
        "Read": ["file_path", "path"],
        "Write": ["file_path", "path"],
        "Edit": ["file_path", "path"],
        "Glob": ["path"],
        "Grep": ["path"],
    }

    async def _guard(
        tool_name: str, tool_input: dict, _ctx
    ) -> PermissionResultAllow | PermissionResultDeny:
        params = _PATH_PARAMS.get(tool_name)
        if params is None:
            # Not a file tool — always allow
            return PermissionResultAllow()

        # Extract the first non-empty path from the known param names
        raw_path = ""
        for param in params:
            raw_path = tool_input.get(param) or ""
            if raw_path:
                break

        if not raw_path:
            # No path specified (e.g. Glob/Grep with no path = cwd) — allow
            return PermissionResultAllow()

        try:
            resolved = str(Path(raw_path).resolve())
        except Exception:
            # Can't resolve — deny defensively
            logger.warning(
                f"[PROJECT GUARD] Cannot resolve path {raw_path!r} — denying {tool_name}"
            )
            return PermissionResultDeny(message=f"Cannot resolve path: {raw_path}")

        if resolved == project_resolved or resolved.startswith(project_resolved + "/"):
            return PermissionResultAllow()

        logger.critical(
            f"[PROJECT GUARD] BLOCKED {tool_name}({raw_path!r}) "
            f"— resolved to {resolved!r} which is outside project {project_resolved!r}"
        )
        return PermissionResultDeny(
            message=(
                f"Access denied: {raw_path!r} is outside the project directory. "
                f"You may only access files within {project_resolved}."
            )
        )

    return _guard


def _make_error_response(
    text: str,
    error_message: str,
    session_id: str = "",
    error_category: ErrorCategory | None = None,
    cost_usd: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    num_turns: int = 0,
    duration_ms: int = 0,
    retry_count: int = 0,
) -> SDKResponse:
    """Create an SDKResponse for an error, auto-populating reason fields.

    If error_category is not provided, it is inferred via classify_error().
    human_reason and suggested_action are always populated from the template.
    """
    if error_category is None:
        error_category = classify_error(error_message)
    human_reason, suggested_action = get_error_reason(error_category)
    return SDKResponse(
        text=text,
        session_id=session_id,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        duration_ms=duration_ms,
        num_turns=num_turns,
        is_error=True,
        error_message=error_message,
        error_category=error_category,
        retry_count=retry_count,
        human_reason=human_reason,
        suggested_action=suggested_action,
    )


# ============================================================
# SDK Response
# ============================================================


@dataclass
class SDKResponse:
    text: str
    session_id: str = ""
    cost_usd: float = 0.0  # Deprecated — kept for backward compat; prefer token fields
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    num_turns: int = 0
    is_error: bool = False
    error_message: str = ""
    error_category: ErrorCategory = ErrorCategory.UNKNOWN
    retry_count: int = 0  # How many retries were needed
    human_reason: str = ""  # User-facing bilingual (Hebrew+English) error reason
    suggested_action: str = ""  # Suggested recovery action for the frontend to display
    tool_uses: list[str] | None = (
        None  # Tool names used during the query (e.g. ["Write", "Edit", "Bash"])
    )


# ============================================================
# Connection Pool / Concurrency Limiter
# ============================================================


class _ConnectionPool:
    """Limits concurrent SDK queries to prevent resource exhaustion.

    The Claude CLI spawns a subprocess per query. Too many concurrent
    queries can overwhelm the system (file descriptors, memory, CPU).
    This semaphore-based pool ensures we stay within safe limits.
    """

    def __init__(self, max_concurrent: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_count = 0
        self._total_queries = 0
        self._total_errors = 0
        self._total_tokens = 0
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        """Return the number of currently acquired semaphore slots."""
        return self._active_count

    @property
    def stats(self) -> dict:
        """Return a snapshot of semaphore utilization statistics."""
        return {
            "active": self._active_count,
            "total_queries": self._total_queries,
            "total_errors": self._total_errors,
            "total_tokens": self._total_tokens,
        }

    async def acquire(self):
        """Acquire a semaphore slot, blocking until one is available."""
        await self._semaphore.acquire()
        async with self._lock:
            self._active_count += 1
            self._total_queries += 1

    async def release(self, tokens: int = 0, is_error: bool = False):
        """Release a previously acquired semaphore slot."""
        async with self._lock:
            self._active_count -= 1
            self._total_tokens += tokens
            if is_error:
                self._total_errors += 1
        self._semaphore.release()


# ============================================================
# Circuit Breaker
# ============================================================


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and rejecting requests.

    Callers should catch this to avoid wasting resources on a
    known-failing SDK backend.
    """

    def __init__(self, open_since: float, failures: int, recovery_in: float) -> None:
        self.open_since: float = open_since
        self.failures: int = failures
        self.recovery_in: float = recovery_in
        super().__init__(
            f"Circuit breaker OPEN (since {open_since:.1f}s ago, "
            f"{failures} consecutive failures, "
            f"recovery probe in {recovery_in:.1f}s)"
        )


class CircuitState(str, Enum):
    """States of the circuit breaker."""

    CLOSED = "closed"  # Normal — all requests pass through
    OPEN = "open"  # Tripped — all requests rejected
    HALF_OPEN = "half_open"  # Probing — one request allowed to test recovery


class CircuitBreaker:
    """Async-safe, thread-safe circuit breaker for SDK calls.

    Opens after ``failure_threshold`` consecutive failures within
    ``failure_window_seconds``.  Once open, all calls are rejected with
    ``CircuitOpenError`` until ``recovery_timeout_seconds`` elapses.
    After that, the circuit enters *half-open* and allows one probe
    request.  A successful probe closes the circuit; a failed probe
    re-opens it.

    Thread safety is achieved via a ``threading.Lock`` guarding state
    mutations (safe because the critical section is tiny — no I/O).
    The asyncio-facing methods use ``asyncio.to_thread`` for the lock
    acquisition so the event loop is never blocked.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        failure_window_seconds: float = 60.0,
        recovery_timeout_seconds: float = 30.0,
    ) -> None:
        self._failure_threshold: int = failure_threshold
        self._failure_window: float = failure_window_seconds
        self._recovery_timeout: float = recovery_timeout_seconds

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_timestamps: list[float] = []
        self._last_failure_time: float = 0.0
        self._lock: threading.Lock = threading.Lock()
        self._half_open_in_flight: bool = False

    # ---- Public properties ----

    @property
    def state(self) -> CircuitState:
        """Current circuit state (read without lock — safe for enum reads)."""
        return self._state

    @property
    def stats(self) -> dict[str, object]:
        """Snapshot of circuit breaker statistics."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - self._failure_window
            recent = [t for t in self._failure_timestamps if t > cutoff]
            return {
                "state": self._state.value,
                "recent_failures": len(recent),
                "failure_threshold": self._failure_threshold,
                "last_failure_ago": (
                    round(now - self._last_failure_time, 1) if self._last_failure_time > 0 else None
                ),
            }

    # ---- Core operations (sync internals, async wrappers) ----

    def _record_success_sync(self) -> None:
        """Record a successful call — resets the circuit to CLOSED."""
        with self._lock:
            self._failure_timestamps.clear()
            self._state = CircuitState.CLOSED
            self._half_open_in_flight = False

    def _record_failure_sync(self) -> None:
        """Record a failed call — may trip the circuit to OPEN."""
        with self._lock:
            now = time.monotonic()
            self._failure_timestamps.append(now)
            self._last_failure_time = now

            # Prune timestamps outside the window
            cutoff = now - self._failure_window
            self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]

            if len(self._failure_timestamps) >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._half_open_in_flight = False
                logger.warning(
                    f"[CircuitBreaker] OPENED: {len(self._failure_timestamps)} "
                    f"failures in {self._failure_window}s window"
                )

    def _check_can_execute_sync(self) -> bool:
        """Check if a request may proceed.  Returns True if allowed.

        Side effects:
        - Transitions OPEN → HALF_OPEN after recovery timeout.
        - Sets ``_half_open_in_flight`` to prevent multiple probes.
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self._recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_in_flight = True
                    logger.info("[CircuitBreaker] HALF-OPEN: allowing one probe request")
                    return True
                return False

            # HALF_OPEN — allow only one concurrent probe
            if self._state == CircuitState.HALF_OPEN:
                if not self._half_open_in_flight:
                    self._half_open_in_flight = True
                    return True
                return False

            return False  # pragma: no cover

    # ---- Async wrappers (safe for the event loop) ----

    async def record_success(self) -> None:
        """Async wrapper — record a successful call."""
        await asyncio.to_thread(self._record_success_sync)

    async def record_failure(self) -> None:
        """Async wrapper — record a failed call."""
        await asyncio.to_thread(self._record_failure_sync)

    async def check_can_execute(self) -> bool:
        """Async wrapper — check if a request may proceed."""
        return await asyncio.to_thread(self._check_can_execute_sync)

    async def raise_if_open(self) -> None:
        """Raise ``CircuitOpenError`` if the circuit is currently open.

        Call this before sending a request to fail fast.
        """
        can = await self.check_can_execute()
        if not can:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_failure_time
                recovery_in = max(0.0, self._recovery_timeout - elapsed)
            raise CircuitOpenError(
                open_since=elapsed,
                failures=len(self._failure_timestamps),
                recovery_in=recovery_in,
            )


# Module-level pool (shared across all ClaudeSDKManager instances)
_pool = _ConnectionPool(max_concurrent=10)  # Allow more concurrent agents

# Module-level circuit breaker (shared across all ClaudeSDKManager instances)
# Uses config constants so values can be tuned via env vars (ARCH-08 fix)
from config import CIRCUIT_FAILURE_THRESHOLD, CIRCUIT_FAILURE_WINDOW, CIRCUIT_RECOVERY_TIMEOUT

_circuit_breaker = CircuitBreaker(
    failure_threshold=CIRCUIT_FAILURE_THRESHOLD,
    failure_window_seconds=CIRCUIT_FAILURE_WINDOW,
    recovery_timeout_seconds=CIRCUIT_RECOVERY_TIMEOUT,
)


# ============================================================
# Performance Tracker (for ETA estimation)
# ============================================================


class _PerformanceTracker:
    """Tracks historical agent query durations for ETA estimation.

    Maintains a rolling window of the last N durations per agent role.
    Used by _GranularEventEmitter to estimate remaining time for
    currently running agents.

    Thread-safe via threading.Lock (critical section is tiny — no I/O).
    """

    def __init__(self, window_size: int = 20):
        self._window_size: int = window_size
        self._durations: dict[str, collections.deque] = {}
        self._lock: threading.Lock = threading.Lock()

    def record(self, agent_role: str, duration_seconds: float) -> None:
        """Record a completed query duration for an agent role."""
        with self._lock:
            if agent_role not in self._durations:
                self._durations[agent_role] = collections.deque(maxlen=self._window_size)
            self._durations[agent_role].append(duration_seconds)

    def get_avg_duration(self, agent_role: str) -> float:
        """Get the rolling average duration for an agent role.

        Returns 0.0 if no historical data is available.
        """
        with self._lock:
            durations = self._durations.get(agent_role)
            if not durations:
                return 0.0
            return sum(durations) / len(durations)

    @property
    def stats(self) -> dict[str, dict]:
        """Snapshot of all tracked agent performance data."""
        with self._lock:
            return {
                role: {
                    "avg_duration": round(sum(d) / len(d), 1) if d else 0,
                    "samples": len(d),
                    "last": round(d[-1], 1) if d else 0,
                }
                for role, d in self._durations.items()
            }


# Module-level performance tracker (shared across all queries)
_performance_tracker = _PerformanceTracker(window_size=20)


# ============================================================
# Granular Event Emitter (helper for _consume_stream)
# ============================================================


class _GranularEventEmitter:
    """Emits granular streaming events during SDK query consumption.

    Handles throttling (via EventThrottler), thinking state tracking,
    tool start/end pairing, and periodic ETA estimation.

    Created per _consume_stream() call with an optional on_event callback.
    If on_event is None, all methods are no-ops for zero overhead.

    Event types emitted:
    - agent_text_chunk: Throttled per-chunk text updates (max 4/sec/agent)
    - agent_thinking: Boolean indicator when agent is reasoning (no tools yet)
    - tool_start: Fired when a ToolUseBlock is encountered
    - tool_end: Fired when a ToolResultBlock is encountered or stream ends
    - agent_eta: Periodic ETA estimate based on historical performance
    """

    def __init__(
        self,
        on_event,
        agent_role: str | None,
        request_id: str = "",
    ):
        self._on_event = on_event
        self._agent_role: str = agent_role or "unknown"
        self._request_id: str = request_id
        self._start_time: float = time.monotonic()
        self._thinking_active: bool = False
        self._has_used_tools: bool = False
        self._last_eta_time: float = 0.0
        self._text_offset: int = 0
        self._message_count: int = 0
        # Track active tools by tool_use_id → (tool_name, start_monotonic)
        self._active_tools: dict[str, tuple[str, float]] = {}

    @property
    def is_active(self) -> bool:
        """Whether the emitter has a callback and will emit events."""
        return self._on_event is not None

    async def _emit(self, event_type: str, **data) -> None:
        """Emit a single event through the on_event callback."""
        if not self._on_event:
            return
        event = {
            "type": event_type,
            "agent": self._agent_role,
            "timestamp": time.time(),
            **data,
        }
        try:
            await self._on_event(event)
        except Exception as e:
            logger.error(
                "[%s] Granular event emission error (%s): %s",
                self._request_id,
                event_type,
                e,
            )

    async def on_text_chunk(self, new_text: str, full_text_len: int) -> None:
        """Handle new text from the agent stream.

        Emits agent_thinking (start) if no tools have been used yet,
        and emits throttled agent_text_chunk events.
        """
        if not self._on_event or not new_text:
            return

        # Detect thinking phase: text being generated before any tool use
        if not self._has_used_tools and not self._thinking_active:
            self._thinking_active = True
            await self._emit("agent_thinking", active=True)

        # Import throttler lazily to avoid circular imports
        from dashboard.events import text_chunk_throttler

        throttle_key = f"{self._agent_role}:agent_text_chunk"
        # Prepare the text chunk (last 500 chars for bandwidth efficiency)
        preview = new_text[-500:] if len(new_text) > 500 else new_text
        chunk_event = {
            "type": "agent_text_chunk",
            "agent": self._agent_role,
            "text": preview,
            "offset": self._text_offset,
            "timestamp": time.time(),
        }

        if text_chunk_throttler.should_emit(throttle_key):
            await self._emit("agent_text_chunk", text=preview, offset=self._text_offset)
        else:
            text_chunk_throttler.set_pending(throttle_key, chunk_event)

        self._text_offset += len(new_text)

    async def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict,
        tool_info: str,
        tool_use_id: str = "",
    ) -> None:
        """Emit tool_start when a ToolUseBlock is encountered.

        Also ends the thinking phase if still active.
        """
        if not self._on_event:
            return

        # End thinking phase when tools start
        if self._thinking_active:
            self._thinking_active = False
            await self._emit("agent_thinking", active=False)

        self._has_used_tools = True
        tid = tool_use_id or f"tool_{int(time.monotonic() * 1000000)}"
        self._active_tools[tid] = (tool_name, time.monotonic())

        await self._emit(
            "tool_start",
            tool_name=tool_name,
            tool_input=tool_input,
            description=tool_info,
            tool_use_id=tid,
        )

    async def on_tool_end(self, tool_use_id: str, success: bool = True) -> None:
        """Emit tool_end when a ToolResultBlock is encountered."""
        if not self._on_event:
            return

        tool_name, start_time = self._active_tools.pop(tool_use_id, ("unknown", time.monotonic()))
        duration_ms = int((time.monotonic() - start_time) * 1000)

        await self._emit(
            "tool_end",
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            success=success,
            duration_ms=duration_ms,
        )

    async def maybe_emit_eta(self, num_turns: int, max_turns: int) -> None:
        """Emit agent_eta periodically (every 5 seconds).

        Uses historical performance data when available, falls back to
        turn-based progress estimation.
        """
        if not self._on_event:
            return

        now = time.monotonic()
        if now - self._last_eta_time < 5.0:
            return  # Throttle to once per 5 seconds
        self._last_eta_time = now
        self._message_count += 1

        elapsed = now - self._start_time
        avg = _performance_tracker.get_avg_duration(self._agent_role)

        if avg > 0:
            # Historical data available — estimate from average duration
            eta = max(0.0, avg - elapsed)
            progress = min(95.0, (elapsed / avg) * 100.0)
        elif max_turns > 0 and num_turns > 0:
            # Fallback: estimate from turn progress
            turn_progress = num_turns / max_turns
            progress = min(95.0, turn_progress * 100.0)
            estimated_total = elapsed / turn_progress if turn_progress > 0 else elapsed * 2
            eta = max(0.0, estimated_total - elapsed)
        else:
            # No data — report elapsed only
            eta = 0.0
            progress = 0.0

        await self._emit(
            "agent_eta",
            eta_seconds=round(eta, 1),
            elapsed_seconds=round(elapsed, 1),
            progress_pct=round(progress, 1),
        )

    async def flush(self) -> None:
        """Flush any pending state at the end of a stream.

        Emits pending throttled text chunks, ends thinking phase,
        and closes any outstanding tool_start events.
        """
        if not self._on_event:
            return

        # Flush pending throttled text chunk
        from dashboard.events import text_chunk_throttler

        throttle_key = f"{self._agent_role}:agent_text_chunk"
        pending = text_chunk_throttler.pop_pending(throttle_key)
        if pending:
            # Emit the pending event directly
            try:
                await self._on_event(pending)
            except Exception as e:
                logger.error(
                    "[%s] Error flushing pending text chunk: %s",
                    self._request_id,
                    e,
                )
        text_chunk_throttler.reset(throttle_key)

        # End thinking if still active
        if self._thinking_active:
            self._thinking_active = False
            await self._emit("agent_thinking", active=False)

        # Close any outstanding tool_start events (tools that never got a result)
        for tid in list(self._active_tools.keys()):
            await self.on_tool_end(tid, success=True)

    def record_completion(self, duration_seconds: float) -> None:
        """Record this query's duration for future ETA estimations."""
        _performance_tracker.record(self._agent_role, duration_seconds)


# ============================================================
# Claude SDK Manager
# ============================================================


class ClaudeSDKManager:
    """Wrapper over claude-agent-sdk's query() with connection pooling,
    error classification, structured retry logic, and request/response logging.
    """

    def __init__(self):
        self._pool = _pool  # Share the module-level pool
        self._circuit_breaker = _circuit_breaker  # Share the module-level circuit breaker
        # Keep strong references to generators until they're explicitly closed.
        # This prevents Python's GC from collecting them in a random asyncio task,
        # which triggers the anyio cancel-scope RuntimeError.
        self._active_generators: set = set()

    @property
    def pool_stats(self) -> dict:
        """Return current connection pool and circuit breaker statistics."""
        stats = self._pool.stats
        stats["circuit_breaker"] = self._circuit_breaker.stats
        return stats

    async def query(
        self,
        prompt: str,
        system_prompt: str,
        cwd: str,
        session_id: str | None = None,
        max_turns: int = 10,
        max_budget_usd: float = 2.0,
        permission_mode: str | None = "bypassPermissions",
        on_stream=None,
        on_tool_use=None,
        allowed_tools: list[str] | None = None,
        tools: list[str] | None = None,
        agent_role: str | None = None,
        retry_attempt: int = 0,
        on_event=None,
    ) -> SDKResponse:
        """Send a query to Claude Agent SDK with connection pooling.

        Args:
            prompt: The user/agent prompt.
            system_prompt: System prompt for the agent role.
            cwd: Working directory for the agent.
            session_id: If set, resumes a previous session.
            max_turns: Max agentic turns.
            max_budget_usd: Max budget for this query.
            permission_mode: Permission mode — "bypassPermissions" for full
                access, None for default (no auto-tool-approval).
            on_stream: Optional async callback for real-time text updates.
            on_tool_use: Optional async callback for tool use events.
            tools: Base tool set — [] disables ALL tools (passes --tools ""),
                None uses default tools. Different from allowed_tools.
            agent_role: Optional agent role name for per-role timeout lookup
                (e.g. "researcher", "developer"). Falls back to global
                AGENT_TIMEOUT_SECONDS if not specified. Also used to tag
                granular streaming events with the agent identity.
            retry_attempt: Current retry attempt number (0 = first try).
                Used for timeout escalation — first retry gets 50% longer.
            on_event: Optional async callback for granular streaming events.
                Receives dicts with 'type' field: agent_text_chunk,
                agent_thinking, tool_start, tool_end, agent_eta.

        Returns:
            SDKResponse with text, session_id, cost, error classification, etc.
        """
        # Resolve per-agent timeout with escalation
        effective_timeout = get_agent_timeout(agent_role, retry_attempt)

        # Sandbox enforcement: reject cwd that escapes the projects root
        from config import CLAUDE_PROJECTS_ROOT, SANDBOX_ENABLED

        if SANDBOX_ENABLED and cwd:
            cwd_resolved = str(Path(cwd).resolve())
            root_resolved = str(Path(CLAUDE_PROJECTS_ROOT).resolve())
            if not cwd_resolved.startswith(root_resolved + "/") and cwd_resolved != root_resolved:
                logger.critical(
                    f"SANDBOX VIOLATION: cwd={cwd!r} is outside projects root={root_resolved!r}"
                )
                violation_msg = f"Sandbox violation: working directory {cwd!r} is outside allowed root {root_resolved!r}"
                return _make_error_response(
                    text=violation_msg,
                    error_message=violation_msg,
                    error_category=ErrorCategory.PERMANENT,
                )

        # Build options
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            cwd=cwd,
            cli_path=SYSTEM_CLI_PATH,
            # can_use_tool requires streaming mode — always True.
            include_partial_messages=True,
            sandbox={"enabled": False},
            can_use_tool=_make_project_guard(cwd),
        )

        if permission_mode:
            options.permission_mode = permission_mode
        if allowed_tools is not None:
            options.allowed_tools = allowed_tools
        if tools is not None:
            options.tools = tools
        if session_id:
            options.resume = session_id

        # Request logging
        request_id = f"req_{int(time.monotonic() * 1000) % 100000}"
        prompt_preview = prompt[:100].replace("\n", " ")
        logger.info(
            f"[{request_id}] SDK query START: "
            f"max_turns={max_turns}, budget=${max_budget_usd}, "
            f"timeout={effective_timeout}s (role={agent_role or 'default'}, attempt={retry_attempt}), "
            f"session={'resume:' + session_id[:12] + '...' if session_id else 'new'}, "
            f"tools={'disabled' if tools is not None and len(tools) == 0 else 'default'}, "
            f"pool_active={self._pool.active_count}, "
            f'prompt="{prompt_preview}..."'
        )
        query_start = time.monotonic()

        # Acquire connection pool slot
        try:
            await asyncio.wait_for(self._pool.acquire(), timeout=SEMAPHORE_ACQUIRE_TIMEOUT)
        except TimeoutError:
            logger.error(f"[{request_id}] Connection pool exhausted — waited 60s")
            return _make_error_response(
                text="Error: Connection pool exhausted. Too many concurrent queries.",
                session_id=session_id or "",
                error_message="Connection pool exhausted (60s timeout)",
                error_category=ErrorCategory.TRANSIENT,
            )

        # Snapshot running Claude processes BEFORE we start ours.
        # Used to identify and kill orphans if timeout/cancel fails to
        # clean up the subprocess properly.
        pids_before = _snapshot_claude_pids()
        # Mutable container so _consume_stream can populate it and
        # the finally block in query() can unregister them.
        my_pids: list[set[int]] = [set()]

        try:
            # Run the stream consumption with timeout
            result = await asyncio.wait_for(
                self._consume_stream(
                    prompt,
                    options,
                    on_stream,
                    on_tool_use,
                    request_id,
                    on_event=on_event,
                    agent_role=agent_role,
                    max_turns=max_turns,
                    pids_before=pids_before,
                    my_pids_out=my_pids,
                ),
                timeout=effective_timeout,
            )

            # Response logging
            elapsed = time.monotonic() - query_start
            if result.is_error:
                result.error_category = classify_error(result.error_message)
                reason, action = get_error_reason(result.error_category)
                result.human_reason = reason
                result.suggested_action = action
                logger.warning(
                    f"[{request_id}] SDK query ERROR ({elapsed:.1f}s): "
                    f"category={result.error_category.value}, "
                    f'msg="{result.error_message[:150]}", '
                    f"tokens={result.total_tokens}, turns={result.num_turns}"
                )
            else:
                logger.info(
                    f"[{request_id}] SDK query OK ({elapsed:.1f}s): "
                    f"text_len={len(result.text)}, tokens={result.total_tokens} "
                    f"(in={result.input_tokens}, out={result.output_tokens}), "
                    f"turns={result.num_turns}, session={result.session_id[:12] + '...' if result.session_id else 'none'}"
                )

            return result

        except TimeoutError:
            elapsed = time.monotonic() - query_start
            logger.warning(
                f"[{request_id}] SDK query TIMEOUT after {elapsed:.1f}s "
                f"(limit={effective_timeout}s, role={agent_role or 'default'})"
            )
            # CRITICAL: Kill any orphan Claude processes that survived cancellation.
            # asyncio.wait_for cancels the coroutine, which calls gen.aclose(),
            # which SHOULD trigger transport.close() → process.terminate().
            # But if anyio cancel-scope RuntimeError is suppressed during cleanup,
            # the subprocess keeps running forever. This is the safety net.
            # FIX(C-2): Use async wrapper to avoid blocking the event loop with time.sleep().
            killed = await _async_kill_orphan_claude_processes(pids_before)
            if killed:
                logger.warning(
                    f"[{request_id}] Killed {killed} orphan Claude process(es) after timeout"
                )
            return _make_error_response(
                text=f"Error: Agent timed out after {effective_timeout} seconds",
                session_id=session_id or "",
                error_message=f"Timeout after {effective_timeout}s (role={agent_role or 'default'})",
                error_category=ErrorCategory.TRANSIENT,
            )
        except asyncio.CancelledError:
            logger.info(f"[{request_id}] SDK query CANCELLED — re-raising")
            # Kill orphans on cancel too — same subprocess leak risk
            # FIX(C-2): Use async wrapper to avoid blocking the event loop with time.sleep().
            killed = await _async_kill_orphan_claude_processes(pids_before)
            if killed:
                logger.warning(
                    f"[{request_id}] Killed {killed} orphan Claude process(es) after cancel"
                )
            # CRITICAL: Re-raise CancelledError so asyncio.wait_for and
            # asyncio.gather can propagate cancellation correctly.
            # Swallowing it here caused the DAG executor to treat cancelled
            # tasks as completed (with an error response) instead of
            # propagating the cancellation signal.
            raise
        except Exception as e:
            elapsed = time.monotonic() - query_start
            category = classify_error(str(e))
            logger.error(
                f"[{request_id}] SDK query EXCEPTION ({elapsed:.1f}s): "
                f"category={category.value}, error={e}",
                exc_info=True,
            )
            # Kill orphans on unexpected exceptions too
            # FIX(C-2): Use async wrapper to avoid blocking the event loop with time.sleep().
            await _async_kill_orphan_claude_processes(pids_before)
            return _make_error_response(
                text=f"Error: {e}",
                session_id=session_id or "",
                error_message=str(e),
                error_category=category,
            )
        finally:
            # Always unregister our PIDs from the global registry.
            if my_pids[0]:
                _unregister_active_pids(my_pids[0])
            # Always release the pool slot — even on CancelledError.
            # The finally block runs regardless of whether we re-raised.
            tokens = 0
            is_err = True
            try:
                # result may not exist if we hit an exception before assignment
                tokens = result.total_tokens  # type: ignore[possibly-undefined]
                is_err = result.is_error  # type: ignore[possibly-undefined]
            except (NameError, UnboundLocalError):
                pass
            try:
                await self._pool.release(tokens=tokens, is_error=is_err)
            except Exception as _rel_err:
                # Pool release must never prevent CancelledError propagation;
                # log at WARNING so slot leaks are visible in production.
                logger.warning("SDK pool release failed (slot may be leaked): %s", _rel_err)

    async def _consume_stream(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        on_stream=None,
        on_tool_use=None,
        request_id: str = "",
        on_event=None,
        agent_role: str | None = None,
        max_turns: int = 10,
        pids_before: set[int] | None = None,
        my_pids_out: list[set[int]] | None = None,
    ) -> SDKResponse:
        """Consume the SDK async stream and return the final SDKResponse.

        With include_partial_messages=True, we get intermediate AssistantMessage
        objects showing tool use and partial text in real time.

        When on_event is provided, emits granular streaming events:
        - agent_text_chunk: per-chunk text (throttled to max 4/sec/agent)
        - agent_thinking: boolean when agent is reasoning before tool use
        - tool_start/tool_end: paired events for each tool execution
        - agent_eta: periodic ETA estimates based on historical performance
        """
        text_parts: list[str] = []
        result_session_id = ""
        cost_usd = 0.0
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        duration_ms = 0
        num_turns = 0
        observed_turns = 0  # Local turn counter based on ToolUseBlock messages
        last_seen_text = ""  # Track to avoid duplicate stream callbacks
        message_count = 0
        tool_uses: list[str] = []  # Track tools used for logging

        # Granular event emitter (no-ops if on_event is None)
        emitter = _GranularEventEmitter(
            on_event=on_event,
            agent_role=agent_role,
            request_id=request_id,
        )

        # Use ClaudeSDKClient with connect() + query() pattern (not the
        # standalone query() function) because can_use_tool requires prompt
        # as AsyncIterable. connect(None) uses an empty async iterable
        # internally, satisfying the requirement. query(prompt) then sends
        # the prompt as a plain string. This matches claude-code-telegram.
        logger.info(f"[{request_id}] Creating ClaudeSDKClient...")
        client = ClaudeSDKClient(options)
        _pids_registered = False  # Track whether we registered PIDs for this query
        try:
            logger.info(f"[{request_id}] Calling client.connect()...")
            await client.connect()
            logger.info(f"[{request_id}] client.connect() done, calling client.query()...")
            await client.query(prompt)
            logger.info(f"[{request_id}] client.query() done, starting message loop...")

            async for raw_data in client._query.receive_messages():
                try:
                    message = parse_message(raw_data)
                except Exception as parse_err:
                    logger.debug(f"[{request_id}] Skipping unparseable message: {parse_err}")
                    continue
                if message is None:
                    continue
                message_count += 1

                # Register our subprocess PIDs after the first message arrives.
                # At this point the Claude subprocess is definitely running.
                # This prevents other queries' orphan cleanup from killing our process.
                if not _pids_registered and pids_before is not None:
                    _pids_registered = True
                    pids_after = _snapshot_claude_pids()
                    new_pids = pids_after - pids_before
                    if new_pids:
                        _register_active_pids(new_pids)
                        if my_pids_out is not None:
                            my_pids_out[0] = new_pids
                        logger.debug(
                            f"[{request_id}] Registered {len(new_pids)} Claude PIDs: {new_pids}"
                        )

                if isinstance(message, AssistantMessage):
                    # Extract content from this message (may be partial or complete)
                    turn_text = ""
                    tool_info = ""
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            turn_text += block.text
                        elif isinstance(block, ToolUseBlock):
                            # Show which tool the agent is using
                            tool_name = block.name
                            tool_input = block.input if block.input else {}
                            # Format tool use for display
                            if tool_name in ("Read", "read_file"):
                                path = tool_input.get("file_path") or tool_input.get("path", "")
                                tool_info = f"📄 Reading: {path}"
                            elif tool_name in ("Write", "write_file", "create_file"):
                                path = tool_input.get("file_path") or tool_input.get("path", "")
                                tool_info = f"✏️ Writing: {path}"
                            elif tool_name in ("Edit", "edit_file"):
                                path = tool_input.get("file_path") or tool_input.get("path", "")
                                tool_info = f"🔧 Editing: {path}"
                            elif tool_name in ("Bash", "execute_bash", "bash"):
                                cmd = str(tool_input.get("command", ""))[:100]
                                tool_info = f"💻 Running: `{cmd}`"
                            elif tool_name in ("Glob", "glob", "ListFiles"):
                                pattern = tool_input.get("pattern", "")
                                tool_info = f"🔍 Searching: {pattern}"
                            elif tool_name in ("Grep", "grep", "SearchFiles"):
                                pattern = tool_input.get("pattern", "")
                                tool_info = f"🔎 Grep: {pattern}"
                            else:
                                tool_info = f"🔧 {tool_name}"

                            tool_uses.append(tool_name)
                            observed_turns += 1

                            # Emit turn_progress event so callers can track
                            if emitter.is_active:
                                await emitter._emit(
                                    "turn_progress",
                                    turns_used=observed_turns,
                                    max_turns=max_turns,
                                    remaining=max(0, max_turns - observed_turns),
                                    pct=round(observed_turns / max(max_turns, 1) * 100, 1),
                                )

                            # Emit granular tool_start event
                            if emitter.is_active:
                                tool_use_id = (
                                    getattr(block, "id", "")
                                    or f"tool_{int(time.monotonic() * 1000000)}"
                                )
                                # Truncate tool_input for the event
                                truncated_for_event = {}
                                for k, v in (tool_input or {}).items():
                                    if isinstance(v, str) and len(v) > 200:
                                        truncated_for_event[k] = v[:200] + "..."
                                    else:
                                        truncated_for_event[k] = v
                                await emitter.on_tool_start(
                                    tool_name,
                                    truncated_for_event,
                                    tool_info,
                                    tool_use_id,
                                )

                            # Fire on_tool_use callback with structured data
                            if on_tool_use:
                                try:
                                    # Truncate tool_input for the callback
                                    truncated_input = {}
                                    for k, v in (tool_input or {}).items():
                                        if isinstance(v, str) and len(v) > 200:
                                            truncated_input[k] = v[:200] + "..."
                                        else:
                                            truncated_input[k] = v
                                    await on_tool_use(tool_name, tool_info, truncated_input)
                                except Exception as e:
                                    logger.error(f"[{request_id}] on_tool_use callback error: {e}")

                    # Call stream callback with meaningful updates
                    if on_stream and (turn_text != last_seen_text or tool_info):
                        try:
                            update = ""
                            if tool_info:
                                update = tool_info
                            if turn_text and turn_text != last_seen_text:
                                # Show last 300 chars of new text
                                new_text = turn_text[len(last_seen_text) :]
                                preview = new_text[-300:] if len(new_text) > 300 else new_text
                                if update:
                                    update += f"\n\n{preview}"
                                else:
                                    update = preview
                            if update:
                                await on_stream(update)
                        except Exception as e:
                            logger.error(f"[{request_id}] Stream callback error: {e}")
                        last_seen_text = turn_text

                    # Collect final text — each turn may extend or replace the previous
                    if turn_text:
                        if text_parts and turn_text.startswith(text_parts[-1]):
                            # This turn's text extends the last one (streaming partial)
                            text_parts[-1] = turn_text
                        elif not text_parts or turn_text != text_parts[-1]:
                            # New distinct turn text
                            text_parts.append(turn_text)

                elif isinstance(message, ResultMessage):
                    result_session_id = message.session_id or ""
                    cost_usd = message.total_cost_usd or 0.0
                    duration_ms = message.duration_ms or 0
                    num_turns = message.num_turns or 0

                    # Extract token counts from usage dict
                    usage = message.usage or {}
                    input_tokens = int(usage.get("input_tokens", 0) or 0)
                    output_tokens = int(usage.get("output_tokens", 0) or 0)
                    total_tokens = input_tokens + output_tokens

                    # ResultMessage may also carry a result text
                    if message.result and message.result not in text_parts:
                        text_parts.append(message.result)

                    combined = "\n\n".join(text_parts).strip()

                    # If agent did work via tools but produced no text output,
                    # provide a meaningful fallback so the orchestrator knows work was done.
                    if not combined and not message.is_error:
                        tools_summary = ", ".join(set(tool_uses)) if tool_uses else "unknown"
                        combined = (
                            f"✅ Task completed via tool use ({num_turns} turn(s), "
                            f"tools: {tools_summary}). "
                            "No text output — work was done directly. "
                            "Verify results in the workspace files."
                        )

                    # Log tool usage summary
                    if tool_uses:
                        logger.debug(
                            f"[{request_id}] Tools used ({len(tool_uses)}): "
                            f"{', '.join(tool_uses[:20])}"
                        )

                    if message.is_error:
                        cat = classify_error(message.result or "")
                        hr, sa = get_error_reason(cat)
                    else:
                        cat = ErrorCategory.UNKNOWN
                        hr, sa = "", ""

                    return SDKResponse(
                        text=combined,
                        session_id=result_session_id,
                        cost_usd=cost_usd,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        duration_ms=duration_ms,
                        num_turns=num_turns,
                        is_error=message.is_error,
                        error_message=""
                        if not message.is_error
                        else (message.result or "Unknown error"),
                        error_category=cat,
                        human_reason=hr,
                        suggested_action=sa,
                    )

                else:
                    # Unknown message type — log and skip
                    logger.debug(f"[{request_id}] Unknown message type: {type(message).__name__}")

        except asyncio.CancelledError:
            raise  # Propagate cancellation
        except RuntimeError as e:
            if "cancel scope" in str(e):
                # anyio cancel-scope leak — suppress and return partial results
                logger.warning(
                    f"[{request_id}] anyio cancel scope error after {message_count} messages (suppressed)"
                )
                combined = "\n\n".join(text_parts).strip()
                return _make_error_response(
                    text=combined or "Agent interrupted (anyio cleanup error).",
                    session_id=result_session_id,
                    error_message="anyio cancel scope error",
                    error_category=ErrorCategory.TRANSIENT,
                    cost_usd=cost_usd,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    duration_ms=duration_ms,
                    num_turns=num_turns,
                )
            raise
        except Exception as e:
            # Stream processing error (not a timeout — that's caught in query())
            logger.error(
                f"[{request_id}] Stream processing error after {message_count} messages: {e}",
                exc_info=True,
            )
            combined = "\n\n".join(text_parts).strip()
            return _make_error_response(
                text=combined or f"Error during stream processing: {e}",
                session_id=result_session_id,
                error_message=f"Stream error: {e}",
                cost_usd=cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                num_turns=num_turns,
            )
        finally:
            try:
                await client.disconnect()
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug(
                        f"[{request_id}] Suppressed anyio cancel scope during client disconnect"
                    )
                else:
                    logger.warning(f"[{request_id}] RuntimeError during client disconnect: {e}")
            except Exception as _dc_err:
                logger.debug("[%s] Client disconnect error (non-critical): %s", request_id, _dc_err)

        # Stream ended without ResultMessage — treat as success if we got any text,
        # otherwise flag as a real error (SDK failed to complete the stream).
        combined = "\n\n".join(text_parts).strip()
        if combined:
            logger.warning(
                f"[{request_id}] Stream ended without ResultMessage but got {len(combined)} chars of text "
                f"({message_count} messages). Treating as partial success."
            )
        else:
            logger.error(
                f"[{request_id}] Stream ended without ResultMessage and no text "
                f"({message_count} messages). This is an SDK-level error."
            )
        is_err = not bool(combined)
        if is_err:
            hr, sa = get_error_reason(ErrorCategory.TRANSIENT)
        else:
            hr, sa = "", ""
        return SDKResponse(
            text=combined or "⚠️ Agent produced no text output (stream ended unexpectedly).",
            session_id=result_session_id,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
            num_turns=num_turns,
            is_error=is_err,
            error_message=f"Stream ended without ResultMessage after {message_count} messages"
            if is_err
            else "",
            error_category=ErrorCategory.TRANSIENT,
            human_reason=hr,
            suggested_action=sa,
        )

    async def query_with_retry(
        self,
        prompt: str,
        system_prompt: str,
        cwd: str,
        session_id: str | None = None,
        max_turns: int = 10,
        max_budget_usd: float = 2.0,
        max_retries: int = SDK_MAX_RETRIES,
        permission_mode: str | None = "bypassPermissions",
        on_stream=None,
        on_tool_use=None,
        allowed_tools: list[str] | None = None,
        tools: list[str] | None = None,
        agent_role: str | None = None,
        on_event=None,
    ) -> SDKResponse:
        """Query with automatic retry based on error classification.

        Includes circuit breaker protection: if the SDK has failed
        ``failure_threshold`` consecutive times within the failure window,
        all calls are rejected with ``CircuitOpenError`` until the recovery
        timeout elapses.

        Retry strategy (exponential backoff):
        - TRANSIENT: retry with 1s, 2s, 4s backoff
        - RATE_LIMIT: retry with 5s, 15s, 30s backoff
        - SESSION: invalidate session_id, retry fresh (no backoff)
        - AUTH/BUDGET/PERMANENT: don't retry at all
        - UNKNOWN: retry once with 2s backoff
        """
        # --- Circuit breaker gate ---
        await self._circuit_breaker.raise_if_open()

        last_response: SDKResponse | None = None
        current_session = session_id
        total_cost = 0.0
        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens_accum = 0
        current_prompt = prompt

        for attempt in range(
            1, max_retries + 2
        ):  # +2 because range is exclusive and attempt 1 is the initial try
            if attempt > max_retries + 1:
                break

            is_retry = attempt > 1

            if is_retry:
                # Re-check circuit breaker before each retry
                try:
                    await self._circuit_breaker.raise_if_open()
                except CircuitOpenError:
                    logger.warning(f"Circuit breaker opened during retries (attempt {attempt})")
                    raise

                logger.info(
                    f"SDK query retry {attempt - 1}/{max_retries} "
                    f"(previous error: {last_response.error_category.value if last_response else 'none'})"
                )

            response = await self.query(
                prompt=current_prompt,
                system_prompt=system_prompt,
                cwd=cwd,
                session_id=current_session,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                permission_mode=permission_mode,
                on_stream=on_stream,
                on_tool_use=on_tool_use,
                allowed_tools=allowed_tools,
                tools=tools,
                agent_role=agent_role,
                retry_attempt=attempt - 1,
                on_event=on_event,
            )

            # Accumulate cost and tokens across retries
            total_cost += response.cost_usd
            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens
            total_tokens_accum += response.total_tokens

            if not response.is_error:
                response.retry_count = attempt - 1
                response.cost_usd = total_cost  # Include cost from failed attempts
                response.input_tokens = total_input_tokens
                response.output_tokens = total_output_tokens
                response.total_tokens = total_tokens_accum
                # Record success with circuit breaker
                await self._circuit_breaker.record_success()
                return response

            last_response = response
            category = response.error_category

            # Only record retryable failures with circuit breaker.
            # AUTH/BUDGET/PERMANENT errors are per-request issues, not
            # systemic failures — they should NOT trip the circuit breaker.
            if category not in (ErrorCategory.AUTH, ErrorCategory.BUDGET, ErrorCategory.PERMANENT):
                await self._circuit_breaker.record_failure()

            # Don't retry permanent errors or timeouts.
            # Timeouts are NOT worth retrying — the agent already ran for the
            # full timeout duration and was killed.  Retrying would just burn
            # another full timeout period (potentially 15+ minutes) with a
            # fresh subprocess that has no context from the previous attempt.
            is_timeout = "timeout" in response.error_message.lower()
            if (
                category in (ErrorCategory.AUTH, ErrorCategory.BUDGET, ErrorCategory.PERMANENT)
                or is_timeout
            ):
                logger.error(
                    f"SDK non-retryable error ({category.value}, timeout={is_timeout}): {response.error_message}"
                )
                response.cost_usd = total_cost
                response.input_tokens = total_input_tokens
                response.output_tokens = total_output_tokens
                response.total_tokens = total_tokens_accum
                return response

            # Check if we have retries left
            if attempt > max_retries:
                break

            # Compute backoff based on error category
            current_prompt = prompt  # Reset to original prompt for next retry
            if category == ErrorCategory.RATE_LIMIT:
                # Aggressive backoff for rate limits: 5s, 15s, 30s
                backoff = min(5 * (3 ** (attempt - 1)), 30)
                logger.warning(f"Rate limited, backing off {backoff}s: {response.error_message}")
            elif category == ErrorCategory.SESSION:
                # Session errors: retry fresh immediately
                backoff = 0.5
                current_session = None  # Drop the stale session
                logger.warning(f"Session error, retrying without session: {response.error_message}")
            elif category == ErrorCategory.TRANSIENT:
                # Exponential backoff: 1s, 2s, 4s
                backoff = min(1 * (2 ** (attempt - 1)), 8)
                logger.warning(f"Transient error, retrying in {backoff}s: {response.error_message}")
            elif category == ErrorCategory.UNKNOWN:
                # Cautious single retry
                backoff = 2
                logger.warning(
                    f"Unknown error, cautious retry in {backoff}s: {response.error_message}"
                )
            else:
                # Shouldn't reach here, but be safe
                backoff = 2
                logger.warning(f"Unexpected category {category.value}, retrying in {backoff}s")

            await asyncio.sleep(backoff)

        # All retries exhausted
        logger.error(
            f"All {max_retries} retry attempts exhausted. "
            f"Last error: {last_response.error_message if last_response else 'none'}, "
            f"Total tokens: {total_tokens_accum}"
        )
        if last_response:
            last_response.retry_count = max_retries
            last_response.cost_usd = total_cost
            last_response.input_tokens = total_input_tokens
            last_response.output_tokens = total_output_tokens
            last_response.total_tokens = total_tokens_accum
            return last_response
        return _make_error_response(
            text="Error: All retry attempts failed",
            error_message="All retry attempts failed",
            error_category=ErrorCategory.UNKNOWN,
            cost_usd=total_cost,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            total_tokens=total_tokens_accum,
            retry_count=max_retries,
        )
