"""REST API router for conversation history and project execution history.

Endpoints
---------
GET  /api/conversations/{project_id}
    List all conversations for a project (paginated).

GET  /api/conversations/{project_id}/{conversation_id}
    Get a single conversation with its full message history.

GET  /api/conversations/{project_id}/{conversation_id}/messages
    Get paginated messages for a conversation.

POST /api/conversations/{project_id}
    Create a new conversation for a project.

GET  /api/memory/{project_id}
    Get all agent memory for a project.

PUT  /api/memory/{project_id}/{key}
    Set a memory key for a project.

DELETE /api/memory/{project_id}/{key}
    Delete a memory key for a project.

GET  /api/projects/{project_id}/history
    Full chronological execution history grouped by session/run.

GET  /api/projects/{project_id}/history/summary
    Auto-generated summary of what was accomplished across all sessions.

These endpoints are registered on the FastAPI app by calling
``app.include_router(history_router)`` in ``dashboard/api.py``.

All errors follow the RFC 7807 Problem Detail format::

    {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "Conversation abc123 not found."
    }
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from _shared_utils import valid_project_id as _valid_project_id
from src.dependencies import get_conversation_store, get_memory_store
from src.storage.conversation_store import ConversationStore
from src.storage.memory_store import MemoryStore

logger = logging.getLogger(__name__)

history_router = APIRouter(tags=["conversations"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ConversationSummary(BaseModel):
    """Summary of a conversation (no messages included)."""

    id: str = Field(
        description="Conversation UUID", examples=["550e8400-e29b-41d4-a716-446655440000"]
    )
    project_id: str = Field(description="Parent project ID")
    title: str | None = Field(description="Human-readable title, or null if not set")
    created_at: str | None = Field(description="ISO-8601 UTC creation timestamp")
    last_active_at: str | None = Field(description="ISO-8601 UTC last-activity timestamp")

    model_config = {"from_attributes": True}


class MessageSchema(BaseModel):
    """A single message in a conversation."""

    id: str = Field(description="Message UUID")
    conversation_id: str = Field(description="Parent conversation UUID")
    role: str = Field(description="'user' | 'assistant' | 'system' | 'tool'")
    content: str = Field(description="Full message text")
    timestamp: str | None = Field(description="ISO-8601 UTC timestamp")
    metadata: dict | None = Field(
        default=None, description="Optional metadata (model, tokens, …)"
    )

    model_config = {"from_attributes": True}


class ConversationDetail(BaseModel):
    """A conversation with its full message list."""

    id: str
    project_id: str
    title: str | None
    created_at: str | None
    last_active_at: str | None
    messages: list[MessageSchema] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""

    conversations: list[ConversationSummary]
    total: int
    limit: int
    offset: int
    project_id: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "conversations": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "project_id": "my-project",
                        "title": "Fix login bug",
                        "created_at": "2026-03-11T10:00:00+00:00",
                        "last_active_at": "2026-03-11T10:05:00+00:00",
                    }
                ],
                "total": 1,
                "limit": 50,
                "offset": 0,
                "project_id": "my-project",
            }
        }
    }


class CreateConversationRequest(BaseModel):
    """Request body for creating a new conversation."""

    title: str | None = Field(
        default=None,
        max_length=500,
        description="Optional conversation title.",
        examples=["Fix login bug"],
    )

    model_config = {"json_schema_extra": {"example": {"title": "Fix login bug"}}}


class MemorySetRequest(BaseModel):
    """Request body for setting a memory key."""

    value: Any = Field(
        description="Any JSON-serialisable value. Secrets are forbidden.",
        examples=["Alice", 42, ["Python", "FastAPI"], {"nested": True}],
    )

    model_config = {"json_schema_extra": {"example": {"value": "Alice"}}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _problem(status: int, detail: str) -> JSONResponse:
    _TITLES = {
        400: "Bad Request",
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable Content",
        500: "Internal Server Error",
    }
    return JSONResponse(
        {
            "type": "about:blank",
            "title": _TITLES.get(status, "Error"),
            "status": status,
            "detail": detail,
        },
        status_code=status,
    )


# ---------------------------------------------------------------------------
# Conversation endpoints
# ---------------------------------------------------------------------------


@history_router.get(
    "/api/conversations/{project_id}",
    response_model=ConversationListResponse,
    summary="List conversations for a project",
    description=(
        "Returns all conversations for the given project, ordered by most-recently-active first. "
        "Supports pagination via ``limit`` and ``offset`` query parameters."
    ),
    responses={
        200: {"description": "Paginated list of conversations"},
        400: {"description": "Invalid project_id format"},
        500: {"description": "Internal server error"},
    },
)
async def list_conversations(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=500, description="Max results per page"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    store: ConversationStore = Depends(get_conversation_store),
) -> ConversationListResponse:
    """List all conversations for a project (most-recently-active first).

    This is the primary endpoint for the multi-project conversation sidebar.
    Each conversation summary includes its UUID, title, and timestamps —
    clients can then fetch full message history via the detail endpoint.
    """
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        convs = await store.list_conversations(project_id, limit=limit, offset=offset)
        return ConversationListResponse(
            conversations=[ConversationSummary(**c) for c in convs],
            total=len(convs),
            limit=limit,
            offset=offset,
            project_id=project_id,
        )
    except Exception:
        logger.error("GET /api/conversations/%s failed", project_id, exc_info=True)
        return _problem(500, "Failed to load conversations. Check server logs.")


@history_router.get(
    "/api/conversations/{project_id}/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get a conversation with its message history",
    description="Returns a conversation and its full message list for LLM context replay.",
    responses={
        200: {"description": "Conversation with messages"},
        400: {"description": "Invalid project_id format"},
        404: {"description": "Conversation not found"},
        500: {"description": "Internal server error"},
    },
)
async def get_conversation(
    project_id: str,
    conversation_id: str,
    store: ConversationStore = Depends(get_conversation_store),
) -> ConversationDetail:
    """Return a conversation and its full message history.

    Designed for the reconnect flow: after a WebSocket reconnects, the client
    fetches this endpoint to load the agent's full prior context before sending
    new messages. The message list is ordered chronologically (oldest first).
    """
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        # Load conversations to find this one
        convs = await store.list_conversations(project_id, limit=1000, offset=0)
        conv = next((c for c in convs if c["id"] == conversation_id), None)
        if conv is None:
            return _problem(
                404, f"Conversation {conversation_id!r} not found in project {project_id!r}."
            )

        messages = await store.get_conversation_history(conversation_id)
        return ConversationDetail(
            **conv,
            messages=[MessageSchema(**m) for m in messages],
        )
    except Exception:
        logger.error(
            "GET /api/conversations/%s/%s failed",
            project_id,
            conversation_id,
            exc_info=True,
        )
        return _problem(500, "Failed to load conversation history. Check server logs.")


@history_router.get(
    "/api/conversations/{project_id}/{conversation_id}/messages",
    summary="Get paginated messages for a conversation",
    description="Returns messages for a conversation in chronological order.",
    responses={
        200: {"description": "List of messages with pagination metadata"},
        400: {"description": "Invalid project_id format"},
        500: {"description": "Internal server error"},
    },
)
async def get_conversation_messages(
    project_id: str,
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=1000, description="Max messages per page"),
    store: ConversationStore = Depends(get_conversation_store),
) -> dict:
    """Return paginated messages for a conversation.

    Use ``limit`` to restrict context window size on reconnect.
    """
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        messages = await store.get_conversation_history(
            conversation_id, limit=limit if limit < 1000 else None
        )
        return {
            "conversation_id": conversation_id,
            "project_id": project_id,
            "messages": messages,
            "count": len(messages),
        }
    except Exception:
        logger.error(
            "GET /api/conversations/%s/%s/messages failed",
            project_id,
            conversation_id,
            exc_info=True,
        )
        return _problem(500, "Failed to load messages. Check server logs.")


@history_router.post(
    "/api/conversations/{project_id}",
    response_model=ConversationSummary,
    status_code=201,
    summary="Create a new conversation",
    description="Creates a new conversation for the given project.",
    responses={
        201: {"description": "Conversation created"},
        400: {"description": "Invalid project_id format"},
        500: {"description": "Internal server error"},
    },
)
async def create_conversation(
    project_id: str,
    req: CreateConversationRequest,
    store: ConversationStore = Depends(get_conversation_store),
) -> ConversationSummary | JSONResponse:
    """Create a new conversation for a project.

    Returns the new conversation's UUID and metadata. The conversation will
    have no messages until ``append_message`` is called.
    """
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        conv_id = await store.create_conversation(project_id, title=req.title)
        convs = await store.list_conversations(project_id, limit=1000, offset=0)
        conv = next((c for c in convs if c["id"] == conv_id), None)
        if conv is None:
            return _problem(500, "Conversation created but could not be retrieved.")
        return ConversationSummary(**conv)
    except Exception:
        logger.error("POST /api/conversations/%s failed", project_id, exc_info=True)
        return _problem(500, "Failed to create conversation. Check server logs.")


# ---------------------------------------------------------------------------
# Memory endpoints
# ---------------------------------------------------------------------------


@history_router.get(
    "/api/memory/{project_id}",
    summary="Get all agent memory for a project",
    description=(
        "Returns the full persistent agent context for a project as a flat key/value dict. "
        "Used by agents on reconnect to restore their working state."
    ),
    responses={
        200: {"description": "Dict of all memory entries"},
        400: {"description": "Invalid project_id format"},
        500: {"description": "Internal server error"},
    },
)
async def get_project_memory(
    project_id: str,
    store: MemoryStore = Depends(get_memory_store),
) -> dict:
    """Return all persisted agent memory for a project.

    Example response::

        {
            "project_id": "my-project",
            "memory": {
                "user.name": "Alice",
                "project.tech_stack": ["Python", "FastAPI"],
                "agent.orchestrator.last_plan": "..."
            },
            "count": 3
        }
    """
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        memory = await store.get_all_memory(project_id)
        return {
            "project_id": project_id,
            "memory": memory,
            "count": len(memory),
        }
    except Exception:
        logger.error("GET /api/memory/%s failed", project_id, exc_info=True)
        return _problem(500, "Failed to load memory. Check server logs.")


@history_router.put(
    "/api/memory/{project_id}/{key:path}",
    status_code=200,
    summary="Set a memory key for a project",
    description="Upserts a key/value pair in the project's agent memory. Secrets are forbidden.",
    responses={
        200: {"description": "Memory entry written"},
        400: {"description": "Invalid project_id format or key"},
        500: {"description": "Internal server error"},
    },
)
async def set_project_memory(
    project_id: str,
    key: str,
    req: MemorySetRequest,
    store: MemoryStore = Depends(get_memory_store),
) -> dict:
    """Set (upsert) a memory key for a project.

    Keys must follow dot-notation naming (e.g. ``agent.last_plan``).
    Secrets, API keys, and passwords are explicitly forbidden — the memory
    table is not encrypted and is readable by DB-level access.
    """
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        await store.set_memory(project_id, key, req.value)
        return {"ok": True, "project_id": project_id, "key": key}
    except ValueError as e:
        return _problem(400, str(e))
    except Exception:
        logger.error("PUT /api/memory/%s/%s failed", project_id, key, exc_info=True)
        return _problem(500, "Failed to set memory. Check server logs.")


@history_router.delete(
    "/api/memory/{project_id}/{key:path}",
    summary="Delete a memory key for a project",
    description="Removes a key from the project's agent memory. Returns 404 if not found.",
    responses={
        200: {"description": "Memory entry deleted"},
        400: {"description": "Invalid project_id format"},
        404: {"description": "Key not found"},
        500: {"description": "Internal server error"},
    },
)
async def delete_project_memory(
    project_id: str,
    key: str,
    store: MemoryStore = Depends(get_memory_store),
) -> dict:
    """Delete a memory key from a project."""
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        deleted = await store.delete_memory(project_id, key)
        if not deleted:
            return _problem(404, f"Memory key {key!r} not found for project {project_id!r}.")
        return {"ok": True, "project_id": project_id, "key": key, "deleted": True}
    except ValueError as e:
        return _problem(400, str(e))
    except Exception:
        logger.error("DELETE /api/memory/%s/%s failed", project_id, key, exc_info=True)
        return _problem(500, "Failed to delete memory. Check server logs.")


# ---------------------------------------------------------------------------
# Pydantic models — Project execution history
# ---------------------------------------------------------------------------


class AgentActionSchema(BaseModel):
    """A single agent action in an execution session."""

    id: str = Field(description="Action UUID")
    conversation_id: str = Field(description="Parent conversation UUID")
    agent_role: str = Field(description="Role of the agent (e.g. 'backend_developer')")
    action_type: str = Field(
        description="Action category: 'tool_call' | 'message' | 'decision' | etc."
    )
    task_id: str | None = Field(default=None, description="DAG task ID")
    round: int | None = Field(default=None, description="Orchestration round number")
    payload: dict | None = Field(default=None, description="Action input/arguments")
    result: dict | None = Field(default=None, description="Action output/result")
    input_tokens: int | None = Field(default=None, description="Input tokens consumed")
    output_tokens: int | None = Field(default=None, description="Output tokens produced")
    total_tokens: int | None = Field(default=None, description="Total tokens consumed")
    timestamp: str | None = Field(description="ISO-8601 UTC timestamp")

    model_config = {"from_attributes": True}


class ExecutionSessionSchema(BaseModel):
    """An execution session (DAG run) in project history."""

    id: str = Field(description="Session UUID")
    project_id: str = Field(description="Parent project UUID")
    title: str | None = Field(description="Human-readable title")
    status: str = Field(description="'running' | 'completed' | 'failed' | 'cancelled'")
    prompt: str | None = Field(default=None, description="Original user prompt")
    plan: dict | None = Field(default=None, description="DAG plan as JSON")
    summary: str | None = Field(default=None, description="Auto-generated session summary")
    total_tasks: int = Field(default=0, description="Total tasks in plan")
    completed_tasks: int = Field(default=0, description="Successfully completed tasks")
    failed_tasks: int = Field(default=0, description="Failed tasks")
    total_input_tokens: int = Field(default=0, description="Aggregate input tokens")
    total_output_tokens: int = Field(default=0, description="Aggregate output tokens")
    total_tokens: int = Field(default=0, description="Aggregate total tokens")
    started_at: str | None = Field(description="ISO-8601 UTC start time")
    completed_at: str | None = Field(default=None, description="ISO-8601 UTC end time")
    created_at: str | None = Field(description="ISO-8601 UTC creation time")
    agent_actions: dict[str, list[AgentActionSchema]] | None = Field(
        default=None, description="Agent actions grouped by task_id"
    )

    model_config = {"from_attributes": True}


class ConversationHistoryEntry(BaseModel):
    """A conversation entry in project history (with message count)."""

    id: str = Field(description="Conversation UUID")
    project_id: str = Field(description="Parent project UUID")
    title: str | None = Field(description="Conversation title")
    created_at: str | None = Field(description="ISO-8601 UTC creation timestamp")
    last_active_at: str | None = Field(description="ISO-8601 UTC last-activity timestamp")
    message_count: int = Field(default=0, description="Number of messages")

    model_config = {"from_attributes": True}


class ProjectHistoryResponse(BaseModel):
    """Full chronological project history grouped by session/run."""

    project_id: str
    sessions: list[ExecutionSessionSchema] = Field(default_factory=list)
    conversations: list[ConversationHistoryEntry] = Field(default_factory=list)
    total_sessions: int = Field(default=0)
    total_conversations: int = Field(default=0)
    limit: int
    offset: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "project_id": "my-project",
                "sessions": [],
                "conversations": [],
                "total_sessions": 0,
                "total_conversations": 0,
                "limit": 50,
                "offset": 0,
            }
        }
    }


class AggregateTokens(BaseModel):
    """Aggregate token usage across all sessions."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class TaskStats(BaseModel):
    """Aggregate task completion statistics."""

    total: int = 0
    completed: int = 0
    failed: int = 0


class SessionSummaryEntry(BaseModel):
    """Per-session summary in the history summary response."""

    id: str
    title: str | None = None
    status: str
    prompt: str | None = None
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_tokens: int = 0
    duration_seconds: float | None = None
    started_at: str | None = None
    completed_at: str | None = None
    summary: str | None = None

    model_config = {"from_attributes": True}


class ProjectHistorySummaryResponse(BaseModel):
    """Auto-generated summary of project execution history."""

    project_id: str
    summary: str = Field(description="Natural-language summary of project history")
    total_sessions: int = 0
    total_conversations: int = 0
    total_messages: int = 0
    total_agent_actions: int = 0
    aggregate_tokens: AggregateTokens = Field(default_factory=AggregateTokens)
    task_stats: TaskStats = Field(default_factory=TaskStats)
    sessions: list[SessionSummaryEntry] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "project_id": "my-project",
                "summary": "Project has 3 execution session(s)...",
                "total_sessions": 3,
                "total_conversations": 5,
                "total_messages": 120,
                "total_agent_actions": 45,
                "aggregate_tokens": {
                    "input_tokens": 50000,
                    "output_tokens": 25000,
                    "total_tokens": 75000,
                },
                "task_stats": {"total": 12, "completed": 10, "failed": 2},
                "sessions": [],
            }
        }
    }


# ---------------------------------------------------------------------------
# Project history endpoints
# ---------------------------------------------------------------------------


@history_router.get(
    "/api/projects/{project_id}/history",
    response_model=ProjectHistoryResponse,
    summary="Get full project execution history",
    description=(
        "Returns the complete chronological execution history for a project, "
        "grouped by execution session/run. Each session includes its agent actions "
        "grouped by task_id, along with all project conversations and message counts. "
        "Data persists across server restarts."
    ),
    responses={
        200: {"description": "Full project history grouped by session"},
        400: {"description": "Invalid project_id format"},
        500: {"description": "Internal server error"},
    },
)
async def get_project_history(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=500, description="Max sessions per page"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    store: ConversationStore = Depends(get_conversation_store),
) -> ProjectHistoryResponse | JSONResponse:
    """Return full chronological project history grouped by session/run.

    This endpoint aggregates execution sessions, conversations, messages,
    and agent actions into a unified timeline. It is the primary endpoint
    for the project history UI panel.
    """
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        history = await store.get_project_history(
            project_id, limit=limit, offset=offset
        )
        return ProjectHistoryResponse(**history)
    except Exception:
        logger.error(
            "GET /api/projects/%s/history failed", project_id, exc_info=True
        )
        return _problem(500, "Failed to load project history. Check server logs.")


@history_router.get(
    "/api/projects/{project_id}/history/summary",
    response_model=ProjectHistorySummaryResponse,
    summary="Get project execution history summary",
    description=(
        "Returns an auto-generated summary of what was accomplished across all "
        "execution sessions in the project, including aggregate token usage, "
        "task completion rates, and per-session summaries."
    ),
    responses={
        200: {"description": "Project history summary"},
        400: {"description": "Invalid project_id format"},
        500: {"description": "Internal server error"},
    },
)
async def get_project_history_summary(
    project_id: str,
    store: ConversationStore = Depends(get_conversation_store),
) -> ProjectHistorySummaryResponse | JSONResponse:
    """Return an auto-generated summary of project execution history.

    This endpoint provides a concise, actionable overview of all work done
    in the project — aggregate token usage, task outcomes, and a natural-language
    summary of recent sessions. Designed for the project dashboard overview.
    """
    if not _valid_project_id(project_id):
        return _problem(400, f"Invalid project_id format: {project_id!r}")

    try:
        summary = await store.get_project_history_summary(project_id)
        return ProjectHistorySummaryResponse(**summary)
    except Exception:
        logger.error(
            "GET /api/projects/%s/history/summary failed",
            project_id,
            exc_info=True,
        )
        return _problem(
            500, "Failed to generate project history summary. Check server logs."
        )
