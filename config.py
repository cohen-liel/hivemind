"""Configuration for the Web Claude Bot.

Reads settings from environment variables (via .env), optional JSON overrides,
and exposes them as module-level constants.  Import ``config`` anywhere to access.

Resolution order (first wins):
    data/settings_overrides.json  →  environment variable  →  hardcoded default

All public constants are type-hinted.  Call ``validate_config()`` at startup to
assert invariants (e.g. positive timeouts, valid thresholds).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── Load settings overrides from data/settings_overrides.json ────────
_PROJECT_ROOT: Path = Path(__file__).resolve().parent
_OVERRIDES: dict[str, Any] = {}
_overrides_path: Path = _PROJECT_ROOT / "data" / "settings_overrides.json"
if _overrides_path.exists():
    try:
        _raw_overrides = json.loads(_overrides_path.read_text())
        # Normalize keys to lowercase for case-insensitive lookup
        _OVERRIDES = {k.lower(): v for k, v in _raw_overrides.items()}
        logger.info("Loaded settings overrides: %s", list(_OVERRIDES.keys()))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load settings overrides: %s", e)


def _get(key: str, default: str, type_fn: Callable[[str], T] = str) -> T:
    """Resolve a configuration value: overrides > env > *default*.

    Args:
        key: Environment variable / override key name.
        default: Fallback value (as a string — will be converted by *type_fn*).
        type_fn: Conversion function (``int``, ``float``, ``str``, …).

    Returns:
        The resolved value, converted to the type produced by *type_fn*.

    Raises:
        ValueError: If *type_fn* rejects the resolved string (e.g. ``int("abc")``).
    """
    raw: str
    if key.lower() in _OVERRIDES:
        raw = str(_OVERRIDES[key.lower()])
    else:
        raw = os.getenv(key.upper(), default)
    try:
        return type_fn(raw)
    except (ValueError, TypeError) as exc:
        logger.error("Config %s: cannot convert %r via %s — %s", key, raw, type_fn.__name__, exc)
        return type_fn(default)


# CORS origins (comma-separated)
CORS_ORIGINS: list[str] = [
    x.strip()
    for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",")
    if x.strip()
]

# Claude CLI path — configurable for Docker / non-standard installations
CLAUDE_CLI_PATH: str = os.getenv("CLAUDE_CLI_PATH", "claude")

# Projects
PROJECTS_BASE_DIR = Path(os.getenv("CLAUDE_PROJECTS_DIR", "~/hivemind-projects")).expanduser()
try:
    PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)
except OSError as e:
    if not PROJECTS_BASE_DIR.exists():
        raise RuntimeError(f"Cannot create PROJECTS_BASE_DIR {PROJECTS_BASE_DIR}: {e}") from e
    # Already exists (race with another process) — safe to continue

# Agent limits
MAX_TURNS_PER_CYCLE: int = _get("MAX_TURNS_PER_CYCLE", "200", int)
MAX_BUDGET_USD: float = _get("MAX_BUDGET_USD", "50.0", float)  # Real limit per session
AGENT_TIMEOUT_SECONDS: int = _get("AGENT_TIMEOUT_SECONDS", "300", int)  # 5 min default
SESSION_TIMEOUT_SECONDS: int = _get("SESSION_TIMEOUT_SECONDS", "7200", int)  # 2h default

# ── AGENT REGISTRY — Single Source of Truth ─────────────────────────
# Every per-role configuration lives here.  All consumers (dag_executor,
# orchestrator, pm_agent, sdk_client, frontend) derive their values from
# this registry.  To add a new role, add ONE entry here.
#
# Fields:
#   timeout  — wall-clock timeout (seconds) for SDK calls
#   turns    — max_turns limit for the agent
#   budget   — per-task budget (USD) for DAG mode
#   layer    — brain / execution / quality (for PM team listing)
#   emoji    — notification emoji
#   label    — human-readable display name
#   legacy   — True if this is a backward-compat alias (PM won't assign)
# ─────────────────────────────────────────────────────────────────────

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    """Immutable configuration for a single agent role."""

    timeout: int = 900  # seconds
    turns: int = 100  # max_turns
    budget: float = 15.0
    layer: str = "execution"  # brain | execution | quality
    emoji: str = "\U0001f527"  # 🔧
    label: str = ""
    legacy: bool = False  # legacy alias — PM won't assign tasks to it
    # Frontend styling
    tw_color: str = "blue"  # Tailwind color name for border/bg/text classes
    accent: str = "#638cff"  # Hex accent color for UI highlights/glow


AGENT_REGISTRY: dict[str, AgentConfig] = {
    # ── Layer 1: Brain ────────────────────────────────────────────
    "pm": AgentConfig(
        timeout=300,
        turns=8,
        budget=5.0,
        layer="brain",
        emoji="\U0001f9e0",
        label="PM",
        tw_color="orange",
        accent="#f97316",
    ),
    "orchestrator": AgentConfig(
        timeout=1800,
        turns=25,
        budget=20.0,
        layer="brain",
        emoji="\U0001f3af",
        label="Orchestrator",
        tw_color="gray",
        accent="#8b90a5",
    ),
    "memory": AgentConfig(
        timeout=300,
        turns=15,
        budget=5.0,
        layer="brain",
        emoji="\U0001f4da",
        label="Memory",
        tw_color="teal",
        accent="#14b8a6",
    ),
    # ── Layer 2: Execution (write code) ──────────────────────────
    "frontend_developer": AgentConfig(
        timeout=900,
        turns=100,
        budget=15.0,
        layer="execution",
        emoji="\U0001f3a8",
        label="Frontend",
        tw_color="pink",
        accent="#ec4899",
    ),
    "backend_developer": AgentConfig(
        timeout=900,
        turns=100,
        budget=15.0,
        layer="execution",
        emoji="\u26a1",
        label="Backend",
        tw_color="yellow",
        accent="#eab308",
    ),
    "database_expert": AgentConfig(
        timeout=600,
        turns=75,
        budget=10.0,
        layer="execution",
        emoji="\U0001f5c4\ufe0f",
        label="Database",
        tw_color="indigo",
        accent="#6366f1",
    ),
    "devops": AgentConfig(
        timeout=600,
        turns=75,
        budget=10.0,
        layer="execution",
        emoji="\U0001f680",
        label="DevOps",
        tw_color="cyan",
        accent="#22d3ee",
    ),
    # ── Layer 3: Quality (read/analyse) ──────────────────────────
    "security_auditor": AgentConfig(
        timeout=300,
        turns=30,
        budget=5.0,
        layer="quality",
        emoji="\U0001f510",
        label="Security",
        tw_color="red",
        accent="#ef4444",
    ),
    "test_engineer": AgentConfig(
        timeout=600,
        turns=50,
        budget=10.0,
        layer="quality",
        emoji="\U0001f9ea",
        label="Tester",
        tw_color="amber",
        accent="#f5a623",
    ),
    "reviewer": AgentConfig(
        timeout=300,
        turns=25,
        budget=5.0,
        layer="quality",
        emoji="\U0001f50d",
        label="Reviewer",
        tw_color="purple",
        accent="#a78bfa",
    ),
    "researcher": AgentConfig(
        timeout=600,
        turns=40,
        budget=10.0,
        layer="quality",
        emoji="\U0001f50e",
        label="Researcher",
        tw_color="emerald",
        accent="#34d399",
    ),
    "ux_critic": AgentConfig(
        timeout=300,
        turns=20,
        budget=5.0,
        layer="quality",
        emoji="\U0001f3ad",
        label="UX",
        tw_color="fuchsia",
        accent="#d946ef",
    ),
    # ── Legacy aliases (backward compat) ─────────────────────────
    "developer": AgentConfig(
        timeout=900,
        turns=100,
        budget=15.0,
        layer="execution",
        emoji="\U0001f4bb",
        label="Developer",
        tw_color="blue",
        accent="#638cff",
        legacy=True,
    ),
    "tester": AgentConfig(
        timeout=600,
        turns=50,
        budget=10.0,
        layer="quality",
        emoji="\U0001f9ea",
        label="Tester",
        tw_color="amber",
        accent="#f5a623",
        legacy=True,
    ),
    "typescript_architect": AgentConfig(
        timeout=900,
        turns=100,
        budget=15.0,
        layer="execution",
        emoji="\U0001f3a8",
        label="TS Architect",
        tw_color="pink",
        accent="#ec4899",
        legacy=True,
    ),
    "python_backend": AgentConfig(
        timeout=900,
        turns=100,
        budget=15.0,
        layer="execution",
        emoji="\u26a1",
        label="Py Backend",
        tw_color="yellow",
        accent="#eab308",
        legacy=True,
    ),
}

# ── Derived maps (backward-compatible) ───────────────────────────
# These are auto-generated from AGENT_REGISTRY so existing code
# continues to work without changes during the migration.
_DEFAULT_AGENT_TIMEOUT_MAP: dict[str, int] = {
    role: cfg.timeout for role, cfg in AGENT_REGISTRY.items()
}

# Load override from env/settings_overrides.json, merge with defaults
_agent_timeout_map_raw: str = _get("AGENT_TIMEOUT_MAP", "", str)
_agent_timeout_map_override: dict[str, int] = {}
if _agent_timeout_map_raw:
    try:
        _agent_timeout_map_override = {
            k: int(v) for k, v in json.loads(_agent_timeout_map_raw).items()
        }
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Failed to parse AGENT_TIMEOUT_MAP override: %s", e)

AGENT_TIMEOUT_MAP: dict[str, int] = {**_DEFAULT_AGENT_TIMEOUT_MAP, **_agent_timeout_map_override}

# Timeout escalation factor — on first retry, timeout is extended by this factor.
# E.g. 1.5 means 50% longer timeout on retry.
TIMEOUT_ESCALATION_FACTOR: float = _get("TIMEOUT_ESCALATION_FACTOR", "1.5", float)

# SDK settings
SDK_MAX_RETRIES: int = _get("SDK_MAX_RETRIES", "2", int)
SDK_MAX_TURNS_PER_QUERY: int = _get("SDK_MAX_TURNS_PER_QUERY", "25", int)
SDK_MAX_BUDGET_PER_QUERY: float = _get(
    "SDK_MAX_BUDGET_PER_QUERY", "15.0", float
)  # Per-query budget limit

# Session persistence
SESSION_EXPIRY_HOURS: int = _get("SESSION_EXPIRY_HOURS", "24", int)

# Stuck detection
STUCK_SIMILARITY_THRESHOLD: float = 0.85
STUCK_WINDOW_SIZE: int = 4
MAX_ORCHESTRATOR_LOOPS: int = _get("MAX_ORCHESTRATOR_LOOPS", "100", int)
RATE_LIMIT_SECONDS: float = _get("RATE_LIMIT_SECONDS", "0.5", float)

# Budget warning threshold (fraction of MAX_BUDGET_USD, e.g. 0.8 = warn at 80%)
BUDGET_WARNING_THRESHOLD: float = _get("BUDGET_WARNING_THRESHOLD", "0.8", float)

# Stall detection for proactive alerts (seconds)
STALL_ALERT_SECONDS: int = _get(
    "STALL_ALERT_SECONDS", "300", int
)  # 5 min — agents need time to think

# Pipeline settings
PIPELINE_MAX_STEPS: int = _get("PIPELINE_MAX_STEPS", "10", int)

# ── DAG Executor — parallel execution bounds ─────────────────────────
# Maximum number of DAG task *nodes* that execute concurrently within a
# single graph execution.  Lower values reduce memory/CPU contention;
# higher values increase throughput for graphs with many independent tasks.
# Override via env var or data/settings_overrides.json.
DAG_MAX_CONCURRENT_NODES: int = _get("DAG_MAX_CONCURRENT_NODES", "8", int)

# Maximum number of full task-graphs (i.e. user requests) that may execute
# concurrently at the server level.  Each graph runs inside an
# OrchestratorManager; the bounded ingestion queue serialises submissions
# that exceed this limit so the server never becomes overloaded.
DAG_MAX_CONCURRENT_GRAPHS: int = _get("DAG_MAX_CONCURRENT_GRAPHS", "5", int)

# Maximum number of projects that may execute graphs concurrently.
# Within a single project, graphs execute sequentially (FIFO queue).
# Across projects, graphs execute in parallel up to this limit.
MAX_CONCURRENT_PROJECTS: int = _get("MAX_CONCURRENT_PROJECTS", "3", int)

# File-lock acquisition timeout (seconds).  If a writer task cannot
# acquire all its file locks within this window the task is failed
# rather than blocking indefinitely.
FILE_LOCK_TIMEOUT: float = _get("FILE_LOCK_TIMEOUT", "300", float)

# Scheduler check interval (seconds)
SCHEDULER_CHECK_INTERVAL: int = _get("SCHEDULER_CHECK_INTERVAL", "30", int)

# Data directory (kept for STORE_DIR references elsewhere)
STORE_DIR = Path(os.getenv("CONVERSATION_STORE_DIR", str(_PROJECT_ROOT / "data"))).expanduser()
try:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

# Database maintenance
DB_VACUUM_INTERVAL_HOURS: int = _get("DB_VACUUM_INTERVAL_HOURS", "168", int)  # Weekly

# User input validation
MAX_USER_MESSAGE_LENGTH: int = _get("MAX_USER_MESSAGE_LENGTH", "200000", int)

# Request body size limit (bytes)
MAX_REQUEST_BODY_SIZE: int = _get("MAX_REQUEST_BODY_SIZE", str(1 * 1024 * 1024), int)  # 1MB default

# Authentication — auth is enabled only when DASHBOARD_API_KEY is a non-empty,
# non-falsy string.  Explicitly setting it to "0", "false", or "no" disables auth
# even if the env var is technically set.
_raw_api_key = os.getenv("DASHBOARD_API_KEY", "")
AUTH_ENABLED: bool = bool(_raw_api_key) and _raw_api_key.lower() not in ("0", "false", "no", "off")

# Device-based authentication — enabled by default. Set DEVICE_AUTH_ENABLED=false to disable.
_raw_device_auth = os.getenv("DEVICE_AUTH_ENABLED", "true")
DEVICE_AUTH_ENABLED: bool = _raw_device_auth.lower() not in ("0", "false", "no", "off")


# ── Infrastructure constants ─────────────────────────────────────────
# WebSocket
WS_HEARTBEAT_INTERVAL: int = _get(
    "WS_HEARTBEAT_INTERVAL", "10", int
)  # seconds between server pings
WS_SENDER_TIMEOUT: int = _get("WS_SENDER_TIMEOUT", "600", int)  # seconds before closing idle WS
WS_RECONNECT_BASE_DELAY: int = _get(
    "WS_RECONNECT_BASE_DELAY", "1000", int
)  # ms, client-side base delay
WS_RECONNECT_MAX_DELAY: int = _get(
    "WS_RECONNECT_MAX_DELAY", "30000", int
)  # ms, client-side max delay
WS_KEEPALIVE_INTERVAL: int = _get(
    "WS_KEEPALIVE_INTERVAL", "10000", int
)  # ms, client-side keepalive

# Circuit breaker
CIRCUIT_FAILURE_THRESHOLD: int = _get("CIRCUIT_FAILURE_THRESHOLD", "5", int)
CIRCUIT_FAILURE_WINDOW: float = _get("CIRCUIT_FAILURE_WINDOW", "120.0", float)  # seconds
CIRCUIT_RECOVERY_TIMEOUT: float = _get("CIRCUIT_RECOVERY_TIMEOUT", "60.0", float)  # seconds

# State writer
STATE_WRITER_INTERVAL: int = _get("STATE_WRITER_INTERVAL", "10", int)  # seconds

# Cleanup
CLEANUP_INTERVAL: int = _get("CLEANUP_INTERVAL", "3600", int)  # seconds (1 hour)
CLEANUP_KEEP_LAST_ACTIVITY: int = _get("CLEANUP_KEEP_LAST_ACTIVITY", "2000", int)

# ── Database connection pool (SQLAlchemy / PostgreSQL) ───────────────
# These were previously read via os.getenv() inside src/db/database.py (H-3 fix).
DB_POOL_SIZE: int = _get("DB_POOL_SIZE", "5", int)  # SA pool_size for PostgreSQL
DB_MAX_OVERFLOW: int = _get("DB_MAX_OVERFLOW", "10", int)  # SA max_overflow for PostgreSQL

# ── Database URL settings ────────────────────────────────────────────
# Resolved by src/db/url_helpers.resolve_database_url(); exposed here so
# validate_config() can surface misconfiguration early.
DATABASE_URL: str = _get("DATABASE_URL", "", str)  # Empty → fall back to PLATFORM_DB_PATH
PLATFORM_DB_PATH: str = _get("PLATFORM_DB_PATH", str(_PROJECT_ROOT / "data" / "platform.db"), str)

# ── Task execution limits (DAG executor) ────────────────────────────
# Previously hardcoded as module-level literals in dag_executor.py (H-3 fix).
MAX_TASK_RETRIES: int = _get("MAX_TASK_RETRIES", "2", int)  # Direct retries per task
MAX_REMEDIATION_DEPTH: int = _get("MAX_REMEDIATION_DEPTH", "2", int)  # Max fix_xxx chain length
MAX_TOTAL_REMEDIATIONS: int = _get(
    "MAX_TOTAL_REMEDIATIONS", "5", int
)  # Total remediations per graph
MAX_DAG_ROUNDS: int = _get("MAX_DAG_ROUNDS", "50", int)  # Reasonable limit per execution

# ── Feature flags ────────────────────────────────────────────────────
# Previously read via os.getenv() inside orchestrator.py (H-3 fix).
USE_DAG_EXECUTOR: bool = _get("USE_DAG_EXECUTOR", "true", str).lower() == "true"

# ── Project Sandboxing ──────────────────────────────────────────────────────
_raw_sandbox = os.getenv("SANDBOX_ENABLED", "true")
SANDBOX_ENABLED: bool = _raw_sandbox.lower() not in ("0", "false", "no", "off")
CLAUDE_PROJECTS_ROOT: str = str(Path.home() / "claude-projects")  # Hard boundary

# ── Parallel task queue ──────────────────────────────────────────────
# Previously read via os.getenv() inside src/workers/task_queue.py (H-3 fix).
# task_003 agent should migrate src/workers/task_queue.py to read from here.
PARALLEL_TASKS_LIMIT: int = _get(
    "PARALLEL_TASKS_LIMIT", "5", int
)  # Max concurrent tasks per project
MAX_TASK_HISTORY: int = _get("MAX_TASK_HISTORY", "200", int)  # Completed tasks to keep in memory

# ── Dashboard / HTTP server ──────────────────────────────────────────
# Previously read via os.getenv() inside dashboard/api.py and server.py (H-3 fix).
DASHBOARD_HOST: str = _get("DASHBOARD_HOST", "127.0.0.1", str)
DASHBOARD_PORT: int = _get("DASHBOARD_PORT", "8000", int)
DASHBOARD_API_KEY: str = os.getenv("DASHBOARD_API_KEY", "")  # Raw key — AUTH_ENABLED derived below

# Rate limiting (dashboard)
RATE_LIMIT_MAX_REQUESTS: int = _get("RATE_LIMIT_MAX_REQUESTS", "300", int)  # per minute window
RATE_LIMIT_BURST: int = _get("RATE_LIMIT_BURST", "100", int)  # max burst in 5s

# ── Project isolation mode ───────────────────────────────────────────
# Previously read via os.getenv() inside src/projects/project_manager.py (H-3 fix).
ISOLATION_MODE: str = _get("ISOLATION_MODE", "", str).strip().lower()  # "" | "per_db"

# ── Agent execution mode ──────────────────────────────────────────────
# Controls whether agents ask for confirmation before executing or run immediately.
#
#   "autonomous"  — agent executes the full task immediately, no confirmation needed.
#                   Used when the user's intent is clear.
#   "interactive" — agent briefly states its plan and asks the user to confirm or
#                   adjust before executing.  Used for ambiguous or high-impact tasks.
#
# The per-project mode can be overridden in the DB / project config.
# Default: autonomous (matches pre-existing behaviour — agents execute directly).
AGENT_MODE_DEFAULT: str = _get("AGENT_MODE_DEFAULT", "autonomous", str).strip().lower()

# System prompt snippet injected into sub-agent prompts based on the session mode.
AGENT_MODE_PROMPTS: dict[str, str] = {
    "autonomous": (
        "<execution_mode>\n"
        "Work independently. Execute the full task without asking for confirmation "
        "unless you encounter a blocking ambiguity that makes the goal impossible to complete.\n"
        "</execution_mode>"
    ),
    "interactive": (
        "<execution_mode>\n"
        "Before executing, briefly state your plan and ask the user to confirm or adjust. "
        "Wait for approval before making changes.\n"
        "</execution_mode>"
    ),
}


# ── Operational constants (previously hardcoded) ────────────────────
# These were scattered as magic numbers across orchestrator, agents, and SDK.

# Subprocess timeouts
SUBPROCESS_SHORT_TIMEOUT: float = 5.0  # Quick commands (git status, file reads)
SUBPROCESS_MEDIUM_TIMEOUT: float = 30.0  # Medium commands (git diff, builds)
SUBPROCESS_LONG_TIMEOUT: float = 120.0  # Long commands (test suites)

# Async wait/cancel timeouts
ASYNC_WAIT_TIMEOUT: float = 5.0  # Waiting for pending tasks to finish
ASYNC_CANCEL_TIMEOUT: float = 10.0  # Waiting for task cancellation
WS_AUTH_TIMEOUT: float = 10.0  # WebSocket auth handshake

# Retry / resilience
MAX_ANYIO_RETRIES: int = 3  # Retries on spurious CancelledError
MAX_CANCEL_WAIT_RETRIES: int = 10  # Iterations waiting for agent cancellation
SEMAPHORE_ACQUIRE_TIMEOUT: float = 60.0  # SDK pool slot acquisition

# Conversation / logging
CONVERSATION_LOG_MAXLEN: int = 2000  # Max messages kept in memory deque

# ── Memory management — caps for in-memory data structures ──────────
# These prevent unbounded growth during long-running sessions that
# previously caused swap thrashing and kernel panics on macOS.
MAX_STREAM_BUFFER_CHARS: int = 4000  # Per-agent streaming buffer (keep last N chars)
MAX_SHARED_CONTEXT_ENTRIES: int = 50  # Absolute cap on shared_context list
MAX_COMPLETED_ROUNDS: int = 30  # In-memory completed round summaries
MAX_TASK_SUMMARIES: int = 50  # Short task summary strings across sessions
MAX_STRUCTURED_NOTES: int = 200  # StructuredNotes.notes list cap (FIFO)
MAX_TECH_PATTERNS: int = 100  # CrossProjectMemory tech_patterns cap
MAX_CONVENTIONS: int = 100  # CrossProjectMemory conventions cap
MAX_VERIFY_FILES: int = 50  # Max files to syntax-check in verify_fixes
SYMBOL_CACHE_TTL: float = 30.0  # Seconds before _scan_codebase_symbols re-scans

# Sleep intervals (non-configurable operational delays)
AGENT_RETRY_DELAY: float = 4.0  # Delay before retrying a failed agent
AGENT_CANCEL_POLL_DELAY: float = 8.0  # Delay between cancel-poll iterations
SCHEDULER_RETRY_DELAY: float = 60.0  # Delay before retrying scheduler on error
HEALTH_CHECK_INTERVAL: float = 10.0  # Background health-check loop interval
GRACEFUL_STOP_TIMEOUT: float = 10.0  # Max wait for manager.stop() on shutdown
POLL_RETRY_DELAY: float = 2.0  # Delay between poll retries in isolated queries
GIT_DIFF_TIMEOUT: float = 10.0  # Timeout for git diff subprocess
EVENT_QUEUE_TIMEOUT: float = 5.0  # Timeout for event queue get() operations
PYTEST_TIMEOUT: int = 30  # Timeout flag for pytest runs

# ── Agent timeout helper ─────────────────────────────────────────────


def get_agent_timeout(role: str | None = None, retry_attempt: int = 0) -> int:
    """Return the timeout (seconds) for a given agent role with escalation.

    Args:
        role: Agent role name (e.g. "researcher", "developer").
            If ``None`` or not found in ``AGENT_TIMEOUT_MAP``,
            falls back to ``AGENT_TIMEOUT_SECONDS``.
        retry_attempt: Current retry attempt number (0 = first try).
            On the first retry (attempt=1), timeout is escalated by
            ``TIMEOUT_ESCALATION_FACTOR`` (default 1.5×).  Subsequent
            retries keep the escalated value.

    Returns:
        Timeout in seconds (always >= 30 to prevent too-short timeouts).
    """
    base = AGENT_TIMEOUT_MAP.get(role, AGENT_TIMEOUT_SECONDS) if role else AGENT_TIMEOUT_SECONDS
    if retry_attempt >= 1:
        base = int(base * TIMEOUT_ESCALATION_FACTOR)
    return max(base, 30)  # Never go below 30s


# ── Validation ───────────────────────────────────────────────────────


class ConfigError(ValueError):
    """Raised by ``validate_config()`` when a config value is invalid."""


def validate_config() -> list[str]:
    """Check all configuration invariants and return a list of warnings.

    Raises:
        ConfigError: If any *critical* invariant is violated (e.g. negative
            timeout, threshold out of range).

    Returns:
        A (possibly empty) list of non-fatal warning messages.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- Positive integers ------------------------------------------------
    _positive_ints: dict[str, int] = {
        "MAX_TURNS_PER_CYCLE": MAX_TURNS_PER_CYCLE,
        "AGENT_TIMEOUT_SECONDS": AGENT_TIMEOUT_SECONDS,
        "SESSION_TIMEOUT_SECONDS": SESSION_TIMEOUT_SECONDS,
        "SDK_MAX_TURNS_PER_QUERY": SDK_MAX_TURNS_PER_QUERY,
        "SESSION_EXPIRY_HOURS": SESSION_EXPIRY_HOURS,
        "MAX_ORCHESTRATOR_LOOPS": MAX_ORCHESTRATOR_LOOPS,
        "STALL_ALERT_SECONDS": STALL_ALERT_SECONDS,
        "PIPELINE_MAX_STEPS": PIPELINE_MAX_STEPS,
        "SCHEDULER_CHECK_INTERVAL": SCHEDULER_CHECK_INTERVAL,
        "MAX_USER_MESSAGE_LENGTH": MAX_USER_MESSAGE_LENGTH,
        "DAG_MAX_CONCURRENT_NODES": DAG_MAX_CONCURRENT_NODES,
        "DAG_MAX_CONCURRENT_GRAPHS": DAG_MAX_CONCURRENT_GRAPHS,
        "MAX_CONCURRENT_PROJECTS": MAX_CONCURRENT_PROJECTS,
    }
    for name, val in _positive_ints.items():
        if not isinstance(val, int) or val <= 0:
            errors.append(f"{name} must be a positive integer, got {val!r}")

    # --- Non-negative integers --------------------------------------------
    if not isinstance(SDK_MAX_RETRIES, int) or SDK_MAX_RETRIES < 0:
        errors.append(f"SDK_MAX_RETRIES must be >= 0, got {SDK_MAX_RETRIES!r}")

    # --- Positive floats --------------------------------------------------
    _positive_floats: dict[str, float] = {
        "MAX_BUDGET_USD": MAX_BUDGET_USD,
        "SDK_MAX_BUDGET_PER_QUERY": SDK_MAX_BUDGET_PER_QUERY,
    }
    for name, val in _positive_floats.items():
        if not isinstance(val, int | float) or val <= 0:
            errors.append(f"{name} must be a positive number, got {val!r}")

    # --- Thresholds in (0, 1] ---------------------------------------------
    if not (0.0 < STUCK_SIMILARITY_THRESHOLD <= 1.0):
        errors.append(
            f"STUCK_SIMILARITY_THRESHOLD must be in (0, 1], got {STUCK_SIMILARITY_THRESHOLD}"
        )
    if not (0.0 < BUDGET_WARNING_THRESHOLD <= 1.0):
        errors.append(f"BUDGET_WARNING_THRESHOLD must be in (0, 1], got {BUDGET_WARNING_THRESHOLD}")

    # --- Non-negative floats -----------------------------------------------
    if RATE_LIMIT_SECONDS < 0:
        errors.append(f"RATE_LIMIT_SECONDS must be >= 0, got {RATE_LIMIT_SECONDS}")

    # --- Paths ------------------------------------------------------------
    if not PROJECTS_BASE_DIR.is_absolute():
        warnings.append(f"PROJECTS_BASE_DIR is relative: {PROJECTS_BASE_DIR}")

    # --- Relationship checks -----------------------------------------------
    if SDK_MAX_BUDGET_PER_QUERY > MAX_BUDGET_USD:
        warnings.append(
            f"SDK_MAX_BUDGET_PER_QUERY ({SDK_MAX_BUDGET_PER_QUERY}) > MAX_BUDGET_USD ({MAX_BUDGET_USD})"
        )
    if STUCK_WINDOW_SIZE < 2:
        errors.append(f"STUCK_WINDOW_SIZE must be >= 2 for comparison, got {STUCK_WINDOW_SIZE}")

    # --- Agent timeout map -------------------------------------------------
    for role, timeout in AGENT_TIMEOUT_MAP.items():
        if not isinstance(timeout, int) or timeout <= 0:
            errors.append(
                f"AGENT_TIMEOUT_MAP['{role}'] must be a positive integer, got {timeout!r}"
            )

    # --- Timeout escalation factor ----------------------------------------
    if not isinstance(TIMEOUT_ESCALATION_FACTOR, int | float) or TIMEOUT_ESCALATION_FACTOR < 1.0:
        errors.append(f"TIMEOUT_ESCALATION_FACTOR must be >= 1.0, got {TIMEOUT_ESCALATION_FACTOR}")

    # --- F-01: Non-localhost binding (auth optional for personal local tool) ------
    # Auth is disabled by default — security is enforced by project-directory sandboxing.
    _host = DASHBOARD_HOST
    _is_localhost = _host in ("127.0.0.1", "localhost", "::1")
    # No error — personal tool, network binding without auth is intentional.

    # --- F-04: CORS wildcard + credentials is an invalid combination ------
    if "*" in CORS_ORIGINS:
        warnings.append(
            "CORS_ORIGINS contains wildcard '*'. This is insecure for production. "
            "Set CORS_ORIGINS to specific origins (e.g. 'http://localhost:5173')."
        )
        # Per the Fetch spec, Access-Control-Allow-Origin: * combined with
        # Access-Control-Allow-Credentials: true is forbidden by browsers and
        # will cause CORS failures.  We surface this as a hard error because
        # the CORSMiddleware is configured with allow_credentials=True.
        if len(CORS_ORIGINS) == 1 and CORS_ORIGINS[0] == "*":
            errors.append(
                "CORS_ORIGINS='*' is incompatible with allow_credentials=True. "
                "Browsers will reject credentialed cross-origin requests. "
                "Set CORS_ORIGINS to explicit origins or disable credentials."
            )

    # --- AGENT_REGISTRY invariants ----------------------------------------
    _emoji_to_roles: dict[str, list[str]] = {}
    for role, cfg in AGENT_REGISTRY.items():
        # (1) Positive numeric fields
        if not isinstance(cfg.timeout, int | float) or cfg.timeout <= 0:
            errors.append(f"AGENT_REGISTRY['{role}'].timeout must be > 0, got {cfg.timeout!r}")
        if not isinstance(cfg.turns, int) or cfg.turns <= 0:
            errors.append(f"AGENT_REGISTRY['{role}'].turns must be > 0, got {cfg.turns!r}")
        if not isinstance(cfg.budget, int | float) or cfg.budget <= 0:
            errors.append(f"AGENT_REGISTRY['{role}'].budget must be > 0, got {cfg.budget!r}")

        # (2) Budget > MAX_BUDGET_USD warning
        if isinstance(cfg.budget, int | float) and cfg.budget > MAX_BUDGET_USD:
            logger.warning(
                "AGENT_REGISTRY['%s'].budget (%.2f) exceeds MAX_BUDGET_USD (%.2f)",
                role,
                cfg.budget,
                MAX_BUDGET_USD,
            )

        # (3) Collect emojis for non-legacy roles (duplicate check below)
        if not cfg.legacy:
            _emoji_to_roles.setdefault(cfg.emoji, []).append(role)

    # (3) Raise if any two non-legacy roles share an emoji
    for emoji, roles_using_it in _emoji_to_roles.items():
        if len(roles_using_it) > 1:
            errors.append(f"Duplicate emoji {emoji!r} used by non-legacy roles: {roles_using_it}")

    # --- Report ------------------------------------------------------------
    for w in warnings:
        logger.warning("Config warning: %s", w)
    if errors:
        msg = "Configuration validation failed:\n  • " + "\n  • ".join(errors)
        logger.error(msg)
        raise ConfigError(msg)

    logger.info("Configuration validated OK (%d warnings)", len(warnings))
    return warnings


# Predefined projects — set PREDEFINED_PROJECTS env var as JSON to override.
# Example: PREDEFINED_PROJECTS='{"my-project": "~/projects/my-project"}'
# The defaults below are developer-specific and should be overridden in .env (ARCH-03 fix).
_DEFAULT_PROJECTS: dict[str, str] = {}
_env_projects = os.getenv("PREDEFINED_PROJECTS", "")
if _env_projects:
    try:
        PREDEFINED_PROJECTS: dict[str, str] = json.loads(_env_projects)
    except Exception:
        logger.warning("Failed to parse PREDEFINED_PROJECTS env var, using empty defaults")
        PREDEFINED_PROJECTS = _DEFAULT_PROJECTS.copy()
else:
    PREDEFINED_PROJECTS = _DEFAULT_PROJECTS.copy()

# Default agent roles — derived from AGENT_REGISTRY (non-legacy only)
DEFAULT_AGENTS: list[dict[str, str]] = [
    {"name": role, "role": cfg.label or role.replace("_", " ").title()}
    for role, cfg in AGENT_REGISTRY.items()
    if not cfg.legacy
]

# ── Registry helper functions ───────────────────────────────────────


def get_agent_config(role: str) -> AgentConfig:
    """Return the AgentConfig for a role, falling back to defaults."""
    return AGENT_REGISTRY.get(role, AgentConfig())


def get_agent_turns(role: str) -> int:
    """Return the max_turns for a given agent role."""
    return get_agent_config(role).turns


def get_agent_budget(role: str) -> float:
    """Return the per-task budget (USD) for a given agent role."""
    return get_agent_config(role).budget


def get_agent_emoji(role: str) -> str:
    """Return the emoji for a given agent role."""
    return get_agent_config(role).emoji


def get_agent_label(role: str) -> str:
    """Return the human-readable label for a given agent role."""
    cfg = get_agent_config(role)
    return cfg.label or role.replace("_", " ").title()


def get_all_role_names(include_legacy: bool = True) -> set[str]:
    """Return all known agent role names."""
    if include_legacy:
        return set(AGENT_REGISTRY.keys())
    return {r for r, c in AGENT_REGISTRY.items() if not c.legacy}


def get_active_role_names() -> set[str]:
    """Return non-legacy role names (what PM can assign to)."""
    return get_all_role_names(include_legacy=False)


def get_roles_by_layer(layer: str) -> list[str]:
    """Return non-legacy role names for a given layer."""
    return [r for r, c in AGENT_REGISTRY.items() if c.layer == layer and not c.legacy]


def get_agent_mode_prompt(mode: str) -> str:
    """Return the execution-mode system-prompt snippet for sub-agents.

    Args:
        mode: ``"autonomous"`` or ``"interactive"``.
             Falls back to the ``AGENT_MODE_DEFAULT`` if unrecognised.
    Returns:
        A short XML-tagged string to inject into agent system prompts.
    """
    return AGENT_MODE_PROMPTS.get(
        mode, AGENT_MODE_PROMPTS.get(AGENT_MODE_DEFAULT, AGENT_MODE_PROMPTS["autonomous"])
    )


# Backward-compatible emoji map (derived from registry)
AGENT_EMOJI: dict[str, str] = {role: cfg.emoji for role, cfg in AGENT_REGISTRY.items()}
AGENT_EMOJI["user"] = "\U0001f464"  # 👤 — not an agent, just a display role

# --- Orchestrator system prompt ---
# Import org hierarchy for prompt injection
from org_hierarchy import build_org_prompt_section as _build_org_section

ORCHESTRATOR_SYSTEM_PROMPT: str = (
    "<role>\n"
    "You are the Orchestrator — the CEO of a world-class AI software engineering company.\n"
    "You are the STRATEGIC LEADER, INSPECTOR, and COORDINATOR.\n"
    "You have READ-ONLY tools: Read, Glob, Grep, LS, and limited Bash (git log/diff/status, cat, pytest).\n"
    "Use these tools to INSPECT the project state before deciding what to delegate.\n"
    "You delegate to your executive team and specialist agents — you never write code yourself.\n"
    "You operate on a MARATHON mindset — complex tasks take many rounds. You have up to 500 rounds.\n"
    "</role>\n\n" + _build_org_section() + "\n\n"
    "<task_classification>\n"
    "Before your first delegation, classify the task scale. Your strategy MUST match:\n"
    "- SIMPLE (1-2 rounds): Fix a bug, add a field, update config\n"
    "- MEDIUM (3-5 rounds): Add a feature, refactor a module\n"
    "- LARGE (6-10 rounds): Build a service, add authentication\n"
    "- EPIC (10-25 rounds): Build an app, create a complete system\n\n"
    "For EPIC tasks, follow these phases in order:\n"
    "  Phase 1 (rounds 1-3): Architecture — read existing code, plan file structure, create manifest\n"
    "  Phase 2 (rounds 4-8): Foundation — core models, database, config, utilities\n"
    "  Phase 3 (rounds 9-13): Features — implement each feature module one by one\n"
    "  Phase 4 (rounds 14-17): Integration — connect all pieces, handle error paths\n"
    "  Phase 5 (rounds 18-22): Testing — comprehensive tests, fix all failures\n"
    "  Phase 6 (rounds 23+): Polish — error handling, docs, deployment config\n"
    "</task_classification>\n\n"
    "<epic_initialization>\n"
    "When you receive an EPIC task AND .hivemind/PROJECT_MANIFEST.md does NOT exist yet,\n"
    "your FIRST delegations MUST follow this pattern:\n"
    "<example>\n"
    "<delegate>\n"
    '{"agent": "developer", "task": "Create .hivemind/PROJECT_MANIFEST.md with: Goal, Architecture, File Status table, Feature Checklist, Technical Decisions. Then create the project directory structure.", "context": "Phase 1: Architecture. No code yet — planning only."}\n'
    "</delegate>\n"
    "<delegate>\n"
    '{"agent": "reviewer", "task": "Review user requirements. List: (1) ambiguities, (2) technical risks, (3) suggested architecture. Write to .hivemind/REQUIREMENTS_REVIEW.md", "context": "Phase 1: Requirements analysis. No code exists yet."}\n'
    "</delegate>\n"
    "</example>\n"
    "Do NOT start building code until the manifest exists.\n"
    "</epic_initialization>\n\n"
    "<instructions>\n"
    "Before EVERY delegation round, reason through these steps:\n"
    "1. Read .hivemind/PROJECT_MANIFEST.md — what phase are we in? What is done? What is pending?\n"
    "2. Understand the end goal — what does 'done' look like?\n"
    "3. Assess current state — what has changed since last round?\n"
    "4. Decompose — break the current phase into concrete, parallel-executable sub-tasks\n"
    "5. Prioritize — which tasks block others? Which can run in parallel?\n"
    "6. Delegate — assign each sub-task to the right agent with precise instructions\n"
    "7. After agents finish — verify: is it really done? Did it work? What is next?\n"
    "</instructions>\n\n"
    "<agents>\n"
    "Available agents and their specialties:\n"
    "- developer: Reads code, writes code, creates/edits files, runs commands, fixes bugs\n"
    "- reviewer: Reviews code for bugs, security holes, best practices — gives SPECIFIC file:line feedback\n"
    "- tester: Writes AND runs tests — reports exact PASS/FAIL with output\n"
    "- devops: Docker, CI/CD, deployment configs, infrastructure, env setup\n"
    "- researcher: Web research, documentation lookup, competitive analysis, content writing\n"
    "</agents>\n\n"
    "<delegation_format>\n"
    "Use <delegate> blocks with JSON. Each block = one agent with one focused task.\n\n"
    "<example>\n"
    "<delegate>\n"
    '{"agent": "developer", "task": "Add rate limiting middleware to server.py — per-IP, 60 req/min", "context": "FastAPI app, Python 3.11, see config.py for settings"}\n'
    "</delegate>\n"
    "<delegate>\n"
    '{"agent": "reviewer", "task": "Review server.py for security issues and best practices", "context": "FastAPI Python backend, focus on auth, input validation, error handling"}\n'
    "</delegate>\n"
    "</example>\n"
    "</delegation_format>\n\n"
    "<execution_model>\n"
    "The system automatically schedules agents for you:\n"
    "- Code-modifying agents (developer, devops) run SEQUENTIALLY to avoid file conflicts\n"
    "- Read-only agents (reviewer, tester, researcher) run in PARALLEL after writers finish\n"
    "You can safely delegate developer + reviewer + tester in the same round.\n\n"
    "Patterns:\n"
    "- New feature: developer (implement) + reviewer (review) + researcher (docs) → developer (fix issues) + tester (tests)\n"
    "- Bug fix: developer (fix) + tester (regression test) → reviewer (verify) → TASK_COMPLETE\n"
    "- EPIC: developer (plan) + reviewer (requirements) → developer (build) + devops (config) → feature-by-feature with review + test\n"
    "</execution_model>\n\n"
    "<review_workflow>\n"
    "After each round you receive a REVIEW PROMPT with agent summaries and suggested next delegations.\n"
    "Your workflow each round:\n"
    "1. READ the review prompt carefully\n"
    "2. USE your tools to inspect if needed (Read files, git diff, run tests)\n"
    "3. CHECK the task ledger (.hivemind/todo.md) for progress\n"
    "4. USE the suggested <delegate> blocks or create better ones\n"
    "5. Always respond with <delegate> blocks (unless truly TASK_COMPLETE)\n\n"
    "Key insight: Reports about problems are NOT the same as fixing them.\n"
    "If reviewer found 20 issues, delegate developer to FIX them, then re-review.\n"
    "</review_workflow>\n\n"
    "<context_passing>\n"
    "Always pass relevant context to agents:\n"
    "- Developer wrote code → tell reviewer EXACTLY which files to review\n"
    "- Reviewer found issues → tell developer the EXACT file:line and what to fix\n"
    "- Tests failed → give developer the EXACT error message and failing test\n"
    "- Context field: 2-5 sentences of focused, actionable information\n"
    "</context_passing>\n\n"
    "<task_sizing>\n"
    "Each delegation should be a FOCUSED, COMPLETABLE task:\n"
    "- Achievable in 5-15 turns (not 30)\n"
    "- Touches 1-3 files (not 10)\n"
    "- Has a clear 'done' condition\n"
    "- If too big, split into 2-3 smaller delegations\n\n"
    "Good: 'Add rate limiting middleware to server.py'\n"
    "Good: 'Fix path traversal bug in api.py:read_file'\n"
    "Bad: 'Implement the entire authentication system'\n"
    "</task_sizing>\n\n"
    "<failure_handling>\n"
    "When an agent crashes, times out, or reports failure:\n"
    "1. The task was NOT completed — do not treat it as done\n"
    "2. Re-delegate the same task (or simplified version) immediately\n"
    "3. Include the crash error in context so the agent can avoid it\n"
    "4. If same agent crashes twice, try a different agent or simpler approach\n"
    "</failure_handling>\n\n"
    "<completion_criteria>\n"
    "Say TASK_COMPLETE ONLY when ALL of these are true:\n"
    "- All planned files have been created\n"
    "- No agent reported NEEDS_FOLLOWUP or BLOCKED\n"
    "- Tests have been run and pass\n"
    "- Code has been reviewed\n"
    "- No CRITICAL or HIGH issues remain unfixed\n"
    "- The app/service can actually start and run\n"
    "- No crashed agents remain unretried\n\n"
    "Continue working if ANY of these conditions are not met.\n"
    "For EPIC tasks: work through ALL phases before TASK_COMPLETE.\n"
    "</completion_criteria>\n\n"
    "<memory>\n"
    "The system maintains persistent memory:\n"
    "- Task ledger at .hivemind/todo.md — tracks phases, progress, open issues\n"
    "- Experience memory in .hivemind/.experience.md — lessons from past tasks\n"
    "- Auto-evaluation runs tests after code changes and retries on failure\n"
    "Read and use these resources every round to stay on track.\n"
    "</memory>"
)

# --- Solo agent prompt (when user selects 1 agent) ---
SOLO_AGENT_PROMPT: str = (
    "<role>\n"
    "You are a world-class software engineer working directly on a project.\n"
    "</role>\n\n"
    "<workflow>\n"
    "1. READ first — understand the codebase before touching anything\n"
    "2. PLAN — think through the approach before implementing\n"
    "3. IMPLEMENT — write clean, production-quality code\n"
    "4. VERIFY — run tests/linters, check your work actually works\n"
    "5. REPORT — summarize exactly what you changed and why\n"
    "</workflow>\n\n"
    "<standards>\n"
    "- Read existing files fully before modifying them\n"
    "- Write actual working code — never pseudocode\n"
    "- Handle errors explicitly (try/except, logging)\n"
    "- Match the existing code style and patterns\n"
    "- Run tests if they exist; report PASS/FAIL\n"
    "- Commit changes with a clear message when done\n"
    "</standards>\n\n"
    "<when_stuck>\n"
    "- Read the error message carefully before guessing\n"
    "- Check if files/paths exist before operating on them\n"
    "- Try the simplest fix first\n"
    "- After 2 failed attempts, explain exactly what is blocking you\n"
    "</when_stuck>\n\n"
    "<report_format>\n"
    "End your response with:\n"
    "## SUMMARY\n"
    "What you did and whether it worked.\n\n"
    "## FILES CHANGED\n"
    "- path/to/file — what changed and why\n\n"
    "## STATUS\n"
    "DONE | NEEDS_FOLLOWUP: <what> | BLOCKED: <exact error>\n"
    "</report_format>"
)

# --- Sub-agent system prompts ---
# Consolidated into prompts.py — single source of truth.
# Re-exported here for backward compatibility.
