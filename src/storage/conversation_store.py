"""ConversationStore — persistent SSOT for conversation history.

All messages are persisted to the ``conversations`` and ``messages`` tables
in platform.db. This store is the single source of truth for conversation
history.

Design decisions:
- Projects are auto-provisioned: if a project row doesn't exist yet,
  we create a minimal stub so the FK constraint is satisfied.
- Conversations are project-scoped and identified by UUID. Each project has
  at least one "default" conversation created on first message.
- Messages are append-only (no edits). Full history is reconstructed by
  fetching all messages ordered by timestamp.
- All DB calls use async SQLAlchemy — zero blocking I/O in async context.
- ``_ensure_project`` is dialect-agnostic (works on both SQLite and PostgreSQL)
  via the shared helper in ``src/storage/_store_utils``.

Public interface::

    store = ConversationStore(session_factory)

    # Create or resume a conversation
    conv_id = await store.create_conversation(project_id, title="Fix login bug")

    # Append user/assistant messages
    await store.append_message(conv_id, role="user", content="Hello")
    await store.append_message(conv_id, role="assistant", content="Hi!", metadata={"model": "claude-3-5-sonnet"})

    # Reload full history on reconnect
    messages = await store.get_conversation_history(conv_id)

    # List all conversations for a project (most recent first)
    convs = await store.list_conversations(project_id)

    # Get or create the default conversation for a project
    conv_id = await store.get_or_create_default_conversation(project_id)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import AgentAction, Conversation, ExecutionSession, Message
from src.storage._store_utils import _ensure_project, _utcnow

logger = logging.getLogger(__name__)


class ConversationStore:
    """Async service for reading and writing conversation history.

    Injected via FastAPI ``Depends()`` — see ``src/dependencies.py``.

    Args:
        session_factory: An ``async_sessionmaker[AsyncSession]`` produced by
            ``get_session_factory()`` from ``src.db.database``.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    # ─────────────────────────────────────────────────────────────────────────
    # Public: Conversations
    # ─────────────────────────────────────────────────────────────────────────

    async def create_conversation(
        self,
        project_id: str,
        title: str | None = None,
    ) -> str:
        """Create a new conversation and return its UUID.

        Args:
            project_id: The project this conversation belongs to.
            title:      Optional human-readable title. If omitted, callers may
                        set it later via ``set_conversation_title()``.

        Returns:
            The new conversation's UUID string.

        Raises:
            Exception: On any DB error (logged with full traceback).
        """
        async with self._factory() as db:
            try:
                await _ensure_project(db, project_id)
                conv = Conversation(
                    project_id=project_id,
                    title=title,
                    created_at=_utcnow(),
                    last_active_at=_utcnow(),
                )
                db.add(conv)
                await db.flush()  # populate conv.id before commit
                conv_id = conv.id
                await db.commit()
                logger.info(
                    "ConversationStore: created conversation %s for project %s (title=%r)",
                    conv_id,
                    project_id,
                    title,
                )
                return conv_id
            except Exception:
                logger.error(
                    "ConversationStore.create_conversation failed for project %s",
                    project_id,
                    exc_info=True,
                )
                await db.rollback()
                raise

    async def get_or_create_default_conversation(self, project_id: str) -> str:
        """Return the most-recently-active conversation for a project.

        If no conversation exists, creates one with title ``"default"``.
        This is the main entry point for WebSocket sessions that need a
        ``conversation_id`` without the caller specifying one.

        Args:
            project_id: The project to look up or create a conversation for.

        Returns:
            UUID of the conversation.
        """
        async with self._factory() as db:
            try:
                await _ensure_project(db, project_id)
                stmt = (
                    select(Conversation)
                    .where(Conversation.project_id == project_id)
                    .order_by(Conversation.last_active_at.desc())
                    .limit(1)
                )
                result = await db.execute(stmt)
                conv = result.scalar_one_or_none()
                if conv is not None:
                    await db.commit()
                    return conv.id

                # No conversation yet — create the default one
                conv = Conversation(
                    project_id=project_id,
                    title="default",
                    created_at=_utcnow(),
                    last_active_at=_utcnow(),
                )
                db.add(conv)
                await db.flush()
                conv_id = conv.id
                await db.commit()
                logger.info(
                    "ConversationStore: created default conversation %s for project %s",
                    conv_id,
                    project_id,
                )
                return conv_id
            except Exception:
                logger.error(
                    "ConversationStore.get_or_create_default_conversation failed for project %s",
                    project_id,
                    exc_info=True,
                )
                await db.rollback()
                raise

    async def set_conversation_title(
        self,
        conversation_id: str,
        title: str,
    ) -> None:
        """Update the title of an existing conversation.

        Args:
            conversation_id: UUID of the conversation to update.
            title:           New human-readable title.
        """
        async with self._factory() as db:
            try:
                stmt = (
                    update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(title=title, last_active_at=_utcnow())
                )
                await db.execute(stmt)
                await db.commit()
            except Exception:
                logger.error(
                    "ConversationStore.set_conversation_title failed for conv %s",
                    conversation_id,
                    exc_info=True,
                )
                await db.rollback()
                raise

    async def list_conversations(
        self,
        project_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return conversations for a project, most-recently-active first.

        Args:
            project_id: Filter by this project.
            limit:      Max number of results (clamped to 1–500 by caller).
            offset:     Pagination offset.

        Returns:
            List of dicts with keys: id, project_id, title, created_at,
            last_active_at.
        """
        async with self._factory() as db:
            try:
                stmt = (
                    select(Conversation)
                    .where(Conversation.project_id == project_id)
                    .order_by(Conversation.last_active_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                result = await db.execute(stmt)
                convs = result.scalars().all()
                return [_conv_to_dict(c) for c in convs]
            except Exception:
                logger.error(
                    "ConversationStore.list_conversations failed for project %s",
                    project_id,
                    exc_info=True,
                )
                raise

    # ─────────────────────────────────────────────────────────────────────────
    # Public: Messages
    # ─────────────────────────────────────────────────────────────────────────

    async def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Persist a message to the conversation and update last_active_at.

        Args:
            conversation_id: UUID of the target conversation.
            role:            ``"user"`` | ``"assistant"`` | ``"system"`` | ``"tool"``.
            content:         Full message text.
            metadata:        Optional metadata blob (model, tokens, cost, etc.).

        Returns:
            UUID of the newly created message.

        Raises:
            ValueError: If role is not a recognised value.
            Exception:  On DB errors (logged with full traceback).
        """
        _VALID_ROLES = {"user", "assistant", "system", "tool"}
        if role not in _VALID_ROLES:
            raise ValueError(f"Invalid role {role!r}. Must be one of {_VALID_ROLES}.")

        # Sanitize surrogate characters that SQLite/aiosqlite cannot encode
        if content and isinstance(content, str):
            content = content.encode("utf-8", errors="replace").decode("utf-8")

        async with self._factory() as db:
            try:
                msg = Message(
                    conversation_id=conversation_id,
                    role=role,
                    content=content,
                    timestamp=_utcnow(),
                    metadata_json=metadata,
                )
                db.add(msg)
                # Update last_active_at on the parent conversation
                await db.execute(
                    update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(last_active_at=_utcnow())
                )
                await db.flush()
                msg_id = msg.id
                await db.commit()
                logger.debug(
                    "ConversationStore: appended %s message %s to conv %s",
                    role,
                    msg_id,
                    conversation_id,
                )
                return msg_id
            except Exception:
                logger.error(
                    "ConversationStore.append_message failed (conv=%s role=%s)",
                    conversation_id,
                    role,
                    exc_info=True,
                )
                await db.rollback()
                raise

    async def get_conversation_history(
        self,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return all messages in a conversation, ordered by timestamp ASC.

        Designed for context-window reconstruction on agent reconnect.
        Returns the full, untruncated history by default; pass ``limit``
        to retrieve only the N most recent messages.

        Args:
            conversation_id: UUID of the conversation.
            limit:           If set, return only the last N messages.

        Returns:
            List of dicts with keys: id, conversation_id, role, content,
            timestamp, metadata_json.
        """
        async with self._factory() as db:
            try:
                stmt = (
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.timestamp.asc())
                )
                if limit is not None:
                    # We still order ASC but take the tail — use a subquery approach
                    # or simply fetch all and slice (conversations are typically small).
                    # For very long conversations use proper DESC + LIMIT + reverse.
                    inner = (
                        select(Message)
                        .where(Message.conversation_id == conversation_id)
                        .order_by(Message.timestamp.desc())
                        .limit(limit)
                        .subquery()
                    )
                    from sqlalchemy.orm import aliased

                    msg_alias = aliased(Message, inner)
                    stmt = select(msg_alias).order_by(msg_alias.timestamp.asc())

                result = await db.execute(stmt)
                messages = result.scalars().all()
                return [_msg_to_dict(m) for m in messages]
            except Exception:
                logger.error(
                    "ConversationStore.get_conversation_history failed for conv %s",
                    conversation_id,
                    exc_info=True,
                )
                raise

    # ─────────────────────────────────────────────────────────────────────────
    # Public: Execution Sessions
    # ─────────────────────────────────────────────────────────────────────────

    async def create_execution_session(
        self,
        project_id: str,
        *,
        title: str | None = None,
        prompt: str | None = None,
        plan_json: dict | None = None,
        total_tasks: int = 0,
    ) -> str:
        """Create a new execution session and return its UUID.

        Args:
            project_id: The project this session belongs to.
            title:      Human-readable title (typically the user prompt).
            prompt:     The original user prompt.
            plan_json:  The DAG plan as a dict.
            total_tasks: Number of tasks in the plan.

        Returns:
            The new session's UUID string.
        """
        async with self._factory() as db:
            try:
                await _ensure_project(db, project_id)
                session = ExecutionSession(
                    project_id=project_id,
                    title=title,
                    prompt=prompt,
                    plan_json=plan_json,
                    total_tasks=total_tasks,
                    status="running",
                    started_at=_utcnow(),
                )
                db.add(session)
                await db.flush()
                session_id = session.id
                await db.commit()
                logger.info(
                    "ConversationStore: created execution session %s for project %s",
                    session_id,
                    project_id,
                )
                return session_id
            except Exception:
                logger.error(
                    "ConversationStore.create_execution_session failed for project %s",
                    project_id,
                    exc_info=True,
                )
                await db.rollback()
                raise

    async def update_execution_session(
        self,
        session_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        completed_tasks: int | None = None,
        failed_tasks: int | None = None,
        total_input_tokens: int | None = None,
        total_output_tokens: int | None = None,
        total_tokens: int | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Update fields on an existing execution session.

        Only non-None arguments are applied.
        """
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status
        if summary is not None:
            values["summary"] = summary
        if completed_tasks is not None:
            values["completed_tasks"] = completed_tasks
        if failed_tasks is not None:
            values["failed_tasks"] = failed_tasks
        if total_input_tokens is not None:
            values["total_input_tokens"] = total_input_tokens
        if total_output_tokens is not None:
            values["total_output_tokens"] = total_output_tokens
        if total_tokens is not None:
            values["total_tokens"] = total_tokens
        if completed_at is not None:
            values["completed_at"] = completed_at

        if not values:
            return

        async with self._factory() as db:
            try:
                stmt = (
                    update(ExecutionSession)
                    .where(ExecutionSession.id == session_id)
                    .values(**values)
                )
                await db.execute(stmt)
                await db.commit()
            except Exception:
                logger.error(
                    "ConversationStore.update_execution_session failed for session %s",
                    session_id,
                    exc_info=True,
                )
                await db.rollback()
                raise

    async def list_execution_sessions(
        self,
        project_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return execution sessions for a project, most recent first."""
        async with self._factory() as db:
            try:
                stmt = (
                    select(ExecutionSession)
                    .where(ExecutionSession.project_id == project_id)
                    .order_by(ExecutionSession.started_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                result = await db.execute(stmt)
                sessions = result.scalars().all()
                return [_exec_session_to_dict(s) for s in sessions]
            except Exception:
                logger.error(
                    "ConversationStore.list_execution_sessions failed for project %s",
                    project_id,
                    exc_info=True,
                )
                raise

    # ─────────────────────────────────────────────────────────────────────────
    # Public: Project History (aggregated)
    # ─────────────────────────────────────────────────────────────────────────

    async def get_project_history(
        self,
        project_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return full chronological project history grouped by session/run.

        Aggregates execution sessions, conversations, messages, and agent
        actions into a unified timeline. Each execution session includes its
        associated conversations and agent actions.

        Returns:
            Dict with keys: project_id, sessions (list of session dicts with
            nested conversations and agent_actions), total_sessions.
        """
        async with self._factory() as db:
            try:
                # 1. Fetch execution sessions
                sess_stmt = (
                    select(ExecutionSession)
                    .where(ExecutionSession.project_id == project_id)
                    .order_by(ExecutionSession.started_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                sess_result = await db.execute(sess_stmt)
                sessions = sess_result.scalars().all()

                # 2. Fetch all conversations for this project
                conv_stmt = (
                    select(Conversation)
                    .where(Conversation.project_id == project_id)
                    .order_by(Conversation.last_active_at.desc())
                )
                conv_result = await db.execute(conv_stmt)
                conversations = conv_result.scalars().all()

                # 3. Fetch all agent actions via conversations
                conv_ids = [c.id for c in conversations]
                actions: list[AgentAction] = []
                if conv_ids:
                    action_stmt = (
                        select(AgentAction)
                        .where(AgentAction.conversation_id.in_(conv_ids))
                        .order_by(AgentAction.timestamp.asc())
                    )
                    action_result = await db.execute(action_stmt)
                    actions = list(action_result.scalars().all())

                # 4. Fetch message counts per conversation
                msg_counts: dict[str, int] = {}
                if conv_ids:
                    count_stmt = (
                        select(
                            Message.conversation_id,
                            func.count(Message.id).label("cnt"),
                        )
                        .where(Message.conversation_id.in_(conv_ids))
                        .group_by(Message.conversation_id)
                    )
                    count_result = await db.execute(count_stmt)
                    for row in count_result:
                        msg_counts[row[0]] = row[1]

                # 5. Build session-grouped response
                # Group actions by task_id for richer session data
                actions_by_task: dict[str | None, list[dict]] = {}
                for a in actions:
                    key = a.task_id or "__unassigned__"
                    actions_by_task.setdefault(key, []).append(_action_to_dict(a))

                session_dicts = []
                for s in sessions:
                    sd = _exec_session_to_dict(s)
                    # Attach task-grouped agent actions from the plan
                    task_ids: set[str] = set()
                    if s.plan_json and isinstance(s.plan_json, dict):
                        for task in s.plan_json.get("tasks", []):
                            tid = task.get("task_id") or task.get("id")
                            if tid:
                                task_ids.add(tid)

                    sd["agent_actions"] = {}
                    for tid in task_ids:
                        if tid in actions_by_task:
                            sd["agent_actions"][tid] = actions_by_task[tid]
                    # Include unassigned actions if no plan tasks
                    if not task_ids and "__unassigned__" in actions_by_task:
                        sd["agent_actions"]["__unassigned__"] = actions_by_task["__unassigned__"]

                    session_dicts.append(sd)

                # 6. Build conversations list with message counts
                conv_dicts = []
                for c in conversations:
                    cd = _conv_to_dict(c)
                    cd["message_count"] = msg_counts.get(c.id, 0)
                    conv_dicts.append(cd)

                # Total sessions count
                total_stmt = select(func.count(ExecutionSession.id)).where(
                    ExecutionSession.project_id == project_id
                )
                total_result = await db.execute(total_stmt)
                total_sessions = total_result.scalar() or 0

                return {
                    "project_id": project_id,
                    "sessions": session_dicts,
                    "conversations": conv_dicts,
                    "total_sessions": total_sessions,
                    "total_conversations": len(conv_dicts),
                    "limit": limit,
                    "offset": offset,
                }
            except Exception:
                logger.error(
                    "ConversationStore.get_project_history failed for project %s",
                    project_id,
                    exc_info=True,
                )
                raise

    async def get_project_history_summary(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        """Generate a concise summary of all execution history for a project.

        Aggregates token usage, task outcomes, and session statuses into an
        actionable overview.

        Returns:
            Dict with keys: project_id, total_sessions, total_conversations,
            total_messages, total_agent_actions, aggregate token counts,
            sessions (list of per-session summaries), and a generated
            natural-language summary.
        """
        async with self._factory() as db:
            try:
                # 1. Execution session stats
                sess_stmt = (
                    select(ExecutionSession)
                    .where(ExecutionSession.project_id == project_id)
                    .order_by(ExecutionSession.started_at.desc())
                )
                sess_result = await db.execute(sess_stmt)
                sessions = sess_result.scalars().all()

                # 2. Conversation count
                conv_count_stmt = select(func.count(Conversation.id)).where(
                    Conversation.project_id == project_id
                )
                conv_count_result = await db.execute(conv_count_stmt)
                total_conversations = conv_count_result.scalar() or 0

                # 3. Message count
                msg_count_stmt = (
                    select(func.count(Message.id))
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(Conversation.project_id == project_id)
                )
                msg_count_result = await db.execute(msg_count_stmt)
                total_messages = msg_count_result.scalar() or 0

                # 4. Agent action stats
                action_stats_stmt = (
                    select(
                        func.count(AgentAction.id).label("total_actions"),
                        func.coalesce(func.sum(AgentAction.input_tokens), 0).label(
                            "sum_input_tokens"
                        ),
                        func.coalesce(func.sum(AgentAction.output_tokens), 0).label(
                            "sum_output_tokens"
                        ),
                        func.coalesce(func.sum(AgentAction.total_tokens), 0).label(
                            "sum_total_tokens"
                        ),
                    )
                    .join(
                        Conversation,
                        AgentAction.conversation_id == Conversation.id,
                    )
                    .where(Conversation.project_id == project_id)
                )
                action_result = await db.execute(action_stats_stmt)
                action_row = action_result.one()
                total_actions = action_row[0]
                sum_input_tokens = action_row[1]
                sum_output_tokens = action_row[2]
                sum_total_tokens = action_row[3]

                # 5. Build per-session summaries
                session_summaries = []
                total_completed_tasks = 0
                total_failed_tasks = 0
                total_all_tasks = 0

                for s in sessions:
                    total_completed_tasks += s.completed_tasks or 0
                    total_failed_tasks += s.failed_tasks or 0
                    total_all_tasks += s.total_tasks or 0

                    duration_seconds: float | None = None
                    if s.completed_at and s.started_at:
                        duration_seconds = (s.completed_at - s.started_at).total_seconds()

                    session_summaries.append(
                        {
                            "id": s.id,
                            "title": s.title,
                            "status": s.status,
                            "prompt": (
                                s.prompt[:200] + "…"
                                if s.prompt and len(s.prompt) > 200
                                else s.prompt
                            ),
                            "total_tasks": s.total_tasks,
                            "completed_tasks": s.completed_tasks,
                            "failed_tasks": s.failed_tasks,
                            "total_tokens": s.total_tokens,
                            "duration_seconds": duration_seconds,
                            "started_at": (s.started_at.isoformat() if s.started_at else None),
                            "completed_at": (
                                s.completed_at.isoformat() if s.completed_at else None
                            ),
                            "summary": s.summary,
                        }
                    )

                # 6. Generate natural-language summary
                nl_summary = _generate_project_summary(
                    total_sessions=len(sessions),
                    total_conversations=total_conversations,
                    total_messages=total_messages,
                    total_actions=total_actions,
                    total_all_tasks=total_all_tasks,
                    total_completed_tasks=total_completed_tasks,
                    total_failed_tasks=total_failed_tasks,
                    sum_total_tokens=sum_total_tokens,
                    session_summaries=session_summaries,
                )

                return {
                    "project_id": project_id,
                    "summary": nl_summary,
                    "total_sessions": len(sessions),
                    "total_conversations": total_conversations,
                    "total_messages": total_messages,
                    "total_agent_actions": total_actions,
                    "aggregate_tokens": {
                        "input_tokens": sum_input_tokens,
                        "output_tokens": sum_output_tokens,
                        "total_tokens": sum_total_tokens,
                    },
                    "task_stats": {
                        "total": total_all_tasks,
                        "completed": total_completed_tasks,
                        "failed": total_failed_tasks,
                    },
                    "sessions": session_summaries,
                }
            except Exception:
                logger.error(
                    "ConversationStore.get_project_history_summary failed for project %s",
                    project_id,
                    exc_info=True,
                )
                raise


# ─────────────────────────────────────────────────────────────────────────────
# Summary generation helper
# ─────────────────────────────────────────────────────────────────────────────


def _generate_project_summary(
    *,
    total_sessions: int,
    total_conversations: int,
    total_messages: int,
    total_actions: int,
    total_all_tasks: int,
    total_completed_tasks: int,
    total_failed_tasks: int,
    sum_total_tokens: int,
    session_summaries: list[dict[str, Any]],
) -> str:
    """Generate a concise, actionable natural-language summary of project history."""
    if total_sessions == 0:
        return "No execution history found for this project."

    parts: list[str] = []

    # Overview line
    parts.append(
        f"Project has {total_sessions} execution session(s), "
        f"{total_conversations} conversation(s), and {total_messages} message(s)."
    )

    # Task stats
    if total_all_tasks > 0:
        success_rate = (
            round(total_completed_tasks / total_all_tasks * 100, 1) if total_all_tasks > 0 else 0
        )
        parts.append(
            f"Tasks: {total_completed_tasks}/{total_all_tasks} completed "
            f"({success_rate}% success rate), {total_failed_tasks} failed."
        )

    # Token usage
    if sum_total_tokens > 0:
        if sum_total_tokens >= 1_000_000:
            token_str = f"{sum_total_tokens / 1_000_000:.1f}M"
        elif sum_total_tokens >= 1_000:
            token_str = f"{sum_total_tokens / 1_000:.1f}K"
        else:
            token_str = str(sum_total_tokens)
        parts.append(f"Total token usage: {token_str}.")

    # Recent sessions
    recent = session_summaries[:5]
    if recent:
        parts.append("\nRecent sessions:")
        for s in recent:
            status_icon = {
                "completed": "✓",
                "failed": "✗",
                "running": "⟳",
                "cancelled": "⊘",
            }.get(s["status"], "?")
            title_part = s["title"] or s.get("prompt") or "Untitled"
            if len(title_part) > 80:
                title_part = title_part[:77] + "…"
            duration_part = ""
            if s.get("duration_seconds") is not None:
                mins = s["duration_seconds"] / 60
                if mins >= 1:
                    duration_part = f" ({mins:.0f}m)"
                else:
                    duration_part = f" ({s['duration_seconds']:.0f}s)"
            task_part = ""
            if s.get("total_tasks", 0) > 0:
                task_part = f" [{s['completed_tasks']}/{s['total_tasks']} tasks]"
            parts.append(f"  {status_icon} {title_part}{task_part}{duration_part}")
            if s.get("summary"):
                # Include first line of session summary
                first_line = s["summary"].split("\n")[0].strip()
                if len(first_line) > 100:
                    first_line = first_line[:97] + "…"
                parts.append(f"    → {first_line}")

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helpers (keep ORM objects out of API layer)
# ─────────────────────────────────────────────────────────────────────────────


def _conv_to_dict(c: Conversation) -> dict[str, Any]:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "title": c.title,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "last_active_at": c.last_active_at.isoformat() if c.last_active_at else None,
    }


def _msg_to_dict(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "role": m.role,
        "content": m.content,
        "timestamp": m.timestamp.isoformat() if m.timestamp else None,
        "metadata": m.metadata_json,
    }


def _action_to_dict(a: AgentAction) -> dict[str, Any]:
    """Serialize an AgentAction ORM instance to a plain dict."""
    return {
        "id": a.id,
        "conversation_id": a.conversation_id,
        "agent_role": a.agent_role,
        "action_type": a.action_type,
        "task_id": a.task_id,
        "round": a.round,
        "payload": a.payload_json,
        "result": a.result_json,
        "input_tokens": a.input_tokens,
        "output_tokens": a.output_tokens,
        "total_tokens": a.total_tokens,
        "timestamp": a.timestamp.isoformat() if a.timestamp else None,
    }


def _exec_session_to_dict(s: ExecutionSession) -> dict[str, Any]:
    """Serialize an ExecutionSession ORM instance to a plain dict."""
    return {
        "id": s.id,
        "project_id": s.project_id,
        "title": s.title,
        "status": s.status,
        "prompt": s.prompt,
        "plan": s.plan_json,
        "summary": s.summary,
        "total_tasks": s.total_tasks,
        "completed_tasks": s.completed_tasks,
        "failed_tasks": s.failed_tasks,
        "total_input_tokens": s.total_input_tokens,
        "total_output_tokens": s.total_output_tokens,
        "total_tokens": s.total_tokens,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
