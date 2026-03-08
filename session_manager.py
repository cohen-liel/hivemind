from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import aiosqlite

from config import SESSION_DB_PATH, SESSION_EXPIRY_HOURS, STORE_DIR

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

CREATE INDEX IF NOT EXISTS idx_task_history_project
    ON task_history(project_id, completed_at);

CREATE INDEX IF NOT EXISTS idx_away_digest_user
    ON away_digest(user_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_schedules_enabled
    ON schedules(enabled, schedule_time);
"""


class SessionManager:
    """Async SQLite persistence for sessions, projects, and messages."""

    def __init__(self, db_path: str = SESSION_DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self):
        """Create tables and migrate old JSON data if present."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        # Enable WAL mode for better concurrent read/write performance
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        # Add away_mode column to existing projects table if missing
        await self._migrate_add_columns()
        await self._migrate_json()
        logger.info(f"SessionManager initialized: {self.db_path}")

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def _get_db(self) -> aiosqlite.Connection:
        if not self._db:
            raise RuntimeError("SessionManager not initialized. Call initialize() first.")
        return self._db

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
        # Use a single LEFT JOIN to count messages instead of N+1 queries
        cursor = await db.execute(
            """SELECT p.project_id, p.user_id, p.name, p.description, p.project_dir,
                      p.status, p.created_at, p.updated_at,
                      COUNT(m.id) as message_count
               FROM projects p
               LEFT JOIN messages m ON p.project_id = m.project_id
               GROUP BY p.project_id
               ORDER BY p.updated_at DESC"""
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
