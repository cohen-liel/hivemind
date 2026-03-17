"""Centralised application settings — single source of truth for all tuneable constants.

Uses Pydantic ``BaseSettings`` (pydantic-settings ≥ 2.0) so that every value
can be overridden via environment variables *or* a ``.env`` file.  This is the
12-factor-app compliant configuration layer for the ``src/`` package.

Resolution order (first wins):
    environment variable  →  ``.env`` file  →  field default

Usage
-----
    from src.config import settings

    pool_size = settings.DB_POOL_SIZE          # int
    host = settings.DASHBOARD_HOST             # str
    limit = settings.PARALLEL_TASKS_LIMIT      # int

Relationship to root ``config.py``
------------------------------------
The legacy root-level ``config.py`` was built before this project adopted
Pydantic Settings.  It reads values via a custom ``_get()`` helper and
exposes them as bare module-level constants (e.g. ``config.AGENT_TIMEOUT_SECONDS``).

``src/config.py`` (this file) supersedes the scattered ``os.getenv()`` calls
found in ``src/db/database.py``, ``src/workers/task_queue.py``, etc.  All
*new* code inside ``src/`` should import from here.  Root-level modules that
cannot yet be migrated may continue to use ``import config`` until they are
moved into ``src/``.

The full list of constants is also mirrored in root ``config.py`` so that
``config.validate_config()`` covers all of them and ``import config`` keeps
working for legacy callers.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Project root (needed for default path construction)
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Settings class
# ---------------------------------------------------------------------------


class AppSettings(BaseSettings):
    """All tuneable constants for the hivemind platform.

    Every field can be overridden by the corresponding environment variable
    (name is the field name, upper-cased).  The ``.env`` file at the project
    root is loaded automatically when present.

    Sections
    --------
    DATABASE        — connection URLs, pool sizing
    AGENT_LIMITS    — timeouts, retries, budget
    DAG_EXECUTION   — DAG executor limits and feature flags
    PARALLEL_QUEUE  — task queue concurrency
    DASHBOARD       — HTTP server host/port/auth
    RATE_LIMITING   — per-IP request limits
    ISOLATION       — per-project DB isolation mode
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # DB_POOL_SIZE == db_pool_size in env
        extra="ignore",  # silently ignore unknown env vars
        populate_by_name=True,
    )

    # ── DATABASE ─────────────────────────────────────────────────────────

    DATABASE_URL: str = Field(
        default="",
        description=(
            "Full async database URL.  If empty, PLATFORM_DB_PATH is used to "
            "construct a SQLite URL.  Supported schemes: "
            "sqlite+aiosqlite://, postgresql+asyncpg://, postgresql://, postgres://."
        ),
    )

    PLATFORM_DB_PATH: Path = Field(
        default=_PROJECT_ROOT / "data" / "platform.db",
        description="Path to the shared SQLite database file (used when DATABASE_URL is empty).",
    )

    # PostgreSQL connection pool (ignored for SQLite)
    DB_POOL_SIZE: int = Field(
        default=5,
        ge=1,
        description="SQLAlchemy ``pool_size`` for PostgreSQL engines (min open connections).",
    )
    DB_MAX_OVERFLOW: int = Field(
        default=10,
        ge=0,
        description=(
            "SQLAlchemy ``max_overflow`` for PostgreSQL engines "
            "(extra connections beyond pool_size allowed under peak load)."
        ),
    )

    # ── AGENT LIMITS ─────────────────────────────────────────────────────

    AGENT_TIMEOUT_SECONDS: int = Field(
        default=900,
        ge=30,
        description="Default wall-clock timeout (seconds) for any agent SDK call.",
    )
    SDK_MAX_RETRIES: int = Field(
        default=2,
        ge=0,
        description="Maximum number of SDK-level retries before propagating the error.",
    )
    MAX_BUDGET_USD: float = Field(
        default=100.0,
        gt=0,
        description="Hard budget ceiling (USD) per orchestration session.",
    )
    SDK_MAX_BUDGET_PER_QUERY: float = Field(
        default=50.0,
        gt=0,
        description="Per-query budget ceiling (USD) passed to the Claude SDK.",
    )
    SDK_MAX_TURNS_PER_QUERY: int = Field(
        default=200,
        ge=1,
        description="Maximum turns passed to the Claude SDK per call.",
    )

    # ── DAG EXECUTION ────────────────────────────────────────────────────

    MAX_TASK_RETRIES: int = Field(
        default=2,
        ge=0,
        description="Direct retries per task inside the DAG executor before marking failed.",
    )
    MAX_REMEDIATION_DEPTH: int = Field(
        default=2,
        ge=0,
        description="Maximum chain length of auto-generated fix_xxx remediation tasks.",
    )
    MAX_TOTAL_REMEDIATIONS: int = Field(
        default=5,
        ge=0,
        description="Total remediation tasks allowed per DAG graph execution.",
    )
    MAX_DAG_ROUNDS: int = Field(
        default=50,
        ge=1,
        description="Safety limit on execution rounds within one DAG run.",
    )
    USE_DAG_EXECUTOR: bool = Field(
        default=True,
        description=(
            "Feature flag: when True and multi_agent=True, the orchestrator uses "
            "the typed-contract DAG execution engine instead of the legacy path."
        ),
    )

    # ── PARALLEL TASK QUEUE ──────────────────────────────────────────────

    PARALLEL_TASKS_LIMIT: int = Field(
        default=5,
        ge=1,
        description=(
            "Maximum number of user-message tasks that may run concurrently for "
            "a single project (asyncio Semaphore).  Owned by task_003."
        ),
    )
    MAX_TASK_HISTORY: int = Field(
        default=200,
        ge=1,
        description="Maximum completed/failed task records to keep in memory per project.",
    )

    # ── DASHBOARD ────────────────────────────────────────────────────────

    DASHBOARD_HOST: str = Field(
        default="127.0.0.1",
        description="Network interface the dashboard HTTP server binds to.",
    )
    DASHBOARD_PORT: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="TCP port the dashboard HTTP server listens on.",
    )
    DASHBOARD_API_KEY: str = Field(
        default="",
        description=(
            "Pre-shared API key for dashboard authentication.  Empty string disables auth. "
            "Set to '0', 'false', 'no', or 'off' to explicitly disable even if the var is set."
        ),
    )

    # ── RATE LIMITING ────────────────────────────────────────────────────

    RATE_LIMIT_MAX_REQUESTS: int = Field(
        default=300,
        ge=1,
        description="Maximum HTTP requests allowed per IP per 60-second sliding window.",
    )
    RATE_LIMIT_BURST: int = Field(
        default=100,
        ge=1,
        description="Maximum requests allowed from one IP in any 5-second window (burst guard).",
    )
    RATE_LIMIT_MAX_STORE_SIZE: int = Field(
        default=500,
        ge=10,
        description="Force rate-limit store cleanup when it exceeds this many entries.",
    )

    # ── PROJECT ISOLATION ────────────────────────────────────────────────

    ISOLATION_MODE: str = Field(
        default="",
        description=(
            "Project isolation mode.  '' or 'row_level' (default) → shared platform.db.  "
            "'per_db' → each project gets its own SQLite file under data/projects/."
        ),
    )

    # ── CORS ─────────────────────────────────────────────────────────────

    CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://localhost:8080",
        description="Comma-separated list of allowed CORS origins.",
    )

    # ── DERIVED PROPERTIES ───────────────────────────────────────────────

    @property
    def auth_enabled(self) -> bool:
        """True when a non-falsy API key is configured."""
        key = self.DASHBOARD_API_KEY
        return bool(key) and key.lower() not in ("0", "false", "no", "off")

    @model_validator(mode="after")
    def _validate_constraints(self) -> AppSettings:
        """Runtime cross-field invariant checks."""
        errors: list[str] = []

        if self.SDK_MAX_BUDGET_PER_QUERY > self.MAX_BUDGET_USD:
            errors.append(
                f"SDK_MAX_BUDGET_PER_QUERY ({self.SDK_MAX_BUDGET_PER_QUERY}) "
                f"> MAX_BUDGET_USD ({self.MAX_BUDGET_USD})"
            )
        if self.ISOLATION_MODE not in ("", "row_level", "per_db"):
            errors.append(
                f"ISOLATION_MODE must be '' | 'row_level' | 'per_db', got '{self.ISOLATION_MODE}'"
            )
        if errors:
            raise ValueError("AppSettings validation errors:\n  • " + "\n  • ".join(errors))
        return self


# ---------------------------------------------------------------------------
# Singleton instance — import this everywhere
# ---------------------------------------------------------------------------

#: Global singleton.  All application code should use ``from src.config import settings``.
settings: AppSettings = AppSettings()

__all__ = ["AppSettings", "settings"]
