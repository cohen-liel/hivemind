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

CREATE INDEX IF NOT EXISTS idx_sessions_lookup
    ON sessions(project_id, user_id, agent_role);

CREATE INDEX IF NOT EXISTS idx_messages_project
    ON messages(project_id, timestamp);
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
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
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
        cursor = await db.execute(
            "SELECT project_id, user_id, name, description, project_dir, status, created_at, updated_at FROM projects ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            # Add message count
            count_cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE project_id=?",
                (d["project_id"],),
            )
            count_row = await count_cursor.fetchone()
            d["message_count"] = count_row["cnt"] if count_row else 0
            result.append(d)
        return result

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

    async def cleanup_expired(self, max_age_hours: int = SESSION_EXPIRY_HOURS):
        """Clean up sessions older than max_age_hours."""
        db = await self._get_db()
        cutoff = time.time() - (max_age_hours * 3600)
        await db.execute(
            "UPDATE sessions SET status='expired' WHERE updated_at < ? AND status='active'",
            (cutoff,),
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
