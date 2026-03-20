"""Async SQLAlchemy engine and session factory for the platform persistence layer.

This module provides:
- ``get_engine()``         – creates (and caches) the async engine for the configured DB.
- ``get_session_factory()``– returns an ``AsyncSessionmaker`` bound to the engine.
- ``get_db()``             – async context manager / FastAPI dependency for DB sessions.
- ``init_db()``            – creates all tables (dev/testing only; prod uses Alembic).
- ``start_wal_checkpoint_task()`` – periodic SQLite WAL checkpointing (background).
- ``stop_wal_checkpoint_task()``  – cancel the WAL checkpoint background task.
- ``get_db_health()``      – connection pool status and database metrics.

DATABASE_URL resolution order:
  1. ``DATABASE_URL`` env var — if set, used verbatim (with driver auto-upgrade).
  2. ``PLATFORM_DB_PATH``  env var — path to an SQLite file (default: ``data/platform.db``).

Driver auto-upgrade rules (applied before passing to SQLAlchemy):
  ``sqlite://``             → ``sqlite+aiosqlite://``
  ``postgresql://``         → ``postgresql+asyncpg://``
  ``postgres://``           → ``postgresql+asyncpg://``

Both SQLite and PostgreSQL are fully supported:
- SQLite: uses ``aiosqlite`` + WAL mode + foreign-key enforcement.
- PostgreSQL: uses ``asyncpg`` + connection pool sizing from env.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from src.config import settings
from src.db.models import Base
from src.db.url_helpers import is_sqlite, resolve_database_url, upgrade_driver

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Cached singletons — created once per process.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# WAL checkpoint background task handle.
_wal_checkpoint_task: asyncio.Task | None = None

# WAL checkpoint configuration (overridable via env vars).
WAL_CHECKPOINT_INTERVAL_SECONDS: int = int(
    os.environ.get("HIVEMIND_WAL_CHECKPOINT_INTERVAL", "300")
)
WAL_CHECKPOINT_THRESHOLD_PAGES: int = int(
    os.environ.get("HIVEMIND_WAL_CHECKPOINT_THRESHOLD", "1000")
)


# ---------------------------------------------------------------------------
# URL helpers (public re-exports for backward compatibility)
# ---------------------------------------------------------------------------


def _upgrade_driver(url: str) -> str:
    """Deprecated: import ``upgrade_driver`` from ``src.db.url_helpers`` instead."""
    return upgrade_driver(url)


def _resolve_database_url() -> str:
    """Return the effective database URL with the correct async driver prefix.

    Delegates to ``src.db.url_helpers.resolve_database_url``, which is the
    single source of truth for this logic.
    """
    return resolve_database_url(settings.PLATFORM_DB_PATH)


def _is_sqlite(url: str) -> bool:
    return is_sqlite(url)


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def get_engine(database_url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    """Return (and cache) the async SQLAlchemy engine.

    Args:
        database_url: Override the resolved DATABASE_URL. Useful in tests.
        echo:         If True, log all SQL statements. Defaults to False.

    Engine configuration:
        SQLite:     StaticPool (single in-memory connection) for ``":memory:"``
                    URLs; NullPool otherwise (aiosqlite manages its own pool).
                    WAL mode and FK enforcement enabled via ``connect_args``.
        PostgreSQL: Configurable pool (pool_size, max_overflow) via env vars.
    """
    global _engine
    if _engine is not None and database_url is None:
        return _engine

    url = database_url or _resolve_database_url()

    if _is_sqlite(url):
        if ":memory:" in url:
            # In-memory SQLite for unit tests — share a single connection.
            engine = create_async_engine(
                url,
                echo=echo,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            engine = create_async_engine(
                url,
                echo=echo,
                # aiosqlite manages its own connection; disable SA pool.
                poolclass=NullPool,
                connect_args={"check_same_thread": False},
            )
    else:
        # PostgreSQL — use pool settings from centralised config (H-3 fix).
        engine = create_async_engine(
            url,
            echo=echo,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_pre_ping=True,  # detect stale connections
        )

    if database_url is None:
        _engine = engine
    return engine


# ---------------------------------------------------------------------------
# Post-connect hooks (SQLite only)
# ---------------------------------------------------------------------------


async def _configure_sqlite(engine: AsyncEngine) -> None:
    """Enable WAL mode and foreign-key enforcement for SQLite connections.

    These pragmas must be set per-connection; they are not persistent settings.
    WAL mode dramatically improves concurrent read throughput and reduces
    write-lock contention — critical for an async application.
    """
    if not _is_sqlite(str(engine.url)):
        return

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def get_session_factory(
    database_url: str | None = None,
    *,
    echo: bool = False,
) -> async_sessionmaker[AsyncSession]:
    """Return (and cache) the AsyncSessionmaker bound to the engine.

    Args:
        database_url: Override the resolved DATABASE_URL. Useful in tests.
        echo:         Passed through to ``get_engine()``.
    """
    global _session_factory
    if _session_factory is not None and database_url is None:
        return _session_factory

    engine = get_engine(database_url, echo=echo)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,  # keep attributes accessible after commit
        autoflush=False,
        autocommit=False,
    )

    if database_url is None:
        _session_factory = factory
    return factory


# ---------------------------------------------------------------------------
# FastAPI dependency / async context manager
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an ``AsyncSession`` for use as a FastAPI dependency or context manager.

    Usage (FastAPI):
        @router.get("/projects")
        async def list_projects(db: AsyncSession = Depends(get_db)):
            ...

    Usage (context manager):
        async with get_db() as db:
            result = await db.execute(select(Project))
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Table initialisation (dev / test only)
# ---------------------------------------------------------------------------


async def init_db(database_url: str | None = None) -> None:
    """Create all tables and add any missing columns to existing tables.

    After ``create_all`` (which only creates *new* tables), this function
    inspects every existing table and issues ``ALTER TABLE ADD COLUMN`` for
    any column defined in the ORM model but absent from the DB.  This makes
    the startup self-healing: schema changes in models.py are applied
    automatically without needing a manual migration step.

    Args:
        database_url: Override the resolved DATABASE_URL.
    """
    engine = get_engine(database_url)
    await _configure_sqlite(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn) -> None:
    """Inspect DB tables and ALTER TABLE ADD COLUMN for any missing columns.

    Security: table and column names are quoted using the dialect's identifier
    preparer to prevent SQL injection.  Although these names originate from our
    own ORM metadata (not user input), defence-in-depth requires proper quoting.
    """
    inspector = sa_inspect(conn)
    existing_tables = set(inspector.get_table_names())
    preparer = conn.dialect.identifier_preparer

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # Table was just created by create_all — nothing to patch
        db_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in db_columns:
                col_type = col.type.compile(dialect=conn.dialect)
                nullable = "NULL" if col.nullable else "NOT NULL"
                default = ""
                if col.server_default is not None:
                    default = f" DEFAULT {col.server_default.arg}"
                elif not col.nullable:
                    # SQLite requires a DEFAULT for NOT NULL columns added
                    # via ALTER TABLE.  Infer a safe zero-value from the type.
                    _compiled = col_type.upper()
                    if "INT" in _compiled:
                        default = " DEFAULT 0"
                    elif "FLOAT" in _compiled or "REAL" in _compiled or "NUMERIC" in _compiled:
                        default = " DEFAULT 0.0"
                    elif "BOOL" in _compiled:
                        default = " DEFAULT 0"
                    elif "DATE" in _compiled or "TIME" in _compiled:
                        default = " DEFAULT CURRENT_TIMESTAMP"
                    else:
                        default = " DEFAULT ''"
                quoted_table = preparer.quote(table.name)
                quoted_col = preparer.quote(col.name)
                sql = f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_col} {col_type} {nullable}{default}"
                _log.info("Auto-adding missing column: %s", sql)
                conn.execute(text(sql))


async def drop_db(database_url: str | None = None) -> None:
    """Drop all tables. USE ONLY IN TESTS — data will be lost.

    Args:
        database_url: Override the resolved DATABASE_URL.
    """
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Engine teardown
# ---------------------------------------------------------------------------


async def close_engine() -> None:
    """Dispose of the cached engine, releasing all pooled connections.

    Call this during application shutdown (e.g. FastAPI lifespan ``finally``
    block) to ensure clean connection release.
    """
    global _engine, _session_factory
    await stop_wal_checkpoint_task()
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


# ---------------------------------------------------------------------------
# SQLite WAL checkpoint management
# ---------------------------------------------------------------------------


async def _wal_checkpoint_once(engine: AsyncEngine) -> dict:
    """Run a PRAGMA wal_checkpoint(PASSIVE) and return the result.

    PASSIVE mode checkpoints as many WAL frames as possible without blocking
    concurrent readers/writers — safe for production use.

    Returns a dict with checkpoint results or error info.
    """
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
            row = result.fetchone()
            if row:
                return {
                    "busy": row[0],
                    "wal_pages_total": row[1],
                    "wal_pages_checkpointed": row[2],
                }
            return {"status": "ok", "detail": "no rows returned"}
    except Exception as exc:
        _log.warning("WAL checkpoint failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


async def _wal_checkpoint_loop(engine: AsyncEngine) -> None:
    """Background loop that periodically checkpoints the SQLite WAL.

    Only checkpoints if WAL page count exceeds the configured threshold,
    avoiding unnecessary I/O on idle databases.
    """
    _log.info(
        "WAL checkpoint task started (interval=%ds, threshold=%d pages)",
        WAL_CHECKPOINT_INTERVAL_SECONDS,
        WAL_CHECKPOINT_THRESHOLD_PAGES,
    )
    while True:
        try:
            await asyncio.sleep(WAL_CHECKPOINT_INTERVAL_SECONDS)
            # Check WAL size before checkpointing.
            async with engine.connect() as conn:
                result = await conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
                row = result.fetchone()
                if row and row[1] >= WAL_CHECKPOINT_THRESHOLD_PAGES:
                    _log.info(
                        "WAL checkpoint: %d pages (threshold %d), checkpointed %d",
                        row[1],
                        WAL_CHECKPOINT_THRESHOLD_PAGES,
                        row[2],
                    )
                elif row:
                    _log.debug(
                        "WAL checkpoint skipped: %d pages below threshold %d",
                        row[1],
                        WAL_CHECKPOINT_THRESHOLD_PAGES,
                    )
        except asyncio.CancelledError:
            _log.info("WAL checkpoint task cancelled")
            break
        except Exception as exc:
            _log.warning("WAL checkpoint loop error: %s", exc)
            # Continue running — transient errors should not kill the task.


async def start_wal_checkpoint_task() -> None:
    """Start the periodic WAL checkpoint background task for SQLite engines.

    No-op if the engine is not SQLite or if the task is already running.
    Should be called after ``init_db()`` during application startup.
    """
    global _wal_checkpoint_task
    if _wal_checkpoint_task is not None:
        return

    engine = _engine
    if engine is None:
        return

    if not _is_sqlite(str(engine.url)) or ":memory:" in str(engine.url):
        return

    _wal_checkpoint_task = asyncio.create_task(
        _wal_checkpoint_loop(engine), name="wal-checkpoint"
    )


async def stop_wal_checkpoint_task() -> None:
    """Cancel the WAL checkpoint background task if running."""
    global _wal_checkpoint_task
    if _wal_checkpoint_task is not None:
        _wal_checkpoint_task.cancel()
        try:
            await _wal_checkpoint_task
        except asyncio.CancelledError:
            pass
        _wal_checkpoint_task = None


# ---------------------------------------------------------------------------
# Database health metrics
# ---------------------------------------------------------------------------


def _get_sqlite_db_path(engine: AsyncEngine) -> Path | None:
    """Extract the filesystem path from a SQLite engine URL."""
    url_str = str(engine.url)
    # URL formats: sqlite+aiosqlite:///path or sqlite+aiosqlite:////abs/path
    if ":memory:" in url_str:
        return None
    # Strip scheme — everything after ":///"
    parts = url_str.split(":///", 1)
    if len(parts) == 2 and parts[1]:
        return Path(parts[1])
    return None


async def get_db_health() -> dict:
    """Return database health metrics for the active engine.

    Returns:
        dict with keys:
        - backend: "sqlite" | "postgresql" | "unknown"
        - status: "ok" | "error"
        - pool: connection pool status (PostgreSQL only)
        - wal: WAL checkpoint info (SQLite only)
        - db_file_size_mb: database file size in MB (SQLite only)
        - latency_ms: time to execute a simple query
    """
    engine = _engine
    if engine is None:
        return {"backend": "unknown", "status": "error", "detail": "no engine"}

    url_str = str(engine.url)
    is_sq = _is_sqlite(url_str)
    backend = "sqlite" if is_sq else "postgresql"

    result: dict = {"backend": backend, "status": "ok"}

    # Measure query latency with a lightweight probe.
    t0 = time.monotonic()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        result["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
    except Exception as exc:
        result["status"] = "error"
        result["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
        result["detail"] = str(exc)
        return result

    if is_sq:
        # SQLite-specific metrics.
        db_path = _get_sqlite_db_path(engine)
        if db_path and db_path.exists():
            result["db_file_size_mb"] = round(db_path.stat().st_size / (1024 * 1024), 2)
            # Check for WAL file size too.
            wal_path = db_path.with_suffix(db_path.suffix + "-wal")
            if wal_path.exists():
                result["wal_file_size_mb"] = round(
                    wal_path.stat().st_size / (1024 * 1024), 2
                )

        # WAL page count via a passive checkpoint probe (read-only, no blocking).
        try:
            async with engine.connect() as conn:
                wal_result = await conn.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
                row = wal_result.fetchone()
                if row:
                    result["wal"] = {
                        "busy": row[0],
                        "pages_total": row[1],
                        "pages_checkpointed": row[2],
                    }
        except Exception as exc:
            result["wal"] = {"status": "error", "detail": str(exc)}

        result["checkpoint_task_running"] = _wal_checkpoint_task is not None and not _wal_checkpoint_task.done()
        result["checkpoint_interval_seconds"] = WAL_CHECKPOINT_INTERVAL_SECONDS
        result["checkpoint_threshold_pages"] = WAL_CHECKPOINT_THRESHOLD_PAGES

    else:
        # PostgreSQL pool metrics.
        pool = engine.pool
        result["pool"] = {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "pool_pre_ping": True,
        }
        # Active query count via pg_stat_activity (lightweight).
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE state = 'active' AND pid != pg_backend_pid()"
                        )
                    )
                ).scalar()
                result["active_queries"] = row or 0
        except Exception as exc:
            result["active_queries_error"] = str(exc)

    return result
