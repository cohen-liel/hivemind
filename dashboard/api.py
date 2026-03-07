"""FastAPI dashboard backend — REST endpoints + WebSocket for the agent dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from dashboard.events import event_bus
import state

logger = logging.getLogger(__name__)


# --- Request / response models ---

class SendMessageRequest(BaseModel):
    message: str


class TalkAgentRequest(BaseModel):
    message: str


class CreateProjectRequest(BaseModel):
    name: str
    directory: str
    agents_count: int = 2
    description: str = ""


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


# --- Helpers using state module ---

def _find_manager(project_id: str):
    """Find an OrchestratorManager by project_id across all users."""
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
    """Create an OrchestratorManager with web-only callbacks (EventBus, no Telegram)."""
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
            "text": text,
        })

    async def on_result(text: str):
        await event_bus.publish({
            "type": "agent_result",
            "project_id": project_id,
            "project_name": project_name,
            "text": text,
        })

    async def on_final(text: str):
        await event_bus.publish({
            "type": "agent_final",
            "project_id": project_id,
            "project_name": project_name,
            "text": text,
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


def _git_diff(project_dir: str) -> str:
    """Run git diff --stat + git status in the project directory."""
    try:
        diff = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=project_dir, capture_output=True, text=True, timeout=5,
        )
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=project_dir, capture_output=True, text=True, timeout=5,
        )
        full_diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=project_dir, capture_output=True, text=True, timeout=10,
        )
        return json.dumps({
            "stat": diff.stdout.strip(),
            "status": status.stdout.strip(),
            "diff": full_diff.stdout[:50000],  # cap at 50KB
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# --- App factory ---

async def _resolve_project_dir(project_id: str) -> str | None:
    """Resolve project directory from active manager or DB."""
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

    # CORS — configurable via CORS_ORIGINS env var
    from config import CORS_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Health check ---

    @app.get("/api/health")
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "ok",
            "db_connected": state.session_mgr is not None,
            "sdk_ready": state.sdk_client is not None,
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
            data = {
                "project_id": project_id,
                "project_name": db_project["name"],
                "project_dir": db_project.get("project_dir", ""),
                "status": "idle",
                "is_running": False,
                "is_paused": False,
                "turn_count": 0,
                "total_cost_usd": 0,
                "agents": [],
                "multi_agent": False,
                "last_message": None,
                "user_id": db_project.get("user_id", 0),
                "conversation_log": [],
                "description": db_project.get("description", ""),
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
        """
        manager, user_id = _find_manager(project_id)
        if not manager:
            return {
                "status": "idle",
                "agent_states": {},
                "loop_progress": None,
                "shared_context_count": 0,
                "pending_messages": 0,
                "pending_approval": None,
            }

        loop_progress = None
        if manager.is_running:
            from config import MAX_TURNS_PER_CYCLE, MAX_BUDGET_USD, MAX_ORCHESTRATOR_LOOPS
            loop_progress = {
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
        }

    @app.put("/api/projects/{project_id}")
    async def update_project(project_id: str, req: UpdateProjectRequest):
        """Update project settings (name, description, agents_count)."""
        if not state.session_mgr:
            return JSONResponse({"error": "Not initialized"}, status_code=503)

        db_project = await state.session_mgr.load_project(project_id)
        if not db_project:
            return JSONResponse({"error": "Project not found"}, status_code=404)

        db = await state.session_mgr._get_db()
        if req.name is not None:
            name = req.name.strip()
            if not name or not state.PROJECT_NAME_RE.match(name):
                return JSONResponse({"error": "Invalid project name"}, status_code=400)
            await db.execute("UPDATE projects SET name=? WHERE project_id=?", (name, project_id))
            # Update in-memory manager name if active
            manager, _ = _find_manager(project_id)
            if manager:
                manager.project_name = name

        if req.description is not None:
            await db.execute("UPDATE projects SET description=? WHERE project_id=?", (req.description, project_id))

        await db.commit()

        await event_bus.publish({
            "type": "project_status",
            "project_id": project_id,
            "status": "updated",
        })

        return {"ok": True}

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
        if not state.session_mgr:
            return {"messages": [], "total": 0}
        db = await state.session_mgr._get_db()
        cursor = await db.execute(
            "SELECT agent_name, role, content, cost_usd, timestamp FROM messages "
            "WHERE project_id=? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (project_id, limit, offset),
        )
        rows = await cursor.fetchall()
        messages = [dict(row) for row in reversed(rows)]

        cursor2 = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE project_id=?",
            (project_id,),
        )
        total = (await cursor2.fetchone())[0]

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
            diff = subprocess.run(
                ["git", "diff", "--stat", "HEAD"],
                cwd=project_dir, capture_output=True, text=True, timeout=5,
            )
            status = subprocess.run(
                ["git", "status", "--short"],
                cwd=project_dir, capture_output=True, text=True, timeout=5,
            )
            full_diff = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=project_dir, capture_output=True, text=True, timeout=10,
            )
            return {
                "stat": diff.stdout.strip(),
                "status": status.stdout.strip(),
                "diff": full_diff.stdout[:50000],
            }
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/projects/{project_id}/tasks")
    async def get_tasks(project_id: str):
        """Task history from DB."""
        if not state.session_mgr:
            return {"tasks": []}
        db = await state.session_mgr._get_db()
        cursor = await db.execute(
            "SELECT * FROM task_history WHERE project_id=? ORDER BY started_at DESC LIMIT 50",
            (project_id,),
        )
        rows = await cursor.fetchall()
        return {"tasks": [dict(row) for row in rows]}

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
        """Update editable settings (runtime only, does not persist to .env)."""
        import config as cfg
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

        return {"ok": True, "updated": updated}

    @app.post("/api/settings/persist")
    async def persist_settings(request: Request):
        """Persist settings overrides to data/settings_overrides.json."""
        import json as json_mod
        data = await request.json()
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
        """Browse filesystem directories for project creation."""
        target = Path(os.path.expanduser(path)).resolve()
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

    @app.post("/api/projects/{project_id}/message")
    async def send_message(project_id: str, req: SendMessageRequest):
        """Send message to orchestrator."""
        logger.info(f"[{project_id}] Received message: {req.message[:100]}")
        manager, _ = _find_manager(project_id)

        if not manager:
            # Try to activate from DB first
            logger.info(f"[{project_id}] No active manager, trying DB lookup...")
            if state.session_mgr:
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
        else:
            logger.info(f"[{project_id}] Injecting message into running session")
            await manager.inject_user_message("orchestrator", req.message)

        return {"ok": True}

    @app.post("/api/projects/{project_id}/talk/{agent}")
    async def talk_agent(project_id: str, agent: str, req: TalkAgentRequest):
        """Send message to specific agent."""
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
    async def create_schedule(request: Request):
        """Create a new schedule."""
        data = await request.json()
        if not state.session_mgr:
            return JSONResponse({"error": "DB not ready"}, status_code=500)
        schedule_id = await state.session_mgr.add_schedule(
            user_id=data.get("user_id", 0),
            chat_id=data.get("chat_id", 0),
            project_id=data["project_id"],
            schedule_time=data["schedule_time"],
            task_description=data["task_description"],
            repeat=data.get("repeat", "once"),
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

    @app.put("/api/projects/{project_id}/budget")
    async def set_project_budget(project_id: str, request: Request):
        """Set per-project budget."""
        data = await request.json()
        budget = float(data.get("budget_usd", 0))
        if not state.session_mgr:
            return JSONResponse({"error": "DB not ready"}, status_code=500)
        await state.session_mgr.set_project_budget(project_id, budget)
        return {"ok": True, "budget_usd": budget}

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
        """List files in project directory (2 levels deep)."""
        project_dir = await _resolve_project_dir(project_id)
        if not project_dir:
            return {"error": "Project not found"}

        tree = []
        try:
            root = Path(project_dir)
            skip = {'.git', '__pycache__', 'node_modules', 'venv', '.venv', '.mypy_cache', '.pytest_cache', 'dist', 'build'}
            for item in sorted(root.iterdir()):
                if item.name.startswith('.') and item.name != '.env.example':
                    if item.name not in ('.github',):
                        continue
                if item.name in skip:
                    continue
                entry = {"name": item.name, "type": "dir" if item.is_dir() else "file", "path": item.name}
                if item.is_dir():
                    children = []
                    try:
                        for sub in sorted(item.iterdir()):
                            if sub.name.startswith('.') or sub.name in skip:
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
            return {"error": str(e)}

        return {"tree": tree, "project_dir": project_dir}

    @app.get("/api/projects/{project_id}/file")
    async def read_file(project_id: str, path: str):
        """Read a file from the project directory."""
        project_dir = await _resolve_project_dir(project_id)
        if not project_dir:
            return {"error": "Project not found"}

        file_path = Path(project_dir) / path
        try:
            file_path = file_path.resolve()
            proj_resolved = Path(project_dir).resolve()
            if not str(file_path).startswith(str(proj_resolved)):
                return {"error": "Path traversal not allowed"}
        except Exception:
            return {"error": "Invalid path"}

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
            return {"error": str(e)}

        return {"content": content, "path": path, "size": size}

    # --- WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        """WebSocket for real-time event stream with ping/pong heartbeat."""
        await ws.accept()
        queue = event_bus.subscribe()

        async def _sender():
            """Forward events from bus to WebSocket client."""
            while True:
                event = await queue.get()
                await ws.send_json(event)

        async def _heartbeat():
            """Send periodic pings to detect stale connections."""
            while True:
                await asyncio.sleep(30)
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    break

        async def _receiver():
            """Listen for client messages (pong, future commands)."""
            while True:
                try:
                    data = await ws.receive_json()
                    # Client can send pong or other commands
                    if data.get("type") == "pong":
                        pass  # Connection is alive
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
            event_bus.unsubscribe(queue)

    # --- Static files (production build) ---

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            file_path = frontend_dist / full_path
            if full_path and file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(frontend_dist / "index.html")

        app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    return app
