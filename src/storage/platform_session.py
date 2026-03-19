"""PlatformSessionManager — drop-in replacement for the legacy SessionManager.

Implements every SessionManager method using platform DB stores (SQLAlchemy ORM)
so the orchestrator, dashboard API, events, and experience modules keep working
with zero call-site changes while all data flows through ``platform.db``.

Storage mapping:
    Projects         → ``projects`` table via SQLAlchemy ORM
    Messages         → ``conversations`` + ``messages`` tables via ConversationStore
    Orchestrator     → ``memory`` table with ``_sys.orch_state`` key
    Sessions         → ``memory`` table with ``_sys.session.{role}`` keys
    Activity Log     → ``agent_actions`` table
    Task History     → ``memory`` table with ``_sys.task_history`` key
    Agent Perf       → ``agent_actions`` with action_type='performance'
    Lessons          → ``memory`` table with ``_sys.lessons`` key
    Schedules        → ``memory`` table with ``_sys.schedules`` key
    Notif. Prefs     → ``memory`` table with ``_sys.prefs.*`` keys
    Message Queue    → ``memory`` table with ``_sys.msg_queue.{project_id}`` key
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import AgentAction, Conversation, Memory, Message, Project
from src.storage._store_utils import _utcnow
from src.storage.conversation_store import ConversationStore
from src.storage.memory_store import MemoryStore

logger = logging.getLogger(__name__)

# System-key prefix used for internal state (not user-facing agent memory).
_SYS = "_sys"


def _sanitize_surrogates(obj):
    """Remove surrogate characters that SQLite cannot encode."""
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_surrogates(v) for v in obj]
    return obj


class PlatformSessionManager:
    """Drop-in replacement for SessionManager backed by platform.db.

    Constructor requires an ``async_sessionmaker`` (from ``get_session_factory()``).
    All public methods match SessionManager's signatures exactly.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory
        self._conv_store = ConversationStore(session_factory)
        self._mem_store = MemoryStore(session_factory)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """No-op — platform DB tables are created by ``init_db()``."""
        logger.info("PlatformSessionManager initialized (using platform.db)")

    async def close(self) -> None:
        """No-op — engine lifecycle is managed by ``src.db.database``."""
        logger.debug("PlatformSessionManager.close() — nothing to tear down")

    async def __aenter__(self) -> PlatformSessionManager:
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def is_healthy(self) -> bool:
        """Check if the database is responsive."""
        try:
            async with self._factory() as db:
                result = await db.execute(select(func.count()).select_from(Project))
                return result.scalar() is not None
        except Exception:
            logger.error("Database health check failed", exc_info=True)
            return False

    # ── Project CRUD ──────────────────────────────────────────────────────

    async def save_project(
        self,
        project_id: str,
        user_id: int,
        name: str,
        description: str,
        project_dir: str,
        status: str = "active",
    ) -> None:
        """Persist a new project record to the database."""
        async with self._factory() as db:
            try:
                existing = await db.get(Project, project_id)
                now = _utcnow()
                if existing:
                    existing.name = name
                    existing.project_dir = project_dir
                    existing.config_json = {
                        **(existing.config_json or {}),
                        "description": description,
                        "project_dir": project_dir,
                        "status": status,
                        "user_id": user_id,
                    }
                    existing.updated_at = now
                else:
                    db.add(
                        Project(
                            id=project_id,
                            user_id=str(user_id) if user_id is not None else None,
                            name=name,
                            project_dir=project_dir,
                            config_json={
                                "description": description,
                                "project_dir": project_dir,
                                "status": status,
                                "user_id": user_id,
                            },
                            created_at=now,
                            updated_at=now,
                        )
                    )
                await db.commit()
            except Exception:
                logger.error("save_project failed for %s", project_id, exc_info=True)
                await db.rollback()
                raise

    async def load_project(self, project_id: str) -> dict | None:
        """Load a project record by ID, or return None if not found."""
        async with self._factory() as db:
            project = await db.get(Project, project_id)
            if not project:
                return None
            cfg = project.config_json or {}
            return {
                "project_id": project.id,
                "user_id": cfg.get("user_id", 0),
                "name": project.name,
                "description": cfg.get("description", ""),
                "project_dir": cfg.get("project_dir", ""),
                "status": cfg.get("status", "active"),
                "away_mode": cfg.get("away_mode", 0),
                "budget_usd": cfg.get("budget_usd", 0),
                "message_count": cfg.get("message_count", 0),
                "created_at": project.created_at.timestamp() if project.created_at else 0,
                "updated_at": project.updated_at.timestamp() if project.updated_at else 0,
            }

    async def list_projects(self) -> list[dict]:
        """Return all projects ordered by last update time (descending)."""
        async with self._factory() as db:
            stmt = select(Project).order_by(Project.updated_at.desc())
            result = await db.execute(stmt)
            projects = result.scalars().all()
            out = []
            for p in projects:
                cfg = p.config_json or {}
                out.append(
                    {
                        "project_id": p.id,
                        "user_id": cfg.get("user_id", 0),
                        "name": p.name,
                        "description": cfg.get("description", ""),
                        "project_dir": cfg.get("project_dir", ""),
                        "status": cfg.get("status", "active"),
                        "created_at": p.created_at.timestamp() if p.created_at else 0,
                        "updated_at": p.updated_at.timestamp() if p.updated_at else 0,
                        "message_count": cfg.get("message_count", 0),
                    }
                )
            return out

    async def update_status(self, project_id: str, status: str) -> None:
        """Update the status field of a project."""
        async with self._factory() as db:
            try:
                project = await db.get(Project, project_id)
                if project:
                    cfg = dict(project.config_json or {})
                    cfg["status"] = status
                    project.config_json = cfg
                    project.updated_at = _utcnow()
                    await db.commit()
            except Exception:
                logger.error("update_status failed for %s", project_id, exc_info=True)
                await db.rollback()
                raise

    _UPDATABLE_PROJECT_FIELDS = frozenset(
        {
            "name",
            "description",
            "project_dir",
            "status",
            "away_mode",
            "budget_usd",
            "message_count",
        }
    )

    async def update_project_fields(self, project_id: str, **fields) -> None:
        """Update one or more fields on a project record."""
        if not fields:
            return
        invalid = set(fields.keys()) - self._UPDATABLE_PROJECT_FIELDS
        if invalid:
            raise ValueError(f"Disallowed column names: {', '.join(sorted(invalid))}")
        async with self._factory() as db:
            try:
                project = await db.get(Project, project_id)
                if not project:
                    return
                cfg = dict(project.config_json or {})
                for k, v in fields.items():
                    if k == "name":
                        project.name = v
                    else:
                        cfg[k] = v
                project.config_json = cfg
                project.updated_at = _utcnow()
                await db.commit()
            except Exception:
                logger.error("update_project_fields failed for %s", project_id, exc_info=True)
                await db.rollback()
                raise

    async def delete_project(self, project_id: str) -> None:
        """Remove a project and all associated data from the database."""
        async with self._factory() as db:
            try:
                stmt = delete(Project).where(Project.id == project_id)
                await db.execute(stmt)
                await db.commit()
            except Exception:
                logger.error("delete_project failed for %s", project_id, exc_info=True)
                await db.rollback()
                raise

    async def get_project_total_cost(self, project_id: str) -> float:
        """Return total cost from performance records for a project (internal use only)."""
        async with self._factory() as db:
            stmt = (
                select(func.coalesce(func.sum(AgentAction.cost_usd), 0.0))
                .where(AgentAction.action_type == "performance")
                .where(
                    AgentAction.conversation_id.in_(
                        select(Conversation.id).where(Conversation.project_id == project_id)
                    )
                )
            )
            result = await db.execute(stmt)
            return float(result.scalar() or 0.0)

    async def get_project_total_tokens(self, project_id: str) -> dict:
        """Return total token usage from performance records for a project."""
        async with self._factory() as db:
            stmt = (
                select(
                    func.coalesce(func.sum(AgentAction.input_tokens), 0).label("input_tokens"),
                    func.coalesce(func.sum(AgentAction.output_tokens), 0).label("output_tokens"),
                    func.coalesce(func.sum(AgentAction.total_tokens), 0).label("total_tokens"),
                )
                .where(AgentAction.action_type == "performance")
                .where(
                    AgentAction.conversation_id.in_(
                        select(Conversation.id).where(Conversation.project_id == project_id)
                    )
                )
            )
            result = await db.execute(stmt)
            row = result.one()
            return {
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "total_tokens": int(row.total_tokens),
            }

    async def get_project_budget(self, project_id: str) -> float:
        """Return the remaining budget (USD) for a project."""
        async with self._factory() as db:
            project = await db.get(Project, project_id)
            if not project:
                return 0.0
            return float((project.config_json or {}).get("budget_usd", 0))

    async def set_project_budget(self, project_id: str, budget_usd: float) -> None:
        """Set the remaining budget (USD) for a project."""
        async with self._factory() as db:
            try:
                project = await db.get(Project, project_id)
                if project:
                    cfg = dict(project.config_json or {})
                    cfg["budget_usd"] = budget_usd
                    project.config_json = cfg
                    project.updated_at = _utcnow()
                    await db.commit()
            except Exception:
                await db.rollback()
                raise

    # ── Message CRUD ──────────────────────────────────────────────────────

    async def _default_conv(self, project_id: str) -> str:
        """Get or create the default conversation for a project."""
        return await self._conv_store.get_or_create_default_conversation(project_id)

    async def add_message(
        self,
        project_id: str,
        agent_name: str,
        role: str,
        content: str,
        cost_usd: float = 0.0,
    ) -> None:
        """Append a conversation message to the project message log."""
        conv_id = await self._default_conv(project_id)
        # Map role to valid ConversationStore roles
        mapped_role = role if role in {"user", "assistant", "system", "tool"} else "assistant"
        # Sanitize surrogates that SQLite cannot encode
        if content and isinstance(content, str):
            content = content.encode("utf-8", errors="replace").decode("utf-8")
        await self._conv_store.append_message(
            conv_id,
            role=mapped_role,
            content=content,
            metadata={"agent_name": agent_name, "cost_usd": cost_usd},
        )

    async def _all_project_messages(self, project_id: str) -> list[dict]:
        """Fetch messages from ALL conversations for a project, ordered by timestamp."""
        convs = await self._conv_store.list_conversations(project_id)
        all_msgs: list[dict] = []
        for conv in convs:
            msgs = await self._conv_store.get_conversation_history(conv["id"])
            all_msgs.extend(msgs)
        # Sort by timestamp ascending
        all_msgs.sort(key=lambda m: m.get("timestamp", ""))
        return all_msgs

    async def get_recent_messages(self, project_id: str, count: int = 15) -> list[dict]:
        """Return the most recent messages for a project."""
        all_msgs = await self._all_project_messages(project_id)
        # Take the last `count` messages
        recent = all_msgs[-count:] if count else all_msgs
        return [
            {
                "agent_name": (m.get("metadata") or {}).get("agent_name", ""),
                "role": m.get("role", ""),
                "content": m.get("content", ""),
                "timestamp": _iso_to_epoch(m.get("timestamp")),
            }
            for m in recent
        ]

    async def get_messages_paginated(
        self,
        project_id: str,
        limit: int = 50,
        offset: int = 0,
        *,
        cursor: int | None = None,
    ) -> tuple[list[dict], int]:
        """Return a paginated slice of messages for a project."""
        all_msgs = await self._all_project_messages(project_id)
        total = len(all_msgs)
        # Apply offset/limit
        page = all_msgs[offset : offset + limit] if limit > 0 else all_msgs[offset:]
        result = [
            {
                "id": i + offset,
                "agent_name": (m.get("metadata") or {}).get("agent_name", ""),
                "role": m.get("role", ""),
                "content": m.get("content", ""),
                "timestamp": _iso_to_epoch(m.get("timestamp")),
            }
            for i, m in enumerate(page)
        ]
        return result, total

    async def clear_messages(self, project_id: str) -> None:
        """Delete all messages across all conversations for a project."""
        convs = await self._conv_store.list_conversations(project_id)
        async with self._factory() as db:
            try:
                for conv in convs:
                    await db.execute(delete(Message).where(Message.conversation_id == conv["id"]))
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def clear_stale_messages(self, project_id: str) -> None:
        """Remove old error messages and messages from the previous architecture."""
        conv_id = await self._default_conv(project_id)
        async with self._factory() as db:
            try:
                # Delete error messages
                stmt = (
                    delete(Message)
                    .where(Message.conversation_id == conv_id)
                    .where(Message.content.like("Error:%"))
                )
                await db.execute(stmt)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def clear_project_data(self, project_id: str) -> None:
        """Clear all conversations, memory, and actions for a project."""
        async with self._factory() as db:
            try:
                # Delete all conversations (cascades to messages and agent_actions)
                await db.execute(delete(Conversation).where(Conversation.project_id == project_id))
                # Delete all memory
                await db.execute(delete(Memory).where(Memory.project_id == project_id))
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def get_project_tasks(self, project_id: str, limit: int = 50) -> list[dict]:
        """Return task history from memory store."""
        tasks = await self._mem_store.get_memory(project_id, f"{_SYS}.task_history", default=[])
        if not isinstance(tasks, list):
            tasks = []
        # Sort by started_at descending, limit
        tasks.sort(key=lambda t: t.get("started_at", 0), reverse=True)
        return tasks[:limit]

    # ── Session CRUD ──────────────────────────────────────────────────────

    async def get_session(self, user_id: int, project_id: str, agent_role: str) -> str | None:
        """Retrieve a stored session by token, or None if expired/missing."""
        key = f"{_SYS}.session.{agent_role}"
        data = await self._mem_store.get_memory(project_id, key)
        if isinstance(data, dict) and data.get("status") == "active":
            return data.get("session_id")
        return None

    async def save_session(
        self,
        user_id: int,
        project_id: str,
        agent_role: str,
        session_id: str,
        cost: float = 0.0,
        turns: int = 0,
        *,
        accumulate: bool = True,
    ) -> None:
        """Persist a session token with associated metadata."""
        key = f"{_SYS}.session.{agent_role}"
        existing = await self._mem_store.get_memory(project_id, key)
        if accumulate and isinstance(existing, dict):
            cost = existing.get("cost_usd", 0) + cost
            turns = existing.get("turns", 0) + turns
        await self._mem_store.set_memory(
            project_id,
            key,
            {
                "session_id": session_id,
                "user_id": user_id,
                "cost_usd": cost,
                "turns": turns,
                "status": "active",
                "updated_at": time.time(),
            },
        )

    async def invalidate_session(self, user_id: int, project_id: str, agent_role: str) -> None:
        """Invalidate a single session token."""
        key = f"{_SYS}.session.{agent_role}"
        existing = await self._mem_store.get_memory(project_id, key)
        if isinstance(existing, dict):
            existing["status"] = "invalidated"
            existing["updated_at"] = time.time()
            await self._mem_store.set_memory(project_id, key, existing)

    async def invalidate_all_sessions(self, project_id: str) -> None:
        """Invalidate all active sessions for a project."""
        all_mem = await self._mem_store.get_all_memory(project_id)
        count = 0
        for k, v in all_mem.items():
            if (
                k.startswith(f"{_SYS}.session.")
                and isinstance(v, dict)
                and v.get("status") != "invalidated"
            ):
                v["status"] = "invalidated"
                v["updated_at"] = time.time()
                await self._mem_store.set_memory(project_id, k, v)
                count += 1
        logger.info("[%s] Invalidated %d active sessions (full context reset)", project_id, count)

    # ── Task History ──────────────────────────────────────────────────────

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
        """Record a completed task in the project history."""
        tasks = await self._mem_store.get_memory(project_id, f"{_SYS}.task_history", default=[])
        if not isinstance(tasks, list):
            tasks = []
        task_id = len(tasks) + 1
        tasks.append(
            {
                "id": task_id,
                "project_id": project_id,
                "user_id": user_id,
                "task_description": task_description,
                "status": status,
                "cost_usd": cost_usd,
                "turns_used": turns_used,
                "started_at": time.time(),
                "completed_at": None,
                "summary": summary,
            }
        )
        await self._mem_store.set_memory(project_id, f"{_SYS}.task_history", tasks)
        return task_id

    async def update_task_history(
        self,
        task_id: int,
        status: str,
        cost_usd: float = 0.0,
        turns_used: int = 0,
        summary: str = "",
    ) -> None:
        # We need to search across all projects' task history.
        # Since task_id is project-scoped, we search all memory keys.
        # The orchestrator always calls this on the correct project,
        # so we scan all projects' task histories.
        """Update fields on an existing task history record."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.task_history")
            result = await db.execute(stmt)
            rows = result.scalars().all()
            for row in rows:
                tasks = row.value_json
                if not isinstance(tasks, list):
                    continue
                for task in tasks:
                    if task.get("id") == task_id:
                        task["status"] = status
                        task["cost_usd"] = cost_usd
                        task["turns_used"] = turns_used
                        task["completed_at"] = time.time()
                        task["summary"] = summary
                        await self._mem_store.set_memory(
                            row.project_id, f"{_SYS}.task_history", tasks
                        )
                        return

    async def get_recent_task_history(self, user_id: int, count: int = 10) -> list[dict]:
        # Gather task history from all projects
        """Return recent task history entries for a project."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.task_history")
            result = await db.execute(stmt)
            rows = result.scalars().all()
            all_tasks = []
            for row in rows:
                tasks = row.value_json
                if not isinstance(tasks, list):
                    continue
                for t in tasks:
                    if t.get("user_id") == user_id:
                        all_tasks.append(t)
            all_tasks.sort(key=lambda t: t.get("started_at", 0), reverse=True)
            return all_tasks[:count]

    # ── Orchestrator State ────────────────────────────────────────────────

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
    ) -> None:
        """Persist the orchestrator checkpoint for crash recovery."""
        await self._mem_store.set_memory(
            project_id,
            f"{_SYS}.orch_state",
            {
                "project_id": project_id,
                "user_id": user_id,
                "status": status,
                "current_loop": current_loop,
                "turn_count": turn_count,
                "total_cost_usd": total_cost_usd,
                "shared_context": shared_context or [],
                "agent_states": agent_states or {},
                "last_user_message": last_user_message,
                "updated_at": time.time(),
            },
        )

    async def load_orchestrator_state(self, project_id: str) -> dict | None:
        """Load the last orchestrator checkpoint, or None if absent."""
        state = await self._mem_store.get_memory(project_id, f"{_SYS}.orch_state")
        if not isinstance(state, dict):
            return None
        return state

    async def clear_orchestrator_state(self, project_id: str) -> None:
        """Remove the orchestrator checkpoint for a project."""
        await self._mem_store.delete_memory(project_id, f"{_SYS}.orch_state")

    async def get_interrupted_tasks(self) -> list[dict]:
        """Find all orch states with status 'running' (interrupted by crash)."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.orch_state")
            result = await db.execute(stmt)
            rows = result.scalars().all()
            interrupted = []
            for row in rows:
                state = row.value_json
                if isinstance(state, dict) and state.get("status") == "running":
                    # Enrich with project info
                    project = await db.get(Project, row.project_id)
                    if project:
                        cfg = project.config_json or {}
                        state["project_name"] = project.name
                        state["project_dir"] = cfg.get("project_dir", "")
                    interrupted.append(state)
            return interrupted

    async def get_resumable_task(self, project_id: str) -> dict | None:
        """Return the latest non-discarded task eligible for resume."""
        state = await self._mem_store.get_memory(project_id, f"{_SYS}.orch_state")
        if not isinstance(state, dict):
            return None
        if state.get("status") in ("running", "interrupted"):
            # Enrich with project info
            async with self._factory() as db:
                project = await db.get(Project, project_id)
                if project:
                    cfg = project.config_json or {}
                    state["project_name"] = project.name
                    state["project_dir"] = cfg.get("project_dir", "")
            return state
        return None

    async def mark_task_discarded(self, project_id: str) -> None:
        """Mark the current resumable task as discarded."""
        state = await self._mem_store.get_memory(project_id, f"{_SYS}.orch_state")
        if isinstance(state, dict):
            state["status"] = "discarded"
            state["updated_at"] = time.time()
            await self._mem_store.set_memory(project_id, f"{_SYS}.orch_state", state)

    # ── Activity Log ──────────────────────────────────────────────────────

    async def log_activity(
        self,
        project_id: str,
        event_type: str,
        agent: str = "",
        data: dict | None = None,
        timestamp: float | None = None,
    ) -> int:
        """Append a timestamped activity event to the project log."""
        ts = timestamp if isinstance(timestamp, int | float) else time.time()
        conv_id = await self._default_conv(project_id)

        # Get next sequence_id from memory
        seq_key = f"{_SYS}.activity_seq.{project_id}"
        current_seq = await self._mem_store.get_memory(project_id, seq_key, default=0)
        seq_id = (current_seq or 0) + 1
        await self._mem_store.set_memory(project_id, seq_key, seq_id)

        # Write to agent_actions table
        async with self._factory() as db:
            try:
                action = AgentAction(
                    conversation_id=conv_id,
                    agent_role=agent or "system",
                    action_type=event_type,
                    payload_json=_sanitize_surrogates(
                        {
                            "data": data or {},
                            "sequence_id": seq_id,
                        }
                    ),
                    timestamp=datetime.fromtimestamp(ts, tz=UTC),
                )
                db.add(action)
                await db.commit()
            except Exception:
                logger.error("log_activity failed for %s", project_id, exc_info=True)
                await db.rollback()
                raise

        return seq_id

    async def get_activity_since(
        self,
        project_id: str,
        since_sequence: int = 0,
        limit: int = 200,
    ) -> list[dict]:
        """Return activity events after a given sequence number."""
        conv_id = await self._default_conv(project_id)
        async with self._factory() as db:
            stmt = (
                select(AgentAction)
                .where(AgentAction.conversation_id == conv_id)
                .where(AgentAction.action_type != "performance")
                .order_by(AgentAction.timestamp.asc())
            )
            result = await db.execute(stmt)
            actions = result.scalars().all()

            events = []
            for a in actions:
                payload = a.payload_json or {}
                seq = payload.get("sequence_id", 0)
                if seq <= since_sequence:
                    continue
                events.append(
                    {
                        "sequence_id": seq,
                        "event_type": a.action_type,
                        "agent": a.agent_role,
                        "data": payload.get("data", {}),
                        "timestamp": a.timestamp.timestamp() if a.timestamp else 0,
                    }
                )
                if len(events) >= limit:
                    break
            return events

    async def get_latest_sequence(self, project_id: str) -> int:
        """Return the highest activity sequence number for a project."""
        seq_key = f"{_SYS}.activity_seq.{project_id}"
        return await self._mem_store.get_memory(project_id, seq_key, default=0) or 0

    async def cleanup_old_activity(self, project_id: str, keep_last: int = 1000) -> None:
        # Activity events are in agent_actions — clean up old ones
        """Delete activity events older than the retention window."""
        conv_id = await self._default_conv(project_id)
        async with self._factory() as db:
            try:
                # Count total non-performance actions
                count_stmt = (
                    select(func.count())
                    .select_from(AgentAction)
                    .where(AgentAction.conversation_id == conv_id)
                    .where(AgentAction.action_type != "performance")
                )
                total = (await db.execute(count_stmt)).scalar() or 0
                if total <= keep_last:
                    return
                # Delete oldest entries
                cutoff_stmt = (
                    select(AgentAction.id)
                    .where(AgentAction.conversation_id == conv_id)
                    .where(AgentAction.action_type != "performance")
                    .order_by(AgentAction.timestamp.desc())
                    .limit(keep_last)
                )
                keep_ids = (await db.execute(cutoff_stmt)).scalars().all()
                if keep_ids:
                    await db.execute(
                        delete(AgentAction)
                        .where(AgentAction.conversation_id == conv_id)
                        .where(AgentAction.action_type != "performance")
                        .where(AgentAction.id.notin_(keep_ids))
                    )
                    await db.commit()
            except Exception:
                await db.rollback()
                raise

    # ── Agent Performance ─────────────────────────────────────────────────

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
        """Record performance metrics for an agent execution."""
        conv_id = await self._default_conv(project_id)
        async with self._factory() as db:
            try:
                action = AgentAction(
                    conversation_id=conv_id,
                    agent_role=agent_role,
                    action_type="performance",
                    payload_json={
                        "task_description": task_description[:500],
                        "error_message": error_message[:500],
                    },
                    result_json={
                        "status": status,
                        "duration_seconds": duration_seconds,
                        "turns_used": turns_used,
                    },
                    round=round_number,
                    cost_usd=cost_usd,
                    timestamp=_utcnow(),
                )
                db.add(action)
                await db.commit()
            except Exception:
                logger.error("record_agent_performance failed", exc_info=True)
                await db.rollback()
                raise

    async def get_agent_stats(self, project_id: str | None = None) -> list[dict]:
        """Return aggregate performance statistics per agent role."""
        async with self._factory() as db:
            stmt = select(AgentAction).where(AgentAction.action_type == "performance")
            if project_id:
                stmt = stmt.where(
                    AgentAction.conversation_id.in_(
                        select(Conversation.id).where(Conversation.project_id == project_id)
                    )
                )
            result = await db.execute(stmt)
            actions = result.scalars().all()

            # Aggregate by agent_role
            stats: dict[str, dict] = {}
            for a in actions:
                role = a.agent_role
                if role not in stats:
                    stats[role] = {
                        "total": 0,
                        "successes": 0,
                        "durations": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "last_run": 0,
                    }
                s = stats[role]
                s["total"] += 1
                result_json = a.result_json or {}
                if result_json.get("status") == "success":
                    s["successes"] += 1
                s["durations"].append(result_json.get("duration_seconds", 0))
                s["input_tokens"] += a.input_tokens or 0
                s["output_tokens"] += a.output_tokens or 0
                s["total_tokens"] += a.total_tokens or 0
                ts = a.timestamp.timestamp() if a.timestamp else 0
                s["last_run"] = max(s["last_run"], ts)

            return [
                {
                    "agent_role": role,
                    "total_runs": s["total"],
                    "success_rate": round(s["successes"] / max(s["total"], 1) * 100, 1),
                    "avg_duration": round(sum(s["durations"]) / max(len(s["durations"]), 1), 1),
                    "input_tokens": s["input_tokens"],
                    "output_tokens": s["output_tokens"],
                    "total_tokens": s["total_tokens"],
                    "last_run": s["last_run"],
                }
                for role, s in stats.items()
            ]

    async def get_agent_recent_performance(self, agent_role: str, limit: int = 10) -> list[dict]:
        """Return the most recent performance records for an agent."""
        async with self._factory() as db:
            stmt = (
                select(AgentAction)
                .where(AgentAction.action_type == "performance")
                .where(AgentAction.agent_role == agent_role)
                .order_by(AgentAction.timestamp.desc())
                .limit(limit)
            )
            result = await db.execute(stmt)
            actions = result.scalars().all()
            return [
                {
                    "agent_role": a.agent_role,
                    "status": (a.result_json or {}).get("status", ""),
                    "duration_seconds": (a.result_json or {}).get("duration_seconds", 0),
                    "input_tokens": a.input_tokens or 0,
                    "output_tokens": a.output_tokens or 0,
                    "total_tokens": a.total_tokens or 0,
                    "turns_used": (a.result_json or {}).get("turns_used", 0),
                    "task_description": (a.payload_json or {}).get("task_description", ""),
                    "error_message": (a.payload_json or {}).get("error_message", ""),
                    "round_number": a.round or 0,
                    "created_at": a.timestamp.timestamp() if a.timestamp else 0,
                }
                for a in actions
            ]

    async def get_cost_breakdown(self, project_id: str | None = None, days: int = 30) -> dict:
        """Return a per-agent token usage breakdown for a project."""
        since = time.time() - (days * 86400)
        since_dt = datetime.fromtimestamp(since, tz=UTC)
        async with self._factory() as db:
            stmt = (
                select(AgentAction)
                .where(AgentAction.action_type == "performance")
                .where(AgentAction.timestamp >= since_dt)
            )
            if project_id:
                stmt = stmt.where(
                    AgentAction.conversation_id.in_(
                        select(Conversation.id).where(Conversation.project_id == project_id)
                    )
                )
            result = await db.execute(stmt)
            actions = result.scalars().all()

            by_agent: dict[str, dict] = {}
            by_day: dict[str, dict] = {}
            total_input_tokens = 0
            total_output_tokens = 0
            total_tokens = 0
            total_runs = 0

            for a in actions:
                total_runs += 1
                in_tok = a.input_tokens or 0
                out_tok = a.output_tokens or 0
                tot_tok = a.total_tokens or 0
                total_input_tokens += in_tok
                total_output_tokens += out_tok
                total_tokens += tot_tok
                role = a.agent_role
                if role not in by_agent:
                    by_agent[role] = {"agent_role": role, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "runs": 0}
                by_agent[role]["input_tokens"] += in_tok
                by_agent[role]["output_tokens"] += out_tok
                by_agent[role]["total_tokens"] += tot_tok
                by_agent[role]["runs"] += 1

                day = a.timestamp.strftime("%Y-%m-%d") if a.timestamp else "unknown"
                if day not in by_day:
                    by_day[day] = {"day": day, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "runs": 0}
                by_day[day]["input_tokens"] += in_tok
                by_day[day]["output_tokens"] += out_tok
                by_day[day]["total_tokens"] += tot_tok
                by_day[day]["runs"] += 1

            return {
                "by_agent": sorted(by_agent.values(), key=lambda x: x["total_tokens"], reverse=True),
                "by_day": sorted(by_day.values(), key=lambda x: x["day"], reverse=True)[:30],
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
                "total_runs": total_runs,
            }

    async def get_project_cost_summary(self) -> list[dict]:
        """Aggregate per-project token usage from agent_actions with type='performance'.

        Uses a single JOIN query (agent_actions → conversations → projects) to
        avoid the previous N+1 anti-pattern that issued individual
        ``db.get(Conversation, ...)`` and ``db.get(Project, ...)`` calls per
        action row inside a Python loop.
        """
        async with self._factory() as db:
            stmt = (
                select(
                    Project.id.label("project_id"),
                    Project.name.label("project_name"),
                    func.coalesce(func.sum(AgentAction.input_tokens), 0).label("total_input_tokens"),
                    func.coalesce(func.sum(AgentAction.output_tokens), 0).label("total_output_tokens"),
                    func.coalesce(func.sum(AgentAction.total_tokens), 0).label("total_tokens"),
                    func.count(AgentAction.id).label("total_runs"),
                    func.max(AgentAction.timestamp).label("last_activity"),
                )
                .select_from(AgentAction)
                .join(
                    Conversation,
                    AgentAction.conversation_id == Conversation.id,
                )
                .join(
                    Project,
                    Conversation.project_id == Project.id,
                )
                .where(AgentAction.action_type == "performance")
                .group_by(Project.id, Project.name)
                .order_by(func.coalesce(func.sum(AgentAction.total_tokens), 0).desc())
            )
            result = await db.execute(stmt)
            rows = result.all()

            return [
                {
                    "project_id": row.project_id,
                    "project_name": row.project_name,
                    "total_input_tokens": int(row.total_input_tokens),
                    "total_output_tokens": int(row.total_output_tokens),
                    "total_tokens": int(row.total_tokens),
                    "total_runs": row.total_runs,
                    "last_activity": (row.last_activity.timestamp() if row.last_activity else 0),
                }
                for row in rows
            ]

    async def get_round_cost_breakdown(
        self, project_id: str, round_number: int | None = None
    ) -> list[dict]:
        """Return token usage breakdown grouped by orchestration round."""
        conv_id = await self._default_conv(project_id)
        async with self._factory() as db:
            stmt = (
                select(AgentAction)
                .where(AgentAction.action_type == "performance")
                .where(AgentAction.conversation_id == conv_id)
            )
            if round_number is not None:
                stmt = stmt.where(AgentAction.round == round_number)
            stmt = stmt.order_by(AgentAction.round, AgentAction.timestamp)
            result = await db.execute(stmt)
            actions = result.scalars().all()
            return [
                {
                    "round_number": a.round or 0,
                    "agent_role": a.agent_role,
                    "input_tokens": a.input_tokens or 0,
                    "output_tokens": a.output_tokens or 0,
                    "total_tokens": a.total_tokens or 0,
                    "duration_seconds": round((a.result_json or {}).get("duration_seconds", 0), 1),
                    "turns_used": (a.result_json or {}).get("turns_used", 0),
                    "status": (a.result_json or {}).get("status", ""),
                    "timestamp": a.timestamp.timestamp() if a.timestamp else 0,
                }
                for a in actions
            ]

    async def get_round_cost_summary(self, project_id: str) -> list[dict]:
        """Return a high-level token usage summary per round."""
        conv_id = await self._default_conv(project_id)
        async with self._factory() as db:
            stmt = (
                select(AgentAction)
                .where(AgentAction.action_type == "performance")
                .where(AgentAction.conversation_id == conv_id)
                .order_by(AgentAction.round)
            )
            result = await db.execute(stmt)
            actions = result.scalars().all()

            rounds: dict[int, dict] = {}
            for a in actions:
                r = a.round or 0
                if r not in rounds:
                    rounds[r] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "duration": 0.0, "agents": set()}
                rounds[r]["input_tokens"] += a.input_tokens or 0
                rounds[r]["output_tokens"] += a.output_tokens or 0
                rounds[r]["total_tokens"] += a.total_tokens or 0
                rounds[r]["duration"] += (a.result_json or {}).get("duration_seconds", 0)
                rounds[r]["agents"].add(a.agent_role)

            return [
                {
                    "round_number": r,
                    "input_tokens": d["input_tokens"],
                    "output_tokens": d["output_tokens"],
                    "total_tokens": d["total_tokens"],
                    "total_duration": round(d["duration"], 1),
                    "agent_count": len(d["agents"]),
                    "agents": sorted(d["agents"]),
                }
                for r, d in sorted(rounds.items())
            ]

    # ── Away Mode ─────────────────────────────────────────────────────────

    async def set_away_mode(self, user_id: int, enabled: bool) -> None:
        """Enable or disable away mode for all projects owned by a user.

        Uses a WHERE clause on ``Project.user_id`` to avoid loading every
        project row in the database (previous version did a full-table scan
        and filtered in Python).
        """
        async with self._factory() as db:
            try:
                uid = str(user_id)
                stmt = select(Project).where(Project.user_id == uid)
                result = await db.execute(stmt)
                for project in result.scalars().all():
                    cfg = dict(project.config_json or {})
                    cfg["away_mode"] = int(enabled)
                    project.config_json = cfg
                    project.updated_at = _utcnow()
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def is_away(self, user_id: int) -> bool:
        """Check if away mode is enabled for any project owned by a user.

        Uses a WHERE clause on ``Project.user_id`` to avoid loading every
        project row in the database (previous version did a full-table scan).
        """
        async with self._factory() as db:
            uid = str(user_id)
            stmt = select(Project).where(Project.user_id == uid)
            result = await db.execute(stmt)
            for project in result.scalars().all():
                cfg = project.config_json or {}
                if cfg.get("away_mode"):
                    return True
            return False

    async def add_away_digest(
        self, user_id: int, project_id: str, event_type: str, summary: str
    ) -> None:
        """Queue a digest notification for a user who is away."""
        key = f"{_SYS}.away_digest.{user_id}"
        digests = await self._mem_store.get_memory(project_id, key, default=[])
        if not isinstance(digests, list):
            digests = []
        digests.append(
            {
                "project_id": project_id,
                "event_type": event_type,
                "summary": summary,
                "timestamp": time.time(),
            }
        )
        await self._mem_store.set_memory(project_id, key, digests)

    async def get_away_digest(self, user_id: int) -> list[dict]:
        # Scan all projects for digests
        """Return all pending away-digest entries for a user."""
        async with self._factory() as db:
            key = f"{_SYS}.away_digest.{user_id}"
            stmt = select(Memory).where(Memory.key == key)
            result = await db.execute(stmt)
            rows = result.scalars().all()
            all_digests = []
            for row in rows:
                if isinstance(row.value_json, list):
                    all_digests.extend(row.value_json)
            all_digests.sort(key=lambda d: d.get("timestamp", 0))
            return all_digests

    async def clear_away_digest(self, user_id: int) -> None:
        """Remove all pending away-digest entries for a user."""
        async with self._factory() as db:
            key = f"{_SYS}.away_digest.{user_id}"
            stmt = delete(Memory).where(Memory.key == key)
            await db.execute(stmt)
            await db.commit()

    # ── Schedules ─────────────────────────────────────────────────────────

    async def add_schedule(
        self,
        user_id: int,
        project_id: str,
        schedule_time: str,
        task_description: str,
        repeat: str = "once",
    ) -> int:
        """Create a new scheduled task for a user."""
        key = f"{_SYS}.schedules"
        schedules = await self._mem_store.get_memory(project_id, key, default=[])
        if not isinstance(schedules, list):
            schedules = []
        schedule_id = int(time.time() * 1000) % 2147483647  # unique enough id
        schedules.append(
            {
                "id": schedule_id,
                "user_id": user_id,
                "project_id": project_id,
                "schedule_time": schedule_time,
                "task_description": task_description,
                "repeat": repeat,
                "enabled": 1,
                "last_run": None,
                "created_at": time.time(),
            }
        )
        await self._mem_store.set_memory(project_id, key, schedules)
        return schedule_id

    async def get_schedules(self, user_id: int) -> list[dict]:
        """Return all scheduled tasks for a user."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.schedules")
            result = await db.execute(stmt)
            rows = result.scalars().all()
            all_schedules = []
            for row in rows:
                if isinstance(row.value_json, list):
                    for s in row.value_json:
                        if s.get("user_id") == user_id and s.get("enabled"):
                            all_schedules.append(s)
            all_schedules.sort(key=lambda s: s.get("schedule_time", ""))
            return all_schedules

    async def get_due_schedules(self, current_time_hhmm: str) -> list[dict]:
        """Return schedules that are due at the given time."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.schedules")
            result = await db.execute(stmt)
            rows = result.scalars().all()
            due = []
            for row in rows:
                if isinstance(row.value_json, list):
                    for s in row.value_json:
                        if s.get("enabled") and s.get("schedule_time") == current_time_hhmm:
                            # Enrich with project info
                            project = await db.get(Project, s.get("project_id", ""))
                            if project:
                                cfg = project.config_json or {}
                                s["project_name"] = project.name
                                s["project_dir"] = cfg.get("project_dir", "")
                            due.append(s)
            return due

    async def mark_schedule_run(self, schedule_id: int) -> None:
        """Record that a scheduled task has been executed."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.schedules")
            result = await db.execute(stmt)
            for row in result.scalars().all():
                if isinstance(row.value_json, list):
                    for s in row.value_json:
                        if s.get("id") == schedule_id:
                            s["last_run"] = time.time()
                            await self._mem_store.set_memory(
                                row.project_id, f"{_SYS}.schedules", row.value_json
                            )
                            return

    async def disable_schedule(self, schedule_id: int) -> None:
        """Disable a scheduled task without deleting it."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.schedules")
            result = await db.execute(stmt)
            for row in result.scalars().all():
                if isinstance(row.value_json, list):
                    for s in row.value_json:
                        if s.get("id") == schedule_id:
                            s["enabled"] = 0
                            await self._mem_store.set_memory(
                                row.project_id, f"{_SYS}.schedules", row.value_json
                            )
                            return

    async def delete_schedule(self, schedule_id: int, user_id: int) -> bool:
        """Delete a scheduled task; return True if it existed."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.schedules")
            result = await db.execute(stmt)
            for row in result.scalars().all():
                if isinstance(row.value_json, list):
                    original = len(row.value_json)
                    row.value_json[:] = [
                        s
                        for s in row.value_json
                        if not (s.get("id") == schedule_id and s.get("user_id") == user_id)
                    ]
                    if len(row.value_json) < original:
                        await self._mem_store.set_memory(
                            row.project_id, f"{_SYS}.schedules", row.value_json
                        )
                        return True
            return False

    # ── Notification Preferences ──────────────────────────────────────────

    async def get_notification_prefs(self, user_id: int) -> dict:
        # Use a global project key for user-level prefs
        """Return notification preferences for a user."""
        prefs = await self._mem_store.get_memory("__global__", f"{_SYS}.prefs.notif.{user_id}")
        if isinstance(prefs, dict):
            return prefs
        return {"level": "all", "budget_warning": True, "stall_alert": True}

    async def set_notification_prefs(
        self,
        user_id: int,
        level: str = "all",
        budget_warning: bool = True,
        stall_alert: bool = True,
    ) -> None:
        """Update notification preferences for a user."""
        await self._mem_store.set_memory(
            "__global__",
            f"{_SYS}.prefs.notif.{user_id}",
            {"level": level, "budget_warning": budget_warning, "stall_alert": stall_alert},
        )

    # ── Persistent Message Queue ──────────────────────────────────────────

    async def enqueue_message(self, project_id: str, message: str) -> int:
        """Add a message to the project's pending message queue."""
        key = f"{_SYS}.msg_queue.{project_id}"
        queue = await self._mem_store.get_memory(project_id, key, default=[])
        if not isinstance(queue, list):
            queue = []
        queue.append(
            {
                "id": int(time.time() * 1000) % 2147483647,
                "message": message,
                "created_at": time.time(),
            }
        )
        await self._mem_store.set_memory(project_id, key, queue)
        return len(queue)

    async def dequeue_next_message(self, project_id: str) -> str | None:
        """Pop and return the next queued message, or None."""
        key = f"{_SYS}.msg_queue.{project_id}"
        queue = await self._mem_store.get_memory(project_id, key, default=[])
        if not isinstance(queue, list) or not queue:
            return None
        msg = queue.pop(0)
        await self._mem_store.set_memory(project_id, key, queue)
        return msg.get("message")

    async def list_queued_messages(self, project_id: str) -> list[dict]:
        """Return all pending messages in the project queue."""
        key = f"{_SYS}.msg_queue.{project_id}"
        queue = await self._mem_store.get_memory(project_id, key, default=[])
        if not isinstance(queue, list):
            return []
        return [
            {
                "id": item.get("id", i),
                "message": item.get("message", ""),
                "created_at": item.get("created_at", 0),
                "position": i + 1,
            }
            for i, item in enumerate(queue)
        ]

    async def delete_queued_message(self, project_id: str, msg_id: int) -> bool:
        """Delete a specific queued message by ID."""
        key = f"{_SYS}.msg_queue.{project_id}"
        queue = await self._mem_store.get_memory(project_id, key, default=[])
        if not isinstance(queue, list):
            return False
        original = len(queue)
        queue[:] = [item for item in queue if item.get("id") != msg_id]
        if len(queue) < original:
            await self._mem_store.set_memory(project_id, key, queue)
            return True
        return False

    async def clear_queue(self, project_id: str) -> int:
        """Remove all queued messages for a project; return count deleted."""
        key = f"{_SYS}.msg_queue.{project_id}"
        queue = await self._mem_store.get_memory(project_id, key, default=[])
        count = len(queue) if isinstance(queue, list) else 0
        await self._mem_store.set_memory(project_id, key, [])
        return count

    # ── Lessons / Experience Memory ───────────────────────────────────────

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
        """Store a learned lesson associated with a project or user."""
        key = f"{_SYS}.lessons"
        lessons = await self._mem_store.get_memory(project_id, key, default=[])
        if not isinstance(lessons, list):
            lessons = []
        lesson_id = len(lessons) + 1
        lessons.append(
            {
                "id": lesson_id,
                "project_id": project_id,
                "user_id": user_id,
                "task_description": task_description,
                "lesson_type": lesson_type,
                "lesson": lesson,
                "tags": tags,
                "outcome": outcome,
                "rounds_used": rounds_used,
                "cost_usd": cost_usd,
                "created_at": time.time(),
            }
        )
        await self._mem_store.set_memory(project_id, key, lessons)
        logger.info(
            "Stored lesson for project=%s: type=%s, outcome=%s", project_id, lesson_type, outcome
        )
        return lesson_id

    async def get_lessons_for_project(self, project_id: str, limit: int = 20) -> list[dict]:
        """Return lessons learned within a specific project."""
        lessons = await self._mem_store.get_memory(project_id, f"{_SYS}.lessons", default=[])
        if not isinstance(lessons, list):
            return []
        lessons.sort(key=lambda l: l.get("created_at", 0), reverse=True)
        return lessons[:limit]

    async def get_lessons_for_user(self, user_id: int, limit: int = 30) -> list[dict]:
        """Return lessons learned across all projects for a user."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.lessons")
            result = await db.execute(stmt)
            rows = result.scalars().all()
            all_lessons = []
            for row in rows:
                if isinstance(row.value_json, list):
                    for l in row.value_json:
                        if l.get("user_id") == user_id:
                            all_lessons.append(l)
            all_lessons.sort(key=lambda l: l.get("created_at", 0), reverse=True)
            return all_lessons[:limit]

    async def search_lessons(
        self, user_id: int, keywords: list[str], limit: int = 10
    ) -> list[dict]:
        """Search lessons by keyword across title and content."""
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key == f"{_SYS}.lessons")
            result = await db.execute(stmt)
            rows = result.scalars().all()

            matches = []
            clean_keywords = [
                kw.strip().lower().replace("'", "") for kw in keywords[:10] if kw.strip()
            ]
            if not clean_keywords:
                return []

            for row in rows:
                if not isinstance(row.value_json, list):
                    continue
                for lesson in row.value_json:
                    if lesson.get("user_id") != user_id:
                        continue
                    text = " ".join(
                        [
                            lesson.get("lesson", ""),
                            lesson.get("tags", ""),
                            lesson.get("task_description", ""),
                        ]
                    ).lower()
                    if any(kw in text for kw in clean_keywords):
                        matches.append(lesson)

            matches.sort(key=lambda l: l.get("created_at", 0), reverse=True)
            return matches[:limit]

    # ── Cleanup / Maintenance ─────────────────────────────────────────────

    async def cleanup_expired(self, max_age_hours: int = 24) -> None:
        """Clean up expired sessions from memory."""
        cutoff = time.time() - (max_age_hours * 3600)
        async with self._factory() as db:
            stmt = select(Memory).where(Memory.key.like(f"{_SYS}.session.%"))
            result = await db.execute(stmt)
            for row in result.scalars().all():
                if isinstance(row.value_json, dict):
                    updated = row.value_json.get("updated_at", 0)
                    if updated < cutoff and row.value_json.get("status") == "active":
                        row.value_json["status"] = "expired"
                        await self._mem_store.set_memory(row.project_id, row.key, row.value_json)

    async def create_backup(self, backup_dir: str | None = None) -> str:
        """No-op for platform DB — backups managed externally."""
        logger.info("create_backup: platform.db backups are managed externally")
        return ""

    async def vacuum(self) -> None:
        """No-op — SQLAlchemy/platform DB doesn't need manual VACUUM."""
        logger.info("vacuum: no-op for platform DB")

    async def get_last_vacuum(self) -> float | None:
        """Return None — vacuum is not tracked for platform DB."""
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _iso_to_epoch(iso_str: str | None) -> float:
    """Convert an ISO 8601 timestamp string to a Unix epoch float."""
    if not iso_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0
