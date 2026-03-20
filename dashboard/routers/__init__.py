"""Shared helpers, Pydantic models, and utilities used by all router modules.

This module is the single source of truth for request/response models,
RFC 7807 error helpers, and OrchestratorManager lookup utilities.
Router modules import from here to avoid circular dependencies with api.py.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import state
from dashboard.events import event_bus

logger = logging.getLogger("dashboard.api")

# ---------------------------------------------------------------------------
# RFC 7807 Problem Detail
# ---------------------------------------------------------------------------

_HTTP_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    410: "Gone",
    413: "Content Too Large",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _problem(status: int, detail: str, headers: dict | None = None) -> JSONResponse:
    """Return an RFC 7807 Problem Detail JSONResponse."""
    return JSONResponse(
        {
            "type": "about:blank",
            "title": _HTTP_TITLES.get(status, "Error"),
            "status": status,
            "detail": detail,
        },
        status_code=status,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,126}[a-z0-9]$|^[a-z0-9]$")

from _shared_utils import valid_project_id as _valid_project_id


def _sanitize_client_ip(raw_ip: str) -> str:
    """Validate and sanitize an IP address string."""
    raw_ip = raw_ip.strip()
    if not raw_ip:
        return "unknown"
    try:
        return str(ipaddress.ip_address(raw_ip))
    except ValueError:
        return "invalid"


def _max_msg_len() -> int:
    """Lazy import to avoid circular import at module load time."""
    from config import MAX_USER_MESSAGE_LENGTH

    return MAX_USER_MESSAGE_LENGTH


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class MessageRequest(BaseModel):
    """Shared request model for any endpoint that accepts a user message."""

    message: str
    mode: str | None = None

    @field_validator("message")
    @classmethod
    def validate_message_length(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message cannot be empty")
        limit = _max_msg_len()
        if len(v) > limit:
            raise ValueError(f"message too long ({len(v)} chars). Maximum is {limit}.")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("autonomous", "interactive"):
            return None
        return v


# Backward-compatible aliases
SendMessageRequest = MessageRequest
TalkAgentRequest = MessageRequest


class NudgeRequest(BaseModel):
    """Request model for nudging a specific agent mid-run."""

    message: str
    priority: str = "normal"

    @field_validator("message")
    @classmethod
    def validate_nudge_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("nudge message cannot be empty")
        limit = _max_msg_len()
        if len(v) > limit:
            raise ValueError(f"nudge message too long ({len(v)} chars). Maximum is {limit}.")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in ("normal", "high"):
            return "normal"
        return v


class CreateProjectRequest(BaseModel):
    name: str = Field(max_length=200)
    directory: str = Field(max_length=1000)
    agents_count: int = Field(default=2, ge=1, le=20)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9 _\-\.]+$", v.strip()):
            raise ValueError(
                "Project name contains invalid characters. Use letters, numbers, spaces, hyphens, underscores or dots."
            )
        return v.strip()

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, v: str) -> str:
        if ".." in v:
            raise ValueError('Directory path must not contain ".." (path traversal not allowed).')
        return v


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    agents_count: int | None = Field(default=None, ge=1, le=20)


class UpdateSettingsRequest(BaseModel):
    max_turns_per_cycle: int | None = Field(default=None, ge=1, le=10000)
    max_budget_usd: float | None = Field(default=None, gt=0, le=10000)
    agent_timeout_seconds: int | None = Field(default=None, ge=30, le=7200)
    sdk_max_turns_per_query: int | None = Field(default=None, ge=1, le=10000)
    sdk_max_budget_per_query: float | None = Field(default=None, gt=0, le=10000)
    max_user_message_length: int | None = Field(default=None, ge=100, le=100000)
    max_orchestrator_loops: int | None = Field(default=None, ge=1, le=10000)


class SetBudgetRequest(BaseModel):
    """Request model for setting a per-project budget cap."""

    model_config = ConfigDict(strict=True)

    budget_usd: float = Field(
        gt=0, le=10_000, description="Budget cap in USD (0 < budget_usd ≤ 10,000)"
    )

    @field_validator("budget_usd", mode="before")
    @classmethod
    def budget_must_be_numeric_type(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("budget_usd must be a number, not a boolean")
        if not isinstance(v, int | float):
            raise ValueError(f"budget_usd must be a number, got {type(v).__name__!r}")
        return v

    @field_validator("budget_usd")
    @classmethod
    def budget_must_be_finite(cls, v: float) -> float:
        import math

        if not math.isfinite(v):
            raise ValueError("budget_usd must be a finite number (NaN and Inf are not allowed)")
        return round(v, 6)


class CreateScheduleRequest(BaseModel):
    project_id: str
    schedule_time: str
    task_description: str
    user_id: int = 0
    repeat: str = "once"

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        if not _PROJECT_ID_RE.match(v):
            raise ValueError("Invalid project_id format")
        return v

    @field_validator("repeat")
    @classmethod
    def validate_repeat(cls, v: str) -> str:
        if v not in ("once", "daily", "hourly"):
            raise ValueError("repeat must be once, daily, or hourly")
        return v

    @field_validator("task_description")
    @classmethod
    def validate_task_description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("task_description cannot be empty")
        if len(v) > 2000:
            raise ValueError("task_description too long (max 2000 chars)")
        return v


# ---------------------------------------------------------------------------
# Manager helpers
# ---------------------------------------------------------------------------


async def _find_manager(project_id: str):
    """Find an OrchestratorManager by project_id across all users."""
    if not _valid_project_id(project_id):
        return None, None
    return await state.get_manager_safe(project_id)


def _manager_status(manager) -> str:
    """Return the canonical status string for an OrchestratorManager."""
    if manager.is_running:
        return "running"
    if manager.is_paused:
        return "paused"
    return "idle"


def _db_event_to_dict(event: dict, project_id: str) -> dict:
    """Convert a DB activity-log row into the full event dict the frontend expects."""
    return {
        "type": event["event_type"],
        "project_id": project_id,
        "agent": event.get("agent", ""),
        "timestamp": event["timestamp"],
        "sequence_id": event["sequence_id"],
        **(event.get("data", {})),
    }


def _manager_to_dict(manager, project_id: str) -> dict:
    """Serialize an OrchestratorManager to a JSON-friendly dict."""
    last_message = None
    conv_log = manager.conversation_log
    if conv_log:
        last = conv_log[-1]
        last_message = {
            "agent_name": last.agent_name,
            "role": last.role,
            "content": last.content[:200] if last.content else "",
            "timestamp": last.timestamp,
            "input_tokens": last.input_tokens,
            "output_tokens": last.output_tokens,
            "total_tokens": last.total_tokens,
        }

    dag_progress = None
    dag_vision = None
    try:
        if hasattr(manager, "_current_dag_graph") and manager._current_dag_graph:
            graph_data = manager._current_dag_graph
            dag_vision = graph_data.get("vision", None)
            tasks = graph_data.get("tasks", []) or []
            total = len(tasks)
            if total > 0:
                statuses = getattr(manager, "_dag_task_statuses", {}) or {}
                completed = sum(1 for s in statuses.values() if s in ("completed", "skipped"))
                failed = sum(1 for s in statuses.values() if s == "failed")
                running = sum(1 for s in statuses.values() if s == "working")
                dag_progress = {
                    "total": total,
                    "completed": completed,
                    "failed": failed,
                    "running": running,
                    "percent": round(completed / total * 100) if total else 0,
                }
    except Exception:
        logger.debug("DAG progress extraction failed for %s", project_id, exc_info=True)

    diagnostics = None
    try:
        diagnostics = event_bus.get_diagnostics(project_id)
    except Exception:
        logger.debug("EventBus diagnostics unavailable for %s", project_id, exc_info=True)

    return {
        "project_id": project_id,
        "project_name": manager.project_name,
        "project_dir": manager.project_dir,
        "status": _manager_status(manager),
        "is_running": manager.is_running,
        "is_paused": manager.is_paused,
        "turn_count": manager.turn_count,
        "total_input_tokens": manager.total_input_tokens,
        "total_output_tokens": manager.total_output_tokens,
        "total_tokens": manager.total_tokens,
        "agents": manager.agent_names,
        "multi_agent": manager.is_multi_agent,
        "last_message": last_message,
        "agent_states": manager.agent_states,
        "current_agent": manager.current_agent,
        "current_tool": manager.current_tool,
        "pending_messages": manager.pending_message_count,
        "pending_approval": manager.pending_approval,
        "diagnostics": diagnostics,
        "dag_progress": dag_progress,
        "dag_vision": dag_vision,
    }


def _create_web_manager(
    project_id: str,
    project_name: str,
    project_dir: str,
    user_id: int,
    agents_count: int = 2,
):
    """Create an OrchestratorManager with web-only callbacks (EventBus)."""
    sdk = state.sdk_client
    smgr = state.session_mgr

    if not sdk or not smgr:
        return None

    multi_agent = agents_count >= 2

    async def on_update(text: str):
        await event_bus.publish(
            {
                "type": "agent_update",
                "project_id": project_id,
                "project_name": project_name,
                "agent": manager.current_agent or "orchestrator",
                "text": text,
                "timestamp": time.time(),
            }
        )

    async def on_result(text: str):
        await event_bus.publish(
            {
                "type": "agent_result",
                "project_id": project_id,
                "project_name": project_name,
                "text": text,
                "timestamp": time.time(),
            }
        )

    async def on_final(text: str):
        await event_bus.publish(
            {
                "type": "agent_final",
                "project_id": project_id,
                "project_name": project_name,
                "text": text,
                "timestamp": time.time(),
            }
        )

    async def on_event(event: dict):
        event["project_id"] = project_id
        event["project_name"] = project_name
        await event_bus.publish(event)

    from orchestrator import OrchestratorManager

    manager = OrchestratorManager(
        project_name=project_name,
        project_dir=project_dir,
        sdk=sdk,
        session_mgr=smgr,
        user_id=user_id,
        project_id=project_id,
        on_update=on_update,
        on_result=on_result,
        on_final=on_final,
        on_event=on_event,
        multi_agent=multi_agent,
    )
    return manager


async def _resolve_project_dir(project_id: str) -> str | None:
    """Resolve project directory from active manager or DB."""
    if not _valid_project_id(project_id):
        return None
    manager, _ = await _find_manager(project_id)
    if manager:
        return manager.project_dir
    if state.session_mgr:
        db_project = await state.session_mgr.load_project(project_id)
        if db_project:
            return db_project.get("project_dir", "")
    return None


# Per-project locks to prevent duplicate manager creation under concurrent requests
_manager_creation_locks: dict[str, asyncio.Lock] = {}
_manager_creation_locks_lock = asyncio.Lock()


async def _get_or_create_manager_lock(project_id: str) -> asyncio.Lock:
    async with _manager_creation_locks_lock:
        if project_id not in _manager_creation_locks:
            _manager_creation_locks[project_id] = asyncio.Lock()
        return _manager_creation_locks[project_id]


# Shared DeviceAuthManager singleton — used by auth router and api.py middleware.
# DeviceAuthManager.__new__ enforces singleton pattern, so this is safe to import.
def _get_device_auth():
    """Lazy accessor to avoid import-time side effects."""
    from device_auth import DeviceAuthManager

    return DeviceAuthManager()
