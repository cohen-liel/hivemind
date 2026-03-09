"""Async SQLite persistence layer for sessions, projects, and messages.

Provides the ``SessionManager`` class — a high-level async wrapper around
``aiosqlite`` with WAL mode, automatic schema migration, and retry logic
for transient database errors.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from config import SESSION_DB_PATH, SESSION_EXPIRY_HOURS, STORE_DIR, DB_MAX_CONNECTIONS, DB_BACKUP_DIR

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    project_dir TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    away_mode INTEGER DEFAULT 0,
    budget_usd REAL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    agent_role TEXT NOT NULL,
    session_id TEXT NOT NULL,
    cost_usd REAL DEFAULT 0.0,
    turns INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(project_id, user_id, agent_role)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    cost_usd REAL DEFAULT 0.0,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id INTEGER PRIMARY KEY,
    level TEXT DEFAULT 'all',
    budget_warning INTEGER DEFAULT 1,
    stall_alert INTEGER DEFAULT 1,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    task_description TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    cost_usd REAL DEFAULT 0.0,
    turns_used INTEGER DEFAULT 0,
    started_at REAL NOT NULL,
    completed_at REAL,
    summary TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS away_digest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    schedule_time TEXT NOT NULL,
    task_description TEXT NOT NULL,
    repeat TEXT DEFAULT 'once',
    enabled INTEGER DEFAULT 1,
    last_run REAL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_lookup
    ON sessions(project_id, user_id, agent_role);

CREATE INDEX IF NOT EXISTS idx_messages_project
    ON messages(project_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_messages_timestamp
    ON messages(timestamp);

CREATE INDEX IF NOT EXISTS idx_task_history_project
    ON task_history(project_id, completed_at);

CREATE INDEX IF NOT EXISTS idx_away_digest_user
    ON away_digest(user_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_schedules_enabled
    ON schedules(enabled, schedule_time);

CREATE INDEX IF NOT EXISTS idx_sessions_project
    ON sessions(project_id);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    task_description TEXT NOT NULL,
    lesson_type TEXT NOT NULL DEFAULT 'general',
    lesson TEXT NOT NULL,
    tags TEXT DEFAULT '',
    outcome TEXT DEFAULT 'success',
    rounds_used INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lessons_project
    ON lessons(project_id, created_at);

CREATE INDEX IF NOT EXISTS idx_lessons_user
    ON lessons(user_id, created_at);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    sequence_id INTEGER NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    agent TEXT DEFAULT '',
    data TEXT DEFAULT '{}',
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_project_seq
    ON activity_log(project_id, sequence_id);

CREATE INDEX IF NOT EXISTS idx_activity_project
    ON activity_log(project_id, sequence_id);

CREATE INDEX IF NOT EXISTS idx_activity_project_ts
    ON activity_log(project_id, timestamp);

CREATE TABLE IF NOT EXISTS agent_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    agent_role TEXT NOT NULL,
    task_description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'success',
    duration_seconds REAL DEFAULT 0.0,
    cost_usd REAL DEFAULT 0.0,
    turns_used INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    round_number INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_perf_project
    ON agent_performance(project_id, agent_role, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_perf_role
    ON agent_performance(agent_role, status);

CREATE TABLE IF NOT EXISTS orchestrator_state (
    project_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT DEFAULT 'idle',
    current_loop INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0.0,
    shared_context TEXT DEFAULT '[]',
    agent_states TEXT DEFAULT '{}',
    last_user_message TEXT DEFAULT '',
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS _schema_versions (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS _db_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Trigger: auto-increment message_count on projects when a message is inserted
CREATE TRIGGER IF NOT EXISTS trg_messages_insert_count
    AFTER INSERT ON messages
    BEGIN
        UPDATE projects SET message_count = COALESCE(message_count, 0) + 1
        WHERE project_id = NEW.project_id;
    END;

-- Trigger: auto-decrement message_count when messages are deleted
CREATE TRIGGER IF NOT EXISTS trg_messages_delete_count
    AFTER DELETE ON messages
    BEGIN
        UPDATE projects SET message_count = MAX(COALESCE(message_count, 0) - 1, 0)
        WHERE project_id = OLD.project_id;
    END;
"""


class DatabaseError(Exception):
    """Raised when a database operation fails after retries."""


# ── Schema Migrations ────────────────────────────────────────────────────────
# Each migration is (version, name, list_of_sql_statements).
# Migrations are applied in order and tracked in _schema_versions.
# All statements should be idempotent where possible.

_MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "add_session_id_to_messages",
        [
            "ALTER TABLE messages ADD COLUMN session_id TEXT DEFAULT NULL",
        ],
    ),
    (
        2,
        "add_next_run_to_schedules",
        [
            "ALTER TABLE schedules ADD COLUMN next_run REAL DEFAULT NULL",
        ],
    ),
    (
        3,
        "add_performance_indexes",
        [
            "CREATE INDEX IF NOT EXISTS idx_messages_session_ts ON messages(session_id, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(next_run)",
            "CREATE INDEX IF NOT EXISTS idx_messages_agent_ts ON messages(project_id, agent_name, timestamp)",
        ],
    ),
]


# ── Connection Pool ──────────────────────────────────────────────────────────

class ConnectionPool:
    """Lightweight async connection pool for aiosqlite.

    Manages reusable connections with health checks and lazy creation.
    SQLite WAL mode enables concurrent reads; writes are serialized by SQLite.

    Usage::

        pool = ConnectionPool("data/sessions.db", max_connections=5)
        await pool.initialize()

        async with pool.acquire() as db:
            cursor = await db.execute("SELECT ...")
            ...

        await pool.close()
    """

    def __init__(self, db_path: str, max_connections: int = 5) -> None:
        self._db_path: str = db_path
        self._max_connections: int = max(1, max_connections)
        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        self._size: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self._closed: bool = False

    async def _create_connection(self) -> aiosqlite.Connection:
        """Create and configure a new aiosqlite connection."""
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def initialize(self) -> None:
        """Bootstrap the pool (connections are created lazily on acquire)."""
        self._closed = False

    @contextlib.asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool (creates on demand up to max).

        Yields:
            An aiosqlite.Connection ready for use.
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        conn: aiosqlite.Connection | None = None

        # Try to reuse an idle connection from the queue
        try:
            conn = self._pool.get_nowait()
        except asyncio.QueueEmpty:
            # Create a new one if under the limit
            async with self._lock:
                if self._size < self._max_connections:
                    conn = await self._create_connection()
                    self._size += 1

            if conn is None:
                # At capacity — block until one is released
                conn = await self._pool.get()

        try:
            # Health check — recreate if broken
            try:
                await conn.execute("SELECT 1")
            except Exception:
                try:
                    await conn.close()
                except Exception:
                    pass
                async with self._lock:
                    conn = await self._create_connection()
            yield conn
        finally:
            if not self._closed:
                try:
                    await self._pool.put(conn)
                except Exception:
                    pass

    @property
    def size(self) -> int:
        """Return the current number of connections in the pool."""
        return self._size

    @property
    def max_connections(self) -> int:
        """Return the configured maximum pool size."""
        return self._max_connections

    async def close(self) -> None:
        """Close all pooled connections."""
        self._closed = True
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                await conn.close()
            except Exception:
                pass
        self._size = 0


def _retry_on_db_error(max_retries: int = 2, delay: float = 0.1):
    """Decorator: retry an async method on transient SQLite errors.

    Retries on ``aiosqlite.OperationalError`` (e.g. database locked)
    with exponential back-off.  Non-retryable errors propagate immediately.
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except aiosqlite.OperationalError as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        wait = delay * (2 ** attempt)
                        logger.warning(
                            "%s attempt %d/%d failed (%s), retrying in %.1fs",
                            fn.__name__, attempt + 1, max_retries + 1, exc, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            fn.__name__, max_retries + 1, exc,
                        )
            raise DatabaseError(f"{fn.__name__} failed after {max_retries + 1} attempts") from last_exc
        return wrapper
    return decorator


class SessionManager:
    """Async SQLite persistence for sessions, projects, and messages.

    Usage::

        mgr = SessionManager()
        await mgr.initialize()
        # … use mgr …
        await mgr.close()

    Or as an async context manager::

        async with SessionManager() as mgr:
            await mgr.save_project(…)
    """

    def __init__(self, db_path: str = SESSION_DB_PATH, max_connections: int = DB_MAX_CONNECTIONS) -> None:
        self.db_path: str = db_path
        self._db: aiosqlite.Connection | None = None
        self._pool: ConnectionPool = ConnectionPool(db_path, max_connections)
        self._max_connections: int = max_connections

    async def initialize(self) -> None:
        """Create tables, run migrations, and initialize connection pool.

        Raises:
            aiosqlite.Error: If the database cannot be opened or schema creation fails.
        """
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        # Enable WAL mode for better concurrent read/write performance
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        # Initialize connection pool for concurrent operations
        await self._pool.initialize()
        # Run versioned schema migrations
        await self._run_migrations()
        # Legacy column migrations (for pre-migration-system DBs)
        await self._migrate_add_columns()
        await self._migrate_json()
        logger.info(
            "SessionManager initialized: %s (pool_max=%d)",
            self.db_path, self._max_connections,
        )

    async def close(self) -> None:
        """Close the connection pool and primary database connection."""
        # Close pool connections first
        if self._pool:
            try:
                await self._pool.close()
            except Exception as exc:
                logger.warning("Error closing connection pool: %s", exc)
        # Close primary connection
        if self._db:
            try:
                await self._db.close()
            except Exception as exc:
                logger.warning("Error closing database: %s", exc)
            finally:
                self._db = None
            logger.debug("SessionManager closed")

    async def __aenter__(self) -> "SessionManager":
        """Support ``async with SessionManager() as mgr:``."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close the DB connection on context exit."""
        await self.close()

    async def is_healthy(self) -> bool:
        """Return ``True`` if the database connection is alive and responsive."""
        try:
            db = await self._get_db()
            cursor = await db.execute("SELECT 1")
            row = await cursor.fetchone()
            return row is not None and row[0] == 1
        except Exception:
            return False

    async def _get_db(self) -> aiosqlite.Connection:
        if not self._db:
            raise RuntimeError("SessionManager not initialized. Call initialize() first.")
        return self._db

    @contextlib.asynccontextmanager
    async def _connect(self):
        """Async context manager that yields a pooled DB connection.

        Uses the connection pool for concurrent-safe access.
        Falls back to the primary connection if the pool is unavailable.
        """
        if self._pool and not self._pool._closed:
            async with self._pool.acquire() as db:
                yield db
        else:
            db = await self._get_db()
            yield db

    # --- Session CRUD ---

    async def get_session(self, user_id: int, project_id: str, agent_role: str) -> str | None:
        """Return the SDK session_id for resuming, or None if no active session."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT session_id FROM sessions WHERE project_id=? AND user_id=? AND agent_role=? AND status='active'",
            (project_id, user_id, agent_role),
        )
        row = await cursor.fetchone()
        return row["session_id"] if row else None

    async def save_session(
        self,
        user_id: int,
        project_id: str,
        agent_role: str,
        session_id: str,
        cost: float = 0.0,
        turns: int = 0,
    ):
        """Save or update a session for a given project+user+role."""
        db = await self._get_db()
        now = time.time()
        await db.execute(
            """INSERT INTO sessions (project_id, user_id, agent_role, session_id, cost_usd, turns, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
               ON CONFLICT(project_id, user_id, agent_role)
               DO UPDATE SET session_id=?, cost_usd=cost_usd+?, turns=turns+?, updated_at=?, status='active'""",
            (project_id, user_id, agent_role, session_id, cost, turns, now, now,
             session_id, cost, turns, now),
        )
        await db.commit()

    async def invalidate_session(self, user_id: int, project_id: str, agent_role: str):
        """Mark a session as invalidated so it won't be resumed."""
        db = await self._get_db()
        await db.execute(
            "UPDATE sessions SET status='invalidated', updated_at=? WHERE project_id=? AND user_id=? AND agent_role=?",
            (time.time(), project_id, user_id, agent_role),
        )
        await db.commit()

    # --- Message CRUD ---

    async def add_message(
        self,
        project_id: str,
        agent_name: str,
        role: str,
        content: str,
        cost_usd: float = 0.0,
    ):
        """Append a message to the project conversation log."""
        db = await self._get_db()
        await db.execute(
            "INSERT INTO messages (project_id, agent_name, role, content, cost_usd, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, agent_name, role, content, cost_usd, time.time()),
        )
        await db.commit()

    async def get_recent_messages(self, project_id: str, count: int = 15) -> list[dict]:
        """Return the last N messages for a project."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT agent_name, role, content, cost_usd, timestamp FROM messages WHERE project_id=? ORDER BY timestamp DESC LIMIT ?",
            (project_id, count),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]

    # --- Project CRUD ---

    async def save_project(
        self,
        project_id: str,
        user_id: int,
        name: str,
        description: str,
        project_dir: str,
        status: str = "active",
    ):
        """Create or update a project."""
        db = await self._get_db()
        now = time.time()
        await db.execute(
            """INSERT INTO projects (project_id, user_id, name, description, project_dir, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                   name=?, description=?, project_dir=?, status=?, updated_at=?""",
            (project_id, user_id, name, description, project_dir, status, now, now,
             name, description, project_dir, status, now),
        )
        await db.commit()

    async def load_project(self, project_id: str) -> dict | None:
        """Load project metadata."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM projects WHERE project_id=?",
            (project_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_projects(self) -> list[dict]:
        """List all projects, sorted by most recently updated."""
        db = await self._get_db()
        # Use cached message_count column (maintained by triggers) instead of expensive LEFT JOIN
        cursor = await db.execute(
            """SELECT project_id, user_id, name, description, project_dir,
                      status, created_at, updated_at,
                      COALESCE(message_count, 0) as message_count
               FROM projects
               ORDER BY updated_at DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_status(self, project_id: str, status: str):
        """Update a project's status."""
        db = await self._get_db()
        await db.execute(
            "UPDATE projects SET status=?, updated_at=? WHERE project_id=?",
            (status, time.time(), project_id),
        )
        await db.commit()

    # Whitelist of columns that can be updated via update_project_fields.
    # Prevents SQL injection through dynamic column names.
    _UPDATABLE_PROJECT_FIELDS = frozenset({
        "name", "description", "project_dir", "status",
        "away_mode", "budget_usd", "message_count",
    })

    async def update_project_fields(self, project_id: str, **fields):
        """Update project fields safely with column-name whitelist."""
        if not fields:
            return
        # Reject any column names not in the whitelist
        invalid = set(fields.keys()) - self._UPDATABLE_PROJECT_FIELDS
        if invalid:
            raise ValueError(f"Disallowed column names: {', '.join(sorted(invalid))}")
        db = await self._get_db()
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [time.time(), project_id]
        await db.execute(f"UPDATE projects SET {sets}, updated_at=? WHERE project_id=?", vals)
        await db.commit()

    async def get_messages_paginated(self, project_id: str, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """Return paginated messages and total count."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT agent_name, role, content, cost_usd, timestamp FROM messages "
            "WHERE project_id=? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (project_id, limit, offset),
        )
        rows = await cursor.fetchall()
        cursor2 = await db.execute("SELECT COUNT(*) FROM messages WHERE project_id=?", (project_id,))
        total = (await cursor2.fetchone())[0]
        return [dict(row) for row in reversed(rows)], total

    async def get_project_tasks(self, project_id: str, limit: int = 50) -> list[dict]:
        """Return task history for a project."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM task_history WHERE project_id=? ORDER BY started_at DESC LIMIT ?",
            (project_id, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def clear_project_data(self, project_id: str):
        """Clear all messages, sessions, and task_history for a project."""
        db = await self._get_db()
        await db.execute("DELETE FROM messages WHERE project_id=?", (project_id,))
        await db.execute("DELETE FROM sessions WHERE project_id=?", (project_id,))
        await db.execute("DELETE FROM task_history WHERE project_id=?", (project_id,))
        await db.commit()

    # --- Cleanup ---

    async def clear_messages(self, project_id: str):
        """Delete all messages for a project."""
        db = await self._get_db()
        await db.execute("DELETE FROM messages WHERE project_id=?", (project_id,))
        await db.commit()

    async def clear_stale_messages(self, project_id: str):
        """Remove old error messages and messages from the previous architecture."""
        db = await self._get_db()
        # Delete messages from old architecture (system/update, architect role)
        await db.execute(
            "DELETE FROM messages WHERE project_id=? AND (agent_name='system' OR role='update' OR agent_name='architect')",
            (project_id,),
        )
        # Delete error messages (stale errors clutter the log)
        await db.execute(
            "DELETE FROM messages WHERE project_id=? AND content LIKE 'Error:%'",
            (project_id,),
        )
        await db.execute(
            "DELETE FROM messages WHERE project_id=? AND content LIKE 'Invalid API key%'",
            (project_id,),
        )
        await db.commit()

    async def delete_project(self, project_id: str):
        """Delete a project and all its associated sessions and messages."""
        db = await self._get_db()
        await db.execute("DELETE FROM messages WHERE project_id=?", (project_id,))
        await db.execute("DELETE FROM sessions WHERE project_id=?", (project_id,))
        await db.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
        await db.commit()

    async def get_project_total_cost(self, project_id: str) -> float:
        """Return the total cost_usd spent across all sessions for a project."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM sessions WHERE project_id=?",
            (project_id,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def get_project_budget(self, project_id: str) -> float:
        """Return the per-project budget (0 = unlimited)."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT budget_usd FROM projects WHERE project_id=?",
            (project_id,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row and row[0] else 0.0

    async def set_project_budget(self, project_id: str, budget_usd: float):
        """Set the per-project budget."""
        db = await self._get_db()
        await db.execute(
            "UPDATE projects SET budget_usd=?, updated_at=? WHERE project_id=?",
            (budget_usd, time.time(), project_id),
        )
        await db.commit()

    # --- Notification Preferences ---

    async def get_notification_prefs(self, user_id: int) -> dict:
        """Get notification preferences for a user."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT level, budget_warning, stall_alert FROM notification_prefs WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if row:
            return {"level": row["level"], "budget_warning": bool(row["budget_warning"]), "stall_alert": bool(row["stall_alert"])}
        # Default prefs
        return {"level": "all", "budget_warning": True, "stall_alert": True}

    async def set_notification_prefs(self, user_id: int, level: str = "all", budget_warning: bool = True, stall_alert: bool = True):
        """Set notification preferences for a user."""
        db = await self._get_db()
        now = time.time()
        await db.execute(
            """INSERT INTO notification_prefs (user_id, level, budget_warning, stall_alert, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET level=?, budget_warning=?, stall_alert=?, updated_at=?""",
            (user_id, level, int(budget_warning), int(stall_alert), now,
             level, int(budget_warning), int(stall_alert), now),
        )
        await db.commit()

    # --- Task History ---

    async def add_task_history(
        self,
        project_id: str,
        user_id: int,
        task_description: str,
        status: str = "running",
        cost_usd: float = 0.0,
        turns_used: int = 0,
        summary: str = "",
    ) -> int:
        """Add a task history entry, returns the row ID."""
        db = await self._get_db()
        now = time.time()
        cursor = await db.execute(
            """INSERT INTO task_history (project_id, user_id, task_description, status, cost_usd, turns_used, started_at, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, user_id, task_description, status, cost_usd, turns_used, now, summary),
        )
        await db.commit()
        return cursor.lastrowid

    async def update_task_history(
        self,
        task_id: int,
        status: str,
        cost_usd: float = 0.0,
        turns_used: int = 0,
        summary: str = "",
    ):
        """Update a task history entry on completion."""
        db = await self._get_db()
        now = time.time()
        await db.execute(
            """UPDATE task_history SET status=?, cost_usd=?, turns_used=?, completed_at=?, summary=?
               WHERE id=?""",
            (status, cost_usd, turns_used, now, summary, task_id),
        )
        await db.commit()

    async def get_recent_task_history(self, user_id: int, count: int = 10) -> list[dict]:
        """Get the last N completed tasks for a user."""
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT th.*, p.name as project_name
               FROM task_history th
               LEFT JOIN projects p ON th.project_id = p.project_id
               WHERE th.user_id=?
               ORDER BY th.started_at DESC LIMIT ?""",
            (user_id, count),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # --- Away Mode ---

    async def set_away_mode(self, user_id: int, enabled: bool):
        """Set away mode for all projects of a user."""
        db = await self._get_db()
        await db.execute(
            "UPDATE projects SET away_mode=?, updated_at=? WHERE user_id=?",
            (int(enabled), time.time(), user_id),
        )
        await db.commit()

    async def is_away(self, user_id: int) -> bool:
        """Check if user is in away mode (checks any project)."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT away_mode FROM projects WHERE user_id=? AND away_mode=1 LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return bool(row)

    async def add_away_digest(self, user_id: int, project_id: str, event_type: str, summary: str):
        """Add an event to the away digest queue."""
        db = await self._get_db()
        await db.execute(
            "INSERT INTO away_digest (user_id, project_id, event_type, summary, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, project_id, event_type, summary, time.time()),
        )
        await db.commit()

    async def get_away_digest(self, user_id: int) -> list[dict]:
        """Get all pending away digest entries for a user."""
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT ad.*, p.name as project_name
               FROM away_digest ad
               LEFT JOIN projects p ON ad.project_id = p.project_id
               WHERE ad.user_id=?
               ORDER BY ad.timestamp ASC""",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def clear_away_digest(self, user_id: int):
        """Clear all away digest entries for a user after catchup."""
        db = await self._get_db()
        await db.execute("DELETE FROM away_digest WHERE user_id=?", (user_id,))
        await db.commit()

    # --- Schedules ---

    async def add_schedule(
        self,
        user_id: int,
        project_id: str,
        schedule_time: str,
        task_description: str,
        repeat: str = "once",
    ) -> int:
        """Add a scheduled task, returns the row ID."""
        db = await self._get_db()
        now = time.time()
        cursor = await db.execute(
            """INSERT INTO schedules (user_id, project_id, schedule_time, task_description, repeat, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (user_id, project_id, schedule_time, task_description, repeat, now),
        )
        await db.commit()
        return cursor.lastrowid

    async def get_schedules(self, user_id: int) -> list[dict]:
        """Get all schedules for a user."""
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT s.*, p.name as project_name
               FROM schedules s
               LEFT JOIN projects p ON s.project_id = p.project_id
               WHERE s.user_id=? AND s.enabled=1
               ORDER BY s.schedule_time ASC""",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_due_schedules(self, current_time_hhmm: str) -> list[dict]:
        """Get all enabled schedules matching the given HH:MM time."""
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT s.*, p.name as project_name, p.project_dir
               FROM schedules s
               LEFT JOIN projects p ON s.project_id = p.project_id
               WHERE s.enabled=1 AND s.schedule_time=?""",
            (current_time_hhmm,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_schedule_run(self, schedule_id: int):
        """Mark a schedule as having run."""
        db = await self._get_db()
        now = time.time()
        await db.execute(
            "UPDATE schedules SET last_run=? WHERE id=?",
            (now, schedule_id),
        )
        await db.commit()

    async def disable_schedule(self, schedule_id: int):
        """Disable a one-time schedule after it runs."""
        db = await self._get_db()
        await db.execute(
            "UPDATE schedules SET enabled=0 WHERE id=?",
            (schedule_id,),
        )
        await db.commit()

    async def delete_schedule(self, schedule_id: int, user_id: int) -> bool:
        """Delete a schedule (ensures user owns it). Returns True if deleted."""
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM schedules WHERE id=? AND user_id=?",
            (schedule_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def cleanup_expired(self, max_age_hours: int = SESSION_EXPIRY_HOURS):
        """Clean up sessions older than max_age_hours."""
        db = await self._get_db()
        cutoff = time.time() - (max_age_hours * 3600)
        await db.execute(
            "UPDATE sessions SET status='expired' WHERE updated_at < ? AND status='active'",
            (cutoff,),
        )
        await db.commit()

    async def _migrate_add_columns(self):
        """Add columns that may be missing in older DBs."""
        db = await self._get_db()
        try:
            await db.execute("SELECT away_mode FROM projects LIMIT 1")
        except Exception:
            await db.execute("ALTER TABLE projects ADD COLUMN away_mode INTEGER DEFAULT 0")
            await db.commit()
        try:
            await db.execute("SELECT budget_usd FROM projects LIMIT 1")
        except Exception:
            await db.execute("ALTER TABLE projects ADD COLUMN budget_usd REAL DEFAULT 0")
            await db.commit()
        try:
            await db.execute("SELECT message_count FROM projects LIMIT 1")
        except Exception:
            await db.execute("ALTER TABLE projects ADD COLUMN message_count INTEGER DEFAULT 0")
            # Backfill message_count from existing messages
            await db.execute(
                "UPDATE projects SET message_count = "
                "(SELECT COUNT(*) FROM messages WHERE messages.project_id = projects.project_id)"
            )
            await db.commit()

    # --- Migration from JSON ConversationStore ---

    async def _migrate_json(self):
        """Migrate old JSON project files from data/ into SQLite."""
        store_dir = STORE_DIR
        if not store_dir.exists():
            return

        json_files = list(store_dir.glob("*.json"))
        if not json_files:
            return

        logger.info(f"Migrating {len(json_files)} JSON project files to SQLite...")
        db = await self._get_db()

        for json_path in json_files:
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                project_id = data.get("project_id", json_path.stem)
                user_id = data.get("user_id", 0)
                name = data.get("name", json_path.stem)
                description = data.get("description", "")
                project_dir = data.get("project_dir", "")
                status = data.get("status", "active")
                created_at = data.get("created_at", time.time())
                updated_at = data.get("updated_at", time.time())

                # Check if already migrated
                cursor = await db.execute(
                    "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
                )
                if await cursor.fetchone():
                    continue

                await db.execute(
                    """INSERT OR IGNORE INTO projects
                       (project_id, user_id, name, description, project_dir, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (project_id, user_id, name, description, project_dir, status, created_at, updated_at),
                )

                # Migrate messages
                for msg in data.get("messages", []):
                    await db.execute(
                        "INSERT INTO messages (project_id, agent_name, role, content, cost_usd, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            project_id,
                            msg.get("agent_name", "unknown"),
                            msg.get("role", "unknown"),
                            msg.get("content", ""),
                            msg.get("cost_usd", 0.0),
                            msg.get("timestamp", time.time()),
                        ),
                    )

                await db.commit()

                # Rename to .migrated
                migrated_path = json_path.with_suffix(".migrated")
                json_path.rename(migrated_path)
                logger.info(f"Migrated {json_path.name} -> {migrated_path.name}")

            except Exception as e:
                logger.warning(f"Failed to migrate {json_path}: {e}")

    # ── Versioned Schema Migrations ─────────────────────────────────────

    async def _run_migrations(self) -> None:
        """Run pending schema migrations with version tracking.

        Reads the ``_schema_versions`` table to determine which migrations
        have already been applied, then executes any new ones in order.
        Each migration is recorded in ``_schema_versions`` after success.
        """
        db = await self._get_db()

        # Ensure the tracking table exists (idempotent)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS _schema_versions (
                   version INTEGER PRIMARY KEY,
                   name TEXT NOT NULL,
                   applied_at REAL NOT NULL
               )"""
        )
        await db.commit()

        # Determine already-applied versions
        cursor = await db.execute("SELECT version FROM _schema_versions ORDER BY version")
        applied = {row[0] for row in await cursor.fetchall()}

        for version, name, statements in _MIGRATIONS:
            if version in applied:
                continue

            logger.info("Applying migration %d: %s", version, name)
            try:
                for stmt in statements:
                    try:
                        await db.execute(stmt)
                    except Exception as e:
                        err_msg = str(e).lower()
                        # ALTER TABLE ADD COLUMN fails if column already exists — skip
                        if "duplicate column" in err_msg or "already exists" in err_msg:
                            logger.debug("Already applied, skipping: %s", e)
                        else:
                            raise

                await db.execute(
                    "INSERT INTO _schema_versions (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, time.time()),
                )
                await db.commit()
                logger.info("Migration %d applied successfully: %s", version, name)
            except Exception as e:
                logger.error("Migration %d (%s) failed: %s", version, name, e)
                # Don't halt — log and continue so the app can still start
                # The migration will be retried on next startup

    # ── Database Backup ─────────────────────────────────────────────────

    async def create_backup(self, backup_dir: str | None = None) -> str:
        """Create a timestamped backup of the database file.

        Checkpoints the WAL first to ensure the backup is self-contained,
        then copies the database file to the backup directory.

        Args:
            backup_dir: Target directory for the backup.  Defaults to
                ``DB_BACKUP_DIR`` (``data/backups/``).

        Returns:
            The absolute path to the created backup file.
        """
        target_dir = backup_dir or DB_BACKUP_DIR
        os.makedirs(target_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"sessions_{timestamp}.db"
        backup_path = os.path.join(target_dir, backup_name)

        # Checkpoint WAL so the .db file is self-contained
        db = await self._get_db()
        try:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as exc:
            logger.warning("WAL checkpoint before backup failed: %s", exc)

        # Copy the database file (thread-safe via asyncio.to_thread)
        await asyncio.to_thread(shutil.copy2, self.db_path, backup_path)

        logger.info("Database backup created: %s", backup_path)

        # Housekeeping — remove old backups, keep last 10
        await self._cleanup_old_backups(target_dir, keep=10)

        return backup_path

    async def _cleanup_old_backups(self, backup_dir: str, keep: int = 10) -> None:
        """Remove old backup files, retaining the *keep* most recent."""
        backup_path = Path(backup_dir)
        backups = sorted(
            backup_path.glob("sessions_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_backup in backups[keep:]:
            try:
                old_backup.unlink()
                logger.debug("Removed old backup: %s", old_backup)
            except Exception as e:
                logger.warning("Failed to remove old backup %s: %s", old_backup, e)

    # ── VACUUM & Maintenance ────────────────────────────────────────────

    async def vacuum(self) -> None:
        """Run VACUUM to reclaim space and defragment the database.

        Also runs ``PRAGMA optimize`` to update query-planner statistics.
        The last-vacuum timestamp is stored in ``_db_metadata`` for scheduling.

        Note:
            VACUUM requires exclusive access — avoid calling while heavy
            writes are in progress.
        """
        db = await self._get_db()
        logger.info("Starting VACUUM…")
        await db.execute("VACUUM")
        await db.execute("PRAGMA optimize")

        # Record vacuum timestamp
        await db.execute(
            """INSERT INTO _db_metadata (key, value) VALUES ('last_vacuum', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(time.time()),),
        )
        await db.commit()
        logger.info("VACUUM completed successfully")

    async def get_last_vacuum(self) -> float | None:
        """Return the Unix timestamp of the last VACUUM, or None."""
        db = await self._get_db()
        try:
            cursor = await db.execute(
                "SELECT value FROM _db_metadata WHERE key = 'last_vacuum'"
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else None
        except Exception:
            return None

    # ── Lessons / Experience Memory ──────────────────────────────────────

    async def add_lesson(
        self,
        project_id: str,
        user_id: int,
        task_description: str,
        lesson: str,
        lesson_type: str = "general",
        tags: str = "",
        outcome: str = "success",
        rounds_used: int = 0,
        cost_usd: float = 0.0,
    ) -> int:
        """Store a lesson learned from a task execution.

        Args:
            project_id: The project this lesson came from.
            user_id: The user who ran the task.
            task_description: Brief description of the task.
            lesson: The actual lesson text (what worked, what failed, what to do differently).
            lesson_type: Category — 'strategy', 'error_pattern', 'tool_usage', 'general'.
            tags: Comma-separated tags for retrieval (e.g., 'pytest,testing,developer').
            outcome: 'success', 'partial', or 'failure'.
            rounds_used: How many orchestrator rounds the task took.
            cost_usd: Total cost of the task.

        Returns:
            The row ID of the inserted lesson.
        """
        db = await self._get_db()
        now = time.time()
        cursor = await db.execute(
            """INSERT INTO lessons
               (project_id, user_id, task_description, lesson_type, lesson, tags, outcome, rounds_used, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, user_id, task_description, lesson_type, lesson, tags, outcome, rounds_used, cost_usd, now),
        )
        await db.commit()
        logger.info(f"Stored lesson for project={project_id}: type={lesson_type}, outcome={outcome}")
        return cursor.lastrowid

    async def get_lessons_for_project(self, project_id: str, limit: int = 20) -> list[dict]:
        """Retrieve lessons for a specific project, most recent first.

        These are project-specific lessons that help the orchestrator avoid
        repeating mistakes within the same codebase.
        """
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT lesson_type, lesson, tags, outcome, rounds_used, cost_usd, task_description, created_at
               FROM lessons
               WHERE project_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (project_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_lessons_for_user(self, user_id: int, limit: int = 30) -> list[dict]:
        """Retrieve all lessons for a user across all projects.

        These cross-project lessons help the orchestrator transfer knowledge
        between different codebases (e.g., 'pytest always needs --tb=short').
        """
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT l.lesson_type, l.lesson, l.tags, l.outcome, l.rounds_used,
                      l.cost_usd, l.task_description, l.created_at, p.name as project_name
               FROM lessons l
               LEFT JOIN projects p ON l.project_id = p.project_id
               WHERE l.user_id = ?
               ORDER BY l.created_at DESC
               LIMIT ?""",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def search_lessons(self, user_id: int, keywords: list[str], limit: int = 10) -> list[dict]:
        """Search lessons by keywords in task_description, lesson text, and tags.

        This is the retrieval mechanism for injecting relevant past experience
        into the orchestrator's prompt at task start.
        """
        db = await self._get_db()
        # Build a WHERE clause that matches any keyword in lesson, tags, or task_description
        conditions = []
        params: list = [user_id]
        for kw in keywords[:10]:  # Cap at 10 keywords
            kw_clean = kw.strip().replace("'", "")
            if kw_clean:
                conditions.append(
                    "(l.lesson LIKE ? OR l.tags LIKE ? OR l.task_description LIKE ?)"
                )
                like_val = f"%{kw_clean}%"
                params.extend([like_val, like_val, like_val])

        if not conditions:
            return []

        where_clause = " OR ".join(conditions)
        cursor = await db.execute(
            f"""SELECT l.lesson_type, l.lesson, l.tags, l.outcome, l.rounds_used,
                       l.task_description, l.created_at, p.name as project_name
                FROM lessons l
                LEFT JOIN projects p ON l.project_id = p.project_id
                WHERE l.user_id = ? AND ({where_clause})
                ORDER BY l.created_at DESC
                LIMIT ?""",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Activity Log (cross-device sync) ─────────────────────────────────

    async def log_activity(
        self,
        project_id: str,
        event_type: str,
        agent: str = "",
        data: dict | None = None,
        timestamp: float | None = None,
    ) -> int:
        """Persist an activity event and return its sequence_id.

        The sequence_id is a monotonically increasing integer per project,
        enabling clients to request missed events after a reconnect.
        """
        ts = timestamp or time.time()
        data_json = json.dumps(data or {}, default=str)

        async with self._connect() as db:
            # Get next sequence_id for this project
            cursor = await db.execute(
                "SELECT COALESCE(MAX(sequence_id), 0) + 1 FROM activity_log WHERE project_id = ?",
                (project_id,),
            )
            row = await cursor.fetchone()
            seq_id = row[0] if row else 1

            await db.execute(
                """INSERT INTO activity_log (project_id, sequence_id, event_type, agent, data, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (project_id, seq_id, event_type, agent, data_json, ts),
            )
            await db.commit()
            return seq_id

    async def get_activity_since(
        self,
        project_id: str,
        since_sequence: int = 0,
        limit: int = 200,
    ) -> list[dict]:
        """Retrieve activity events after a given sequence_id.

        Used by clients on reconnect to catch up on missed events.
        Returns events in chronological order.
        """
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT sequence_id, event_type, agent, data, timestamp
                   FROM activity_log
                   WHERE project_id = ? AND sequence_id > ?
                   ORDER BY sequence_id ASC
                   LIMIT ?""",
                (project_id, since_sequence, limit),
            )
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                entry = dict(row)
                # Parse the JSON data field back into a dict
                try:
                    entry["data"] = json.loads(entry.get("data", "{}"))
                except (json.JSONDecodeError, TypeError):
                    entry["data"] = {}
                result.append(entry)
            return result

    async def get_latest_sequence(self, project_id: str) -> int:
        """Get the latest sequence_id for a project (0 if no events)."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT COALESCE(MAX(sequence_id), 0) FROM activity_log WHERE project_id = ?",
                (project_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def cleanup_old_activity(self, project_id: str, keep_last: int = 1000):
        """Remove old activity entries, keeping only the most recent N per project."""
        async with self._connect() as db:
            await db.execute(
                """DELETE FROM activity_log
                   WHERE project_id = ? AND id NOT IN (
                       SELECT id FROM activity_log
                       WHERE project_id = ?
                       ORDER BY sequence_id DESC
                       LIMIT ?
                   )""",
                (project_id, project_id, keep_last),
            )
            await db.commit()

    # ── Orchestrator State Persistence ───────────────────────────────────

    async def save_orchestrator_state(
        self,
        project_id: str,
        user_id: int,
        status: str = "idle",
        current_loop: int = 0,
        turn_count: int = 0,
        total_cost_usd: float = 0.0,
        shared_context: list | None = None,
        agent_states: dict | None = None,
        last_user_message: str = "",
    ):
        """Save or update the orchestrator state for crash recovery.

        Called periodically during task execution and on graceful shutdown.
        """
        ctx_json = json.dumps(shared_context or [], default=str)
        states_json = json.dumps(agent_states or {}, default=str)
        ts = time.time()

        async with self._connect() as db:
            await db.execute(
                """INSERT INTO orchestrator_state
                       (project_id, user_id, status, current_loop, turn_count,
                        total_cost_usd, shared_context, agent_states,
                        last_user_message, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                       status = excluded.status,
                       current_loop = excluded.current_loop,
                       turn_count = excluded.turn_count,
                       total_cost_usd = excluded.total_cost_usd,
                       shared_context = excluded.shared_context,
                       agent_states = excluded.agent_states,
                       last_user_message = excluded.last_user_message,
                       updated_at = excluded.updated_at""",
                (project_id, user_id, status, current_loop, turn_count,
                 total_cost_usd, ctx_json, states_json, last_user_message, ts),
            )
            await db.commit()

    async def load_orchestrator_state(self, project_id: str) -> dict | None:
        """Load saved orchestrator state for crash recovery.

        Returns None if no saved state exists.
        """
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM orchestrator_state WHERE project_id = ?",
                (project_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            state = dict(row)
            # Parse JSON fields
            try:
                state["shared_context"] = json.loads(state.get("shared_context", "[]"))
            except (json.JSONDecodeError, TypeError):
                state["shared_context"] = []
            try:
                state["agent_states"] = json.loads(state.get("agent_states", "{}"))
            except (json.JSONDecodeError, TypeError):
                state["agent_states"] = {}
            return state

    async def clear_orchestrator_state(self, project_id: str):
        """Remove saved orchestrator state after successful task completion."""
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM orchestrator_state WHERE project_id = ?",
                (project_id,),
            )
            await db.commit()

    async def get_interrupted_tasks(self) -> list[dict]:
        """Find all orchestrator states that were 'running' (interrupted by crash).

        Used on startup to offer task resumption.
        """
        db = await self._get_db()
        cursor = await db.execute(
            """SELECT os.*, p.name as project_name, p.project_dir
               FROM orchestrator_state os
               JOIN projects p ON os.project_id = p.project_id
               WHERE os.status = 'running'
               ORDER BY os.updated_at DESC""",
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            entry = dict(row)
            try:
                entry["shared_context"] = json.loads(entry.get("shared_context", "[]"))
            except (json.JSONDecodeError, TypeError):
                entry["shared_context"] = []
            try:
                entry["agent_states"] = json.loads(entry.get("agent_states", "{}"))
            except (json.JSONDecodeError, TypeError):
                entry["agent_states"] = {}
            result.append(entry)
        return result

    # ── Agent Performance Tracking ────────────────────────────────────

    @_retry_on_db_error()
    async def record_agent_performance(
        self,
        project_id: str,
        agent_role: str,
        status: str = "success",
        duration_seconds: float = 0.0,
        cost_usd: float = 0.0,
        turns_used: int = 0,
        task_description: str = "",
        error_message: str = "",
        round_number: int = 0,
    ) -> None:
        """Record a single agent execution for performance tracking."""
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO agent_performance
                   (project_id, agent_role, task_description, status,
                    duration_seconds, cost_usd, turns_used, error_message,
                    round_number, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, agent_role, task_description[:500], status,
                 duration_seconds, cost_usd, turns_used, error_message[:500],
                 round_number, time.time()),
            )
            await db.commit()

    @_retry_on_db_error()
    async def get_agent_stats(self, project_id: str | None = None) -> list[dict]:
        """Get aggregated performance stats per agent role.

        If project_id is provided, stats are scoped to that project.
        Returns: [{agent_role, total_runs, success_rate, avg_duration, avg_cost, total_cost}]
        """
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()
        async with self._connect() as db:
            cursor = await db.execute(
                f"""SELECT
                        agent_role,
                        COUNT(*) as total_runs,
                        SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes,
                        AVG(duration_seconds) as avg_duration,
                        AVG(cost_usd) as avg_cost,
                        SUM(cost_usd) as total_cost,
                        MAX(created_at) as last_run
                    FROM agent_performance
                    {where}
                    GROUP BY agent_role
                    ORDER BY total_runs DESC""",
                params,
            )
            rows = await cursor.fetchall()
            return [
                {
                    "agent_role": row["agent_role"],
                    "total_runs": row["total_runs"],
                    "success_rate": round(row["successes"] / max(row["total_runs"], 1) * 100, 1),
                    "avg_duration": round(row["avg_duration"] or 0, 1),
                    "avg_cost": round(row["avg_cost"] or 0, 4),
                    "total_cost": round(row["total_cost"] or 0, 4),
                    "last_run": row["last_run"],
                }
                for row in rows
            ]

    @_retry_on_db_error()
    async def get_agent_recent_performance(
        self, agent_role: str, limit: int = 10
    ) -> list[dict]:
        """Get recent performance entries for a specific agent role."""
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT * FROM agent_performance
                   WHERE agent_role = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (agent_role, limit),
            )
            return [dict(r) for r in await cursor.fetchall()]

    # ── Cost Analytics ────────────────────────────────────────────────

    @_retry_on_db_error()
    async def get_cost_breakdown(
        self, project_id: str | None = None, days: int = 30
    ) -> dict:
        """Get cost breakdown by agent, by day, and totals.

        Returns: {by_agent: [...], by_day: [...], total_cost, total_runs}
        """
        since = time.time() - (days * 86400)
        where = "WHERE created_at >= ?"
        params: list = [since]
        if project_id:
            where += " AND project_id = ?"
            params.append(project_id)

        async with self._connect() as db:
            # By agent
            cursor = await db.execute(
                f"""SELECT agent_role, SUM(cost_usd) as cost, COUNT(*) as runs
                    FROM agent_performance {where}
                    GROUP BY agent_role ORDER BY cost DESC""",
                params,
            )
            by_agent = [dict(r) for r in await cursor.fetchall()]

            # By day
            cursor = await db.execute(
                f"""SELECT date(created_at, 'unixepoch') as day,
                           SUM(cost_usd) as cost, COUNT(*) as runs
                    FROM agent_performance {where}
                    GROUP BY day ORDER BY day DESC LIMIT 30""",
                params,
            )
            by_day = [dict(r) for r in await cursor.fetchall()]

            # Totals
            cursor = await db.execute(
                f"""SELECT SUM(cost_usd) as total_cost, COUNT(*) as total_runs
                    FROM agent_performance {where}""",
                params,
            )
            totals = dict(await cursor.fetchone())

            return {
                "by_agent": by_agent,
                "by_day": by_day,
                "total_cost": round(totals.get("total_cost") or 0, 4),
                "total_runs": totals.get("total_runs") or 0,
            }

    @_retry_on_db_error()
    async def get_project_cost_summary(self) -> list[dict]:
        """Get cost summary per project (for dashboard overview)."""
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT ap.project_id, p.name as project_name,
                          SUM(ap.cost_usd) as total_cost,
                          COUNT(*) as total_runs,
                          MAX(ap.created_at) as last_activity
                   FROM agent_performance ap
                   JOIN projects p ON ap.project_id = p.project_id
                   GROUP BY ap.project_id
                   ORDER BY total_cost DESC""",
            )
            return [dict(r) for r in await cursor.fetchall()]

    # ── Interrupted Task Resume ───────────────────────────────────────

    @_retry_on_db_error()
    async def get_resumable_task(self, project_id: str) -> dict | None:
        """Get the interrupted task state for a project, if any.

        Returns the full orchestrator state dict or None.
        """
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT os.*, p.name as project_name, p.project_dir
                   FROM orchestrator_state os
                   JOIN projects p ON os.project_id = p.project_id
                   WHERE os.project_id = ? AND os.status IN ('running', 'interrupted')""",
                (project_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            entry = dict(row)
            try:
                entry["shared_context"] = json.loads(entry.get("shared_context", "[]"))
            except (json.JSONDecodeError, TypeError):
                entry["shared_context"] = []
            try:
                entry["agent_states"] = json.loads(entry.get("agent_states", "{}"))
            except (json.JSONDecodeError, TypeError):
                entry["agent_states"] = {}
            return entry

    @_retry_on_db_error()
    async def mark_task_discarded(self, project_id: str) -> None:
        """Mark an interrupted task as discarded (user chose not to resume)."""
        async with self._connect() as db:
            await db.execute(
                """UPDATE orchestrator_state SET status = 'discarded', updated_at = ?
                   WHERE project_id = ?""",
                (time.time(), project_id),
            )
            await db.commit()
