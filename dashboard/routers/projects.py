"""Project CRUD and project-scoped data endpoints.

Handles project listing, creation, deletion, updates, history clearing,
file browsing, git diffs, activity replay, and project state dumps.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter

import state
from dashboard.events import event_bus
from dashboard.routers import (
    CreateProjectRequest,
    UpdateProjectRequest,
    _create_web_manager,
    _db_event_to_dict,
    _find_manager,
    _manager_status,
    _manager_to_dict,
    _problem,
    _resolve_project_dir,
    _valid_project_id,
)

logger = logging.getLogger("dashboard.api")

router = APIRouter(tags=["projects"])


@router.get("/api/projects")
async def list_projects():
    """List all projects with live status from active_sessions + DB."""
    _del_file = Path("data/deleted_projects.json")
    deleted_ids: set[str] = set()
    try:
        if _del_file.exists():
            deleted_ids = set(json.loads(_del_file.read_text()))
    except Exception:
        pass

    active_managers = state.get_all_managers()

    active_map = {}
    for user_id, project_id, manager in active_managers:
        if project_id in deleted_ids:
            continue
        active_map[project_id] = _manager_to_dict(manager, project_id)
        active_map[project_id]["user_id"] = user_id

    db_projects = await state.session_mgr.list_projects() if state.session_mgr else []
    db_project_map = {dbp["project_id"]: dbp for dbp in db_projects}

    projects = []
    seen = set()

    for project_id, data in active_map.items():
        seen.add(project_id)
        dbp = db_project_map.get(project_id)
        if dbp:
            data["description"] = dbp.get("description", "")
            data["created_at"] = dbp.get("created_at", 0)
            data["updated_at"] = dbp.get("updated_at", 0)
            data["message_count"] = dbp.get("message_count", 0)
        projects.append(data)

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


@router.get("/api/projects/{project_id}")
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

        recent_msgs = await state.session_mgr.get_recent_messages(project_id, count=20)
        last_msg = recent_msgs[-1] if recent_msgs else None
        saved_orch = await state.session_mgr.load_orchestrator_state(project_id)
        _, total_msgs = await state.session_mgr.get_messages_paginated(
            project_id, limit=1_000_000, offset=0
        )

        from config import DEFAULT_AGENTS

        default_agent_names = [a["name"] for a in DEFAULT_AGENTS]
        # Extract DAG progress from saved orchestrator state
        dag_progress = None
        dag_vision = None
        if saved_orch:
            agents_blob = saved_orch.get("agent_states", {})
            if isinstance(agents_blob, dict) and "dag_graph" in agents_blob:
                graph_data = agents_blob.get("dag_graph")
                statuses = agents_blob.get("dag_task_statuses", {})
                if graph_data:
                    dag_vision = graph_data.get("vision")
                    tasks = graph_data.get("tasks", []) or []
                    total = len(tasks)
                    if total > 0:
                        completed = sum(
                            1 for s in statuses.values() if s in ("completed", "skipped")
                        )
                        failed = sum(1 for s in statuses.values() if s == "failed")
                        running = sum(1 for s in statuses.values() if s == "working")
                        dag_progress = {
                            "total": total,
                            "completed": completed,
                            "failed": failed,
                            "running": running,
                            "percent": round(completed / total * 100) if total else 0,
                        }

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
            "dag_progress": dag_progress,
            "dag_vision": dag_vision,
        }

    return data


@router.get("/api/projects/{project_id}/live")
async def get_live_state(project_id: str):
    """Full live state snapshot — designed for recovery after browser refresh."""
    diagnostics = event_bus.get_diagnostics(project_id)

    manager, _user_id = await _find_manager(project_id)
    if not manager:
        if state.session_mgr:
            saved = await state.session_mgr.load_orchestrator_state(project_id)
            if saved and saved.get("status") in ("running", "interrupted", "completed"):
                agents_blob = saved.get("agent_states", {})
                if isinstance(agents_blob, dict) and "agent_states" in agents_blob:
                    inner_agent_states = agents_blob.get("agent_states", {})
                    dag_graph = agents_blob.get("dag_graph")
                    dag_task_statuses = agents_blob.get("dag_task_statuses", {})
                else:
                    inner_agent_states = agents_blob
                    dag_graph = saved.get("dag_graph")
                    dag_task_statuses = saved.get("dag_task_statuses", {})

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


@router.put("/api/projects/{project_id}")
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


@router.get("/api/projects/{project_id}/state-dump")
async def get_state_dump(project_id: str):
    """Complete state dump for debugging."""
    result: dict = {
        "project_id": project_id,
        "timestamp": time.time(),
    }

    if state.session_mgr:
        db_project = await state.session_mgr.load_project(project_id)
        result["project"] = db_project or {}

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

    if state.session_mgr:
        msgs = await state.session_mgr.get_recent_messages(project_id, count=20)
        result["recent_messages"] = msgs
        _, total = await state.session_mgr.get_messages_paginated(project_id, limit=0, offset=0)
        result["total_messages"] = total

    if state.session_mgr:
        events = await state.session_mgr.get_activity_since(project_id, since_sequence=0, limit=50)
        result["recent_activity"] = events
        result["total_activity_events"] = await state.session_mgr.get_latest_sequence(project_id)

    if state.session_mgr:
        orch_state = await state.session_mgr.load_orchestrator_state(project_id)
        result["orchestrator_state"] = orch_state or {}

    return result


@router.get("/api/projects/{project_id}/agents")
async def get_project_agents(project_id: str):
    """Detailed agent info with individual stats."""
    manager, _ = await _find_manager(project_id)
    if not manager:
        return {"agents": []}

    agents = []
    for agent_name in manager.agent_names:
        agent_msgs = [m for m in manager.conversation_log if m.agent_name == agent_name]
        agent_tokens = sum(m.total_tokens for m in agent_msgs)
        agent_turns = len(agent_msgs)
        last_activity = agent_msgs[-1].content[:200] if agent_msgs else ""
        last_timestamp = agent_msgs[-1].timestamp if agent_msgs else 0
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


@router.get("/api/projects/{project_id}/messages")
async def get_messages(project_id: str, limit: int = 50, offset: int = 0):
    """Conversation history (paginated, from DB)."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    if not state.session_mgr:
        return {"messages": [], "total": 0}
    messages, total = await state.session_mgr.get_messages_paginated(project_id, limit, offset)
    return {"messages": messages, "total": total}


@router.get("/api/projects/{project_id}/files")
async def get_files(project_id: str):
    """Git diff + git status in project dir."""
    from config import GIT_DIFF_TIMEOUT

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

    # Check if git is initialized in the project directory
    if not (Path(project_dir) / ".git").exists():
        return {
            "stat": "",
            "status": "",
            "diff": "",
            "project_dir": str(project_dir),
            "error": "Git repository not initialized. Changes will be tracked after the first agent run.",
        }

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


@router.get("/api/projects/{project_id}/tasks")
async def get_tasks(project_id: str):
    """Task history from DB."""
    if not state.session_mgr:
        return {"tasks": []}
    tasks = await state.session_mgr.get_project_tasks(project_id)
    return {"tasks": tasks}


@router.get("/api/projects/{project_id}/summary")
async def get_session_summary(project_id: str):
    """Return the last orchestrator summary message and session stats."""
    if not _valid_project_id(project_id):
        return _problem(400, "Invalid project ID format")

    manager, _ = await _find_manager(project_id)

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

    last_summary_text: str | None = None
    if state.session_mgr:
        msgs, _ = await state.session_mgr.get_messages_paginated(project_id, limit=200, offset=0)
        for msg in reversed(msgs):
            if msg.get("role") in ("system", "System") or msg.get("agent_name") in (
                "System",
                "system",
            ):
                last_summary_text = msg.get("content")
                break

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


@router.get("/api/projects/{project_id}/brain-summary")
async def get_brain_summary(project_id: str):
    """Return a structured executive digest of the blackboard state."""
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


@router.post("/api/projects")
async def create_project(req: CreateProjectRequest):
    """Create a new project from the web dashboard."""
    name = req.name.strip()
    if not name or not state.PROJECT_NAME_RE.match(name):
        return _problem(
            400, "Invalid project name. Use letters, numbers, spaces, hyphens, underscores."
        )

    directory = req.directory.strip()
    if not directory:
        from config import PROJECTS_BASE_DIR

        slug = name.lower().replace(" ", "-")
        directory = str(PROJECTS_BASE_DIR / slug)

    if not state.session_mgr:
        return _problem(503, "Not initialized")

    project_dir = os.path.expanduser(directory)

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

    # Initialize a git repo so the Diff tab can track file changes.
    # Claude Code CLI normally does this, but we ensure it exists regardless.
    git_dir = Path(project_dir) / ".git"
    if not git_dir.exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "init",
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
            # Set default user for commits if not configured globally
            for cfg_args in (
                ["git", "config", "user.email", "hivemind@local"],
                ["git", "config", "user.name", "Hivemind"],
            ):
                p = await asyncio.create_subprocess_exec(
                    *cfg_args,
                    cwd=project_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(p.communicate(), timeout=3.0)
            # Create an initial empty commit so HEAD exists for diff operations
            p = await asyncio.create_subprocess_exec(
                "git",
                "commit",
                "--allow-empty",
                "-m",
                "Initial commit",
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(p.communicate(), timeout=5.0)
            logger.info("[%s] Initialized git repo in %s", name, project_dir)
        except Exception as git_err:
            logger.warning("[%s] Git init failed (non-fatal): %s", name, git_err)

    project_id = name.lower().replace(" ", "-")
    existing = await state.session_mgr.load_project(project_id)
    if existing:
        project_id = f"{project_id}-{uuid.uuid4().hex[:6]}"

    user_id = None
    state_user_id = 0

    await state.session_mgr.save_project(
        project_id=project_id,
        user_id=user_id,
        name=name,
        description=req.description or "",
        project_dir=project_dir,
    )

    _deleted_file = Path("data/deleted_projects.json")
    try:
        if _deleted_file.exists():
            deleted_ids = json.loads(_deleted_file.read_text())
            if project_id in deleted_ids:
                deleted_ids.remove(project_id)
                _deleted_file.write_text(json.dumps(deleted_ids))
    except Exception:
        pass

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


@router.delete("/api/projects/{project_id}")
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


@router.post("/api/projects/{project_id}/clear-history")
async def clear_project_history(project_id: str):
    """Clear all messages and task history for a project, starting fresh."""
    from config import CONVERSATION_LOG_MAXLEN
    from src.api.websocket_handler import invalidate_conversation_cache

    manager, _ = await _find_manager(project_id)
    if manager and manager.is_running:
        return _problem(400, "Cannot clear history while project is running")

    if state.session_mgr:
        await state.session_mgr.clear_project_data(project_id)

    if manager:
        manager.shared_context = []
        manager.conversation_log = collections.deque(maxlen=CONVERSATION_LOG_MAXLEN)
        manager.turn_count = 0
        manager.total_input_tokens = 0
        manager.total_output_tokens = 0
        manager.total_tokens = 0
        manager.agent_states = {}
        manager._completed_rounds = []
        manager._agents_used = set()
        manager._current_dag_graph = None
        manager._dag_task_statuses = {}

        drained = manager.drain_message_queue()
        if drained:
            logger.info(f"[{project_id}] Drained {drained} pending messages")

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

    event_bus.clear_project_events(project_id)
    invalidate_conversation_cache(project_id)

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


@router.post("/api/projects/{project_id}/start")
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


@router.get("/api/projects/{project_id}/tree")
async def get_file_tree(project_id: str):
    """List files in project directory (2 levels deep)."""
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
            resolved_item = item.resolve()
            if not resolved_item.is_relative_to(root):
                continue
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


@router.get("/api/projects/{project_id}/file")
async def read_file(project_id: str, path: str):
    """Read a file from the project directory."""
    project_dir = await _resolve_project_dir(project_id)
    if not project_dir:
        return {"error": "Project not found"}

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
        file_path = file_path.resolve()
        proj_resolved = Path(project_dir).resolve()
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
            "Access denied: this file resolves to a sensitive path and cannot be read via the API.",
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


def _sanitize_surrogates(obj):
    """Recursively replace surrogate characters that break JSON encoding."""
    if isinstance(obj, str):
        return obj.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_surrogates(v) for v in obj]
    return obj


@router.get("/api/projects/{project_id}/activity")
async def get_activity(project_id: str, since: int = 0, limit: int = 200):
    """Get activity events after a given sequence_id."""
    since = max(0, since)
    limit = max(1, min(limit, 1000))
    buffered = event_bus.get_buffered_events(project_id, since_sequence=since)
    if buffered:
        return _sanitize_surrogates(
            {
                "events": buffered,
                "latest_sequence": event_bus.get_latest_sequence(project_id),
                "source": "memory",
            }
        )

    if state.session_mgr:
        events = await state.session_mgr.get_activity_since(project_id, since, limit)
        full_events = [_db_event_to_dict(e, project_id) for e in events]
        return _sanitize_surrogates(
            {
                "events": full_events,
                "latest_sequence": await state.session_mgr.get_latest_sequence(project_id),
                "source": "database",
            }
        )

    return {"events": [], "latest_sequence": 0, "source": "none"}


@router.get("/api/projects/{project_id}/activity/latest")
async def get_latest_sequence(project_id: str):
    """Get the latest sequence_id for a project (for sync protocol)."""
    return {
        "latest_sequence": event_bus.get_latest_sequence(project_id),
    }
