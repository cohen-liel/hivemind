"""FastAPI dashboard backend — app factory, middleware, and static file serving.

All endpoint handlers live in ``dashboard.routers.*`` modules.
This file is responsible for:
- Creating and configuring the FastAPI application
- Registering exception handlers (RFC 7807 Problem Detail)
- Applying middleware (CORS, security headers, device auth, body size, rate limiting, request ID)
- Including router modules
- Serving the SPA frontend (production build)
"""

from __future__ import annotations

import html
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

# Re-export shared models and helpers so existing ``from dashboard.api import ...``
# call-sites continue to work without changes.
from dashboard.routers import (  # noqa: F401
    _HTTP_TITLES,
    _PROJECT_ID_RE,
    CreateProjectRequest,
    CreateScheduleRequest,
    MessageRequest,
    NudgeRequest,
    SendMessageRequest,
    SetBudgetRequest,
    TalkAgentRequest,
    UpdateProjectRequest,
    UpdateSettingsRequest,
    _create_web_manager,
    _db_event_to_dict,
    _find_manager,
    _manager_status,
    _manager_to_dict,
    _max_msg_len,
    _problem,
    _resolve_project_dir,
    _sanitize_client_ip,
    _valid_project_id,
    event_bus,
)

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI dashboard application."""
    from src.api.history import history_router
    from src.api.projects import projects_router
    from src.api.tasks import admin_tasks_router, tasks_router
    from src.db.database import init_db
    from src.workers.task_queue import TaskQueueRegistry

    app = FastAPI(title="Agent Dashboard", docs_url="/api/docs")

    # --- Platform DB initialisation ---
    @app.on_event("startup")
    async def _init_platform_db():
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
        try:
            await TaskQueueRegistry.get_registry().stop_all()
            logger.info("Task queues stopped cleanly")
        except Exception as _tq_err:
            logger.warning("Error stopping task queues on shutdown: %s", _tq_err)

    # --- Exception handlers (RFC 7807 Problem Detail) ---

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
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
            {"type": "about:blank", "title": "Bad Request", "status": 400, "detail": detail},
            status_code=400,
        )

    from fastapi import HTTPException as _HTTPException

    @app.exception_handler(_HTTPException)
    async def http_exception_handler(request: Request, exc: _HTTPException):
        request_id = getattr(request.state, "request_id", "")
        status = exc.status_code
        title = _HTTP_TITLES.get(status, "HTTP Error")
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
            {"type": "about:blank", "title": title, "status": status, "detail": detail},
            status_code=status,
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
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

    # --- CORS ---
    from config import (
        AUTH_ENABLED,
        CORS_ORIGINS,
        DASHBOARD_API_KEY,
        DEVICE_AUTH_ENABLED,
        MAX_REQUEST_BODY_SIZE,
    )
    from config import DASHBOARD_HOST as _CFG_HOST

    dashboard_host = _CFG_HOST
    if "*" in CORS_ORIGINS:
        logger.warning(
            "CORS is configured with wildcard origin (*). "
            "Set CORS_ORIGINS env var to restrict access in production."
        )
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
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if not request.url.path.startswith("/assets/"):
            response.headers.setdefault(
                "Content-Security-Policy",
                (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                    "font-src 'self' https://fonts.gstatic.com data:; "
                    "img-src 'self' data: blob:; "
                    "connect-src 'self' ws: wss:; "
                    "frame-ancestors 'none'; "
                    "object-src 'none'; "
                    "base-uri 'self';"
                ),
            )
        return response

    # --- Device Token Authentication middleware ---
    from device_auth import COOKIE_NAME, DeviceAuthManager

    _device_auth = DeviceAuthManager()

    _AUTH_EXEMPT_PATHS = {
        "/api/health",
        "/api/ready",
        "/api/stats",
        "/api/auth/verify",
        "/api/auth/status",
        "/api/agent-registry",
    }

    @app.middleware("http")
    async def device_auth_middleware(request: Request, call_next):
        if not DEVICE_AUTH_ENABLED:
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") or path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)
        token = request.cookies.get(COOKIE_NAME, "")
        if not token:
            token = request.headers.get("X-Device-Token", "")
        if not token:
            token = request.headers.get("X-API-Key", "")
        if token and _device_auth.verify_device_token(token):
            return await call_next(request)
        return _problem(401, "Device not authorized. Please enter the access code.")

    # --- Request body size limit ---
    _MAX_BODY_SIZE = MAX_REQUEST_BODY_SIZE

    @app.middleware("http")
    async def body_size_limit(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
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
            body = b""
            async for chunk in request.stream():
                if len(body) + len(chunk) > _MAX_BODY_SIZE:
                    return _problem(
                        413,
                        f"Request body too large. Maximum is {_MAX_BODY_SIZE // 1024}KB.",
                    )
                body += chunk

            async def _receive():
                return {"type": "http.request", "body": body}

            request._receive = _receive

        return await call_next(request)

    # --- Rate limiting middleware ---
    _rate_limit_store: dict[str, list[float]] = {}
    _RATE_LIMIT_WINDOW = 60
    _RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "300"))
    _RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "100"))
    _RATE_LIMIT_EXEMPT = {
        "/api/health",
        "/api/ready",
        "/api/stats",
        "/api/agent-registry",
        "/api/projects",
        "/health",
    }
    _RATE_LIMIT_REQUEST_COUNT = 0
    _RATE_LIMIT_CLEANUP_INTERVAL = 500
    _RATE_LIMIT_MAX_STORE_SIZE = 500
    _RATE_LIMIT_TTL_MULTIPLIER = 3

    def _rate_limit_cleanup(now: float) -> None:
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
        nonlocal _RATE_LIMIT_REQUEST_COUNT
        path = request.url.path
        if not path.startswith("/api/") or path in _RATE_LIMIT_EXEMPT:
            return await call_next(request)
        if path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            raw_ip = forwarded_for.split(",")[0].strip()
            client_ip = _sanitize_client_ip(raw_ip)
        else:
            client_ip = request.client.host if request.client else "unknown"

        now = time.time()
        timestamps = _rate_limit_store.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]

        if len(timestamps) >= _RATE_LIMIT_MAX_REQUESTS:
            logger.warning(
                "Rate limit exceeded for %s: %d requests in %ds (method=%s path=%s)",
                client_ip,
                len(timestamps),
                _RATE_LIMIT_WINDOW,
                request.method,
                path,
            )
            return _problem(
                429,
                "Rate limit exceeded. Please slow down.",
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )

        recent_burst = sum(1 for t in timestamps if now - t < 5)
        burst_limit = _RATE_LIMIT_BURST
        if request.method == "DELETE":
            burst_limit = _RATE_LIMIT_BURST * 2
        if recent_burst >= burst_limit:
            logger.warning(
                "Burst limit exceeded for %s: %d requests in 5s (method=%s path=%s)",
                client_ip,
                recent_burst,
                request.method,
                path,
            )
            return _problem(
                429,
                "Too many requests in a short time. Please wait a moment.",
                headers={"Retry-After": "5"},
            )

        timestamps.append(now)
        _rate_limit_store[client_ip] = timestamps
        _RATE_LIMIT_REQUEST_COUNT += 1

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

    # --- Request ID + logging middleware ---
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        from dashboard.events import current_request_id as _req_id_var

        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        token = _req_id_var.set(request_id)
        start = time.time()
        try:
            response = await call_next(request)
        finally:
            _req_id_var.reset(token)
        duration_ms = (time.time() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/"):
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

    # --- Include routers ---
    from dashboard.routers.agents import router as agents_router
    from dashboard.routers.auth import router as auth_router
    from dashboard.routers.execution import router as execution_router
    from dashboard.routers.projects import router as projects_router_local
    from dashboard.routers.stats import router as stats_router
    from dashboard.routers.system import router as system_router

    app.include_router(system_router)
    app.include_router(stats_router)
    app.include_router(auth_router)
    app.include_router(projects_router_local)
    app.include_router(agents_router)
    app.include_router(execution_router)

    # External routers from src/api/
    app.include_router(history_router)
    app.include_router(projects_router)
    app.include_router(tasks_router)
    app.include_router(admin_tasks_router)

    # Plugin management
    from api.plugins import router as plugins_router

    app.include_router(plugins_router)

    # DAG state and history
    from api.dag import router as dag_router

    app.include_router(dag_router)

    # --- Static files (production build) ---
    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        _index_html_path = frontend_dist / "index.html"
        _index_html_cache: str | None = None

        def _get_index_html() -> str:
            nonlocal _index_html_cache
            if _index_html_cache is None:
                raw = _index_html_path.read_text(encoding="utf-8")
                if AUTH_ENABLED and DASHBOARD_API_KEY:
                    meta_tag = f'<meta name="hivemind-auth-token" content="{html.escape(DASHBOARD_API_KEY, quote=True)}">'
                    raw = raw.replace("</head>", f"  {meta_tag}\n  </head>", 1)
                _index_html_cache = raw
            return _index_html_cache

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            file_path = (frontend_dist / full_path).resolve()
            if (
                full_path
                and file_path.is_relative_to(frontend_dist.resolve())
                and file_path.exists()
                and file_path.is_file()
            ):
                if full_path.startswith("assets/"):
                    return FileResponse(
                        file_path,
                        headers={"Cache-Control": "public, max-age=31536000, immutable"},
                    )
                return FileResponse(
                    file_path,
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
                )
            from starlette.responses import HTMLResponse

            return HTMLResponse(
                content=_get_index_html(),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

    return app
