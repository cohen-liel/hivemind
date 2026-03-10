"""FastAPI dashboard backend — REST endpoints + WebSocket for the agent dashboard."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from dashboard.events import event_bus
import state

logger = logging.getLogger(__name__)

# Valid project_id: lowercase letters, digits, hyphens — max 128 chars
_PROJECT_ID_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{0,126}[a-z0-9]$|^[a-z0-9]$')


def _valid_project_id(project_id: str) -> bool:
    """Return True if project_id matches the expected slug format."""
    return bool(_PROJECT_ID_RE.match(project_id))


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


# --- Request / response models ---

def _max_msg_len() -> int:
    """Lazy import to avoid circular import at module load time."""
    from config import MAX_USER_MESSAGE_LENGTH
    return MAX_USER_MESSAGE_LENGTH


class SendMessageRequest(BaseModel):
    message: str

    @field_validator('message')
    @classmethod
    def validate_message_length(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError('message cannot be empty')
        limit = _max_msg_len()
        if len(v) > limit:
            raise ValueError(f'message too long ({len(v)} chars). Maximum is {limit}.')
        return v


class TalkAgentRequest(BaseModel):
    message: str

    @field_validator('message')
    @classmethod
    def validate_message_length(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError('message cannot be empty')
        limit = _max_msg_len()
        if len(v) > limit:
            raise ValueError(f'message too long ({len(v)} chars). Maximum is {limit}.')
        return v


class CreateProjectRequest(BaseModel):
    name: str = Field(max_length=200)
    directory: str = Field(max_length=1000)
    agents_count: int = Field(default=2, ge=1, le=20)
    description: str = Field(default="", max_length=2000)


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    agents_count: int | None = None


class UpdateSettingsRequest(BaseModel):
    max_turns_per_cycle: int | None = None
    max_budget_usd: float | None = None
    agent_timeout_seconds: int | None = None
    sdk_max_turns_per_query: int | None = None
    sdk_max_budget_per_query: float | None = None
    max_user_message_length: int | None = None
    max_orchestrator_loops: int | None = None


class CreateScheduleRequest(BaseModel):
    project_id: str
    schedule_time: str
    task_description: str
    user_id: int = 0
    repeat: str = "once"

    @field_validator('project_id')
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        if not _PROJECT_ID_RE.match(v):
            raise ValueError('Invalid project_id format')
        return v

    @field_validator('repeat')
    @classmethod
    def validate_repeat(cls, v: str) -> str:
        if v not in ('once', 'daily', 'hourly'):
            raise ValueError('repeat must be once, daily, or hourly')
        return v

    @field_validator('task_description')
    @classmethod
    def validate_task_description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError('task_description cannot be empty')
        if len(v) > 2000:
            raise ValueError('task_description too long (max 2000 chars)')
        return v


# --- Helpers using state module ---

def _find_manager(project_id: str):
    """Find an OrchestratorManager by project_id across all users.

    Returns (None, None) for invalid project_id formats to prevent injection.
    """
    if not _valid_project_id(project_id):
        return None, None
    return state.get_manager(project_id)


def _manager_to_dict(manager, project_id: str) -> dict:
    """Serialize an OrchestratorManager to a JSON-friendly dict."""
    last_message = None
    if manager.conversation_log:
        last = manager.conversation_log[-1]
        last_message = {
            "agent_name": last.agent_name,
            "role": last.role,
            "content": last.content[:200],
            "timestamp": last.timestamp,
            "cost_usd": last.cost_usd,
        }

    if manager.is_running:
        status = "running"
    elif manager.is_paused:
        status = "paused"
    else:
        status = "idle"

    return {
        "project_id": project_id,
        "project_name": manager.project_name,
        "project_dir": manager.project_dir,
        "status": status,
        "is_running": manager.is_running,
        "is_paused": manager.is_paused,
        "turn_count": manager.turn_count,
        "total_cost_usd": manager.total_cost_usd,
        "agents": manager.agent_names,
        "multi_agent": manager.is_multi_agent,
        "last_message": last_message,
        # Live agent states — survives browser refresh
        "agent_states": manager.agent_states,
        "current_agent": manager.current_agent,
        "current_tool": manager.current_tool,
        # Queue status — so frontend knows about pending messages
        "pending_messages": manager._message_queue.qsize(),
        "pending_approval": manager.pending_approval,
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
        await event_bus.publish({
            "type": "agent_update",
            "project_id": project_id,
            "project_name": project_name,
            "agent": manager.current_agent or "orchestrator",
            "text": text,
            "timestamp": time.time(),
        })

    async def on_result(text: str):
        await event_bus.publish({
            "type": "agent_result",
            "project_id": project_id,
            "project_name": project_name,
            "text": text,
            "timestamp": time.time(),
        })

    async def on_final(text: str):
        await event_bus.publish({
            "type": "agent_final",
            "project_id": project_id,
            "project_name": project_name,
            "text": text,
            "timestamp": time.time(),
        })

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
    manager, _ = _find_manager(project_id)
    if manager:
        return manager.project_dir
    if state.session_mgr:
        db_project = await state.session_mgr.load_project(project_id)
        if db_project:
            return db_project.get("project_dir", "")
    return None


def create_app() -> FastAPI:
    """Create and configure the FastAPI dashboard application."""

    app = FastAPI(title="Agent Dashboard", docs_url="/api/docs")

    # Per-project locks to prevent duplicate manager creation under concurrent requests
    _manager_creation_locks: dict[str, asyncio.Lock] = {}
    _manager_creation_locks_lock = asyncio.Lock()

    async def _get_or_create_manager_lock(project_id: str) -> asyncio.Lock:
        async with _manager_creation_locks_lock:
            if project_id not in _manager_creation_locks:
                _manager_creation_locks[project_id] = asyncio.Lock()
            return _manager_creation_locks[project_id]

    # CORS — configurable via CORS_ORIGINS env var
    from config import CORS_ORIGINS, AUTH_ENABLED
    dashboard_host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    if "*" in CORS_ORIGINS:
        logger.warning(
            "CORS is configured with wildcard origin (*). "
            "Set CORS_ORIGINS env var to restrict access in production."
        )
    # Warn loudly when binding on all interfaces without authentication
    if dashboard_host not in ("127.0.0.1", "localhost") and not AUTH_ENABLED:
        logger.warning(
            "⚠️  SECURITY WARNING: Server is bound to %s (all interfaces) but "
            "DASHBOARD_API_KEY is not set. Any host on the network can fully "
            "control agents, spend budget, and browse project files. "
            "Set DASHBOARD_API_KEY in your .env to enable authentication.",
            dashboard_host,
        )
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
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    # --- Optional API key middleware ---
    DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
    if DASHBOARD_API_KEY:
        @app.middleware("http")
        async def check_api_key(request: Request, call_next):
            if request.url.path.startswith("/api/") and request.url.path not in ("/api/health", "/api/stats"):
                key = request.headers.get("X-API-Key", "")
                if not hmac.compare_digest(key, DASHBOARD_API_KEY):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return await call_next(request)

    # --- Request body size limit ---
    from config import MAX_REQUEST_BODY_SIZE
    _MAX_BODY_SIZE = MAX_REQUEST_BODY_SIZE

    @app.middleware("http")
    async def body_size_limit(request: Request, call_next):
        """Reject requests with oversized bodies to prevent memory exhaustion."""
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                cl = int(content_length)
            except (ValueError, TypeError):
                return JSONResponse(
                    {"error": "Invalid Content-Length header."},
                    status_code=400,
                )
            if cl > _MAX_BODY_SIZE:
                return JSONResponse(
                    {"error": f"Request body too large. Maximum is {_MAX_BODY_SIZE // 1024}KB."},
                    status_code=413,
                )
        return await call_next(request)

    # --- Rate limiting middleware ---
    # Simple in-memory rate limiter per IP address with TTL-based cleanup
    _rate_limit_store: dict[str, list[float]] = {}  # ip -> list of timestamps
    _RATE_LIMIT_WINDOW = 60  # seconds
    _RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120"))  # per window
    _RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "30"))  # max burst in 5s
    _RATE_LIMIT_EXEMPT = {"/api/health", "/api/stats"}  # endpoints exempt from rate limiting
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
        stale_ips = [
            ip for ip, ts in _rate_limit_store.items()
            if not ts or now - ts[-1] > ttl
        ]
        for ip in stale_ips:
            del _rate_limit_store[ip]
        if stale_ips:
            logger.debug("Rate limiter cleanup: evicted %d stale IPs, %d remaining",
                         len(stale_ips), len(_rate_limit_store))

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        """Per-IP rate limiting with sliding window and burst protection."""
        nonlocal _RATE_LIMIT_REQUEST_COUNT

        # Skip non-API routes and exempt endpoints
        if not request.url.path.startswith("/api/") or request.url.path in _RATE_LIMIT_EXEMPT:
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
            logger.warning(f"Rate limit exceeded for {client_ip}: {len(timestamps)} requests in {_RATE_LIMIT_WINDOW}s")
            return JSONResponse(
                {"error": "Rate limit exceeded. Please slow down."},
                status_code=429,
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )

        # Check burst limit (last 5 seconds)
        recent_burst = sum(1 for t in timestamps if now - t < 5)
        if recent_burst >= _RATE_LIMIT_BURST:
            logger.warning(f"Burst limit exceeded for {client_ip}: {recent_burst} requests in 5s")
            return JSONResponse(
                {"error": "Too many requests in a short time. Please wait a moment."},
                status_code=429,
                headers={"Retry-After": "5"},
            )

        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps

        # Increment request counter
        _RATE_LIMIT_REQUEST_COUNT += 1

        # TTL-based cleanup: every 500 requests or when store exceeds 500 entries
        if (_RATE_LIMIT_REQUEST_COUNT % _RATE_LIMIT_CLEANUP_INTERVAL == 0
                or len(_rate_limit_store) > _RATE_LIMIT_MAX_STORE_SIZE):
            _rate_limit_cleanup(now)

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, _RATE_LIMIT_MAX_REQUESTS - len(timestamps))
        )
        return response

    # --- Request ID + logging middleware for tracing ---
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        """Attach a unique request_id and log method/path/duration for every API request."""
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        # Log API requests (skip static assets for cleaner logs)
        if request.url.path.startswith("/api/"):
            logger.info(
                f"[{request_id}] {request.method} {request.url.path} "
                f"→ {response.status_code} ({duration_ms:.0f}ms)"
            )
        return response

    # --- Health check ---

    @app.get("/api/health")
    async def health_check():
        """Enhanced health check — DB, CLI binary, disk space, and active sessions."""
        import shutil as _shutil
        from config import CLAUDE_CLI_PATH, STORE_DIR

        # DB connectivity check
        db_status = "error"
        if state.session_mgr is not None:
            try:
                db_status = "ok" if await state.session_mgr.is_healthy() else "error"
            except Exception:
                db_status = "error"

        # Claude CLI binary existence check
        cli_path = CLAUDE_CLI_PATH
        if os.sep not in cli_path and "/" not in cli_path:
            import shlex as _shlex, shutil as _shutil2
            cli_status = "ok" if _shutil2.which(cli_path) else "missing"
        else:
            cli_status = "ok" if os.path.isfile(cli_path) else "missing"

        # Disk space check on the data directory
        try:
            usage = _shutil.disk_usage(str(STORE_DIR))
            disk_free_gb = round(usage.free / (1024 ** 3), 2)
        except Exception:
            disk_free_gb = -1.0

        # Active sessions count
        active_count = sum(
            len(sessions) for sessions in state.active_sessions.values()
        )

        overall = "ok" if db_status == "ok" and cli_status == "ok" else "degraded"

        return {
            "status": overall,
            "db": db_status,
            "cli": cli_status,
            "disk_free_gb": disk_free_gb,
            "active_sessions": active_count,
        }

    # --- REST Endpoints ---

    @app.get("/api/projects")
    async def list_projects():
        """List all projects with live status from active_sessions + DB."""
        active_managers = state.get_all_managers()

        # Build map of active projects
        active_map = {}
        for user_id, project_id, manager in active_managers:
            active_map[project_id] = _manager_to_dict(manager, project_id)
            active_map[project_id]["user_id"] = user_id

        # Get all projects from DB
        db_projects = await state.session_mgr.list_projects() if state.session_mgr else []

        projects = []
        seen = set()

        # Active projects first
        for project_id, data in active_map.items():
            seen.add(project_id)
            # Enrich with DB info
            for dbp in db_projects:
                if dbp["project_id"] == project_id:
                    data["description"] = dbp.get("description", "")
                    data["created_at"] = dbp.get("created_at", 0)
                    data["updated_at"] = dbp.get("updated_at", 0)
                    data["message_count"] = dbp.get("message_count", 0)
                    break
            projects.append(data)

        # DB-only projects (not currently active)
        for dbp in db_projects:
            pid = dbp["project_id"]
            if pid not in seen:
                projects.append({
                    "project_id": pid,
                    "project_name": dbp["name"],
                    "project_dir": dbp.get("project_dir", ""),
                    "status": "idle",
                    "is_running": False,
                    "is_paused": False,
                    "turn_count": 0,
                    "total_cost_usd": 0,
                    "agents": [],
                    "multi_agent": False,
                    "last_message": None,
                    "user_id": dbp.get("user_id", 0),
                    "description": dbp.get("description", ""),
                    "created_at": dbp.get("created_at", 0),
                    "updated_at": dbp.get("updated_at", 0),
                    "message_count": dbp.get("message_count", 0),
                })

        return {"projects": projects}

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str):
        """Project detail: live agent states, config."""
        manager, user_id = _find_manager(project_id)

        if manager:
            data = _manager_to_dict(manager, project_id)
            data["user_id"] = user_id
            data["conversation_log"] = [
                {
                    "agent_name": m.agent_name,
                    "role": m.role,
                    "content": m.content[:500],
                    "timestamp": m.timestamp,
                    "cost_usd": m.cost_usd,
                }
                for m in manager.conversation_log[-50:]
            ]
        else:
            if not state.session_mgr:
                return JSONResponse({"error": "Not initialized"}, status_code=503)
            db_project = await state.session_mgr.load_project(project_id)
            if not db_project:
                return JSONResponse({"error": "Project not found"}, status_code=404)

            # Load recent messages from DB so they show on refresh
            recent_msgs = await state.session_mgr.get_recent_messages(project_id, count=20)
            last_msg = recent_msgs[-1] if recent_msgs else None
            # Load saved orchestrator state for cost/turn info
            saved_orch = await state.session_mgr.load_orchestrator_state(project_id)
            # Count total messages
            _, total_msgs = await state.session_mgr.get_messages_paginated(project_id, limit=0, offset=0)

            data = {
                "project_id": project_id,
                "project_name": db_project["name"],
                "project_dir": db_project.get("project_dir", ""),
                "status": saved_orch.get("status", "idle") if saved_orch else "idle",
                "is_running": False,
                "is_paused": False,
                "turn_count": saved_orch.get("turn_count", 0) if saved_orch else 0,
                "total_cost_usd": saved_orch.get("total_cost_usd", 0) if saved_orch else 0,
                "agents": [],
                "multi_agent": False,
                "last_message": last_msg,
                "user_id": db_project.get("user_id", 0),
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

        manager, user_id = _find_manager(project_id)
        if not manager:
            # Fallback: try to load last known state from DB
            if state.session_mgr:
                saved = await state.session_mgr.load_orchestrator_state(project_id)
                if saved and saved.get("status") in ("running", "interrupted", "completed"):
                    return {
                        "status": saved.get("status", "idle"),
                        "agent_states": saved.get("agent_states", {}),
                        "loop_progress": {
                            "loop": saved.get("current_loop", 0),
                            "turn": saved.get("turn_count", 0),
                            "max_turns": 0,
                            "cost": saved.get("total_cost_usd", 0),
                            "max_budget": 0,
                            "max_loops": 0,
                        } if saved.get("current_loop") else None,
                        "shared_context_count": len(saved.get("shared_context", [])),
                        "pending_messages": 0,
                        "pending_approval": None,
                        # BUG FIX: include DAG fields so fallback path matches success path
                        "dag_graph": saved.get("dag_graph"),
                        "dag_task_statuses": saved.get("dag_task_statuses", {}),
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
            from config import MAX_TURNS_PER_CYCLE, MAX_BUDGET_USD, MAX_ORCHESTRATOR_LOOPS
            loop_progress = {
                "loop": getattr(manager, '_current_loop', 0),
                "turn": manager.turn_count,
                "max_turns": MAX_TURNS_PER_CYCLE,
                "cost": manager.total_cost_usd,
                "max_budget": MAX_BUDGET_USD,
                "max_loops": MAX_ORCHESTRATOR_LOOPS,
            }

        return {
            "status": "running" if manager.is_running else ("paused" if manager.is_paused else "idle"),
            "agent_states": manager.agent_states,
            "current_agent": manager.current_agent,
            "current_tool": manager.current_tool,
            "loop_progress": loop_progress,
            "shared_context_count": len(manager.shared_context),
            "shared_context_preview": [c[:200] for c in manager.shared_context[-5:]],
            "pending_messages": manager._message_queue.qsize(),
            "pending_approval": manager.pending_approval,
            "background_tasks": len(manager._background_tasks),
            "turn_count": manager.turn_count,
            "total_cost_usd": manager.total_cost_usd,
            "dag_graph": getattr(manager, '_current_dag_graph', None),
            "dag_task_statuses": getattr(manager, '_dag_task_statuses', {}),
            "diagnostics": diagnostics,
        }

    @app.put("/api/projects/{project_id}")
    async def update_project(project_id: str, req: UpdateProjectRequest):
        """Update project settings (name, description, agents_count)."""
        if not state.session_mgr:
            return JSONResponse({"error": "Not initialized"}, status_code=503)

        db_project = await state.session_mgr.load_project(project_id)
        if not db_project:
            return JSONResponse({"error": "Project not found"}, status_code=404)

        if req.name is not None:
            name = req.name.strip()
            if not name or not state.PROJECT_NAME_RE.match(name):
                return JSONResponse({"error": "Invalid project name"}, status_code=400)
            await state.session_mgr.update_project_fields(project_id, name=name)
            # Update in-memory manager name if active
            manager, _ = _find_manager(project_id)
            if manager:
                manager.project_name = name

        if req.description is not None:
            await state.session_mgr.update_project_fields(project_id, description=req.description)

        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "updated",
        })

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
        manager, _ = _find_manager(project_id)
        if manager:
            result["live"] = {
                "status": "running" if manager.is_running else ("paused" if manager.is_paused else "idle"),
                "agent_states": manager.agent_states,
                "current_agent": manager.current_agent,
                "turn_count": manager.turn_count,
                "total_cost_usd": manager.total_cost_usd,
                "pending_messages": manager._message_queue.qsize(),
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
            events = await state.session_mgr.get_activity_since(project_id, since_sequence=0, limit=50)
            result["recent_activity"] = events
            result["total_activity_events"] = await state.session_mgr.get_latest_sequence(project_id)

        # 5. Saved orchestrator state (crash recovery)
        if state.session_mgr:
            orch_state = await state.session_mgr.load_orchestrator_state(project_id)
            result["orchestrator_state"] = orch_state or {}

        return result

    @app.get("/api/projects/{project_id}/agents")
    async def get_project_agents(project_id: str):
        """Detailed agent info with individual stats."""
        manager, _ = _find_manager(project_id)
        if not manager:
            return {"agents": []}

        agents = []
        for agent_name in manager.agent_names:
            # Compute per-agent stats from conversation log
            agent_msgs = [m for m in manager.conversation_log if m.agent_name == agent_name]
            agent_cost = sum(m.cost_usd for m in agent_msgs)
            agent_turns = len(agent_msgs)
            last_activity = agent_msgs[-1].content[:200] if agent_msgs else ""
            last_timestamp = agent_msgs[-1].timestamp if agent_msgs else 0

            # Live state from orchestrator tracking
            live_state = manager.agent_states.get(agent_name, {})

            agents.append({
                "name": agent_name,
                "cost_usd": agent_cost,
                "turns": agent_turns,
                "last_activity": last_activity,
                "last_timestamp": last_timestamp,
                "state": live_state.get("state", "idle"),
                "current_tool": live_state.get("current_tool", ""),
                "task": live_state.get("task", ""),
                "duration": live_state.get("duration", 0),
            })

        return {"agents": agents}

    @app.get("/api/projects/{project_id}/messages")
    async def get_messages(project_id: str, limit: int = 50, offset: int = 0):
        """Conversation history (paginated, from DB)."""
        limit = max(1, min(limit, 500))   # clamp: 1–500
        offset = max(0, offset)
        if not state.session_mgr:
            return {"messages": [], "total": 0}
        messages, total = await state.session_mgr.get_messages_paginated(project_id, limit, offset)
        return {"messages": messages, "total": total}

    @app.get("/api/projects/{project_id}/files")
    async def get_files(project_id: str):
        """Git diff + git status in project dir."""
        manager, _ = _find_manager(project_id)

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
                    "git", *args,
                    cwd=project_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return stdout.decode("utf-8", errors="replace")

            stat_out = await _run_git("diff", "--stat", "HEAD")
            status_out = await _run_git("status", "--short")
            diff_out = await _run_git("diff", "HEAD", timeout=10.0)
            return {
                "stat": stat_out.strip(),
                "status": status_out.strip(),
                "diff": diff_out[:50000],
            }
        except Exception as e:
            logger.error("Git operation failed for %s: %s", project_id, e, exc_info=True)
            return {"error": "An internal error occurred. Check server logs for details."}
    async def get_tasks(project_id: str):
        """Task history from DB."""
        if not state.session_mgr:
            return {"tasks": []}
        tasks = await state.session_mgr.get_project_tasks(project_id)
        return {"tasks": tasks}

    @app.post("/api/projects")
    async def create_project(req: CreateProjectRequest):
        """Create a new project from the web dashboard."""
        name = req.name.strip()
        if not name or not state.PROJECT_NAME_RE.match(name):
            return JSONResponse({"error": "Invalid project name. Use letters, numbers, spaces, hyphens, underscores."}, status_code=400)

        directory = req.directory.strip()
        if not directory:
            return JSONResponse({"error": "Directory is required."}, status_code=400)

        if not state.session_mgr:
            return JSONResponse({"error": "Not initialized"}, status_code=503)

        project_dir = os.path.expanduser(directory)

        # Security: restrict directory creation to home dir or configured projects base
        from config import PROJECTS_BASE_DIR
        resolved_dir = Path(project_dir).resolve()
        home = Path.home().resolve()
        projects_base = PROJECTS_BASE_DIR.resolve()
        allowed_roots = [home, projects_base]
        if not any(resolved_dir.is_relative_to(root) for root in allowed_roots):
            return JSONResponse(
                {"error": "Project directory must be within your home directory or configured projects base."},
                status_code=403,
            )

        try:
            os.makedirs(project_dir, exist_ok=True)
        except OSError as e:
            return JSONResponse({"error": f"Cannot create directory: {e}"}, status_code=400)

        project_id = name.lower().replace(" ", "-")
        existing = await state.session_mgr.load_project(project_id)
        if existing:
            project_id = f"{project_id}-{uuid.uuid4().hex[:6]}"

        user_id = 0  # Web-created projects use user_id=0

        await state.session_mgr.save_project(
            project_id=project_id,
            user_id=user_id,
            name=name,
            description=req.description or f"Project: {name}",
            project_dir=project_dir,
        )

        # Create and register the manager
        manager = _create_web_manager(
            project_id=project_id,
            project_name=name,
            project_dir=project_dir,
            user_id=user_id,
            agents_count=req.agents_count,
        )
        if manager:
            await state.register_manager(user_id, project_id, manager)

        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "idle",
        })

        return {"ok": True, "project_id": project_id}

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str):
        """Delete a project."""
        manager, user_id = _find_manager(project_id)
        if manager:
            if manager.is_running:
                await manager.stop()
            if user_id is not None:
                await state.unregister_manager(user_id, project_id)

        if state.session_mgr:
            await state.session_mgr.delete_project(project_id)

        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "deleted",
        })

        return {"ok": True}

    @app.post("/api/projects/{project_id}/clear-history")
    async def clear_project_history(project_id: str):
        """Clear all messages and task history for a project, starting fresh."""
        manager, _ = _find_manager(project_id)
        if manager and manager.is_running:
            return JSONResponse({"error": "Cannot clear history while project is running"}, status_code=400)

        if state.session_mgr:
            await state.session_mgr.clear_project_data(project_id)

        # Reset ALL live state on active manager — full context wipe.
        # Without this, agents resume with stale context from previous sessions.
        if manager:
            # Core state
            manager.shared_context = []
            manager.conversation_log = []
            manager.turn_count = 0
            manager.total_cost_usd = 0.0
            manager.agent_states = {}

            # Agent tracking
            manager._completed_rounds = []
            manager._agents_used = set()

            # DAG state
            manager._current_dag_graph = None
            manager._dag_task_statuses = {}

            # Message queue (drain any pending messages)
            while not manager._message_queue.empty():
                try:
                    manager._message_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # SDK session IDs — forces agents to start fresh sessions
            # (otherwise they resume with context from cleared conversations)
            if hasattr(manager, 'session_mgr') and manager.session_mgr:
                await manager.session_mgr.invalidate_all_sessions(project_id)

            logger.info(
                f"[{project_id}] Full context reset: conversation_log, "
                f"completed_rounds, agents_used, dag_graph, message_queue, "
                f"SDK sessions all cleared"
            )

        # Notify connected clients so UI updates in real-time
        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "idle",
        })
        await event_bus.publish({
            "type": "history_cleared",
            "project_id": project_id,
        })

        return {"ok": True}

    @app.post("/api/projects/{project_id}/start")
    async def start_project(project_id: str):
        """Start/activate a dormant project."""
        manager, _ = _find_manager(project_id)
        if manager:
            return {"ok": True, "message": "Project already active"}

        if not state.session_mgr:
            return JSONResponse({"error": "Not initialized"}, status_code=503)

        db_project = await state.session_mgr.load_project(project_id)
        if not db_project:
            return JSONResponse({"error": "Project not found"}, status_code=404)

        user_id = db_project.get("user_id", 0)
        project_name = db_project["name"]
        project_dir = db_project.get("project_dir", "")

        if not project_dir or not Path(project_dir).exists():
            return JSONResponse({"error": f"Project directory not found: {project_dir}"}, status_code=400)

        manager = _create_web_manager(
            project_id=project_id,
            project_name=project_name,
            project_dir=project_dir,
            user_id=user_id,
            agents_count=2,
        )
        if manager:
            await state.register_manager(user_id, project_id, manager)

        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "idle",
        })

        return {"ok": True}

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
            effective_budget = req.max_budget_usd if req.max_budget_usd is not None else cfg.MAX_BUDGET_USD
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
            return JSONResponse({"error": "; ".join(errors)}, status_code=400)

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
            return JSONResponse({"error": "Expected a JSON object"}, status_code=400)

        # Reject any keys not in the whitelist
        rejected = set(data.keys()) - _ALLOWED_PERSIST_KEYS
        if rejected:
            return JSONResponse(
                {"error": f"Disallowed settings keys: {', '.join(sorted(rejected))}"},
                status_code=400,
            )

        # Clamp numeric settings to sane bounds
        NUMERIC_BOUNDS = {
            "max_budget_usd": (0.1, 500.0),
            "max_turns_per_cycle": (1, 500),
            "max_task_budget_usd": (0.1, 100.0),
        }
        for key, (lo, hi) in NUMERIC_BOUNDS.items():
            if key in data and isinstance(data[key], (int, float)):
                data[key] = max(lo, min(float(data[key]), hi))

        overrides_path = Path("data/settings_overrides.json")
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        # Merge with existing overrides
        existing = {}
        if overrides_path.exists():
            try:
                existing = json_mod.loads(overrides_path.read_text())
            except Exception:
                pass
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
        if not any(
            target == root or target.is_relative_to(root)
            for root in allowed_roots
        ):
            return JSONResponse(
                {"error": "Access denied: browsing is restricted to your home directory"},
                status_code=403,
            )

        if not target.exists():
            return {"error": "Path not found", "entries": []}
        if not target.is_dir():
            target = target.parent

        entries = []
        try:
            for item in sorted(target.iterdir()):
                if item.name.startswith('.'):
                    continue
                if item.is_dir():
                    entries.append({
                        "name": item.name,
                        "path": str(item),
                        "is_dir": True,
                    })
                if len(entries) >= 50:
                    break
        except PermissionError:
            return {"current": str(target), "parent": str(target.parent), "entries": [], "error": "Permission denied"}

        return {
            "current": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "entries": entries,
        }

    # --- Send Message + Talk Agent endpoints ---
    # Message length validation is handled at Pydantic model level
    # using config.MAX_USER_MESSAGE_LENGTH (default: 4000 chars)

    @app.post("/api/projects/{project_id}/message")
    async def send_message(project_id: str, req: SendMessageRequest):
        """Send message to orchestrator.

        Message length is validated at the Pydantic model level using
        config.MAX_USER_MESSAGE_LENGTH.
        """
        logger.info(f"[{project_id}] Received message: {req.message[:100]}")
        manager, _ = _find_manager(project_id)

        if not manager:
            # Try to activate from DB first
            logger.info(f"[{project_id}] No active manager, trying DB lookup...")
            if state.session_mgr:
                _proj_lock = await _get_or_create_manager_lock(project_id)
                async with _proj_lock:
                    # Re-check under the lock to avoid duplicate creation
                    manager, _ = _find_manager(project_id)
                    if not manager:
                        db_project = await state.session_mgr.load_project(project_id)
                        if db_project:
                            user_id = db_project.get("user_id", 0)
                            manager = _create_web_manager(
                                project_id=project_id,
                                project_name=db_project["name"],
                                project_dir=db_project.get("project_dir", ""),
                                user_id=user_id,
                                agents_count=2,
                            )
                            if manager:
                                await state.register_manager(user_id, project_id, manager)
                                logger.info(f"[{project_id}] Manager created from DB")

        if not manager:
            logger.error(f"[{project_id}] No manager found — cannot send message")
            return JSONResponse({"error": "Project not found."}, status_code=404)

        if not manager.is_running:
            logger.info(f"[{project_id}] Starting new session (multi_agent={manager.is_multi_agent})")
            await manager.start_session(req.message)
            return {"ok": True, "action": "started"}
        else:
            logger.info(f"[{project_id}] Injecting message into running session")
            await manager.inject_user_message("orchestrator", req.message)
            queue_size = manager._message_queue.qsize()
            return {"ok": True, "action": "queued", "queue_size": queue_size}

    @app.post("/api/projects/{project_id}/talk/{agent}")
    async def talk_agent(project_id: str, agent: str, req: TalkAgentRequest):
        """Send message to specific agent.

        Message length is validated at the Pydantic model level using
        config.MAX_USER_MESSAGE_LENGTH.
        """
        manager, _ = _find_manager(project_id)
        if not manager:
            return JSONResponse({"error": "Project not active."}, status_code=404)

        if agent not in manager.agent_names:
            return JSONResponse({"error": f"Unknown agent: {agent}. Available: {manager.agent_names}"}, status_code=400)

        await manager.inject_user_message(agent, req.message)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/pause")
    async def pause_project(project_id: str):
        """Pause project."""
        manager, _ = _find_manager(project_id)
        if not manager:
            return JSONResponse({"error": "Project not active"}, status_code=404)
        manager.pause()
        if state.session_mgr:
            await state.session_mgr.update_status(project_id, "paused")
        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "paused",
        })
        return {"ok": True}

    @app.post("/api/projects/{project_id}/resume")
    async def resume_project(project_id: str):
        """Resume project."""
        manager, _ = _find_manager(project_id)
        if not manager:
            return JSONResponse({"error": "Project not active"}, status_code=404)
        manager.resume()
        if state.session_mgr:
            await state.session_mgr.update_status(project_id, "active")
        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "running",
        })
        return {"ok": True}

    @app.post("/api/projects/{project_id}/stop")
    async def stop_project(project_id: str):
        """Stop project."""
        manager, _ = _find_manager(project_id)
        if not manager:
            return JSONResponse({"error": "Project not active"}, status_code=404)
        await manager.stop()
        if state.session_mgr:
            await state.session_mgr.update_status(project_id, "stopped")
        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "stopped",
        })
        return {"ok": True}

    @app.post("/api/projects/{project_id}/approve")
    async def approve_project(project_id: str):
        """Approve a pending HITL checkpoint."""
        manager, _ = _find_manager(project_id)
        if not manager:
            return JSONResponse({"error": "Project not active"}, status_code=404)
        if not manager.pending_approval:
            return JSONResponse({"error": "No pending approval"}, status_code=400)
        manager.approve()
        return {"ok": True}

    @app.post("/api/projects/{project_id}/reject")
    async def reject_project(project_id: str):
        """Reject a pending HITL checkpoint."""
        manager, _ = _find_manager(project_id)
        if not manager:
            return JSONResponse({"error": "Project not active"}, status_code=404)
        if not manager.pending_approval:
            return JSONResponse({"error": "No pending approval"}, status_code=400)
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
            return JSONResponse({"error": "DB not ready"}, status_code=500)
        # Validate schedule_time format
        if not re.match(r"^\d{2}:\d{2}$", req.schedule_time):
            return JSONResponse({"error": "schedule_time must be HH:MM format"}, status_code=400)
        h, m = int(req.schedule_time[:2]), int(req.schedule_time[3:])
        if h > 23 or m > 59:
            return JSONResponse({"error": "Invalid time: hours 0-23, minutes 0-59"}, status_code=400)
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
            return JSONResponse({"error": "DB not ready"}, status_code=500)
        deleted = await state.session_mgr.delete_schedule(schedule_id, user_id)
        if not deleted:
            return JSONResponse({"error": "Schedule not found"}, status_code=404)
        return {"ok": True}

    class SetBudgetRequest(BaseModel):
        budget_usd: float

    @app.put("/api/projects/{project_id}/budget")
    async def set_project_budget(project_id: str, req: SetBudgetRequest):
        """Set per-project budget with validation."""
        if req.budget_usd < 0:
            return JSONResponse({"error": "Budget cannot be negative"}, status_code=400)
        if req.budget_usd > 10_000:
            return JSONResponse({"error": "Budget exceeds maximum ($10,000)"}, status_code=400)
        if not state.session_mgr:
            return JSONResponse({"error": "DB not ready"}, status_code=500)
        await state.session_mgr.set_project_budget(project_id, req.budget_usd)
        return {"ok": True, "budget_usd": req.budget_usd}

    @app.get("/api/stats")
    async def get_stats():
        """Total cost, project count, active agents."""
        active_managers = state.get_all_managers()

        total_cost = sum(m.total_cost_usd for _, _, m in active_managers)
        running = sum(1 for _, _, m in active_managers if m.is_running)
        paused = sum(1 for _, _, m in active_managers if m.is_paused)

        db_projects = await state.session_mgr.list_projects() if state.session_mgr else []

        return {
            "total_cost_usd": total_cost,
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
            skip = {'.git', '__pycache__', 'node_modules', 'venv', '.venv', '.mypy_cache', '.pytest_cache', 'dist', 'build'}
            for item in sorted(root.iterdir()):
                if item.name.startswith('.') and item.name != '.env.example':
                    if item.name not in ('.github',):
                        continue
                if item.name in skip:
                    continue
                # Resolve to prevent symlink escapes
                resolved_item = item.resolve()
                if not resolved_item.is_relative_to(root):
                    continue  # Skip symlinks that escape project dir
                entry = {"name": item.name, "type": "dir" if item.is_dir() else "file", "path": item.name}
                if item.is_dir():
                    children = []
                    try:
                        for sub in sorted(item.iterdir()):
                            if sub.name.startswith('.') or sub.name in skip:
                                continue
                            # Resolve sub-entries too
                            resolved_sub = sub.resolve()
                            if not resolved_sub.is_relative_to(root):
                                continue
                            children.append({
                                "name": sub.name,
                                "type": "dir" if sub.is_dir() else "file",
                                "path": f"{item.name}/{sub.name}",
                            })
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

        Security: resolves symlinks before path check to prevent traversal attacks.
        Uses is_relative_to() (Python 3.9+) for safe containment check.
        """
        project_dir = await _resolve_project_dir(project_id)
        if not project_dir:
            return {"error": "Project not found"}

        file_path = Path(project_dir) / path
        try:
            # Resolve symlinks FIRST, then check containment
            file_path = file_path.resolve()
            proj_resolved = Path(project_dir).resolve()
            # Use is_relative_to for safe containment check (no prefix collisions)
            if not file_path.is_relative_to(proj_resolved):
                logger.warning(
                    "Path traversal blocked: %s tried to access %s (outside %s)",
                    project_id, file_path, proj_resolved,
                )
                return JSONResponse({"error": "Path traversal not allowed"}, status_code=403)
        except Exception:
            return JSONResponse({"error": "Invalid path"}, status_code=400)

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
            # Reconstruct full event format from DB rows
            full_events = []
            for e in events:
                evt = {
                    "type": e["event_type"],
                    "project_id": project_id,
                    "agent": e.get("agent", ""),
                    "timestamp": e["timestamp"],
                    "sequence_id": e["sequence_id"],
                    **(e.get("data", {})),
                }
                full_events.append(evt)
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

    @app.get("/api/cost-breakdown")
    async def get_cost_breakdown(project_id: str | None = None, days: int = 30):
        """Get cost breakdown by agent and by day."""
        days = max(1, min(days, 365))  # clamp: 1–365 days
        if not state.session_mgr:
            return {"by_agent": [], "by_day": [], "total_cost": 0, "total_runs": 0}
        return await state.session_mgr.get_cost_breakdown(project_id, days)

    @app.get("/api/cost-summary")
    async def get_cost_summary():
        """Get per-project cost summary for dashboard overview."""
        if not state.session_mgr:
            return {"projects": []}
        projects = await state.session_mgr.get_project_cost_summary()
        return {"projects": projects}

    # --- Interrupted Task Resume ---

    @app.get("/api/projects/{project_id}/resumable")
    async def get_resumable_task(project_id: str):
        """Check if a project has an interrupted task that can be resumed."""
        if not state.session_mgr:
            return {"resumable": False}
        task = await state.session_mgr.get_resumable_task(project_id)
        if task:
            return {
                "resumable": True,
                "task": {
                    "last_message": task.get("last_user_message", ""),
                    "current_loop": task.get("current_loop", 0),
                    "turn_count": task.get("turn_count", 0),
                    "total_cost_usd": task.get("total_cost_usd", 0),
                    "status": task.get("status", "interrupted"),
                },
            }
        return {"resumable": False}

    @app.post("/api/projects/{project_id}/resume-interrupted")
    async def resume_interrupted_task(project_id: str):
        """Resume an interrupted task from where it left off.

        Restores shared_context and agent_states from DB before restarting,
        and waits for the orchestrator task to actually start before returning.
        """
        if not state.session_mgr:
            return JSONResponse({"error": "Session manager not available"}, 500)

        task = await state.session_mgr.get_resumable_task(project_id)
        if not task:
            return JSONResponse({"error": "No resumable task found"}, 404)

        last_message = task.get("last_user_message", "")
        if not last_message:
            return JSONResponse({"error": "No task message to resume"}, 400)

        # Find or create manager
        manager, user_id = _find_manager(project_id)
        if not manager:
            # Try to create from DB
            project = await state.session_mgr.load_project(project_id)
            if not project:
                return JSONResponse({"error": "Project not found"}, 404)
            manager = _create_web_manager(
                project_id=project_id,
                project_name=project["name"],
                project_dir=project["project_dir"],
                user_id=project["user_id"],
                agents_count=2,
            )
            if manager:
                await state.register_manager(project["user_id"], project_id, manager)
                user_id = project["user_id"]

        if not manager:
            return JSONResponse({"error": "Failed to create manager"}, 500)

        if manager.is_running:
            return JSONResponse({"error": "Project is already running"}, 409)

        # ── Bug fix #2: Restore context from DB before restarting ──
        # Without this, the agent starts fresh with no memory of previous work.
        saved_context = task.get("shared_context", [])
        saved_agent_states = task.get("agent_states", {})
        if saved_context and isinstance(saved_context, list):
            manager.shared_context = saved_context
        if saved_agent_states and isinstance(saved_agent_states, dict):
            manager.agent_states = saved_agent_states
        # Restore cost/turn counters so budget tracking continues
        manager.total_cost_usd = task.get("total_cost_usd", 0.0)
        manager.turn_count = task.get("turn_count", 0)

        # Clear the interrupted state in DB (we've restored what we need)
        await state.session_mgr.clear_orchestrator_state(project_id)

        # Resume with a continuation message that includes context summary
        context_summary = ""
        if saved_context:
            context_summary = (
                f"\n\nRestored context from {len(saved_context)} previous entries. "
                f"Key agents used: {', '.join(saved_agent_states.keys()) if saved_agent_states else 'unknown'}."
            )
        resume_msg = (
            f"RESUME INTERRUPTED TASK — Continue from where you left off.\n\n"
            f"Original task: {last_message}\n\n"
            f"Previous progress: {task.get('current_loop', 0)} rounds completed, "
            f"${task.get('total_cost_usd', 0):.4f} spent."
            f"{context_summary}\n\n"
            f"Check the .nexus/todo.md and git log to understand current state, "
            f"then continue working."
        )

        # ── Bug fix #1: Use an Event to confirm the task has actually started ──
        # Without this, the frontend gets 200 OK but the task hasn't started yet.
        started_event = asyncio.Event()

        async def _run_and_signal():
            manager.is_running = True
            started_event.set()
            try:
                await manager._run_orchestrator(resume_msg)
            except Exception as exc:
                logger.error(f"Resume task error for {project_id}: {exc}")

        asyncio.create_task(_run_and_signal())

        # Wait up to 3 seconds for the task to actually start
        try:
            await asyncio.wait_for(started_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning(f"Resume task for {project_id}: start confirmation timed out")

        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "running",
        })

        return {"ok": True, "message": f"Resuming interrupted task: {last_message[:100]}"}

    @app.post("/api/projects/{project_id}/discard-interrupted")
    async def discard_interrupted_task(project_id: str):
        """Discard an interrupted task (user chose not to resume)."""
        if not state.session_mgr:
            return JSONResponse({"error": "Session manager not available"}, 500)
        await state.session_mgr.mark_task_discarded(project_id)
        return {"ok": True}

    # --- WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        """WebSocket for real-time event stream with ping/pong heartbeat.

        Supports event replay on reconnect: client sends
        {"type": "replay", "project_id": "...", "since_sequence": N}
        and receives all missed events before switching to live mode.

        When DASHBOARD_API_KEY is set (AUTH_ENABLED is True), the client must
        provide the key via query parameter ``?api_key=...`` to connect.
        """
        # Security: enforce API key on WebSocket when authentication is enabled
        if AUTH_ENABLED and DASHBOARD_API_KEY:
            client_key = ws.query_params.get("api_key", "")
            if not client_key:
                logger.warning("WebSocket connection rejected: no API key provided")
                await ws.close(code=4003, reason="Unauthorized: API key required")
                return
            if not hmac.compare_digest(client_key, DASHBOARD_API_KEY):
                logger.warning("WebSocket connection rejected: invalid API key")
                await ws.close(code=4003, reason="Unauthorized: invalid API key")
                return

        await ws.accept()
        queue = await event_bus.subscribe()

        async def _sender():
            """Forward events from bus to WebSocket client."""
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=120.0)
                        await ws.send_json(event)
                    except asyncio.TimeoutError:
                        break  # No events for 120s — client likely gone
                    except Exception:
                        break  # WebSocket send error — exit cleanly
            except asyncio.CancelledError:
                raise

        async def _heartbeat():
            """Send periodic pings to detect stale connections."""
            while True:
                await asyncio.sleep(30)
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    break

        async def _receiver():
            """Listen for client messages (pong, replay requests, future commands)."""
            while True:
                try:
                    data = await ws.receive_json()
                    msg_type = data.get("type", "")

                    if msg_type == "pong":
                        pass  # Connection is alive

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
                                events = [
                                    {
                                        "type": e["event_type"],
                                        "project_id": project_id,
                                        "agent": e.get("agent", ""),
                                        "timestamp": e["timestamp"],
                                        "sequence_id": e["sequence_id"],
                                        **(e.get("data", {})),
                                    }
                                    for e in db_events
                                ]
                            # Send replay batch
                            await ws.send_json({
                                "type": "replay_batch",
                                "project_id": project_id,
                                "events": events,
                                "latest_sequence": event_bus.get_latest_sequence(project_id),
                            })

                except Exception:
                    break

        try:
            # Run sender, heartbeat, and receiver concurrently
            # When any one fails (disconnect), all are cancelled
            await asyncio.gather(_sender(), _heartbeat(), _receiver())
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"WebSocket error: {e}")
        finally:
            await event_bus.unsubscribe(queue)

    # --- Static files (production build) ---

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            file_path = (frontend_dist / full_path).resolve()
            if full_path and file_path.is_relative_to(frontend_dist.resolve()) and file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(frontend_dist / "index.html")

        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    return app
