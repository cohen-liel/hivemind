"""System health, readiness, stats, settings, and directory browsing endpoints.

Handles liveness/readiness probes, enhanced health checks, runtime settings
management, and the filesystem browser for project creation.
"""

from __future__ import annotations

import logging
import os
import time as _time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import state
from dashboard.routers import UpdateSettingsRequest, _problem

logger = logging.getLogger("dashboard.api")

# Module-level tracker for last error timestamp (updated by any endpoint that catches errors)
_last_error_timestamp: float | None = None


def _record_error() -> None:
    """Update the module-level last-error timestamp."""
    global _last_error_timestamp
    _last_error_timestamp = _time.time()


router = APIRouter(tags=["system"])


@router.get("/api/health/detailed")
async def health_detailed():
    """Detailed health snapshot — uptime, active sessions, task totals, memory, last error."""
    try:
        # Uptime
        server_start = getattr(state, "server_start_time", None)
        uptime_seconds: float | None = None
        if server_start is not None:
            uptime_seconds = round(_time.monotonic() - server_start, 1)

        # Active sessions count
        try:
            active_sessions_count = sum(len(v) for v in state.active_sessions.values())
        except Exception:
            active_sessions_count = 0

        # Total tasks completed — sum done/error agent states across all managers
        total_tasks_completed = 0
        try:
            for _uid, _pid, manager in state.get_all_managers():
                for agent_state in dict(manager.agent_states).values():
                    if agent_state.get("state") in ("done", "error"):
                        total_tasks_completed += 1
        except Exception as _agg_err:
            logger.error(
                "health/detailed: task count aggregation failed: %s", _agg_err, exc_info=True
            )
            _record_error()

        # Memory usage — prefer psutil, fall back to resource module
        memory_usage_mb: float | None = None
        try:
            import psutil as _psutil

            proc = _psutil.Process()
            memory_usage_mb = round(proc.memory_info().rss / (1024**2), 1)
        except ImportError:
            try:
                import resource as _resource

                usage = _resource.getrusage(_resource.RUSAGE_SELF)
                # ru_maxrss is in bytes on macOS, kilobytes on Linux
                import platform as _platform

                rss_raw = usage.ru_maxrss
                if _platform.system() == "Darwin":
                    memory_usage_mb = round(rss_raw / (1024**2), 1)
                else:
                    memory_usage_mb = round(rss_raw / 1024, 1)
            except Exception as _res_err:
                logger.debug("memory via resource module failed: %s", _res_err)
        except Exception as _psutil_err:
            logger.debug("memory via psutil failed: %s", _psutil_err)

        return {
            "uptime_seconds": uptime_seconds,
            "active_sessions_count": active_sessions_count,
            "total_tasks_completed": total_tasks_completed,
            "memory_usage_mb": memory_usage_mb,
            "last_error_timestamp": _last_error_timestamp,
        }
    except Exception as exc:
        logger.error("health/detailed endpoint error: %s", exc, exc_info=True)
        _record_error()
        # Return partial data with 200 rather than letting it bubble to 500
        return JSONResponse(
            {
                "uptime_seconds": None,
                "active_sessions_count": 0,
                "total_tasks_completed": 0,
                "memory_usage_mb": None,
                "last_error_timestamp": _last_error_timestamp,
                "error": "partial data — internal error during collection",
            },
            status_code=200,
        )


@router.get("/api/metrics")
async def get_metrics():
    """Per-agent metrics — tasks run, avg duration, success rate, total cost.

    Aggregates across all active OrchestratorManager instances.
    Each key in the returned dict is an agent role name.
    """
    try:
        # Accumulate per-role stats from all active managers
        # Structure: role -> {runs, success, failed, total_duration_s, total_cost_usd}
        role_stats: dict[str, dict] = {}

        try:
            for _uid, _pid, manager in state.get_all_managers():
                for role, agent_state in dict(manager.agent_states).items():
                    st = agent_state.get("state", "")
                    if st not in ("done", "error"):
                        continue  # only count completed tasks

                    if role not in role_stats:
                        role_stats[role] = {
                            "runs": 0,
                            "success": 0,
                            "failed": 0,
                            "total_duration_s": 0.0,
                            "total_cost_usd": 0.0,
                        }

                    entry = role_stats[role]
                    entry["runs"] += 1
                    if st == "done":
                        entry["success"] += 1
                    else:
                        entry["failed"] += 1

                    duration = agent_state.get("duration", 0.0)
                    if isinstance(duration, int | float):
                        entry["total_duration_s"] += float(duration)

                    cost = agent_state.get("cost", 0.0)
                    if isinstance(cost, int | float):
                        entry["total_cost_usd"] += float(cost)

        except Exception as _iter_err:
            logger.error("metrics: manager iteration failed: %s", _iter_err, exc_info=True)
            _record_error()

        # Shape the response
        result: dict[str, dict] = {}
        for role, stats in role_stats.items():
            runs = stats["runs"]
            avg_duration_ms = round(stats["total_duration_s"] / runs * 1000, 1) if runs > 0 else 0.0
            success_rate = round(stats["success"] / runs, 4) if runs > 0 else 0.0
            result[role] = {
                "total_tasks_run": runs,
                "avg_duration_ms": avg_duration_ms,
                "success_rate": success_rate,
                "total_cost_usd": round(stats["total_cost_usd"], 6),
            }

        return result

    except Exception as exc:
        logger.error("metrics endpoint error: %s", exc, exc_info=True)
        _record_error()
        return JSONResponse({}, status_code=200)


@router.get("/health")
async def liveness():
    """Kubernetes-style liveness probe — always returns 200."""
    return {"status": "ok"}


@router.get("/api/ready")
async def readiness():
    """Kubernetes-style readiness probe — 200 when fully initialised, 503 otherwise."""
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


@router.get("/api/health")
async def health_check():
    """Enhanced health check — DB, CLI binary, disk space, active sessions, uptime, and memory."""
    import platform as _platform
    import shutil as _shutil
    import time as _time

    from config import CLAUDE_CLI_PATH, STORE_DIR

    db_status = "error"
    if state.session_mgr is not None:
        try:
            db_status = "ok" if await state.session_mgr.is_healthy() else "error"
        except Exception as _db_err:
            logger.error("Health check: DB health probe failed: %s", _db_err, exc_info=True)
            db_status = "error"

    cli_path = CLAUDE_CLI_PATH
    if os.sep not in cli_path and "/" not in cli_path:
        cli_status = "ok" if _shutil.which(cli_path) else "missing"
    else:
        cli_status = "ok" if os.path.isfile(cli_path) else "missing"

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
        pass
    except Exception:
        logger.debug("Memory info collection failed", exc_info=True)

    active_count = sum(len(sessions) for sessions in state.active_sessions.values())

    uptime_seconds: float | None = None
    server_start = getattr(state, "server_start_time", None)
    if server_start is not None:
        uptime_seconds = round(_time.monotonic() - server_start, 1)

    overall = "ok" if db_status == "ok" and cli_status == "ok" else "degraded"
    if disk_free_gb > 0 and disk_free_gb < 0.5:
        overall = "degraded"

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


@router.get("/api/stats")
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


@router.get("/api/settings")
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


@router.put("/api/settings")
async def update_settings(req: UpdateSettingsRequest):
    """Update editable settings (runtime only, does not persist to .env)."""
    import config as cfg

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


@router.post("/api/settings/persist")
async def persist_settings(request: Request):
    """Persist settings overrides to data/settings_overrides.json."""
    import json as json_mod

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

    rejected = set(data.keys()) - _ALLOWED_PERSIST_KEYS
    if rejected:
        return _problem(
            400,
            f"Disallowed settings keys: {', '.join(sorted(rejected))}",
        )

    NUMERIC_BOUNDS = {
        "max_budget_usd": (0.1, 500.0),
        "max_turns_per_cycle": (1, 500),
    }
    for key, (lo, hi) in NUMERIC_BOUNDS.items():
        if key in data and isinstance(data[key], int | float):
            data[key] = max(lo, min(float(data[key]), hi))

    overrides_path = Path("data/settings_overrides.json")
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
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


@router.get("/api/cost-breakdown")
async def get_cost_breakdown(project_id: str | None = None, days: int = 30):
    """Return cost breakdown by agent and by day."""
    if state.session_mgr is None:
        return _problem(503, "Database not initialised")
    data = await state.session_mgr.get_cost_breakdown(project_id=project_id, days=days)
    return data


@router.get("/api/cost-summary")
async def get_cost_summary():
    """Return per-project cost summary."""
    if state.session_mgr is None:
        return _problem(503, "Database not initialised")
    data = await state.session_mgr.get_project_cost_summary()
    return data


@router.get("/api/browse-dirs")
async def browse_dirs(path: str = "~"):
    """Browse filesystem directories for project creation."""
    from config import PROJECTS_BASE_DIR

    target = Path(os.path.expanduser(path)).resolve()

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
