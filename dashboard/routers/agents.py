"""Agent communication endpoints — talk, nudge, approve/reject, registry, and stats.

Handles direct agent interaction, HITL approval gates, the agent registry
metadata endpoint, and per-agent performance analytics.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

import state
from dashboard.routers import (
    NudgeRequest,
    TalkAgentRequest,
    _find_manager,
    _problem,
)

logger = logging.getLogger("dashboard.api")

router = APIRouter(tags=["agents"])


@router.post("/api/projects/{project_id}/talk/{agent}")
async def talk_agent(project_id: str, agent: str, req: TalkAgentRequest):
    """Send message to specific agent."""
    manager, _ = await _find_manager(project_id)
    if not manager:
        return _problem(404, "Project not active.")

    if agent not in manager.agent_names:
        return _problem(400, f"Unknown agent: {agent}. Available: {manager.agent_names}")

    await manager.inject_user_message(agent, req.message)
    return {"ok": True}


@router.post("/api/projects/{project_id}/nudge/{agent}")
async def nudge_agent(project_id: str, agent: str, req: NudgeRequest):
    """Nudge a specific agent mid-run without stopping other agents."""
    manager, _ = await _find_manager(project_id)
    if not manager:
        return _problem(404, "Project not active.")

    from config import get_all_role_names

    all_known = get_all_role_names(include_legacy=True)
    if agent not in all_known and agent != "orchestrator":
        return _problem(400, f"Unknown agent: {agent}. Available: {list(all_known)}")

    agent_state = manager.agent_states.get(agent, {})
    is_working = agent_state.get("state") == "working"

    nudge_text = (
        "\n\n"
        f"\U0001f4ac **User nudge** (priority: {req.priority}):\n"
        f"{req.message}\n"
        "\nApply this guidance to your current work without starting over."
    )

    if is_working:
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


@router.post("/api/projects/{project_id}/approve")
async def approve_project(project_id: str):
    """Approve a pending HITL checkpoint."""
    manager, _ = await _find_manager(project_id)
    if not manager:
        return _problem(404, "Project not active")
    if not manager.pending_approval:
        return _problem(400, "No pending approval")
    manager.approve()
    return {"ok": True}


@router.post("/api/projects/{project_id}/reject")
async def reject_project(project_id: str):
    """Reject a pending HITL checkpoint."""
    manager, _ = await _find_manager(project_id)
    if not manager:
        return _problem(404, "Project not active")
    if not manager.pending_approval:
        return _problem(400, "No pending approval")
    manager.reject()
    return {"ok": True}


@router.get("/api/agent-registry")
async def get_agent_registry():
    """Expose AGENT_REGISTRY metadata for the frontend."""
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
    return {
        "agents": registry,
        "ws": {
            "keepalive_interval_ms": cfg.WS_KEEPALIVE_INTERVAL,
            "reconnect_base_delay_ms": cfg.WS_RECONNECT_BASE_DELAY,
            "reconnect_max_delay_ms": cfg.WS_RECONNECT_MAX_DELAY,
        },
    }


@router.get("/api/agent-stats")
async def get_agent_stats(project_id: str | None = None):
    """Get aggregated agent performance statistics."""
    if not state.session_mgr:
        return {"stats": []}
    stats = await state.session_mgr.get_agent_stats(project_id)
    return {"stats": stats}


@router.get("/api/agent-stats/{agent_role}/recent")
async def get_agent_recent(agent_role: str, limit: int = 10):
    """Get recent performance entries for a specific agent."""
    limit = max(1, min(limit, 200))
    if not state.session_mgr:
        return {"entries": []}
    entries = await state.session_mgr.get_agent_recent_performance(agent_role, limit)
    return {"entries": entries}
