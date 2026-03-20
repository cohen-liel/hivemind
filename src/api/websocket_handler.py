"""WebSocket handler extensions for conversation history persistence.

This module provides the ``ConversationWebSocketMixin`` — a set of helper
coroutines that plug into the existing WebSocket handler in
``dashboard/api.py`` to:

1. Load full conversation history from DB on reconnect (so agents remember
   prior interactions without any in-memory-only state).
2. Persist user messages and assistant responses to the ConversationStore
   so all state survives server restarts.

Additionally this module defines the **canonical WebSocket event type
registry** (``WebSocketEventType``) and schema helpers for all events that
the orchestration server emits to connected clients.  Consumers should import
event type constants from this module rather than using raw strings so that
typos are caught at import time and the API contract is documented in one
place.

Design decisions
----------------
- This module does NOT replace the existing WebSocket handler in
  ``dashboard/api.py``. It augments it via standalone async functions
  that the handler calls at key points in the message lifecycle.
- The ``ConversationStore`` is obtained from the cached session factory so
  no HTTP request context is needed (safe to call from WebSocket handlers).
- Project-level conversation IDs are cached in a module-level dict
  (``_active_conversation_ids``) to avoid a DB round-trip on every message.
  The cache is keyed by ``project_id`` and survives the lifetime of the
  process. It is NOT shared across server restarts (that is intentional —
  the DB is the authoritative source).

Usage (in the existing WebSocket receiver)
------------------------------------------
::

    from src.api.websocket_handler import (
        load_history_on_connect,
        persist_user_message,
        persist_assistant_message,
        get_or_create_conversation_id,
        WebSocketEventType,
        build_task_queued_event,
    )

    # On WebSocket connect for a project:
    conv_id = await get_or_create_conversation_id(project_id)
    history = await load_history_on_connect(project_id, conv_id)
    if history:
        await ws.send_json({
            "type": "conversation_history",
            "project_id": project_id,
            "conversation_id": conv_id,
            "messages": history,
        })

    # When a user message arrives:
    await persist_user_message(project_id, conv_id, user_text)

    # When an assistant response is published:
    await persist_assistant_message(project_id, conv_id, assistant_text, metadata)

    # When a new task graph is submitted to the ingestion queue:
    event = build_task_queued_event(
        project_id=project_id,
        message_preview="fix the login bug",
        queue_position=2,
        queue_depth=2,
        running_graphs=1,
        max_concurrent_graphs=3,
    )
    await ws.send_json(event)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.db.database import get_session_factory
from src.storage.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

# Module-level cache: project_id → conversation_id
# Avoids a DB round-trip on every WebSocket message within a server session.
# The DB remains authoritative; this is just a performance optimisation.
_active_conversation_ids: dict[str, str] = {}


# ---------------------------------------------------------------------------
# WebSocket Event Type Registry (canonical single source of truth)
# ---------------------------------------------------------------------------


class WebSocketEventType:
    """String constants for every WebSocket event type the server emits.

    Use these constants instead of raw strings so that the contract is
    enforced at import time and documented in one place.

    Server → Client events
    ----------------------
    Standard lifecycle events (existed before task_003):

    ``PROJECT_STATUS``
        Project-level status change.
        Payload: ``{status: "running"|"idle"|"paused"|"error"}``

    ``AGENT_STARTED``
        A sub-agent has begun executing a task.
        Payload: ``{agent, task, task_id?, is_remediation?}``

    ``AGENT_UPDATE``
        Streaming update from a running agent.
        Payload: ``{agent, summary, text?, task_id?}``

    ``AGENT_FINISHED``
        A sub-agent has completed its task.
        Payload: ``{agent, cost, turns, duration, is_error, task_id,
                    task_status, failure_reason?}``

    ``AGENT_STREAM``
        Raw streaming text from a running agent (token-level).
        Payload: ``{agent, text, task_id}``

    ``TOOL_USE``
        An agent invoked a tool.
        Payload: ``{agent, tool_name, description, task_id}``

    ``TASK_GRAPH``
        The PM agent created a full task graph.
        Payload: ``{graph: TaskGraph}``

    ``TASK_STATUS``
        A single DAG node changed status.
        Payload: ``{task_id, status: "working"|"completed"|"failed"}``

    ``MESSAGE_QUEUED``
        A *user message* was queued while the orchestrator was busy.
        Payload: ``{queue_size, message_preview}``

    ``STUCKNESS_DETECTED``
        The orchestrator detected a stalled execution pattern.
        Payload: ``{category, severity, description, affected_agent?,
                    suggested_action}``

    ``APPROVAL_REQUEST``
        The orchestrator is waiting for human approval.
        Payload: ``{description}``

    ``HEARTBEAT``
        Server keep-alive ping.
        Payload: ``{}``

    New events added in task_003 (parallel task ingestion pipeline):

    ``TASK_QUEUED``  ← NEW
        A new task graph was submitted to the bounded ingestion queue while
        the orchestrator was busy executing another graph.  The frontend
        should show a "Task queued" indicator and update its queue-depth
        counter.

        Payload schema::

            {
              "type":                "task_queued",
              "timestamp":           <float: Unix epoch seconds>,
              "project_id":          <str: project UUID>,
              "message_preview":     <str: first 100 chars of user message>,
              "queue_position":      <int: 1-indexed position in queue>,
              "queue_depth":         <int: total pending items in queue>,
              "running_graphs":      <int: currently executing graph count>,
              "max_concurrent_graphs": <int: semaphore capacity limit>
            }

        Example::

            {
              "type": "task_queued",
              "timestamp": 1741695600.0,
              "project_id": "abc123",
              "message_preview": "Add dark mode to the dashboard",
              "queue_position": 1,
              "queue_depth": 1,
              "running_graphs": 1,
              "max_concurrent_graphs": 3
            }

    Incremental plan update events (task_004):

    ``PLAN_DELTA``  ← NEW
        Incremental plan update carrying only added/skipped tasks.
        Emitted when the PM agent merges new tasks into a running DAG
        or marks tasks as skipped. Clients apply the delta to their
        local plan state.

        Payload schema::

            {
              "type":           "plan_delta",
              "timestamp":      <float: Unix epoch seconds>,
              "project_id":     <str: project UUID>,
              "add_tasks":      <list[dict]: TaskInput dicts to append>,
              "skip_task_ids":  <list[str]: task IDs to mark SKIPPED>,
              "reason":         <str: human-readable reason for changes>
            }

    ``DAG_TASK_UPDATE``  (enhanced)
        Now supports ``skipped`` as a valid status value alongside
        ``working``, ``completed``, ``failed``, and ``cancelled``.
        When status is ``skipped``, an optional ``reason`` field
        explains why the task was skipped.

        Payload schema::

            {
              "type":            "dag_task_update",
              "timestamp":       <float: Unix epoch seconds>,
              "project_id":      <str: project UUID>,
              "task_id":         <str: task ID>,
              "status":          <str: "working"|"completed"|"failed"|"cancelled"|"skipped">,
              "task_name":       <str: human-readable goal>,
              "agent":           <str: agent role (optional)>,
              "failure_reason":  <str: failure details (optional)>,
              "reason":          <str: skip reason (optional, only for skipped)>
            }
    """

    # ── Standard lifecycle ─────────────────────────────────────────────
    PROJECT_STATUS: str = "project_status"
    AGENT_STARTED: str = "agent_started"
    AGENT_UPDATE: str = "agent_update"
    AGENT_FINISHED: str = "agent_finished"
    AGENT_STREAM: str = "agent_stream"
    TOOL_USE: str = "tool_use"
    TASK_GRAPH: str = "task_graph"
    TASK_STATUS: str = "task_status"
    MESSAGE_QUEUED: str = "message_queued"
    STUCKNESS_DETECTED: str = "stuckness_detected"
    APPROVAL_REQUEST: str = "approval_request"
    HEARTBEAT: str = "heartbeat"

    # ── Parallel task ingestion (task_003) ────────────────────────────
    TASK_QUEUED: str = "task_queued"

    # ── Granular DAG progress (task_005) ──────────────────────────────
    TASK_PROGRESS: str = "task_progress"
    DAG_PROGRESS: str = "dag_progress"

    # ── Incremental plan updates (task_004) ────────────────────────────
    PLAN_DELTA: str = "plan_delta"
    DAG_TASK_UPDATE: str = "dag_task_update"

    # ── Real-time chat (task_004 — circles/chat) ─────────────────────
    CHAT_MESSAGE: str = "chat_message"
    CHAT_TYPING: str = "chat_typing"
    CHAT_READ_RECEIPT: str = "chat_read_receipt"


def build_task_queued_event(
    project_id: str,
    message_preview: str,
    queue_position: int,
    queue_depth: int,
    running_graphs: int,
    max_concurrent_graphs: int,
) -> dict[str, Any]:
    """Build a fully-formed ``task_queued`` WebSocket event payload.

    This helper ensures the event always contains every required field
    with correct types — clients can rely on the schema without defensive
    null-checks.

    Args:
        project_id:            UUID of the project the task belongs to.
        message_preview:       First ≤100 characters of the user message.
        queue_position:        1-indexed position of this task in the queue.
        queue_depth:           Total number of pending tasks (including this one).
        running_graphs:        Number of graph executions currently active.
        max_concurrent_graphs: Maximum concurrent graphs (semaphore capacity).

    Returns:
        Dict ready to be serialised to JSON and sent via WebSocket.

    Example::

        event = build_task_queued_event(
            project_id="abc123",
            message_preview="Add dark mode",
            queue_position=1,
            queue_depth=1,
            running_graphs=1,
            max_concurrent_graphs=3,
        )
        await ws.send_json(event)
    """
    return {
        "type": WebSocketEventType.TASK_QUEUED,
        "timestamp": time.time(),
        "project_id": project_id,
        "message_preview": message_preview[:100],
        "queue_position": queue_position,
        "queue_depth": queue_depth,
        "running_graphs": running_graphs,
        "max_concurrent_graphs": max_concurrent_graphs,
    }


def build_message_queued_event(
    project_id: str,
    task_id: str,
    message_preview: str,
    queue_position: int,
    queue_depth: int,
    running_count: int,
    max_concurrent: int,
    estimated_wait_seconds: float,
) -> dict[str, Any]:
    """Build a ``message_queued`` WebSocket event for instant feedback.

    Emitted immediately when a user message is enqueued via the REST API,
    so the frontend can show queue position and estimated wait time before
    any agent work starts.

    Args:
        project_id:            UUID of the project.
        task_id:               UUID of the newly created task.
        message_preview:       First ≤100 characters of the user message.
        queue_position:        1-indexed position in the queue (0 if running immediately).
        queue_depth:           Total pending items in the queue.
        running_count:         Number of tasks currently executing.
        max_concurrent:        Concurrency limit.
        estimated_wait_seconds: Estimated seconds until this task starts.

    Returns:
        Dict ready to be serialised to JSON and sent via WebSocket / EventBus.
    """
    return {
        "type": WebSocketEventType.MESSAGE_QUEUED,
        "timestamp": time.time(),
        "project_id": project_id,
        "task_id": task_id,
        "message_preview": message_preview[:100],
        "queue_position": queue_position,
        "queue_depth": queue_depth,
        "running_count": running_count,
        "max_concurrent": max_concurrent,
        "estimated_wait_seconds": estimated_wait_seconds,
    }


def build_task_progress_event(
    project_id: str,
    task_id: str,
    milestone: str,
    elapsed_s: float,
    est_remaining_s: float = 0.0,
) -> dict[str, Any]:
    """Build a lightweight ``task_progress`` event (< 200 bytes).

    Milestones: preparing, agent_working, writing_files,
    summarising, complete, failed.
    """
    return {
        "type": WebSocketEventType.TASK_PROGRESS,
        "ts": round(time.time(), 1),
        "pid": project_id[:12],
        "tid": task_id,
        "ms": milestone,
        "el": round(elapsed_s, 1),
        "er": round(est_remaining_s, 1),
    }


def build_dag_progress_event(
    project_id: str,
    completed: int,
    total: int,
    elapsed_s: float,
    est_remaining_s: float = 0.0,
) -> dict[str, Any]:
    """Build a lightweight ``dag_progress`` aggregate event (< 200 bytes)."""
    pct = round(completed / total * 100, 1) if total > 0 else 0.0
    return {
        "type": WebSocketEventType.DAG_PROGRESS,
        "ts": round(time.time(), 1),
        "pid": project_id[:12],
        "done": completed,
        "total": total,
        "pct": pct,
        "el": round(elapsed_s, 1),
        "er": round(est_remaining_s, 1),
    }


def _get_store() -> ConversationStore:
    """Return a ConversationStore using the cached session factory."""
    return ConversationStore(get_session_factory())


# ---------------------------------------------------------------------------
# Public helpers — called from the WebSocket handler
# ---------------------------------------------------------------------------


async def get_or_create_conversation_id(project_id: str) -> str:
    """Return the active conversation UUID for a project.

    On the first call for a ``project_id``, loads the most-recently-active
    conversation from DB (or creates a new one if none exists). Subsequent
    calls within the same server process return the cached value.

    Args:
        project_id: The project to get/create a conversation for.

    Returns:
        UUID string of the active conversation.
    """
    if project_id in _active_conversation_ids:
        return _active_conversation_ids[project_id]

    store = _get_store()
    conv_id = await store.get_or_create_default_conversation(project_id)
    _active_conversation_ids[project_id] = conv_id
    logger.info(
        "ws_handler: resolved conversation_id=%s for project=%s",
        conv_id,
        project_id,
    )
    return conv_id


async def load_history_on_connect(
    project_id: str,
    conversation_id: str,
    limit: int | None = 200,
) -> list[dict[str, Any]]:
    """Load full conversation history from DB for a reconnecting WebSocket.

    This is the mechanism that allows agents to remember prior interactions
    without any in-memory-only state. All history comes from the DB.

    Args:
        project_id:      Project the conversation belongs to (for logging).
        conversation_id: UUID of the conversation to load.
        limit:           Max number of messages to return (most recent N).
                         Pass ``None`` for the full history (use carefully
                         for very long conversations).

    Returns:
        List of message dicts ordered chronologically (oldest first), each
        with keys: id, conversation_id, role, content, timestamp, metadata.
        Returns an empty list if no messages have been stored yet.
    """
    store = _get_store()
    try:
        messages = await store.get_conversation_history(conversation_id, limit=limit)
        logger.info(
            "ws_handler: loaded %d messages for project=%s conv=%s (limit=%s)",
            len(messages),
            project_id,
            conversation_id,
            limit,
        )
        return messages
    except Exception:
        logger.error(
            "ws_handler: failed to load history for project=%s conv=%s",
            project_id,
            conversation_id,
            exc_info=True,
        )
        return []  # Degrade gracefully — don't crash the WebSocket handler


async def persist_user_message(
    project_id: str,
    conversation_id: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Persist a user message to the conversation store.

    Called immediately when a user message arrives via the REST endpoint or
    WebSocket. The message is stored with role="user".

    Args:
        project_id:      Used for logging only (FK is conversation_id).
        conversation_id: UUID of the active conversation.
        content:         Full user message text.
        metadata:        Optional metadata dict.

    Returns:
        UUID of the created message, or None on error.
    """
    store = _get_store()
    try:
        msg_id = await store.append_message(
            conversation_id=conversation_id,
            role="user",
            content=content,
            metadata=metadata,
        )
        logger.debug(
            "ws_handler: persisted user message %s for project=%s",
            msg_id,
            project_id,
        )
        return msg_id
    except Exception:
        logger.error(
            "ws_handler: failed to persist user message for project=%s conv=%s",
            project_id,
            conversation_id,
            exc_info=True,
        )
        return None  # Degrade gracefully — don't break the WebSocket flow


async def persist_assistant_message(
    project_id: str,
    conversation_id: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Persist an assistant message to the conversation store.

    Called when an agent response is published via the event bus. Stores
    the message with role="assistant".

    Args:
        project_id:      Used for logging only.
        conversation_id: UUID of the active conversation.
        content:         Full assistant response text.
        metadata:        Optional metadata (model, input_tokens, output_tokens,
                         cost_usd, stop_reason). Do NOT include API keys.

    Returns:
        UUID of the created message, or None on error.
    """
    store = _get_store()
    try:
        msg_id = await store.append_message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            metadata=metadata,
        )
        logger.debug(
            "ws_handler: persisted assistant message %s for project=%s",
            msg_id,
            project_id,
        )
        return msg_id
    except Exception:
        logger.error(
            "ws_handler: failed to persist assistant message for project=%s conv=%s",
            project_id,
            conversation_id,
            exc_info=True,
        )
        return None


async def start_new_conversation(
    project_id: str,
    title: str | None = None,
) -> str:
    """Create a new conversation for a project and update the active cache.

    Call this when a user explicitly starts a "new conversation" (e.g. via
    the UI's "New Conversation" button). The new conversation ID is cached
    so subsequent messages flow into it.

    Args:
        project_id: The project to create the conversation for.
        title:      Optional human-readable title.

    Returns:
        UUID of the new conversation.
    """
    store = _get_store()
    conv_id = await store.create_conversation(project_id, title=title)
    _active_conversation_ids[project_id] = conv_id
    logger.info(
        "ws_handler: started new conversation %s for project=%s",
        conv_id,
        project_id,
    )
    return conv_id


def get_cached_conversation_id(project_id: str) -> str | None:
    """Return the cached conversation ID for a project, or None if not set.

    Useful for background tasks that need the current conversation ID without
    triggering a DB call.

    Args:
        project_id: Project to look up.

    Returns:
        UUID string or None.
    """
    return _active_conversation_ids.get(project_id)


def invalidate_conversation_cache(project_id: str) -> None:
    """Remove a project's cached conversation ID.

    Call this after clearing project history so the next WebSocket connect
    creates a fresh conversation rather than referencing the deleted one.

    Args:
        project_id: Project to invalidate.
    """
    _active_conversation_ids.pop(project_id, None)
    logger.debug("ws_handler: invalidated conversation cache for project=%s", project_id)
