"""FastAPI dashboard backend — REST endpoints + WebSocket for the agent dashboard."""

from __future__ import annotations

import asyncio
import collections
import html
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import state
from dashboard.events import event_bus

logger = logging.getLogger(__name__)

# RFC 7807 Problem Detail status → title map (reused by _problem helper)
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
    """Return an RFC 7807 Problem Detail JSONResponse from a route handler.

    All route handlers must return a consistent, structured error schema.
    Using this helper ensures every error path produces the same JSON shape:

        {"type": "about:blank", "title": "...", "status": NNN, "detail": "..."}
    """
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


# Valid project_id: lowercase letters, digits, hyphens — max 128 chars
_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,126}[a-z0-9]$|^[a-z0-9]$")


from _shared_utils import valid_project_id as _valid_project_id


def _sanitize_client_ip(raw_ip: str) -> str:
    """Validate and sanitize an IP address string.

    Returns the normalized IP if valid, or 'invalid' if the format is bad.
    Prevents attackers from using arbitrary strings in X-Forwarded-For
    to bypass rate limiting or pollute logs.
    """
    raw_ip = raw_ip.strip()
    if not raw_ip:
        return "unknown"
    try:
        # ipaddress.ip_address() validates both IPv4 and IPv6
        return str(ipaddress.ip_address(raw_ip))
    except ValueError:
        return "invalid"


def _max_msg_len() -> int:
    """Lazy import to avoid circular import at module load time."""
    from config import MAX_USER_MESSAGE_LENGTH

    return MAX_USER_MESSAGE_LENGTH


class MessageRequest(BaseModel):
    """Shared request model for any endpoint that accepts a user message.

    Validates that the message is non-empty and within
    ``config.MAX_USER_MESSAGE_LENGTH`` characters.
    """

    message: str
    mode: str | None = None

    @field_validator("message")
    @classmethod
    def validate_message_length(cls, v: str) -> str:
        """Ensure the message does not exceed the maximum allowed length."""
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
        """Ensure the mode is one of the supported orchestration modes."""
        if v is not None and v not in ("autonomous", "interactive"):
            return None
        return v


class NudgeRequest(BaseModel):
    """Request model for nudging a specific agent mid-run.

    Unlike talk (which queues a message for after the current task),
    nudge injects guidance into the agent's context WITHOUT stopping
    other agents that are running in parallel.
    """

    message: str
    priority: str = "normal"  # 'normal' or 'high'

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


# Backward-compatible aliases — keep import sites working without changes
SendMessageRequest = MessageRequest
TalkAgentRequest = MessageRequest


class CreateProjectRequest(BaseModel):
    name: str = Field(max_length=200)
    directory: str = Field(max_length=1000)
    agents_count: int = Field(default=2, ge=1, le=20)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Ensure the project name is non-empty and within length limits."""
        if not re.match(r"^[a-zA-Z0-9 _\-\.]+$", v.strip()):
            raise ValueError(
                "Project name contains invalid characters. Use letters, numbers, spaces, hyphens, underscores or dots."
            )
        return v.strip()

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, v: str) -> str:
        """Ensure the directory path is absolute and exists on disk."""
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
    """Request model for setting a per-project budget cap.

    Validates that the budget is a finite positive number within allowed limits.
    Uses Pydantic v2 strict mode + a before-validator to reject:
    - Boolean values (True/False), since bool is a subclass of int in Python
    - Non-numeric types (strings, lists, etc.)
    This prevents unbounded-payload DoS from exotic numeric representations
    while staying compatible with valid integer and float inputs.
    """

    # strict=True rejects implicit str→float coercion and bool→float coercion.
    model_config = ConfigDict(strict=True)

    budget_usd: float = Field(
        gt=0, le=10_000, description="Budget cap in USD (0 < budget_usd ≤ 10,000)"
    )

    @field_validator("budget_usd", mode="before")
    @classmethod
    def budget_must_be_numeric_type(cls, v: object) -> object:
        """Reject boolean inputs before Pydantic's own coercion runs.

        bool is a subclass of int in Python so isinstance(v, (int, float)) alone
        would accept True/False; the explicit bool check prevents that footgun.
        """
        if isinstance(v, bool):
            raise ValueError("budget_usd must be a number, not a boolean")
        if not isinstance(v, int | float):
            raise ValueError(f"budget_usd must be a number, got {type(v).__name__!r}")
        return v

    @field_validator("budget_usd")
    @classmethod
    def budget_must_be_finite(cls, v: float) -> float:
        """Ensure the budget value is a finite positive number."""
        import math

        if not math.isfinite(v):
            raise ValueError("budget_usd must be a finite number (NaN and Inf are not allowed)")
        return round(v, 6)  # Normalise to at most 6 decimal places


class CreateScheduleRequest(BaseModel):
    project_id: str
    schedule_time: str
    task_description: str
    user_id: int = 0
    repeat: str = "once"

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        """Ensure the project ID matches the allowed format."""
        if not _PROJECT_ID_RE.match(v):
            raise ValueError("Invalid project_id format")
        return v

    @field_validator("repeat")
    @classmethod
    def validate_repeat(cls, v: str) -> str:
        """Ensure the repeat pattern is a valid cron expression."""
        if v not in ("once", "daily", "hourly"):
            raise ValueError("repeat must be once, daily, or hourly")
        return v

    @field_validator("task_description")
    @classmethod
    def validate_task_description(cls, v: str) -> str:
        """Ensure the task description is non-empty."""
        v = v.strip()
        if not v:
            raise ValueError("task_description cannot be empty")
        if len(v) > 2000:
            raise ValueError("task_description too long (max 2000 chars)")
        return v


# --- Helpers using state module ---


async def _find_manager(project_id: str):
    """Find an OrchestratorManager by project_id across all users.

    Uses get_manager_safe() which acquires _state_lock to prevent reading
    a manager that is being torn down concurrently (BUG-01 fix).

    Returns (None, None) for invalid project_id formats to prevent injection.
    """
    if not _valid_project_id(project_id):
        return None, None
    return await state.get_manager_safe(project_id)


def _manager_status(manager) -> str:
    """Return the canonical status string for an OrchestratorManager.

    Centralises the ``running / paused / idle`` logic so it is defined in
    exactly one place.
    """
    if manager.is_running:
        return "running"
    if manager.is_paused:
        return "paused"
    return "idle"


def _db_event_to_dict(event: dict, project_id: str) -> dict:
    """Convert a DB activity-log row into the full event dict the frontend expects.

    Avoids duplicating the reconstruction logic in multiple places.
    """
    return {
        "type": event["event_type"],
        "project_id": project_id,
        "agent": event.get("agent", ""),
        "timestamp": event["timestamp"],
        "sequence_id": event["sequence_id"],
        **(event.get("data", {})),
    }


def _manager_to_dict(manager, project_id: str) -> dict:
    """Serialize an OrchestratorManager to a JSON-friendly dict.

    Accesses only the last conversation entry to avoid copying the full log.
    """
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

    # Compute DAG progress if available
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
                running = sum(1 for s in statuses.values() if s == "running")
                dag_progress = {
                    "total": total,
                    "completed": completed,
                    "failed": failed,
                    "running": running,
                    "percent": round(completed / total * 100) if total else 0,
                }
    except Exception:
        logger.debug("DAG progress extraction failed for %s", project_id, exc_info=True)

    # Get diagnostics from EventBus
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
        # Live agent states — survives browser refresh
        "agent_states": manager.agent_states,
        "current_agent": manager.current_agent,
        "current_tool": manager.current_tool,
        # Queue status — so frontend knows about pending messages
        "pending_messages": manager.pending_message_count,
        "pending_approval": manager.pending_approval,
        # Project health & progress
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
        """Handle a progress-update callback from the orchestrator."""
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
        """Handle a result callback from the orchestrator."""
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
        """Handle the final-summary callback from the orchestrator."""
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
        """Forward orchestrator events to the EventBus with project_id attached."""
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


# --- App factory ---


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


def create_app() -> FastAPI:
    """Create and configure the FastAPI dashboard application."""
    from src.api.history import history_router
    from src.api.projects import projects_router
    from src.api.tasks import admin_tasks_router, tasks_router
    from src.api.websocket_handler import (
        get_or_create_conversation_id,
        invalidate_conversation_cache,
        load_history_on_connect,
        start_new_conversation,
    )
    from src.db.database import init_db
    from src.workers.task_queue import TaskQueueRegistry
    from src.workers.task_worker import process_message_task

    app = FastAPI(title="Agent Dashboard", docs_url="/api/docs")

    # --- Platform DB initialisation: runs once when the server starts ---
    @app.on_event("startup")
    async def _init_platform_db():
        """Ensure platform DB tables exist (idempotent — safe if already created by Alembic)."""
        try:
            await init_db()
            logger.info("Platform DB initialised (tables ready)")
        except Exception as _db_init_err:
            logger.warning(
                "Platform DB init_db() failed — use 'alembic upgrade head' in production: %s",
                _db_init_err,
            )

    @app.on_event("shutdown")
    async def _stop_task_queues():
        """Gracefully stop all per-project task queues on server shutdown."""
        try:
            await TaskQueueRegistry.get_registry().stop_all()
            logger.info("Task queues stopped cleanly")
        except Exception as _tq_err:
            logger.warning("Error stopping task queues on shutdown: %s", _tq_err)

    # --- Pydantic validation errors → 400 with clean error message ---
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Convert Pydantic validation errors from 422 to 400 with a user-friendly message.

        FastAPI's default returns 422 with verbose detail, but our API convention
        uses 400 + ``{"error": "..."}`` for all client errors.
        """
        messages: list[str] = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error.get("loc", []) if loc != "body")
            msg = error.get("msg", "Validation error")
            messages.append(f"{field}: {msg}" if field else msg)
        detail = "; ".join(messages) if messages else "Validation error"
        request_id = getattr(request.state, "request_id", "")
        logger.warning(
            "[%s] Validation error on %s %s: %s",
            request_id,
            request.method,
            request.url.path,
            detail,
        )
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Bad Request",
                "status": 400,
                "detail": detail,
            },
            status_code=400,
        )

    # --- RFC 7807 Problem Detail: HTTPException handler ---
    from fastapi import HTTPException as _HTTPException

    @app.exception_handler(_HTTPException)
    async def http_exception_handler(request: Request, exc: _HTTPException):
        """Return RFC 7807 Problem Detail JSON for all HTTPExceptions.

        Maps FastAPI/Starlette HTTPException to the structured problem-detail
        format so all error responses are machine-parseable and consistent.
        """
        request_id = getattr(request.state, "request_id", "")
        status = exc.status_code
        title = {
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
        }.get(status, "HTTP Error")
        detail = str(exc.detail) if exc.detail else title
        logger.warning(
            "[%s] HTTP %d on %s %s: %s",
            request_id,
            status,
            request.method,
            request.url.path,
            detail,
        )
        return JSONResponse(
            {
                "type": "about:blank",
                "title": title,
                "status": status,
                "detail": detail,
            },
            status_code=status,
            headers=getattr(exc, "headers", None) or {},
        )

    # --- RFC 7807 Problem Detail: catch-all for unhandled exceptions ---
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """Return RFC 7807 Problem Detail JSON for any unhandled server error.

        Prevents raw tracebacks from leaking to clients while giving operators
        a structured log entry with request_id for correlation.
        """
        request_id = getattr(request.state, "request_id", "")
        logger.error(
            "[%s] Unhandled exception on %s %s: %s",
            request_id,
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "An unexpected server error occurred.",
                "instance": f"urn:request:{request_id}" if request_id else None,
            },
            status_code=500,
        )

    # Per-project locks to prevent duplicate manager creation under concurrent requests
    _manager_creation_locks: dict[str, asyncio.Lock] = {}
    _manager_creation_locks_lock = asyncio.Lock()

    async def _get_or_create_manager_lock(project_id: str) -> asyncio.Lock:
        async with _manager_creation_locks_lock:
            if project_id not in _manager_creation_locks:
                _manager_creation_locks[project_id] = asyncio.Lock()
            return _manager_creation_locks[project_id]

    # CORS — configurable via CORS_ORIGINS env var
    from config import (
        AUTH_ENABLED,
        CONVERSATION_LOG_MAXLEN,
        CORS_ORIGINS,
        DASHBOARD_API_KEY,
        DEVICE_AUTH_ENABLED,
        GIT_DIFF_TIMEOUT,
        WS_AUTH_TIMEOUT,
    )
    from config import DASHBOARD_HOST as _CFG_HOST

    dashboard_host = _CFG_HOST
    if "*" in CORS_ORIGINS:
        logger.warning(
            "CORS is configured with wildcard origin (*). "
            "Set CORS_ORIGINS env var to restrict access in production."
        )
    # F-01: Warn loudly when binding on non-localhost without authentication.
    # The hard failure lives in validate_config() (config.py) which runs at
    # server startup and raises ConfigError.  Here we log a CRITICAL warning
    # Personal local tool — auth disabled. Sandboxing enforced at project-directory level.
    _is_localhost = dashboard_host in ("127.0.0.1", "localhost", "::1")
    if not _is_localhost and not AUTH_ENABLED:
        logger.info("Auth disabled — running as personal local tool.")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID", "Authorization"],
    )

    # --- Security headers middleware ---
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Inject security headers (CSP, HSTS, X-Frame-Options) into responses."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        # Content-Security-Policy — applied to all non-static responses
        # Allows: self for scripts/styles, Google Fonts, data URIs for images
        # Blocks: inline scripts/styles, eval(), object/embed elements, frames
        if not request.url.path.startswith("/assets/"):
            response.headers.setdefault(
                "Content-Security-Policy",
                (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline'; "  # React needs inline for dev mode
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                    "font-src 'self' https://fonts.gstatic.com data:; "
                    "img-src 'self' data: blob:; "
                    "connect-src 'self' ws: wss:; "  # WebSocket support
                    "frame-ancestors 'none'; "
                    "object-src 'none'; "
                    "base-uri 'self';"
                ),
            )
        return response

    # --- Device Token Authentication ---
    from device_auth import COOKIE_NAME, DeviceAuthManager

    _device_auth = DeviceAuthManager()

    # Public paths that don't require authentication
    _AUTH_EXEMPT_PATHS = {
        "/api/health",
        "/api/ready",
        "/api/stats",
        "/api/auth/verify",
        "/api/auth/status",
        "/api/agent-registry",  # Read-only public metadata (icons, labels, colors)
    }

    @app.middleware("http")
    async def device_auth_middleware(request: Request, call_next):
        """Enforce device-token authentication on incoming requests."""
        # Skip device auth entirely when disabled (e.g. testing, CI)
        if not DEVICE_AUTH_ENABLED:
            return await call_next(request)

        path = request.url.path

        # Static assets and health checks are always public
        # Only /api/auth/verify and /api/auth/status are exempt (login flow)
        # Other /api/auth/* endpoints (devices, rotate-code) require auth
        if not path.startswith("/api/") or path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Check device token from cookie or header
        token = request.cookies.get(COOKIE_NAME, "")
        if not token:
            token = request.headers.get("X-Device-Token", "")
        if not token:
            # Legacy: also check X-API-Key header for backward compatibility
            token = request.headers.get("X-API-Key", "")

        if token and _device_auth.verify_device_token(token):
            return await call_next(request)

        return _problem(401, "Device not authorized. Please enter the access code.")

    # --- Auth API endpoints ---
    @app.post("/api/auth/verify")
    async def verify_access_code(request: Request):
        """Verify an access code and return a device token."""
        if not DEVICE_AUTH_ENABLED:
            return _problem(400, "Device authentication is disabled")

        body = await request.json()
        code = body.get("code", "").strip()
        if not code:
            return _problem(400, "Access code is required")

        ip = request.client.host if request.client else "unknown"
        ua = request.headers.get("user-agent", "")

        password = body.get("password", "").strip()
        device_token = _device_auth.verify_access_code(code, ip, ua, password=password)
        if device_token is None:
            return _problem(401, "Invalid access code or too many attempts")

        from starlette.responses import JSONResponse

        response = JSONResponse(
            {
                "ok": True,
                "message": "Device approved",
                "device_token": device_token,
            }
        )
        # Set permanent cookie
        response.set_cookie(
            key=COOKIE_NAME,
            value=device_token,
            max_age=365 * 24 * 3600,  # 1 year
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return response

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        """Check if the current device is authenticated.

        When device auth is disabled (DEVICE_AUTH_ENABLED=false), every
        request is considered authenticated — the login screen is skipped.
        """
        if not DEVICE_AUTH_ENABLED:
            return {"authenticated": True, "password_required": False}

        token = request.cookies.get(COOKIE_NAME, "")
        if not token:
            token = request.headers.get("X-Device-Token", "")
        is_authenticated = bool(token and _device_auth.verify_device_token(token))
        return {
            "authenticated": is_authenticated,
            "password_required": bool(os.getenv("HIVEMIND_PASSWORD", "")),
        }

    @app.get("/api/auth/devices")
    async def list_devices(request: Request):
        """List all approved devices (requires auth)."""
        if not DEVICE_AUTH_ENABLED:
            return {"devices": []}
        return {"devices": _device_auth.list_devices()}

    @app.delete("/api/auth/devices/{device_id}")
    async def revoke_device(device_id: str, request: Request):
        """Revoke an approved device."""
        if not DEVICE_AUTH_ENABLED:
            return _problem(400, "Device authentication is disabled")
        if _device_auth.revoke_device(device_id):
            return {"ok": True, "message": "Device revoked"}
        return _problem(404, "Device not found")

    @app.post("/api/auth/rotate-code")
    async def rotate_code(request: Request):
        """Force-rotate the access code."""
        if not DEVICE_AUTH_ENABLED:
            return _problem(400, "Device authentication is disabled")
        _device_auth.force_rotate_code()
        # Only log it, don't return it (security: code is only shown in terminal)
        _device_auth.print_access_code()
        return {"ok": True, "message": "Access code rotated. Check the terminal."}

    # --- Request body size limit ---
    from config import MAX_REQUEST_BODY_SIZE

    _MAX_BODY_SIZE = MAX_REQUEST_BODY_SIZE

    @app.middleware("http")
    async def body_size_limit(request: Request, call_next):
        """Reject requests with oversized bodies to prevent memory exhaustion.

        F-08: Handles both Content-Length-based and chunked transfer encoding.
        When Content-Length is present, we do a fast header check.
        When it's absent (chunked encoding), we read the body incrementally
        and reject if it exceeds the limit, then reassemble the body for
        downstream handlers via a wrapper.
        """
        content_length = request.headers.get("content-length")
        if content_length:
            # Fast path: Content-Length header is present
            try:
                cl = int(content_length)
            except (ValueError, TypeError):
                return _problem(400, "Invalid Content-Length header.")
            if cl > _MAX_BODY_SIZE:
                return _problem(
                    413,
                    f"Request body too large. Maximum is {_MAX_BODY_SIZE // 1024}KB.",
                )
        elif request.method in ("POST", "PUT", "PATCH"):
            # F-08: Chunked transfer encoding — no Content-Length header.
            # We must read the body to enforce the size limit, then make it
            # available to downstream handlers via receive() override.
            body = b""
            async for chunk in request.stream():
                if len(body) + len(chunk) > _MAX_BODY_SIZE:
                    return _problem(
                        413,
                        f"Request body too large. Maximum is {_MAX_BODY_SIZE // 1024}KB.",
                    )
                body += chunk

            # Reassemble the body so downstream handlers can read it.
            # We override the receive() callable to return the buffered body.
            async def _receive():
                return {"type": "http.request", "body": body}

            request._receive = _receive

        return await call_next(request)

    # --- Rate limiting middleware ---
    # Simple in-memory rate limiter per IP address with TTL-based cleanup
    _rate_limit_store: dict[str, list[float]] = {}  # ip -> list of timestamps
    _RATE_LIMIT_WINDOW = 60  # seconds
    _RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "300"))  # per window
    _RATE_LIMIT_BURST = int(
        os.getenv("RATE_LIMIT_BURST", "100")
    )  # max burst in 5s — page load fires ~20 concurrent requests
    _RATE_LIMIT_EXEMPT = {
        "/api/health",
        "/api/ready",
        "/api/stats",
        "/api/agent-registry",  # Public metadata, called on every page load
        "/api/projects",  # Dashboard polls this frequently
        "/health",
    }  # endpoints exempt from rate limiting
    _RATE_LIMIT_REQUEST_COUNT = 0  # track requests for periodic cleanup
    _RATE_LIMIT_CLEANUP_INTERVAL = 500  # run cleanup every N requests
    _RATE_LIMIT_MAX_STORE_SIZE = 500  # force cleanup when store exceeds this many entries
    _RATE_LIMIT_TTL_MULTIPLIER = 3  # evict IPs idle for window × this multiplier

    def _rate_limit_cleanup(now: float) -> None:
        """Evict stale IPs whose last request is older than 3× the rate limit window.

        Called every 500 requests or when the store exceeds 500 entries.
        Guarantees the store never exceeds ~1000 entries in normal operation.
        """
        ttl = _RATE_LIMIT_WINDOW * _RATE_LIMIT_TTL_MULTIPLIER
        stale_ips = [ip for ip, ts in _rate_limit_store.items() if not ts or now - ts[-1] > ttl]
        for ip in stale_ips:
            del _rate_limit_store[ip]
        if stale_ips:
            logger.debug(
                "Rate limiter cleanup: evicted %d stale IPs, %d remaining",
                len(stale_ips),
                len(_rate_limit_store),
            )

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """Per-IP rate limiting with sliding window and burst protection.

        DELETE requests get a higher burst tolerance to prevent delete cascades
        (which trigger page reloads and many follow-up GETs) from being blocked.
        Auth-exempt paths are also rate-limit exempt to prevent login/health
        checks from consuming the rate-limit budget.
        """
        nonlocal _RATE_LIMIT_REQUEST_COUNT

        # Skip non-API routes and exempt endpoints
        path = request.url.path
        if not path.startswith("/api/") or path in _RATE_LIMIT_EXEMPT:
            return await call_next(request)

        # Also exempt auth-exempt paths from rate limiting — they should never
        # consume the user's rate-limit budget (login flow, status checks).
        if path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Get client IP (support X-Forwarded-For for reverse proxy)
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            # Take the first (client) IP, validate its format
            raw_ip = forwarded_for.split(",")[0].strip()
            client_ip = _sanitize_client_ip(raw_ip)
        else:
            client_ip = request.client.host if request.client else "unknown"

        now = time.time()
        timestamps = _rate_limit_store.get(client_ip, [])

        # Clean old timestamps outside the window
        timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]

        # Check window limit
        if len(timestamps) >= _RATE_LIMIT_MAX_REQUESTS:
            logger.warning(
                "Rate limit exceeded for %s: %d requests in %ds (method=%s path=%s)",
                client_ip, len(timestamps), _RATE_LIMIT_WINDOW,
                request.method, path,
            )
            return _problem(
                429,
                "Rate limit exceeded. Please slow down.",
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )

        # Check burst limit (last 5 seconds)
        # DELETE and its follow-up page-reload GETs get 2× burst tolerance
        # to prevent delete cascades from triggering 429s.
        recent_burst = sum(1 for t in timestamps if now - t < 5)
        burst_limit = _RATE_LIMIT_BURST
        if request.method == "DELETE":
            burst_limit = _RATE_LIMIT_BURST * 2
        if recent_burst >= burst_limit:
            logger.warning(
                "Burst limit exceeded for %s: %d requests in 5s (method=%s path=%s)",
                client_ip, recent_burst, request.method, path,
            )
            return _problem(
                429,
                "Too many requests in a short time. Please wait a moment.",
                headers={"Retry-After": "5"},
            )

        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps

        # Increment request counter
        _RATE_LIMIT_REQUEST_COUNT += 1

        # TTL-based cleanup: every 500 requests or when store exceeds 500 entries
        if (
            _RATE_LIMIT_REQUEST_COUNT % _RATE_LIMIT_CLEANUP_INTERVAL == 0
            or len(_rate_limit_store) > _RATE_LIMIT_MAX_STORE_SIZE
        ):
            _rate_limit_cleanup(now)

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, _RATE_LIMIT_MAX_REQUESTS - len(timestamps))
        )
        return response

    # --- Request ID + logging middleware for tracing ---
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        """Attach a unique request_id and log method/path/duration for every API request.

        Also sets the ``current_request_id`` ContextVar so that any EventBus events
        published during this request automatically carry the same request_id for
        end-to-end traceability.
        """
        from dashboard.events import current_request_id as _req_id_var

        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        # Propagate into async context so EventBus.publish() can pick it up
        token = _req_id_var.set(request_id)
        start = time.time()
        try:
            response = await call_next(request)
        finally:
            _req_id_var.reset(token)
        duration_ms = (time.time() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        # Log API requests (skip static assets for cleaner logs)
        if request.url.path.startswith("/api/"):
            # Use debug level for 401s to avoid flooding logs before login
            log_fn = logger.debug if response.status_code == 401 else logger.info
            log_fn(
                "[%s] %s %s → %d (%.0fms)",
                request_id,
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
        return response

    # --- Liveness probe (always 200) ---

    @app.get("/health")
    async def liveness():
        """Kubernetes-style liveness probe — always returns 200 {"status": "ok"}.

        This endpoint has no external dependencies — it just confirms the
        process is alive and the HTTP server is responding. Load balancers,
        container orchestrators, and monitoring tools should use this for
        liveness checks. It is exempt from authentication and rate-limiting.

        Returns:
            200 {"status": "ok"} — always, unconditionally.
        """
        return {"status": "ok"}

    # --- Readiness probe (503 until DB is up) ---

    @app.get("/api/ready")
    async def readiness():
        """Kubernetes-style readiness probe — 200 when fully initialised, 503 otherwise.

        Unlike /health (which always succeeds), this endpoint verifies that the
        application is truly ready to handle requests by checking the database
        connection. Use this for readiness probes in orchestrators that support
        traffic-routing based on readiness.

        Returns:
            200 {"status": "ok"}   — app is ready.
            503 {"status": "..."}  — DB not yet initialised or unhealthy.
        """
        if state.session_mgr is None:
            return JSONResponse(
                {"status": "starting", "reason": "database not initialised"},
                status_code=503,
            )
        try:
            healthy = await state.session_mgr.is_healthy()
            if not healthy:
                return JSONResponse(
                    {"status": "not_ready", "reason": "database unhealthy"},
                    status_code=503,
                )
        except Exception as _ready_err:
            logger.error("Readiness probe: DB health check failed: %s", _ready_err, exc_info=True)
            return JSONResponse(
                {"status": "not_ready", "reason": "database error"},
                status_code=503,
            )
        return {"status": "ok"}

    # --- Health check ---

    @app.get("/api/health")
    async def health_check():
        """Enhanced health check — DB, CLI binary, disk space, active sessions, uptime, and memory."""
        import platform as _platform
        import shutil as _shutil
        import time as _time

        from config import CLAUDE_CLI_PATH, STORE_DIR

        # DB connectivity check
        db_status = "error"
        if state.session_mgr is not None:
            try:
                db_status = "ok" if await state.session_mgr.is_healthy() else "error"
            except Exception as _db_err:
                logger.error("Health check: DB health probe failed: %s", _db_err, exc_info=True)
                db_status = "error"

        # Claude CLI binary existence check
        cli_path = CLAUDE_CLI_PATH
        if os.sep not in cli_path and "/" not in cli_path:
            cli_status = "ok" if _shutil.which(cli_path) else "missing"
        else:
            cli_status = "ok" if os.path.isfile(cli_path) else "missing"

        # Disk space check on the data directory
        try:
            usage = _shutil.disk_usage(str(STORE_DIR))
            disk_free_gb = round(usage.free / (1024**3), 2)
            disk_total_gb = round(usage.total / (1024**3), 2)
            disk_pct_used = round((usage.used / usage.total) * 100, 1)
        except Exception as _disk_err:
            logger.warning("Health check: disk usage probe failed: %s", _disk_err)
            disk_free_gb = -1.0
            disk_total_gb = -1.0
            disk_pct_used = -1.0

        # Memory usage (optional — psutil not always available)
        memory_info: dict = {}
        try:
            import psutil as _psutil

            proc = _psutil.Process()
            mem = proc.memory_info()
            memory_info = {
                "rss_mb": round(mem.rss / (1024**2), 1),
                "vms_mb": round(mem.vms / (1024**2), 1),
            }
        except ImportError:
            pass  # psutil not installed — skip memory info
        except Exception:
            logger.debug("Memory info collection failed", exc_info=True)

        # Active sessions count
        active_count = sum(len(sessions) for sessions in state.active_sessions.values())

        # Server uptime (if start time is tracked in state)
        uptime_seconds: float | None = None
        server_start = getattr(state, "server_start_time", None)
        if server_start is not None:
            uptime_seconds = round(_time.monotonic() - server_start, 1)

        overall = "ok" if db_status == "ok" and cli_status == "ok" else "degraded"
        if disk_free_gb > 0 and disk_free_gb < 0.5:
            overall = "degraded"  # Less than 500MB free is a warning

        return {
            "status": overall,
            "db": db_status,
            "cli": cli_status,
            "disk_free_gb": disk_free_gb,
            "disk_total_gb": disk_total_gb,
            "disk_pct_used": disk_pct_used,
            "active_sessions": active_count,
            "uptime_seconds": uptime_seconds,
            "python_version": _platform.python_version(),
            "platform": _platform.system(),
            **memory_info,
        }

    # --- REST Endpoints ---

    @app.get("/api/projects")
    async def list_projects():
        """List all projects with live status from active_sessions + DB."""
        # Load deleted project IDs so we never return them
        _del_file = Path("data/deleted_projects.json")
        deleted_ids: set[str] = set()
        try:
            if _del_file.exists():
                deleted_ids = set(json.loads(_del_file.read_text()))
        except Exception:
            pass

        active_managers = state.get_all_managers()

        # Build map of active projects (exclude deleted)
        active_map = {}
        for user_id, project_id, manager in active_managers:
            if project_id in deleted_ids:
                continue
            active_map[project_id] = _manager_to_dict(manager, project_id)
            active_map[project_id]["user_id"] = user_id

        # Get all projects from DB
        db_projects = await state.session_mgr.list_projects() if state.session_mgr else []

        # Build a dict keyed by project_id for O(1) lookup instead of O(n) scan
        db_project_map = {dbp["project_id"]: dbp for dbp in db_projects}

        projects = []
        seen = set()

        # Active projects first
        for project_id, data in active_map.items():
            seen.add(project_id)
            # Enrich with DB info — O(1) dict lookup
            dbp = db_project_map.get(project_id)
            if dbp:
                data["description"] = dbp.get("description", "")
                data["created_at"] = dbp.get("created_at", 0)
                data["updated_at"] = dbp.get("updated_at", 0)
                data["message_count"] = dbp.get("message_count", 0)
            projects.append(data)

        # DB-only projects (not currently active)
        from config import DEFAULT_AGENTS

        default_agent_names = [a["name"] for a in DEFAULT_AGENTS]
        for dbp in db_projects:
            pid = dbp["project_id"]
            if pid not in seen and pid not in deleted_ids:
                projects.append(
                    {
                        "project_id": pid,
                        "project_name": dbp["name"],
                        "project_dir": dbp.get("project_dir", ""),
                        "status": "idle",
                        "is_running": False,
                        "is_paused": False,
                        "turn_count": 0,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_tokens": 0,
                        "agents": default_agent_names,
                        "multi_agent": len(default_agent_names) > 1,
                        "last_message": None,
                        "user_id": dbp.get("user_id") or 0,
                        "description": dbp.get("description", ""),
                        "created_at": dbp.get("created_at", 0),
                        "updated_at": dbp.get("updated_at", 0),
                        "message_count": dbp.get("message_count", 0),
                    }
                )

        return {"projects": projects}

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str):
        """Project detail: live agent states, config."""
        manager, user_id = await _find_manager(project_id)

        if manager:
            data = _manager_to_dict(manager, project_id)
            data["user_id"] = user_id
            data["conversation_log"] = [
                {
                    "agent_name": m.agent_name,
                    "role": m.role,
                    "content": m.content[:500],
                    "timestamp": m.timestamp,
                    "input_tokens": m.input_tokens,
                    "output_tokens": m.output_tokens,
                    "total_tokens": m.total_tokens,
                }
                for m in list(manager.conversation_log)[-50:]
            ]
        else:
            if not state.session_mgr:
                return _problem(503, "Not initialized")
            db_project = await state.session_mgr.load_project(project_id)
            if not db_project:
                return _problem(404, "Project not found")

            # Load recent messages from DB so they show on refresh
            recent_msgs = await state.session_mgr.get_recent_messages(project_id, count=20)
            last_msg = recent_msgs[-1] if recent_msgs else None
            # Load saved orchestrator state for cost/turn info
            saved_orch = await state.session_mgr.load_orchestrator_state(project_id)
            # Count total messages — use a large limit to get accurate count
            # (limit=0 is ambiguous: some implementations return 0 rows)
            _, total_msgs = await state.session_mgr.get_messages_paginated(
                project_id, limit=1_000_000, offset=0
            )

            from config import DEFAULT_AGENTS

            default_agent_names = [a["name"] for a in DEFAULT_AGENTS]
            data = {
                "project_id": project_id,
                "project_name": db_project["name"],
                "project_dir": db_project.get("project_dir", ""),
                "status": saved_orch.get("status", "idle") if saved_orch else "idle",
                "is_running": False,
                "is_paused": False,
                "turn_count": saved_orch.get("turn_count", 0) if saved_orch else 0,
                "total_input_tokens": saved_orch.get("total_input_tokens", 0) if saved_orch else 0,
                "total_output_tokens": saved_orch.get("total_output_tokens", 0) if saved_orch else 0,
                "total_tokens": saved_orch.get("total_tokens", 0) if saved_orch else 0,
                "agents": default_agent_names,
                "multi_agent": len(default_agent_names) > 1,
                "last_message": last_msg,
                "user_id": db_project.get("user_id") or 0,
                "conversation_log": recent_msgs,
                "description": db_project.get("description", ""),
                "message_count": total_msgs,
            }

        return data

    @app.get("/api/projects/{project_id}/live")
    async def get_live_state(project_id: str):
        """Full live state snapshot — designed for recovery after browser refresh.

        Returns everything the frontend needs to restore its UI:
        - Agent states (who's working on what)
        - Loop progress (current turn, cost, budget)
        - Shared context summary
        - Pending messages in queue
        - Pending approval
        - Diagnostics: health_score, warnings_count, last_stuckness, seconds_since_progress

        Falls back to DB-persisted orchestrator_state when no in-memory manager exists.
        """
        # Always compute diagnostics from the EventBus (available even without a manager)
        diagnostics = event_bus.get_diagnostics(project_id)

        manager, _user_id = await _find_manager(project_id)
        if not manager:
            # Fallback: try to load last known state from DB
            if state.session_mgr:
                saved = await state.session_mgr.load_orchestrator_state(project_id)
                if saved and saved.get("status") in ("running", "interrupted", "completed"):
                    # The agent_states column stores a nested blob:
                    # {"agent_states": {...}, "dag_task_statuses": {...}, "dag_graph": {...}}
                    # We must unwrap it to extract the inner fields.
                    agents_blob = saved.get("agent_states", {})
                    # If it's the nested blob format, extract inner fields
                    if isinstance(agents_blob, dict) and "agent_states" in agents_blob:
                        inner_agent_states = agents_blob.get("agent_states", {})
                        dag_graph = agents_blob.get("dag_graph")
                        dag_task_statuses = agents_blob.get("dag_task_statuses", {})
                    else:
                        # Legacy format: agent_states is flat
                        inner_agent_states = agents_blob
                        dag_graph = saved.get("dag_graph")
                        dag_task_statuses = saved.get("dag_task_statuses", {})

                    # Similarly unwrap shared_context blob
                    ctx_blob = saved.get("shared_context", {})
                    if isinstance(ctx_blob, dict) and "shared_context" in ctx_blob:
                        shared_ctx = ctx_blob.get("shared_context", [])
                    elif isinstance(ctx_blob, list):
                        shared_ctx = ctx_blob
                    else:
                        shared_ctx = []

                    return {
                        "status": saved.get("status", "idle"),
                        "agent_states": inner_agent_states,
                        "loop_progress": {
                            "loop": saved.get("current_loop", 0),
                            "turn": saved.get("turn_count", 0),
                            "max_turns": 0,
                            "input_tokens": saved.get("total_input_tokens", 0),
                            "output_tokens": saved.get("total_output_tokens", 0),
                            "total_tokens": saved.get("total_tokens", 0),
                            "max_budget": 0,
                            "max_loops": 0,
                        }
                        if saved.get("current_loop")
                        else None,
                        "shared_context_count": len(shared_ctx),
                        "pending_messages": 0,
                        "pending_approval": None,
                        "dag_graph": dag_graph,
                        "dag_task_statuses": dag_task_statuses,
                        "diagnostics": diagnostics,
                    }
            return {
                "status": "idle",
                "agent_states": {},
                "loop_progress": None,
                "shared_context_count": 0,
                "pending_messages": 0,
                "pending_approval": None,
                "diagnostics": diagnostics,
            }

        loop_progress = None
        if manager.is_running:
            from config import MAX_BUDGET_USD, MAX_ORCHESTRATOR_LOOPS, MAX_TURNS_PER_CYCLE

            loop_progress = {
                "loop": getattr(manager, "_current_loop", 0),
                "turn": manager.turn_count,
                "max_turns": MAX_TURNS_PER_CYCLE,
                "input_tokens": manager.total_input_tokens,
                "output_tokens": manager.total_output_tokens,
                "total_tokens": manager.total_tokens,
                "max_budget": MAX_BUDGET_USD,
                "max_loops": MAX_ORCHESTRATOR_LOOPS,
            }

        return {
            "status": _manager_status(manager),
            "agent_states": manager.agent_states,
            "current_agent": manager.current_agent,
            "current_tool": manager.current_tool,
            "loop_progress": loop_progress,
            "shared_context_count": len(manager.shared_context),
            "shared_context_preview": [c[:200] for c in manager.shared_context[-5:]],
            "pending_messages": manager.pending_message_count,
            "pending_approval": manager.pending_approval,
            "background_tasks": len(manager._background_tasks),
            "turn_count": manager.turn_count,
            "total_input_tokens": manager.total_input_tokens,
            "total_output_tokens": manager.total_output_tokens,
            "total_tokens": manager.total_tokens,
            "dag_graph": getattr(manager, "_current_dag_graph", None),
            "dag_task_statuses": getattr(manager, "_dag_task_statuses", {}),
            "diagnostics": diagnostics,
        }

    @app.put("/api/projects/{project_id}")
    async def update_project(project_id: str, req: UpdateProjectRequest):
        """Update project settings (name, description, agents_count)."""
        if not state.session_mgr:
            return _problem(503, "Not initialized")

        db_project = await state.session_mgr.load_project(project_id)
        if not db_project:
            return _problem(404, "Project not found")

        if req.name is not None:
            name = req.name.strip()
            if not name or not state.PROJECT_NAME_RE.match(name):
                return _problem(400, "Invalid project name")
            await state.session_mgr.update_project_fields(project_id, name=name)
            # Update in-memory manager name if active
            manager, _ = await _find_manager(project_id)
            if manager:
                manager.project_name = name

        if req.description is not None:
            await state.session_mgr.update_project_fields(project_id, description=req.description)

        await event_bus.publish(
            {
                "type": "project_status",
                "project_id": project_id,
                "status": "updated",
            }
        )

        return {"ok": True}

    @app.get("/api/projects/{project_id}/state-dump")
    async def get_state_dump(project_id: str):
        """Complete state dump — shows the full picture of what's happening.

        Combines in-memory live state, DB-persisted messages, activity events,
        and orchestrator state into a single response. Useful for debugging
        and for the "always show state" file the user requested.
        """
        result: dict = {
            "project_id": project_id,
            "timestamp": time.time(),
        }

        # 1. Project metadata
        if state.session_mgr:
            db_project = await state.session_mgr.load_project(project_id)
            result["project"] = db_project or {}

        # 2. Live manager state
        manager, _ = await _find_manager(project_id)
        if manager:
            result["live"] = {
                "status": _manager_status(manager),
                "agent_states": manager.agent_states,
                "current_agent": manager.current_agent,
                "turn_count": manager.turn_count,
                "total_input_tokens": manager.total_input_tokens,
                "total_output_tokens": manager.total_output_tokens,
                "total_tokens": manager.total_tokens,
                "pending_messages": manager.pending_message_count,
                "pending_approval": manager.pending_approval,
            }
        else:
            result["live"] = {"status": "no_manager"}

        # 3. Last N messages from DB
        if state.session_mgr:
            msgs = await state.session_mgr.get_recent_messages(project_id, count=20)
            result["recent_messages"] = msgs
            # Total message count
            _, total = await state.session_mgr.get_messages_paginated(project_id, limit=0, offset=0)
            result["total_messages"] = total

        # 4. Recent activity events from DB
        if state.session_mgr:
            events = await state.session_mgr.get_activity_since(
                project_id, since_sequence=0, limit=50
            )
            result["recent_activity"] = events
            result["total_activity_events"] = await state.session_mgr.get_latest_sequence(
                project_id
            )

        # 5. Saved orchestrator state (crash recovery)
        if state.session_mgr:
            orch_state = await state.session_mgr.load_orchestrator_state(project_id)
            result["orchestrator_state"] = orch_state or {}

        return result

    @app.get("/api/projects/{project_id}/agents")
    async def get_project_agents(project_id: str):
        """Detailed agent info with individual stats."""
        manager, _ = await _find_manager(project_id)
        if not manager:
            return {"agents": []}

        agents = []
        for agent_name in manager.agent_names:
            # Compute per-agent stats from conversation log
            agent_msgs = [m for m in manager.conversation_log if m.agent_name == agent_name]
            agent_tokens = sum(m.total_tokens for m in agent_msgs)
            agent_turns = len(agent_msgs)
            last_activity = agent_msgs[-1].content[:200] if agent_msgs else ""
            last_timestamp = agent_msgs[-1].timestamp if agent_msgs else 0

            # Live state from orchestrator tracking
            live_state = manager.agent_states.get(agent_name, {})

            agents.append(
                {
                    "name": agent_name,
                    "total_tokens": agent_tokens,
                    "turns": agent_turns,
                    "last_activity": last_activity,
                    "last_timestamp": last_timestamp,
                    "state": live_state.get("state", "idle"),
                    "current_tool": live_state.get("current_tool", ""),
                    "task": live_state.get("task", ""),
                    "duration": live_state.get("duration", 0),
                }
            )

        return {"agents": agents}

    @app.get("/api/projects/{project_id}/messages")
    async def get_messages(project_id: str, limit: int = 50, offset: int = 0):
        """Conversation history (paginated, from DB)."""
        limit = max(1, min(limit, 500))  # clamp: 1–500
        offset = max(0, offset)
        if not state.session_mgr:
            return {"messages": [], "total": 0}
        messages, total = await state.session_mgr.get_messages_paginated(project_id, limit, offset)
        return {"messages": messages, "total": total}

    @app.get("/api/projects/{project_id}/files")
    async def get_files(project_id: str):
        """Git diff + git status in project dir."""
        manager, _ = await _find_manager(project_id)

        if manager:
            project_dir = manager.project_dir
        else:
            if not state.session_mgr:
                return {"stat": "", "status": "", "diff": ""}
            db_project = await state.session_mgr.load_project(project_id)
            if not db_project:
                return {"error": "Project not found"}
            project_dir = db_project.get("project_dir", "")

        if not project_dir or not Path(project_dir).exists():
            return {"stat": "", "status": "", "diff": ""}

        try:

            async def _run_git(*args: str, timeout: float = 5.0) -> str:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    *args,
                    cwd=project_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return stdout.decode("utf-8", errors="replace")

            stat_out = await _run_git("diff", "--stat", "HEAD")
            status_out = await _run_git("status", "--short")
            diff_out = await _run_git("diff", "HEAD", timeout=GIT_DIFF_TIMEOUT)
            return {
                "stat": stat_out.strip(),
                "status": status_out.strip(),
                "diff": diff_out[:50000],
                "project_dir": str(project_dir),
            }
        except Exception as e:
            logger.error("Git operation failed for %s: %s", project_id, e, exc_info=True)
            return {"error": "An internal error occurred. Check server logs for details."}

    @app.get("/api/projects/{project_id}/tasks")
    async def get_tasks(project_id: str):
        """Task history from DB."""
        if not state.session_mgr:
            return {"tasks": []}
        tasks = await state.session_mgr.get_project_tasks(project_id)
        return {"tasks": tasks}

    @app.get("/api/projects/{project_id}/summary")
    async def get_session_summary(project_id: str):
        """Return the last orchestrator summary message and session stats.

        The final message is stored by _send_final() as a "system" role message
        in the conversation log. This endpoint fetches it along with session
        stats so the frontend can render a completion card.
        """
        if not _valid_project_id(project_id):
            return _problem(400, "Invalid project ID format")

        manager, _ = await _find_manager(project_id)

        # Collect stats from the live manager if available
        if manager:
            turn_count = manager.turn_count
            total_input_tokens = manager.total_input_tokens
            total_output_tokens = manager.total_output_tokens
            total_tokens = manager.total_tokens
            status = _manager_status(manager)
        else:
            turn_count = 0
            total_input_tokens = 0
            total_output_tokens = 0
            total_tokens = 0
            status = "idle"
            if state.session_mgr:
                saved = await state.session_mgr.load_orchestrator_state(project_id)
                if saved:
                    turn_count = saved.get("turn_count", 0)
                    total_input_tokens = saved.get("total_input_tokens", 0)
                    total_output_tokens = saved.get("total_output_tokens", 0)
                    total_tokens = saved.get("total_tokens", 0)
                    status = saved.get("status", "idle")

        # Find the most recent system message — that is the final summary text
        # sent by orchestrator._send_final() at the end of every session.
        last_summary_text: str | None = None
        if state.session_mgr:
            msgs, _ = await state.session_mgr.get_messages_paginated(
                project_id, limit=200, offset=0
            )
            # Walk backwards to find the last system-role message
            for msg in reversed(msgs):
                if msg.get("role") in ("system", "System") or msg.get("agent_name") in (
                    "System",
                    "system",
                ):
                    last_summary_text = msg.get("content")
                    break

        # If manager has an in-memory conversation log, prefer that (most recent)
        if manager:
            for msg in reversed(list(manager.conversation_log)):
                if msg.agent_name in ("System", "system") or msg.role in ("System", "system"):
                    last_summary_text = msg.content
                    break

        return {
            "project_id": project_id,
            "status": status,
            "summary_text": last_summary_text,
            "turn_count": turn_count,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
        }

    @app.get("/api/projects/{project_id}/brain-summary")
    async def get_brain_summary(project_id: str):
        """Return a structured executive digest of the blackboard state.

        Constructs a Blackboard from the project's persisted StructuredNotes
        and returns clusters, top-scored notes, conflicts, and health metrics.
        """
        if not _valid_project_id(project_id):
            return _problem(400, "Invalid project ID format")

        project_dir = await _resolve_project_dir(project_id)
        if not project_dir:
            return _problem(404, f"Project '{project_id}' not found")

        try:
            from blackboard import Blackboard
            from structured_notes import StructuredNotes

            notes = StructuredNotes(project_dir)
            notes.init_session("")
            bb = Blackboard(notes)
            summary = bb.get_brain_summary()
            return {"project_id": project_id, **summary}
        except Exception as exc:
            logger.error("Brain summary failed for %s: %s", project_id, exc, exc_info=True)
            return _problem(500, f"Failed to generate brain summary: {exc}")

    @app.post("/api/projects")
    async def create_project(req: CreateProjectRequest):
        """Create a new project from the web dashboard."""
        name = req.name.strip()
        if not name or not state.PROJECT_NAME_RE.match(name):
            return _problem(
                400, "Invalid project name. Use letters, numbers, spaces, hyphens, underscores."
            )

        directory = req.directory.strip()
        if not directory:
            # Auto-generate from PROJECTS_BASE_DIR + project name
            from config import PROJECTS_BASE_DIR

            slug = name.lower().replace(" ", "-")
            directory = str(PROJECTS_BASE_DIR / slug)

        if not state.session_mgr:
            return _problem(503, "Not initialized")

        project_dir = os.path.expanduser(directory)

        # Security: restrict directory creation to home dir or configured projects base
        from config import CLAUDE_PROJECTS_ROOT, PROJECTS_BASE_DIR, SANDBOX_ENABLED

        resolved_dir = Path(project_dir).resolve()
        home = Path.home().resolve()
        projects_base = PROJECTS_BASE_DIR.resolve()
        allowed_roots = [home, projects_base]
        if not any(resolved_dir.is_relative_to(root) for root in allowed_roots):
            return _problem(
                403,
                "Project directory must be within your home directory or configured projects base.",
            )

        # Sandbox enforcement: also require directory to be inside CLAUDE_PROJECTS_ROOT
        if SANDBOX_ENABLED:
            dir_resolved = str(resolved_dir)
            root_resolved = str(Path(CLAUDE_PROJECTS_ROOT).resolve())
            if not dir_resolved.startswith(root_resolved + "/") and dir_resolved != root_resolved:
                return _problem(
                    400,
                    f"Project directory must be inside {CLAUDE_PROJECTS_ROOT}",
                )

        try:
            os.makedirs(project_dir, exist_ok=True)
        except OSError as e:
            logger.error("Cannot create project directory: %s", e, exc_info=True)
            return _problem(400, "Cannot create directory: permission denied or path is invalid.")

        project_id = name.lower().replace(" ", "-")
        existing = await state.session_mgr.load_project(project_id)
        if existing:
            project_id = f"{project_id}-{uuid.uuid4().hex[:6]}"

        user_id = None  # Web-created projects are anonymous (no user row)
        state_user_id = 0  # Sentinel int for in-memory session registry

        await state.session_mgr.save_project(
            project_id=project_id,
            user_id=user_id,
            name=name,
            description=req.description or "",
            project_dir=project_dir,
        )

        # Remove from deleted list so auto-discovery won't skip it
        _deleted_file = Path("data/deleted_projects.json")
        try:
            if _deleted_file.exists():
                deleted_ids = json.loads(_deleted_file.read_text())
                if project_id in deleted_ids:
                    deleted_ids.remove(project_id)
                    _deleted_file.write_text(json.dumps(deleted_ids))
        except Exception:
            pass

        # Create and register the manager
        manager = _create_web_manager(
            project_id=project_id,
            project_name=name,
            project_dir=project_dir,
            user_id=state_user_id,
            agents_count=req.agents_count,
        )
        if manager:
            await state.register_manager(state_user_id, project_id, manager)

        await event_bus.publish(
            {
                "type": "project_status",
                "project_id": project_id,
                "status": "idle",
            }
        )

        return {"ok": True, "project_id": project_id}

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str):
        """Delete a project."""
        manager, user_id = await _find_manager(project_id)
        if manager:
            if manager.is_running:
                await manager.stop()
            if user_id is not None:
                await state.unregister_manager(user_id, project_id)

        if state.session_mgr:
            await state.session_mgr.delete_project(project_id)

        # Track deletion so auto-discovery doesn't re-create it on restart
        _deleted_file = Path("data/deleted_projects.json")
        try:
            _deleted_file.parent.mkdir(parents=True, exist_ok=True)
            deleted_ids: list[str] = []
            if _deleted_file.exists():
                deleted_ids = json.loads(_deleted_file.read_text())
            if project_id not in deleted_ids:
                deleted_ids.append(project_id)
            _deleted_file.write_text(json.dumps(deleted_ids))
        except Exception:
            logger.warning("Could not persist deleted project ID %s", project_id)

        await event_bus.publish(
            {
                "type": "project_status",
                "project_id": project_id,
                "status": "deleted",
            }
        )

        return {"ok": True}

    @app.post("/api/projects/{project_id}/clear-history")
    async def clear_project_history(project_id: str):
        """Clear all messages and task history for a project, starting fresh."""
        manager, _ = await _find_manager(project_id)
        if manager and manager.is_running:
            return _problem(400, "Cannot clear history while project is running")

        if state.session_mgr:
            await state.session_mgr.clear_project_data(project_id)

        # Reset ALL live state on active manager — full context wipe.
        # Without this, agents resume with stale context from previous sessions.
        if manager:
            # Core state
            manager.shared_context = []
            manager.conversation_log = collections.deque(maxlen=CONVERSATION_LOG_MAXLEN)
            manager.turn_count = 0
            manager.total_input_tokens = 0
            manager.total_output_tokens = 0
            manager.total_tokens = 0
            manager.agent_states = {}

            # Agent tracking
            manager._completed_rounds = []
            manager._agents_used = set()

            # DAG state
            manager._current_dag_graph = None
            manager._dag_task_statuses = {}

            # Message queue (drain any pending messages)
            drained = manager.drain_message_queue()
            if drained:
                logger.info(f"[{project_id}] Drained {drained} pending messages")

            # SDK session IDs — forces agents to start fresh sessions
            # (otherwise they resume with context from cleared conversations)
            _smgr = getattr(manager, "session_mgr", None)
            if (
                _smgr is not None
                and hasattr(_smgr, "invalidate_all_sessions")
                and callable(getattr(_smgr, "invalidate_all_sessions", None))
            ):
                try:
                    await _smgr.invalidate_all_sessions(project_id)
                except Exception as e:
                    logger.debug(f"[{project_id}] invalidate_all_sessions failed: {e}")

            logger.info(
                f"[{project_id}] Full context reset: conversation_log, "
                f"completed_rounds, agents_used, dag_graph, message_queue, "
                f"SDK sessions all cleared"
            )

        # Clear in-memory event bus data (ring buffer + sequence counters)
        # so stale events don't resurface from the memory cache.
        event_bus.clear_project_events(project_id)

        # Invalidate the conversation cache so the next connect creates a fresh
        # conversation in the ConversationStore rather than appending to the cleared one.
        invalidate_conversation_cache(project_id)

        # Notify connected clients so UI updates in real-time
        await event_bus.publish(
            {
                "type": "project_status",
                "project_id": project_id,
                "status": "idle",
            }
        )
        await event_bus.publish(
            {
                "type": "history_cleared",
                "project_id": project_id,
            }
        )

        return {"ok": True}

    @app.post("/api/projects/{project_id}/start")
    async def start_project(project_id: str):
        """Start/activate a dormant project."""
        manager, _ = await _find_manager(project_id)
        if manager:
            return {"ok": True, "message": "Project already active"}

        if not state.session_mgr:
            return _problem(503, "Not initialized")

        db_project = await state.session_mgr.load_project(project_id)
        if not db_project:
            return _problem(404, "Project not found")

        user_id = db_project.get("user_id") or 0
        project_name = db_project["name"]
        project_dir = db_project.get("project_dir", "")

        if not project_dir or not Path(project_dir).exists():
            return _problem(400, f"Project directory not found: {project_dir}")

        manager = _create_web_manager(
            project_id=project_id,
            project_name=project_name,
            project_dir=project_dir,
            user_id=user_id,
            agents_count=2,
        )
        if manager:
            await state.register_manager(user_id, project_id, manager)

        await event_bus.publish(
            {
                "type": "project_status",
                "project_id": project_id,
                "status": "idle",
            }
        )

        return {"ok": True}

    @app.get("/api/agent-registry")
    async def get_agent_registry():
        """Expose AGENT_REGISTRY metadata for the frontend.

        This is the single source of truth — the frontend should derive
        AGENT_ICONS, AGENT_LABELS, AGENT_COLORS, and AGENT_ACCENTS from
        this data instead of maintaining separate hardcoded maps.
        """
        import config as cfg

        registry = {}
        for role, ac in cfg.AGENT_REGISTRY.items():
            registry[role] = {
                "emoji": ac.emoji,
                "label": ac.label,
                "layer": ac.layer,
                "legacy": ac.legacy,
                "tw_color": ac.tw_color,
                "accent": ac.accent,
            }
        # Also include infrastructure timing constants for the frontend
        return {
            "agents": registry,
            "ws": {
                "keepalive_interval_ms": cfg.WS_KEEPALIVE_INTERVAL,
                "reconnect_base_delay_ms": cfg.WS_RECONNECT_BASE_DELAY,
                "reconnect_max_delay_ms": cfg.WS_RECONNECT_MAX_DELAY,
            },
        }

    @app.get("/api/settings")
    async def get_settings():
        """Get current config values."""
        import config as cfg

        return {
            "max_turns_per_cycle": cfg.MAX_TURNS_PER_CYCLE,
            "max_budget_usd": cfg.MAX_BUDGET_USD,
            "agent_timeout_seconds": cfg.AGENT_TIMEOUT_SECONDS,
            "sdk_max_turns_per_query": cfg.SDK_MAX_TURNS_PER_QUERY,
            "sdk_max_budget_per_query": cfg.SDK_MAX_BUDGET_PER_QUERY,
            "projects_base_dir": str(cfg.PROJECTS_BASE_DIR),
            "max_user_message_length": cfg.MAX_USER_MESSAGE_LENGTH,
            "session_expiry_hours": cfg.SESSION_EXPIRY_HOURS,
            "max_orchestrator_loops": cfg.MAX_ORCHESTRATOR_LOOPS,
        }

    @app.put("/api/settings")
    async def update_settings(req: UpdateSettingsRequest):
        """Update editable settings (runtime only, does not persist to .env).

        Validates all values before applying to prevent invalid configuration
        from crashing the system at runtime.
        """
        import config as cfg

        # --- Validate ranges before applying any changes ---
        errors: list[str] = []
        if req.max_turns_per_cycle is not None and req.max_turns_per_cycle < 1:
            errors.append("max_turns_per_cycle must be >= 1")
        if req.max_budget_usd is not None and req.max_budget_usd <= 0:
            errors.append("max_budget_usd must be > 0")
        if req.max_budget_usd is not None and req.max_budget_usd > 10_000:
            errors.append("max_budget_usd cannot exceed $10,000")
        if req.agent_timeout_seconds is not None and req.agent_timeout_seconds < 10:
            errors.append("agent_timeout_seconds must be >= 10")
        if req.agent_timeout_seconds is not None and req.agent_timeout_seconds > 7200:
            errors.append("agent_timeout_seconds cannot exceed 7200 (2 hours)")
        if req.sdk_max_turns_per_query is not None and req.sdk_max_turns_per_query < 1:
            errors.append("sdk_max_turns_per_query must be >= 1")
        if req.sdk_max_budget_per_query is not None and req.sdk_max_budget_per_query <= 0:
            errors.append("sdk_max_budget_per_query must be > 0")
        if req.sdk_max_budget_per_query is not None:
            effective_budget = (
                req.max_budget_usd if req.max_budget_usd is not None else cfg.MAX_BUDGET_USD
            )
            if req.sdk_max_budget_per_query > effective_budget:
                errors.append(
                    f"sdk_max_budget_per_query ({req.sdk_max_budget_per_query}) "
                    f"cannot exceed max_budget_usd ({effective_budget})"
                )
        if req.max_user_message_length is not None and req.max_user_message_length < 100:
            errors.append("max_user_message_length must be >= 100")
        if req.max_orchestrator_loops is not None and req.max_orchestrator_loops < 1:
            errors.append("max_orchestrator_loops must be >= 1")
        if req.max_orchestrator_loops is not None and req.max_orchestrator_loops > 1000:
            errors.append("max_orchestrator_loops cannot exceed 1000")

        if errors:
            return _problem(400, "; ".join(errors))

        # --- Apply validated changes ---
        updated = {}
        if req.max_turns_per_cycle is not None:
            cfg.MAX_TURNS_PER_CYCLE = req.max_turns_per_cycle
            updated["max_turns_per_cycle"] = req.max_turns_per_cycle
        if req.max_budget_usd is not None:
            cfg.MAX_BUDGET_USD = req.max_budget_usd
            updated["max_budget_usd"] = req.max_budget_usd
        if req.agent_timeout_seconds is not None:
            cfg.AGENT_TIMEOUT_SECONDS = req.agent_timeout_seconds
            updated["agent_timeout_seconds"] = req.agent_timeout_seconds
        if req.sdk_max_turns_per_query is not None:
            cfg.SDK_MAX_TURNS_PER_QUERY = req.sdk_max_turns_per_query
            updated["sdk_max_turns_per_query"] = req.sdk_max_turns_per_query
        if req.sdk_max_budget_per_query is not None:
            cfg.SDK_MAX_BUDGET_PER_QUERY = req.sdk_max_budget_per_query
            updated["sdk_max_budget_per_query"] = req.sdk_max_budget_per_query
        if req.max_user_message_length is not None:
            cfg.MAX_USER_MESSAGE_LENGTH = req.max_user_message_length
            updated["max_user_message_length"] = req.max_user_message_length
        if req.max_orchestrator_loops is not None:
            cfg.MAX_ORCHESTRATOR_LOOPS = req.max_orchestrator_loops
            updated["max_orchestrator_loops"] = req.max_orchestrator_loops

        logger.info(f"Settings updated: {updated}")
        return {"ok": True, "updated": updated}

    @app.post("/api/settings/persist")
    async def persist_settings(request: Request):
        """Persist settings overrides to data/settings_overrides.json.

        Only allows whitelisted configuration keys to prevent arbitrary
        data injection into the settings file.
        """
        import json as json_mod

        # Whitelist of keys that can be persisted via the API
        _ALLOWED_PERSIST_KEYS = {
            "max_turns_per_cycle",
            "max_budget_usd",
            "agent_timeout_seconds",
            "sdk_max_turns_per_query",
            "sdk_max_budget_per_query",
            "max_user_message_length",
            "max_orchestrator_loops",
            "session_expiry_hours",
            "rate_limit_seconds",
            "budget_warning_threshold",
            "stall_alert_seconds",
            "pipeline_max_steps",
            "scheduler_check_interval",
            "session_timeout_seconds",
        }

        data = await request.json()
        if not isinstance(data, dict):
            return _problem(400, "Expected a JSON object")

        # Reject any keys not in the whitelist
        rejected = set(data.keys()) - _ALLOWED_PERSIST_KEYS
        if rejected:
            return _problem(
                400,
                f"Disallowed settings keys: {', '.join(sorted(rejected))}",
            )

        # Clamp numeric settings to sane bounds
        NUMERIC_BOUNDS = {
            "max_budget_usd": (0.1, 500.0),
            "max_turns_per_cycle": (1, 500),
            "max_task_budget_usd": (0.1, 100.0),
        }
        for key, (lo, hi) in NUMERIC_BOUNDS.items():
            if key in data and isinstance(data[key], int | float):
                data[key] = max(lo, min(float(data[key]), hi))

        overrides_path = Path("data/settings_overrides.json")
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        # Merge with existing overrides
        existing = {}
        if overrides_path.exists():
            try:
                existing = json_mod.loads(overrides_path.read_text())
            except Exception as _parse_err:
                logger.warning(
                    "Settings: failed to parse existing overrides file, will overwrite: %s",
                    _parse_err,
                )
        existing.update(data)
        overrides_path.write_text(json_mod.dumps(existing, indent=2))
        return {"ok": True}

    @app.get("/api/browse-dirs")
    async def browse_dirs(path: str = "~"):
        """Browse filesystem directories for project creation.

        Restricted to the user's home directory and PROJECTS_BASE_DIR to
        prevent listing sensitive system directories.
        """
        from config import PROJECTS_BASE_DIR

        target = Path(os.path.expanduser(path)).resolve()

        # Security: only allow browsing within home dir or configured projects dir
        home = Path.home().resolve()
        projects_base = PROJECTS_BASE_DIR.resolve()
        allowed_roots = [home, projects_base]
        if not any(target == root or target.is_relative_to(root) for root in allowed_roots):
            return JSONResponse(
                {"error": "Access denied: browsing is restricted to your home directory"},
                status_code=403,
            )

        if not target.exists():
            return {
                "current": str(target),
                "parent": str(target.parent),
                "entries": [],
                "error": "Path not found",
                "home": str(home),
            }
        if not target.is_dir():
            target = target.parent

        entries = []
        error = None
        try:
            for item in sorted(target.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    # Check if we can actually read this directory
                    is_git = (item / ".git").exists()
                    entries.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "is_dir": True,
                            "is_git": is_git,
                        }
                    )
                if len(entries) >= 100:
                    break
        except PermissionError:
            error = "Permission denied — try a different folder"

        return {
            "current": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "entries": entries,
            "error": error,
            "home": str(home),
        }

    # --- Send Message + Talk Agent endpoints ---
    # Message length validation is handled at Pydantic model level
    # using config.MAX_USER_MESSAGE_LENGTH (default: 4000 chars)

    @app.post("/api/projects/{project_id}/message")
    async def send_message(project_id: str, req: SendMessageRequest):
        """Send message to orchestrator via the parallel task queue.

        Message length is validated at the Pydantic model level using
        config.MAX_USER_MESSAGE_LENGTH.

        Each message is enqueued as an isolated task that gets its own:
        - ``task_id`` (returned immediately in the response)
        - ``conversation_id`` (created by the worker for full state isolation)
        - Ephemeral ``OrchestratorManager`` (no shared mutable state between
          concurrent tasks for the same project)

        Concurrency is bounded by the ``PARALLEL_TASKS_LIMIT`` env var (default 5).
        WebSocket events emitted during processing carry ``task_id`` so the
        frontend can route responses to the correct UI slot.

        Returns:
            200 {"ok": true, "task_id": "...", "status": "queued"}
            404 if the project does not exist in DB or active sessions.
        """
        logger.info("[%s] Received message for task queue: %s", project_id, req.message[:100])

        # ------------------------------------------------------------------
        # 1. Resolve project metadata (name, dir, user_id) — needed by worker
        # ------------------------------------------------------------------
        project_name: str | None = None
        project_dir: str | None = None
        user_id: int = 0

        manager, _uid = await _find_manager(project_id)
        if manager:
            project_name = manager.project_name
            project_dir = manager.project_dir
            user_id = _uid or 0
        elif state.session_mgr:
            # Manager not in memory — try loading from DB
            _proj_lock = await _get_or_create_manager_lock(project_id)
            async with _proj_lock:
                manager, _uid = await _find_manager(project_id)
                if manager:
                    project_name = manager.project_name
                    project_dir = manager.project_dir
                    user_id = _uid or 0
                else:
                    db_project = await state.session_mgr.load_project(project_id)
                    if db_project:
                        project_name = db_project.get("name", project_id)
                        project_dir = db_project.get("project_dir", "")
                        user_id = db_project.get("user_id") or 0
                        # Register a status-tracking manager (not used for task exec)
                        status_manager = _create_web_manager(
                            project_id=project_id,
                            project_name=project_name,
                            project_dir=project_dir,
                            user_id=user_id,
                            agents_count=2,
                        )
                        if status_manager:
                            await state.register_manager(user_id, project_id, status_manager)
                            logger.info("[%s] Status manager registered from DB", project_id)

        if project_name is None:
            logger.error("[%s] No project found — cannot enqueue message", project_id)
            return _problem(404, "Project not found.")

        # ------------------------------------------------------------------
        # 2. Enqueue to parallel task queue — returns immediately with task_id
        # ------------------------------------------------------------------
        registry = TaskQueueRegistry.get_registry()
        task_queue = await registry.get_or_create_queue(project_id)

        record = await task_queue.enqueue(
            message=req.message,
            worker_fn=process_message_task,
            project_name=project_name,
            project_dir=project_dir or "",
            user_id=user_id,
            mode=req.mode,
        )

        logger.info(
            "[%s] Message enqueued as task_id=%s (queue_depth=%d, running=%d/%d)",
            project_id,
            record.task_id,
            task_queue.queue_depth,
            task_queue.running_count,
            task_queue.max_concurrent,
        )

        return {
            "ok": True,
            "task_id": record.task_id,
            "status": record.status.value,
            "queue_depth": task_queue.queue_depth,
        }

    @app.post("/api/projects/{project_id}/queue")
    async def enqueue_message(project_id: str, req: SendMessageRequest):
        """Add a message to the project's persistent queue.

        If the project is idle, starts immediately. If running, queues for after current task.
        """
        if not state.session_mgr:
            return _problem(503, "Session manager unavailable.")
        manager, _ = await _find_manager(project_id)
        if not manager:
            db_project = await state.session_mgr.load_project(project_id)
            if not db_project:
                return _problem(404, "Project not found.")

        if manager and not manager.is_running:
            await manager.start_session(req.message)
            return {"ok": True, "action": "started", "queue_size": 0}

        queue_size = await state.session_mgr.enqueue_message(project_id, req.message)
        if manager:
            await manager.inject_user_message("orchestrator", req.message)
        logger.info(f"[{project_id}] Queued message (position {queue_size}): {req.message[:80]}")
        return {"ok": True, "action": "queued", "position": queue_size, "queue_size": queue_size}

    @app.get("/api/projects/{project_id}/queue")
    async def get_queue(project_id: str):
        """List all messages queued for a project."""
        if not state.session_mgr:
            return _problem(503, "Session manager unavailable.")
        items = await state.session_mgr.list_queued_messages(project_id)
        return {"ok": True, "queue": items, "queue_size": len(items)}

    @app.delete("/api/projects/{project_id}/queue/{msg_id}")
    async def delete_queued_message(project_id: str, msg_id: int):
        """Remove a specific message from the queue by ID."""
        if not state.session_mgr:
            return _problem(503, "Session manager unavailable.")
        deleted = await state.session_mgr.delete_queued_message(project_id, msg_id)
        if not deleted:
            return _problem(404, f"Message {msg_id} not found in queue.")
        return {"ok": True, "deleted_id": msg_id}

    @app.delete("/api/projects/{project_id}/queue")
    async def clear_queue(project_id: str):
        """Clear all queued messages for a project."""
        if not state.session_mgr:
            return _problem(503, "Session manager unavailable.")
        count = await state.session_mgr.clear_queue(project_id)
        logger.info(f"[{project_id}] Cleared {count} queued message(s)")
        return {"ok": True, "cleared": count}

    @app.post("/api/projects/{project_id}/talk/{agent}")
    async def talk_agent(project_id: str, agent: str, req: TalkAgentRequest):
        """Send message to specific agent.

        Message length is validated at the Pydantic model level using
        config.MAX_USER_MESSAGE_LENGTH.
        """
        manager, _ = await _find_manager(project_id)
        if not manager:
            return _problem(404, "Project not active.")

        if agent not in manager.agent_names:
            return _problem(400, f"Unknown agent: {agent}. Available: {manager.agent_names}")

        await manager.inject_user_message(agent, req.message)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/pause")
    async def pause_project(project_id: str):
        """Pause project."""
        manager, _ = await _find_manager(project_id)
        if not manager:
            return _problem(404, "Project not active")
        manager.pause()
        if state.session_mgr:
            await state.session_mgr.update_status(project_id, "paused")
        await event_bus.publish(
            {
                "type": "project_status",
                "project_id": project_id,
                "status": "paused",
            }
        )
        return {"ok": True}

    @app.post("/api/projects/{project_id}/resume")
    async def resume_project(project_id: str):
        """Resume project."""
        manager, _ = await _find_manager(project_id)
        if not manager:
            return _problem(404, "Project not active")
        manager.resume()
        if state.session_mgr:
            await state.session_mgr.update_status(project_id, "active")
        await event_bus.publish(
            {
                "type": "project_status",
                "project_id": project_id,
                "status": "running",
            }
        )
        return {"ok": True}

    @app.post("/api/projects/{project_id}/stop")
    async def stop_project(project_id: str):
        """Stop project."""
        manager, _ = await _find_manager(project_id)
        if not manager:
            return _problem(404, "Project not active")
        await manager.stop()
        if state.session_mgr:
            await state.session_mgr.update_status(project_id, "stopped")
        await event_bus.publish(
            {
                "type": "project_status",
                "project_id": project_id,
                "status": "stopped",
            }
        )
        return {"ok": True}

    @app.post("/api/projects/{project_id}/nudge/{agent}")
    async def nudge_agent(project_id: str, agent: str, req: NudgeRequest):
        """Nudge a specific agent mid-run without stopping other agents.

        Unlike /talk which queues a message for after the current task,
        /nudge injects guidance into the running agent's context immediately.
        This allows the user to steer an agent (e.g., 'change the color to blue')
        while other agents continue working in parallel.
        """
        manager, _ = await _find_manager(project_id)
        if not manager:
            return _problem(404, "Project not active.")

        # Check if agent exists
        from config import get_all_role_names

        all_known = get_all_role_names(include_legacy=True)
        if agent not in all_known and agent != "orchestrator":
            return _problem(400, f"Unknown agent: {agent}. Available: {list(all_known)}")

        # Check if agent is currently working
        agent_state = manager.agent_states.get(agent, {})
        is_working = agent_state.get("state") == "working"

        # Build nudge context
        nudge_text = (
            "\n\n"
            f"\U0001f4ac **User nudge** (priority: {req.priority}):\n"
            f"{req.message}\n"
            "\nApply this guidance to your current work without starting over."
        )

        if is_working:
            # Agent is running - inject into shared context so it is picked up
            manager.shared_context.append(f"[USER NUDGE to {agent}] {req.message}")
            await manager._emit_event(
                "nudge_sent",
                agent=agent,
                message=req.message[:200],
                priority=req.priority,
                status="injected",
            )
            await manager._notify(
                f"\U0001f4ac Nudge sent to *{agent}* (currently working)\n> {req.message[:200]}"
            )
        else:
            # Agent not running - queue for next run
            await manager.inject_user_message(agent, nudge_text)
            await manager._emit_event(
                "nudge_sent",
                agent=agent,
                message=req.message[:200],
                priority=req.priority,
                status="queued",
            )

        return {
            "ok": True,
            "status": "injected" if is_working else "queued",
            "agent": agent,
        }

    @app.post("/api/projects/{project_id}/approve")
    async def approve_project(project_id: str):
        """Approve a pending HITL checkpoint."""
        manager, _ = await _find_manager(project_id)
        if not manager:
            return _problem(404, "Project not active")
        if not manager.pending_approval:
            return _problem(400, "No pending approval")
        manager.approve()
        return {"ok": True}

    @app.post("/api/projects/{project_id}/reject")
    async def reject_project(project_id: str):
        """Reject a pending HITL checkpoint."""
        manager, _ = await _find_manager(project_id)
        if not manager:
            return _problem(404, "Project not active")
        if not manager.pending_approval:
            return _problem(400, "No pending approval")
        manager.reject()
        return {"ok": True}

    # --- Schedules CRUD ---

    @app.get("/api/schedules")
    async def list_schedules(user_id: int = 0):
        """List all schedules for a user."""
        if not state.session_mgr:
            return {"schedules": []}
        schedules = await state.session_mgr.get_schedules(user_id)
        return {"schedules": schedules}

    @app.post("/api/schedules")
    async def create_schedule(req: CreateScheduleRequest):
        """Create a new schedule."""
        if not state.session_mgr:
            return _problem(500, "DB not ready")
        # Validate schedule_time format
        if not re.match(r"^\d{2}:\d{2}$", req.schedule_time):
            return _problem(400, "schedule_time must be HH:MM format")
        h, m = int(req.schedule_time[:2]), int(req.schedule_time[3:])
        if h > 23 or m > 59:
            return _problem(400, "Invalid time: hours 0-23, minutes 0-59")
        schedule_id = await state.session_mgr.add_schedule(
            user_id=req.user_id,
            project_id=req.project_id,
            schedule_time=req.schedule_time,
            task_description=req.task_description,
            repeat=req.repeat,
        )
        return {"ok": True, "schedule_id": schedule_id}

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: int, user_id: int = 0):
        """Delete a schedule."""
        if not state.session_mgr:
            return _problem(500, "DB not ready")
        deleted = await state.session_mgr.delete_schedule(schedule_id, user_id)
        if not deleted:
            return _problem(404, "Schedule not found")
        return {"ok": True}

    @app.put("/api/projects/{project_id}/budget")
    async def set_project_budget(project_id: str, req: SetBudgetRequest):
        """Set per-project budget cap.

        Validation is performed by SetBudgetRequest (Pydantic v2 strict mode
        + finite + range checks). Invalid values return 422 via the
        RequestValidationError handler (RFC 7807 format).
        """
        if not state.session_mgr:
            return _problem(500, "DB not ready")
        await state.session_mgr.set_project_budget(project_id, req.budget_usd)
        return {"ok": True, "budget_usd": req.budget_usd}

    @app.get("/api/stats")
    async def get_stats():
        """Total token usage, project count, active agents."""
        active_managers = state.get_all_managers()

        total_tokens = sum(m.total_tokens for _, _, m in active_managers)
        total_input_tokens = sum(m.total_input_tokens for _, _, m in active_managers)
        total_output_tokens = sum(m.total_output_tokens for _, _, m in active_managers)
        running = sum(1 for _, _, m in active_managers if m.is_running)
        paused = sum(1 for _, _, m in active_managers if m.is_paused)

        db_projects = await state.session_mgr.list_projects() if state.session_mgr else []

        return {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "total_projects": len(db_projects),
            "active_projects": len(active_managers),
            "running": running,
            "paused": paused,
        }

    # --- Code browsing ---

    @app.get("/api/projects/{project_id}/tree")
    async def get_file_tree(project_id: str):
        """List files in project directory (2 levels deep).

        Security: resolves project dir and validates that all entries
        remain within it (prevents symlink escapes).
        """
        project_dir = await _resolve_project_dir(project_id)
        if not project_dir:
            return {"error": "Project not found"}

        tree = []
        try:
            root = Path(project_dir).resolve()
            if not root.exists() or not root.is_dir():
                return {"error": "Project directory not found"}
            skip = {
                ".git",
                "__pycache__",
                "node_modules",
                "venv",
                ".venv",
                ".mypy_cache",
                ".pytest_cache",
                "dist",
                "build",
            }
            for item in sorted(root.iterdir()):
                if item.name.startswith(".") and item.name != ".env.example":
                    if item.name not in (".github",):
                        continue
                if item.name in skip:
                    continue
                # Resolve to prevent symlink escapes
                resolved_item = item.resolve()
                if not resolved_item.is_relative_to(root):
                    continue  # Skip symlinks that escape project dir
                entry = {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "path": item.name,
                }
                if item.is_dir():
                    children = []
                    try:
                        for sub in sorted(item.iterdir()):
                            if sub.name.startswith(".") or sub.name in skip:
                                continue
                            # Resolve sub-entries too
                            resolved_sub = sub.resolve()
                            if not resolved_sub.is_relative_to(root):
                                continue
                            children.append(
                                {
                                    "name": sub.name,
                                    "type": "dir" if sub.is_dir() else "file",
                                    "path": f"{item.name}/{sub.name}",
                                }
                            )
                            if len(children) >= 50:
                                break
                    except PermissionError:
                        pass
                    entry["children"] = children
                tree.append(entry)
                if len(tree) >= 100:
                    break
        except Exception as e:
            logger.error("File tree error for %s: %s", project_id, e, exc_info=True)
            return {"error": "An internal error occurred. Check server logs for details."}

        return {"tree": tree, "project_dir": project_dir}

    @app.get("/api/projects/{project_id}/file")
    async def read_file(project_id: str, path: str):
        """Read a file from the project directory.

        Security:
        - Resolves symlinks before path check to prevent traversal attacks.
        - Uses is_relative_to() (Python 3.9+) for safe containment check.
        - F-05: Blocks access to sensitive files (.env, *.pem, *.key, etc.)
          using the same _SENSITIVE_PATTERNS list from git_discipline.py.
        """
        project_dir = await _resolve_project_dir(project_id)
        if not project_dir:
            return {"error": "Project not found"}

        # F-05: Block access to sensitive files (secrets, keys, certificates).
        # Uses the shared _is_sensitive() from git_discipline.py so the same
        # patterns protect both auto-commit and file-read endpoints.
        from git_discipline import _is_sensitive

        if _is_sensitive(path):
            logger.warning(
                "Sensitive file access blocked: project=%s path=%s",
                project_id,
                path,
            )
            return _problem(
                403,
                "Access denied: this file matches a sensitive pattern "
                "(.env, *.pem, *.key, etc.) and cannot be read via the API.",
            )

        file_path = Path(project_dir) / path
        try:
            # Resolve symlinks FIRST, then check containment
            file_path = file_path.resolve()
            proj_resolved = Path(project_dir).resolve()
            # Use is_relative_to for safe containment check (no prefix collisions)
            if not file_path.is_relative_to(proj_resolved):
                logger.warning(
                    "Path traversal blocked: %s tried to access %s (outside %s)",
                    project_id,
                    file_path,
                    proj_resolved,
                )
                return _problem(403, "Path traversal not allowed")
        except Exception as _path_err:
            logger.warning(
                "File read: invalid path resolution for %s/%s: %s", project_id, path, _path_err
            )
            return _problem(400, "Invalid path")

        # F-05: Also check the resolved filename against sensitive patterns,
        # in case a symlink was used to disguise a sensitive file.
        resolved_relative = str(file_path.relative_to(proj_resolved))
        if _is_sensitive(resolved_relative):
            logger.warning(
                "Sensitive file access blocked (after symlink resolve): project=%s path=%s resolved=%s",
                project_id,
                path,
                resolved_relative,
            )
            return _problem(
                403,
                "Access denied: this file resolves to a sensitive path "
                "and cannot be read via the API.",
            )

        if not file_path.exists():
            return {"error": "File not found"}
        if not file_path.is_file():
            return {"error": "Not a file"}

        size = file_path.stat().st_size
        if size > 500_000:
            return {"error": f"File too large ({size} bytes)", "size": size}

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.error("File read failed for %s path=%s: %s", project_id, path, e, exc_info=True)
            return {"error": "An internal error occurred. Check server logs for details."}

        return {"content": content, "path": path, "size": size}

    # --- Activity Replay (cross-device sync) ---

    @app.get("/api/projects/{project_id}/activity")
    async def get_activity(project_id: str, since: int = 0, limit: int = 200):
        """Get activity events after a given sequence_id."""
        since = max(0, since)
        limit = max(1, min(limit, 1000))  # clamp: 1–1000
        # Fast path: check in-memory ring buffer
        buffered = event_bus.get_buffered_events(project_id, since_sequence=since)
        if buffered:
            return {
                "events": buffered,
                "latest_sequence": event_bus.get_latest_sequence(project_id),
                "source": "memory",
            }

        # Slow path: query DB
        if state.session_mgr:
            events = await state.session_mgr.get_activity_since(project_id, since, limit)
            full_events = [_db_event_to_dict(e, project_id) for e in events]
            return {
                "events": full_events,
                "latest_sequence": await state.session_mgr.get_latest_sequence(project_id),
                "source": "database",
            }

        return {"events": [], "latest_sequence": 0, "source": "none"}

    @app.get("/api/projects/{project_id}/activity/latest")
    async def get_latest_sequence(project_id: str):
        """Get the latest sequence_id for a project (for sync protocol)."""
        return {
            "latest_sequence": event_bus.get_latest_sequence(project_id),
        }

    # --- Agent Performance & Cost Analytics ---

    @app.get("/api/agent-stats")
    async def get_agent_stats(project_id: str | None = None):
        """Get aggregated agent performance statistics."""
        if not state.session_mgr:
            return {"stats": []}
        stats = await state.session_mgr.get_agent_stats(project_id)
        return {"stats": stats}

    @app.get("/api/agent-stats/{agent_role}/recent")
    async def get_agent_recent(agent_role: str, limit: int = 10):
        """Get recent performance entries for a specific agent."""
        limit = max(1, min(limit, 200))  # clamp: 1–200
        if not state.session_mgr:
            return {"entries": []}
        entries = await state.session_mgr.get_agent_recent_performance(agent_role, limit)
        return {"entries": entries}

    # --- Platform persistence: conversation history + memory endpoints ---
    app.include_router(history_router)
    # --- Multi-project CRUD ---
    app.include_router(projects_router)
    # --- Parallel task queue status endpoints ---
    app.include_router(tasks_router)
    app.include_router(admin_tasks_router)

    # --- WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        """WebSocket for real-time event stream with ping/pong heartbeat.

        Supports event replay on reconnect: client sends
        {"type": "replay", "project_id": "...", "since_sequence": N}
        and receives all missed events before switching to live mode.

        Authentication (F-03 — first-frame auth protocol)
        --------------------------------------------------
        When DASHBOARD_API_KEY is set (AUTH_ENABLED is True), the client
        must send an auth message as the **first frame** after connection:

            {"type": "auth", "api_key": "<DASHBOARD_API_KEY>"}

        The server responds with:
            {"type": "auth_ok"}              — on success
            {"type": "auth_failed", ...}     — on failure (then closes 4003)

        **Why first-frame instead of query parameter?**
        Query parameters appear in server access logs, proxy logs, browser
        history, and Referer headers — leaking the API key.  The first-frame
        protocol transmits the key inside the encrypted WebSocket data channel.

        The client has 10 seconds to send the auth frame before the server
        closes the connection.  Legacy ``?api_key=...`` query parameter is
        no longer supported.

        Reconnection guidance
        ---------------------
        Clients should use exponential backoff on disconnect:
            delay = min(base * 2^attempt + jitter, cap)
            e.g. base=1s, cap=30s, jitter=rand(0..1s)
        The server sends {"type": "error", "reconnect_after_ms": <ms>} before
        closing abnormally so clients can adapt their retry delay.
        """
        ws_id = uuid.uuid4().hex[:12]

        # Accept the WebSocket connection first (required for proper Close frames)
        await ws.accept()

        # F-03: First-frame authentication protocol.
        # When DEVICE_AUTH_ENABLED: uses device tokens (cookie or first-frame).
        # When AUTH_ENABLED + DASHBOARD_API_KEY (legacy): uses api_key first-frame.
        # When neither: skip auth entirely.
        if not DEVICE_AUTH_ENABLED and not (AUTH_ENABLED and DASHBOARD_API_KEY):
            # No auth required — skip first-frame protocol
            logger.debug("WebSocket [%s]: auth disabled — skipping", ws_id)
        elif not DEVICE_AUTH_ENABLED and AUTH_ENABLED and DASHBOARD_API_KEY:
            # Legacy api_key auth (no device tokens)
            try:
                raw = await asyncio.wait_for(ws.receive_json(), timeout=WS_AUTH_TIMEOUT)
                if not isinstance(raw, dict) or raw.get("type") != "auth":
                    await ws.send_json(
                        {"type": "auth_failed", "reason": "First message must be an auth frame"}
                    )
                    await ws.close(code=4003, reason="Unauthorized: expected auth frame")
                    return
                client_key = raw.get("api_key", "")
                if not client_key or client_key != DASHBOARD_API_KEY:
                    await ws.send_json({"type": "auth_failed", "reason": "Invalid API key"})
                    await ws.close(code=4003, reason="Unauthorized: invalid key")
                    return
                await ws.send_json({"type": "auth_ok"})
                logger.info("WebSocket [%s]: authenticated via legacy api_key", ws_id)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "auth_failed", "reason": "Auth timeout"})
                    await ws.close(code=4003, reason="Unauthorized: auth timeout")
                except (ConnectionError, RuntimeError):
                    pass
                return
            except WebSocketDisconnect:
                return
            except Exception:
                try:
                    await ws.close(code=4003, reason="Auth error")
                except (ConnectionError, RuntimeError):
                    pass
                return
        else:
            # Device-token auth (cookie or first-frame)
            try:
                cookie_token = ""
                try:
                    ws_cookies = ws.cookies if hasattr(ws, "cookies") else {}
                    cookie_token = ws_cookies.get(COOKIE_NAME, "")
                except Exception:
                    pass
                if not cookie_token:
                    raw_cookie = ""
                    for hdr_name, hdr_val in ws.headers.raw:
                        if hdr_name == b"cookie":
                            raw_cookie = hdr_val.decode("utf-8", errors="replace")
                            break
                    if raw_cookie:
                        import http.cookies

                        c = http.cookies.SimpleCookie()
                        try:
                            c.load(raw_cookie)
                            if COOKIE_NAME in c:
                                cookie_token = c[COOKIE_NAME].value
                        except Exception:
                            pass
                if cookie_token and _device_auth.verify_device_token(cookie_token):
                    await ws.send_json({"type": "auth_ok"})
                    logger.info("WebSocket [%s]: authenticated via device cookie", ws_id)
                else:
                    raw = await asyncio.wait_for(ws.receive_json(), timeout=WS_AUTH_TIMEOUT)
                    if not isinstance(raw, dict) or raw.get("type") != "auth":
                        await ws.send_json(
                            {"type": "auth_failed", "reason": "First message must be an auth frame"}
                        )
                        await ws.close(code=4003, reason="Unauthorized: expected auth frame")
                        return
                    client_token = raw.get("device_token", "") or raw.get("api_key", "")
                    if not isinstance(client_token, str) or not client_token:
                        await ws.send_json(
                            {"type": "auth_failed", "reason": "Missing device_token"}
                        )
                        await ws.close(code=4003, reason="Unauthorized: token required")
                        return
                    if not _device_auth.verify_device_token(client_token):
                        await ws.send_json(
                            {"type": "auth_failed", "reason": "Invalid device token"}
                        )
                        await ws.close(code=4003, reason="Unauthorized: invalid token")
                        return
                    await ws.send_json({"type": "auth_ok"})
                    logger.info("WebSocket [%s]: authenticated via first-frame device token", ws_id)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "auth_failed", "reason": "Auth timeout"})
                    await ws.close(code=4003, reason="Unauthorized: auth timeout")
                except (ConnectionError, RuntimeError):
                    pass
                return
            except WebSocketDisconnect:
                logger.info("WebSocket [%s]: client disconnected during auth", ws_id)
                return
            except Exception as _auth_err:
                logger.debug("WebSocket [%s]: auth error — %s", ws_id, _auth_err)
                try:
                    await ws.close(code=4003, reason="Auth error")
                except (ConnectionError, RuntimeError):
                    pass
                return
        client_info = f"{ws.client.host}:{ws.client.port}" if ws.client else "unknown"
        logger.info("WebSocket [%s]: accepted connection from %s", ws_id, client_info)
        queue = await event_bus.subscribe()

        async def _sender():
            """Forward events from bus to WebSocket client."""
            try:
                while True:
                    try:
                        # Use configurable sender timeout (ARCH-08 fix)
                        from config import WS_SENDER_TIMEOUT

                        event = await asyncio.wait_for(
                            queue.get(), timeout=float(WS_SENDER_TIMEOUT)
                        )
                        await ws.send_json(event)
                    except TimeoutError:
                        logger.debug(
                            "WebSocket [%s]: sender idle for %ss — closing stale connection",
                            ws_id,
                            WS_SENDER_TIMEOUT,
                        )
                        break  # No events for WS_SENDER_TIMEOUT — client likely gone
                    except WebSocketDisconnect:
                        logger.info("WebSocket [%s]: client disconnected during send", ws_id)
                        break
                    except Exception as _send_err:
                        logger.warning(
                            "WebSocket [%s]: send error — %s",
                            ws_id,
                            _send_err,
                        )
                        break  # WebSocket send error — exit cleanly
            except asyncio.CancelledError:
                raise

        async def _heartbeat():
            """Send periodic pings to detect stale connections.            Uses WS_HEARTBEAT_INTERVAL from config (ARCH-08 fix).
            iOS Safari kills idle WebSocket connections after ~30s of
            inactivity, so we need to send pings more frequently.
            Combined with the client-side keepalive, this ensures
            there is always traffic on the connection.
            """
            from config import WS_HEARTBEAT_INTERVAL

            while True:
                await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
                try:
                    await ws.send_json({"type": "ping"})
                except WebSocketDisconnect:
                    logger.info("WebSocket [%s]: client disconnected during heartbeat", ws_id)
                    break
                except Exception as _ping_err:
                    logger.debug(
                        "WebSocket [%s]: heartbeat ping failed — %s",
                        ws_id,
                        _ping_err,
                    )
                    break

        async def _receiver():
            """Listen for client messages (pong, replay requests, future commands)."""
            while True:
                try:
                    data = await ws.receive_json()
                    # ARCH-06: Validate message structure
                    if not isinstance(data, dict):
                        logger.debug("WebSocket [%s]: ignoring non-dict message", ws_id)
                        continue
                    msg_type = data.get("type", "")
                    if not isinstance(msg_type, str) or len(msg_type) > 50:
                        logger.debug("WebSocket [%s]: ignoring invalid message type", ws_id)
                        continue

                    if msg_type == "pong":
                        pass  # Connection is alive

                    elif msg_type == "auth":
                        # Stray auth frame (already authenticated via cookie).
                        # Just acknowledge it so the client doesn't hang.
                        await ws.send_json({"type": "auth_ok"})

                    elif msg_type == "replay":
                        # Client requests missed events since a sequence_id
                        project_id = data.get("project_id", "")
                        since_seq = data.get("since_sequence", 0)
                        # Validate inputs before use
                        if not isinstance(project_id, str) or not _valid_project_id(project_id):
                            continue
                        if not isinstance(since_seq, int) or since_seq < 0:
                            since_seq = 0
                        if project_id:
                            # Try in-memory first (fast)
                            events = event_bus.get_buffered_events(project_id, since_seq)
                            if not events and state.session_mgr:
                                # Fall back to DB
                                db_events = await state.session_mgr.get_activity_since(
                                    project_id, since_seq, limit=200
                                )
                                events = [_db_event_to_dict(e, project_id) for e in db_events]
                            # Send replay batch
                            await ws.send_json(
                                {
                                    "type": "replay_batch",
                                    "project_id": project_id,
                                    "events": events,
                                    "latest_sequence": event_bus.get_latest_sequence(project_id),
                                }
                            )
                            logger.debug(
                                "WebSocket [%s]: replayed %d events for project %s since seq %d",
                                ws_id,
                                len(events),
                                project_id,
                                since_seq,
                            )

                    elif msg_type == "get_history":
                        # Client requests full conversation history on reconnect.
                        # This loads all persisted messages from the ConversationStore
                        # so agents remember prior interactions without in-memory state.
                        project_id = data.get("project_id", "")
                        limit = data.get("limit", 200)
                        if not isinstance(project_id, str) or not _valid_project_id(project_id):
                            continue
                        if not isinstance(limit, int) or limit < 1:
                            limit = 200
                        limit = min(limit, 1000)
                        try:
                            _conv_id = await get_or_create_conversation_id(project_id)
                            _history = await load_history_on_connect(
                                project_id, _conv_id, limit=limit
                            )
                            await ws.send_json(
                                {
                                    "type": "conversation_history",
                                    "project_id": project_id,
                                    "conversation_id": _conv_id,
                                    "messages": _history,
                                    "count": len(_history),
                                }
                            )
                            logger.info(
                                "WebSocket [%s]: sent %d history messages for project %s conv %s",
                                ws_id,
                                len(_history),
                                project_id,
                                _conv_id,
                            )
                        except Exception as _hist_err:
                            logger.error(
                                "WebSocket [%s]: failed to load history for project %s: %s",
                                ws_id,
                                project_id,
                                _hist_err,
                            )
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "code": "HISTORY_LOAD_FAILED",
                                    "message": "Failed to load conversation history.",
                                    "project_id": project_id,
                                }
                            )

                    elif msg_type == "new_conversation":
                        # Client requests a fresh conversation (e.g. "New Chat" button).
                        project_id = data.get("project_id", "")
                        title = data.get("title", None)
                        if not isinstance(project_id, str) or not _valid_project_id(project_id):
                            continue
                        if title is not None and not isinstance(title, str):
                            title = None
                        try:
                            _conv_id = await start_new_conversation(project_id, title=title)
                            await ws.send_json(
                                {
                                    "type": "conversation_created",
                                    "project_id": project_id,
                                    "conversation_id": _conv_id,
                                }
                            )
                            logger.info(
                                "WebSocket [%s]: new conversation %s for project %s",
                                ws_id,
                                _conv_id,
                                project_id,
                            )
                        except Exception as _new_conv_err:
                            logger.error(
                                "WebSocket [%s]: failed to create conversation for project %s: %s",
                                ws_id,
                                project_id,
                                _new_conv_err,
                            )

                    elif msg_type == "get_task_status":
                        # Client polls the status of a specific task_id.
                        # Response: {"type": "task_status", "task_id": "...", "status": "...", ...}
                        project_id = data.get("project_id", "")
                        task_id_req = data.get("task_id", "")
                        if not isinstance(project_id, str) or not _valid_project_id(project_id):
                            continue
                        if not isinstance(task_id_req, str) or not task_id_req.isalnum():
                            continue
                        try:
                            from src.workers.task_queue import TaskQueueRegistry as _TQR

                            _tq_registry = _TQR.get_registry()
                            _task_queue = await _tq_registry.get_queue(project_id)
                            if _task_queue:
                                _task_rec = await _task_queue.get_task(task_id_req)
                                if _task_rec:
                                    await ws.send_json(
                                        {
                                            "type": "task_status",
                                            **_task_rec.to_dict(),
                                        }
                                    )
                                    continue
                            await ws.send_json(
                                {
                                    "type": "error",
                                    "code": "TASK_NOT_FOUND",
                                    "task_id": task_id_req,
                                    "message": "Task not found.",
                                }
                            )
                        except Exception as _ts_err:
                            logger.warning(
                                "WebSocket [%s]: get_task_status error: %s", ws_id, _ts_err
                            )

                    elif msg_type == "get_task_history":
                        # Client requests full conversation history for a specific task.
                        # The task's conversation_id is used to load the transcript.
                        project_id = data.get("project_id", "")
                        task_id_req = data.get("task_id", "")
                        limit = data.get("limit", 200)
                        if not isinstance(project_id, str) or not _valid_project_id(project_id):
                            continue
                        if not isinstance(task_id_req, str) or not task_id_req.isalnum():
                            continue
                        if not isinstance(limit, int) or limit < 1:
                            limit = 200
                        limit = min(limit, 1000)
                        try:
                            from src.workers.task_queue import TaskQueueRegistry as _TQR

                            _tq_registry = _TQR.get_registry()
                            _task_queue = await _tq_registry.get_queue(project_id)
                            _task_conv_id = None
                            if _task_queue:
                                _task_rec = await _task_queue.get_task(task_id_req)
                                if _task_rec:
                                    _task_conv_id = _task_rec.conversation_id
                            if _task_conv_id:
                                _task_history = await load_history_on_connect(
                                    project_id, _task_conv_id, limit=limit
                                )
                                await ws.send_json(
                                    {
                                        "type": "task_history",
                                        "project_id": project_id,
                                        "task_id": task_id_req,
                                        "conversation_id": _task_conv_id,
                                        "messages": _task_history,
                                        "count": len(_task_history),
                                    }
                                )
                            else:
                                await ws.send_json(
                                    {
                                        "type": "error",
                                        "code": "TASK_CONV_NOT_READY",
                                        "task_id": task_id_req,
                                        "message": "Task conversation not yet created (still queued?).",
                                    }
                                )
                        except Exception as _th_err:
                            logger.error(
                                "WebSocket [%s]: get_task_history error: %s",
                                ws_id,
                                _th_err,
                                exc_info=True,
                            )

                    else:
                        # ARCH-06: Log unknown message types for debugging
                        logger.debug("WebSocket [%s]: unknown message type %r", ws_id, msg_type)

                except WebSocketDisconnect:
                    logger.info("WebSocket [%s]: client disconnected (receive)", ws_id)
                    break
                except Exception as _recv_err:
                    logger.warning(
                        "WebSocket [%s]: receive error — %s",
                        ws_id,
                        _recv_err,
                    )
                    break

        try:
            # Run sender, heartbeat, and receiver concurrently via asyncio.gather.
            # When any one coroutine raises (WebSocketDisconnect, send error, etc.)
            # asyncio.gather propagates the exception and cancels the others.
            #
            # CancelledError is re-raised explicitly so that graceful server
            # shutdown (which cancels this task) is never swallowed by the
            # generic `except Exception` handler below.
            await asyncio.gather(_sender(), _heartbeat(), _receiver())
        except asyncio.CancelledError:
            # Server is shutting down — propagate cancellation so the event loop
            # can clean up properly.  Do NOT send an error frame here because the
            # WebSocket may already be in the process of closing.
            logger.info("WebSocket [%s]: handler cancelled (server shutdown)", ws_id)
            raise
        except WebSocketDisconnect:
            logger.info("WebSocket [%s]: disconnected", ws_id)
        except Exception as e:
            logger.error("WebSocket [%s]: unexpected error — %s", ws_id, e, exc_info=True)
            # Attempt to send a structured error frame before closing so the
            # client knows the close was abnormal and can adapt its retry delay.
            try:
                await ws.send_json(
                    {
                        "type": "error",
                        "code": "INTERNAL_ERROR",
                        "message": "An unexpected server error occurred. Please reconnect.",
                        "reconnect_after_ms": 2000,
                    }
                )
            except Exception as _ws_close_err:
                logger.debug(
                    "WebSocket [%s]: error frame suppressed (connection already broken): %s",
                    ws_id,
                    _ws_close_err,
                )
        finally:
            await event_bus.unsubscribe(queue)
            await event_bus._flush_write_queue()
            logger.info("WebSocket [%s]: connection closed, unsubscribed from event bus", ws_id)

    # --- Static files (production build) ---

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        # Cache the index.html template with the auth token injected.
        # When AUTH_ENABLED, inject a <meta> tag so the frontend JS can
        # read the API key without the user having to enter it manually.
        # This is safe because if a client can load the HTML, they're
        # already on the network and authorised to use the dashboard.
        _index_html_path = frontend_dist / "index.html"
        _index_html_cache: str | None = None

        def _get_index_html() -> str:
            nonlocal _index_html_cache
            if _index_html_cache is None:
                raw = _index_html_path.read_text(encoding="utf-8")
                if AUTH_ENABLED and DASHBOARD_API_KEY:
                    # Inject auth token as a <meta> tag before </head>
                    meta_tag = f'<meta name="hivemind-auth-token" content="{html.escape(DASHBOARD_API_KEY, quote=True)}">'
                    raw = raw.replace("</head>", f"  {meta_tag}\n  </head>", 1)
                _index_html_cache = raw
            return _index_html_cache

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """Serve the single-page application for all non-API routes."""
            file_path = (frontend_dist / full_path).resolve()
            if (
                full_path
                and file_path.is_relative_to(frontend_dist.resolve())
                and file_path.exists()
                and file_path.is_file()
            ):
                # Hashed assets (e.g. index-Abc123.js) can be cached forever
                if full_path.startswith("assets/"):
                    return FileResponse(
                        file_path, headers={"Cache-Control": "public, max-age=31536000, immutable"}
                    )
                return FileResponse(
                    file_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
                )
            # SPA fallback — serve index.html (with injected auth token if enabled)
            from starlette.responses import HTMLResponse

            return HTMLResponse(
                content=_get_index_html(),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        # Note: /assets/* is already handled by the serve_spa catch-all above,
        # which sets the correct immutable Cache-Control headers. No additional
        # StaticFiles mount is needed.

    return app
