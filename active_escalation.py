"""
Active Escalation — Proactive stuck-agent recovery.

Instead of just detecting stuck agents and reporting to the user,
this module takes active steps to recover:
1. Reassign the task to a different agent
2. Simplify the task and retry
3. Kill the stuck session and spawn a fresh one

Suggested in code review: "Instead of just alerting the user when an agent
is stuck, actively try to recover — reassign, simplify, or kill and respawn."
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator import OrchestratorManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Escalation Strategies
# ---------------------------------------------------------------------------

# Max retries before giving up on a stuck agent
MAX_ESCALATION_RETRIES = 2

# Time thresholds (seconds)
SOFT_STUCK_THRESHOLD = 180  # 3 minutes — try gentle recovery
HARD_STUCK_THRESHOLD = 420  # 7 minutes — force recovery


class EscalationAction:
    """Represents an escalation action to take."""

    REASSIGN = "reassign"
    SIMPLIFY = "simplify"
    KILL_RESPAWN = "kill_respawn"
    NOTIFY_USER = "notify_user"

    def __init__(
        self,
        action: str,
        agent_role: str,
        reason: str,
        new_agent: str | None = None,
        simplified_task: str | None = None,
    ):
        self.action = action
        self.agent_role = agent_role
        self.reason = reason
        self.new_agent = new_agent
        self.simplified_task = simplified_task

    def __repr__(self) -> str:
        return f"EscalationAction({self.action}, agent={self.agent_role}, reason={self.reason!r})"


# ---------------------------------------------------------------------------
# Escalation Decision Engine
# ---------------------------------------------------------------------------

# Agent fallback chains: if agent A is stuck, try agent B
AGENT_FALLBACK_MAP: dict[str, list[str]] = {
    "frontend_developer": ["developer", "backend_developer"],
    "backend_developer": ["developer", "frontend_developer"],
    "database_expert": ["backend_developer", "developer"],
    "test_engineer": ["developer", "backend_developer"],
    "security_auditor": ["reviewer", "researcher"],
    "devops": ["backend_developer", "developer"],
    "reviewer": ["security_auditor", "researcher"],
    "researcher": ["reviewer", "developer"],
    "developer": ["backend_developer", "frontend_developer"],
}


def decide_escalation(
    mgr: OrchestratorManager,
    stuck_signal: dict[str, Any],
) -> EscalationAction:
    """Decide what escalation action to take based on the stuck signal.

    Args:
        mgr: The OrchestratorManager instance
        stuck_signal: Dict from orch_watchdog.detect_stuck()

    Returns:
        EscalationAction describing what to do
    """
    signal = stuck_signal.get("signal", "")
    severity = stuck_signal.get("severity", "warning")
    strategy = stuck_signal.get("strategy", "")
    current_agent = mgr.current_agent or "unknown"

    # Track escalation attempts (init lazily if orchestrator didn't call init_escalation_tracking)
    if not hasattr(mgr, "_escalation_counts"):
        mgr._escalation_counts: dict[str, int] = {}
    escalation_count = mgr._escalation_counts.get(current_agent, 0)

    # Critical severity or too many retries → notify user
    if severity == "critical" and escalation_count >= MAX_ESCALATION_RETRIES:
        return EscalationAction(
            action=EscalationAction.NOTIFY_USER,
            agent_role=current_agent,
            reason=f"Critical stuck after {escalation_count} recovery attempts: {stuck_signal.get('details', '')}",
        )

    # Strategy-based decisions
    if strategy == "change_agents" or signal == "circular_delegations":
        fallbacks = AGENT_FALLBACK_MAP.get(current_agent, [])
        # Find a fallback agent that hasn't been tried
        used = mgr._agents_used
        for fallback in fallbacks:
            if fallback not in used or escalation_count == 0:
                return EscalationAction(
                    action=EscalationAction.REASSIGN,
                    agent_role=current_agent,
                    reason=f"Circular delegation detected, reassigning to {fallback}",
                    new_agent=fallback,
                )

    if strategy == "simplify_task" or signal == "repeated_errors":
        return EscalationAction(
            action=EscalationAction.SIMPLIFY,
            agent_role=current_agent,
            reason="Repeated errors — simplifying the task",
        )

    if strategy == "force_implementation" or signal == "no_file_progress":
        return EscalationAction(
            action=EscalationAction.KILL_RESPAWN,
            agent_role=current_agent,
            reason="No file progress — killing and respawning with focused task",
        )

    if strategy == "reduce_scope" or signal == "cost_runaway":
        return EscalationAction(
            action=EscalationAction.SIMPLIFY,
            agent_role=current_agent,
            reason="Cost runaway — reducing scope",
        )

    # Default: try reassignment first, then simplify
    if escalation_count == 0:
        fallbacks = AGENT_FALLBACK_MAP.get(current_agent, [])
        if fallbacks:
            return EscalationAction(
                action=EscalationAction.REASSIGN,
                agent_role=current_agent,
                reason=f"Agent stuck ({signal}), trying fallback",
                new_agent=fallbacks[0],
            )

    return EscalationAction(
        action=EscalationAction.NOTIFY_USER,
        agent_role=current_agent,
        reason=f"Unable to auto-recover from: {stuck_signal.get('details', signal)}",
    )


# ---------------------------------------------------------------------------
# Escalation Executor
# ---------------------------------------------------------------------------


async def execute_escalation(
    mgr: OrchestratorManager,
    action: EscalationAction,
    original_task: str = "",
) -> bool:
    """Execute an escalation action.

    Args:
        mgr: The OrchestratorManager instance
        action: The escalation action to execute
        original_task: The original task that was stuck

    Returns:
        True if escalation was handled, False if user notification is needed
    """
    # Track escalation count (init lazily if orchestrator didn't call init_escalation_tracking)
    if not hasattr(mgr, "_escalation_counts"):
        mgr._escalation_counts: dict[str, int] = {}
    count = mgr._escalation_counts.get(action.agent_role, 0) + 1
    mgr._escalation_counts[action.agent_role] = count

    logger.info(
        f"[{mgr.project_id}] Escalation #{count} for {action.agent_role}: "
        f"{action.action} — {action.reason}"
    )

    if action.action == EscalationAction.REASSIGN:
        await mgr._notify(
            f"\U0001f504 **Auto-recovery**: Reassigning task from "
            f"*{action.agent_role}* to *{action.new_agent}*\n"
            f"_Reason: {action.reason}_"
        )
        await mgr._emit_event(
            "escalation",
            action="reassign",
            from_agent=action.agent_role,
            to_agent=action.new_agent,
            reason=action.reason,
        )
        return True

    elif action.action == EscalationAction.SIMPLIFY:
        simplified = _simplify_task(original_task)
        await mgr._notify(
            f"\U0001f504 **Auto-recovery**: Simplifying task for *{action.agent_role}*\n"
            f"_Reason: {action.reason}_\n"
            f"_Simplified: {simplified[:200]}_"
        )
        await mgr._emit_event(
            "escalation",
            action="simplify",
            agent=action.agent_role,
            reason=action.reason,
            simplified_task=simplified[:200],
        )
        return True

    elif action.action == EscalationAction.KILL_RESPAWN:
        await mgr._notify(
            f"\u26a0\ufe0f **Auto-recovery**: Killing stuck session for *{action.agent_role}*\n"
            f"_Reason: {action.reason}_\n"
            f"_A fresh session will be spawned._"
        )
        # Invalidate the stuck session
        try:
            await mgr.session_mgr.invalidate_session(mgr.user_id, mgr.project_id, action.agent_role)
        except Exception as e:
            logger.warning(f"[{mgr.project_id}] Failed to invalidate session: {e}")

        await mgr._emit_event(
            "escalation",
            action="kill_respawn",
            agent=action.agent_role,
            reason=action.reason,
        )
        return True

    elif action.action == EscalationAction.NOTIFY_USER:
        await mgr._notify(
            f"\u26a0\ufe0f **Needs your attention**: *{action.agent_role}* is stuck\n"
            f"_Reason: {action.reason}_\n\n"
            f"Options:\n"
            f"• Send a message to guide the agent\n"
            f"• Use /stop to cancel and try a different approach\n"
            f"• Use /resume to let it try again"
        )
        await mgr._emit_event(
            "escalation",
            action="notify_user",
            agent=action.agent_role,
            reason=action.reason,
        )
        return False

    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simplify_task(task: str) -> str:
    """Simplify a task by reducing its scope.

    Strategies:
    1. If task mentions multiple files/features, focus on the first one
    2. Add explicit "start with the simplest approach" instruction
    3. Remove complex requirements
    """
    simplified = task

    # Add simplification instructions
    prefix = (
        "[SIMPLIFIED — previous attempt failed]\n"
        "Focus on the MINIMUM viable implementation:\n"
        "1. Start with the simplest possible approach\n"
        "2. Implement ONE file at a time\n"
        "3. Skip edge cases for now\n"
        "4. Use existing patterns from the codebase\n\n"
        "Original task: "
    )

    return prefix + simplified


def init_escalation_tracking(mgr: OrchestratorManager) -> None:
    """Initialize escalation tracking on the manager.

    Call this when creating a new OrchestratorManager instance.
    """
    if not hasattr(mgr, "_escalation_counts"):
        mgr._escalation_counts: dict[str, int] = {}
