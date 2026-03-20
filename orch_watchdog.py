"""Watchdog, stuck detection, and premature-completion checks.

Extracted from orchestrator.py to reduce file size.
All functions operate on an OrchestratorManager instance passed as ``mgr``.
This module handles:
  - Agent silence watchdog (background loop that detects unresponsive agents)
  - Five-signal stuck detection (text similarity, repeated errors, circular
    delegations, no file progress, cost runaway)
  - Task-complexity estimation
  - Premature-completion validation
"""

from __future__ import annotations

import asyncio
import logging
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator import OrchestratorManager

from config import (
    MAX_BUDGET_USD,
    MAX_ORCHESTRATOR_LOOPS,
    STUCK_SIMILARITY_THRESHOLD,
    STUCK_WINDOW_SIZE,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
SILENCE_CHECK_INTERVAL_SECONDS = 30
SILENCE_THRESHOLD_SECONDS = 300
SILENCE_CRITICAL_SECONDS = 600  # 10 minutes


# ── Silence watchdog ─────────────────────────────────────────────────────


def start_silence_watchdog(mgr: OrchestratorManager) -> None:
    """Start the agent silence watchdog task. Called on session start."""
    if mgr._watchdog_task is not None and not mgr._watchdog_task.done():
        return  # Already running
    mgr._silence_alerted.clear()
    mgr._watchdog_task = asyncio.create_task(_silence_watchdog_loop(mgr))
    logger.info(f"[{mgr.project_id}] Agent silence watchdog started")


async def stop_silence_watchdog(mgr: OrchestratorManager) -> None:
    """Cancel the agent silence watchdog task. Called on session stop/cleanup."""
    if mgr._watchdog_task is not None and not mgr._watchdog_task.done():
        mgr._watchdog_task.cancel()
        try:
            await mgr._watchdog_task
        except asyncio.CancelledError:
            pass
        logger.info(f"[{mgr.project_id}] Agent silence watchdog stopped")
    mgr._watchdog_task = None
    mgr._silence_alerted.clear()


async def _silence_watchdog_loop(mgr: OrchestratorManager) -> None:
    """Background loop: check every 30s for agents with no activity for 300+ seconds."""
    try:
        while True:
            await asyncio.sleep(SILENCE_CHECK_INTERVAL_SECONDS)

            if not mgr.is_running:
                continue

            now = time.time()
            for agent_name, state in list(mgr.agent_states.items()):
                if state.get("state") not in ("working", "waiting"):
                    mgr._silence_alerted.discard(agent_name)
                    continue

                last_activity_at = state.get("last_activity_at")
                if last_activity_at is None:
                    continue

                silent_seconds = now - last_activity_at
                if silent_seconds >= SILENCE_THRESHOLD_SECONDS:
                    if agent_name in mgr._silence_alerted:
                        continue
                    last_activity_type = state.get("last_activity_type", "unknown")
                    mgr._silence_alerted.add(agent_name)
                    logger.warning(
                        f"[{mgr.project_id}] Agent '{agent_name}' silent for "
                        f"{silent_seconds:.0f}s (last activity: {last_activity_type})"
                    )
                    await mgr._emit_event(
                        "agent_silent",
                        agent_name=agent_name,
                        silent_seconds=round(silent_seconds, 1),
                        last_activity_type=last_activity_type,
                    )
                else:
                    mgr._silence_alerted.discard(agent_name)

            # Critical silence — ALL working agents unresponsive
            _working_agents = [
                (name, st)
                for name, st in list(mgr.agent_states.items())
                if st.get("state") in ("working", "waiting")
            ]
            if _working_agents and mgr.is_running:
                _all_critical = all(
                    (now - st.get("last_activity_at", now)) >= SILENCE_CRITICAL_SECONDS
                    for _, st in _working_agents
                    if st.get("last_activity_at") is not None
                )
                _any_has_activity = any(
                    st.get("last_activity_at") is not None for _, st in _working_agents
                )
                if _all_critical and _any_has_activity:
                    _agent_names = [n for n, _ in _working_agents]
                    logger.critical(
                        f"[{mgr.project_id}] CRITICAL SILENCE: ALL working agents "
                        f"({_agent_names}) silent for >{SILENCE_CRITICAL_SECONDS}s. "
                        f"Session is likely stuck. Force-stopping."
                    )
                    mgr._stop_event.set()
                    mgr.is_running = False
                    try:
                        await mgr._send_final(
                            f"\u26a0\ufe0f **Session timed out** — all agents were unresponsive "
                            f"for over {SILENCE_CRITICAL_SECONDS // 60} minutes.\n\n"
                            f"This usually means an internal error caused the session to hang.\n"
                            f"\ud83d\udcac Send your message again to retry."
                        )
                    except Exception as _sf_err:
                        logger.debug(
                            "[%s] Watchdog: failed to send timeout notice: %s",
                            mgr.project_id,
                            _sf_err,
                        )
                    for name, st in _working_agents:
                        mgr.agent_states[name] = {
                            "state": "error",
                            "task": st.get("task", ""),
                            "cost": st.get("cost", 0),
                            "turns": st.get("turns", 0),
                            "duration": st.get("duration", 0),
                        }
                        try:
                            await mgr._emit_event(
                                "agent_finished",
                                agent=name,
                                cost=st.get("cost", 0),
                                turns=st.get("turns", 0),
                                duration=st.get("duration", 0),
                                is_error=True,
                                failure_reason="critical silence timeout",
                            )
                        except Exception as _ev_err:
                            logger.debug(
                                "[%s] Watchdog: failed to emit agent_finished: %s",
                                mgr.project_id,
                                _ev_err,
                            )

    except asyncio.CancelledError:
        pass  # Normal shutdown
    except Exception as e:
        logger.error(f"[{mgr.project_id}] Silence watchdog error: {e}", exc_info=True)


# ── Stuck detection ──────────────────────────────────────────────────────


def detect_stuck(mgr: OrchestratorManager) -> dict | None:
    """Detect if the orchestrator is stuck and suggest an escalation strategy.

    Returns None if not stuck, or a dict with:
      - signal: str — which signal triggered
      - severity: 'warning' | 'critical'
      - strategy: str — suggested escalation action
      - details: str — human-readable explanation

    Checks five signals:
    1. Orchestrator text similarity: last N responses are nearly identical
    2. Error-repeat: same agent failing with the same error 3+ times
    3. Circular delegations: same agent+task pattern repeating
    4. No file progress: multiple rounds with no new file changes
    5. Cost runaway: spending accelerating without progress
    """
    # --- Signal 1: orchestrator response similarity ---
    recent = [
        m.content
        for m in list(mgr.conversation_log)
        if m.agent_name == "orchestrator" and m.content
    ][-STUCK_WINDOW_SIZE:]

    if len(recent) >= STUCK_WINDOW_SIZE:
        all_similar = True
        for i in range(len(recent) - 1):
            ratio = SequenceMatcher(None, recent[i], recent[i + 1]).ratio()
            if ratio < STUCK_SIMILARITY_THRESHOLD:
                all_similar = False
                break
        if all_similar:
            logger.warning(
                f"[{mgr.project_id}] Stuck detected (text similarity): "
                f"last {len(recent)} orchestrator responses are >{STUCK_SIMILARITY_THRESHOLD:.0%} similar"
            )
            return {
                "signal": "text_similarity",
                "severity": "critical",
                "strategy": "change_approach",
                "details": (
                    f"Last {len(recent)} orchestrator responses are >85% similar. "
                    "The orchestrator is repeating the same delegations. "
                    "Try: (1) different agent for the task, (2) simpler sub-task, "
                    "(3) ask researcher to investigate the blocker."
                ),
            }

    # --- Signal 2: repeated identical failures ---
    if len(mgr.shared_context) >= 3:
        recent_ctx = mgr.shared_context[-6:]
        error_signatures: list[str] = []
        for ctx in recent_ctx:
            for line in ctx.split("\n"):
                stripped = line.strip()
                if stripped.startswith("Status: FAILED") or stripped.startswith("BLOCKED"):
                    error_signatures.append(stripped[:80])
                    break
        if len(error_signatures) >= 3:
            first = error_signatures[0]
            if all(
                SequenceMatcher(None, first, sig).ratio() > 0.70 for sig in error_signatures[1:]
            ):
                logger.warning(
                    f"[{mgr.project_id}] Stuck detected (repeated errors): "
                    f"same failure appearing {len(error_signatures)} times"
                )
                return {
                    "signal": "repeated_errors",
                    "severity": "critical",
                    "strategy": "simplify_task",
                    "details": (
                        f"Same error repeated {len(error_signatures)} times: "
                        f"{error_signatures[0][:60]}... "
                        "Try: (1) break the task into smaller pieces, "
                        "(2) delegate researcher to find a solution, "
                        "(3) skip this sub-task and move to the next one."
                    ),
                }

    # --- Signal 3: circular delegations ---
    if len(mgr._completed_rounds) >= 3:
        recent_rounds = mgr._completed_rounds[-6:]
        patterns = []
        for r in recent_rounds:
            parts = r.split(": ", 1)
            if len(parts) == 2:
                patterns.append(parts[1].strip().lower())
        if len(patterns) >= 3:
            if patterns[-1] == patterns[-2] == patterns[-3]:
                logger.warning(
                    f"[{mgr.project_id}] Stuck detected (circular delegations): "
                    f"same pattern '{patterns[-1]}' repeated 3 times"
                )
                return {
                    "signal": "circular_delegations",
                    "severity": "warning",
                    "strategy": "change_agents",
                    "details": (
                        f"Same delegation pattern repeated 3 times: {patterns[-1]}. "
                        "Try: (1) use different agents, (2) change the task description, "
                        "(3) add more context about what's failing."
                    ),
                }

    # --- Signal 4: no file progress ---
    if len(mgr._completed_rounds) >= 4:
        no_progress_count = 0
        for ctx in mgr.shared_context[-8:]:
            if "Files changed:" in ctx and "(none)" in ctx:
                no_progress_count += 1
            elif "REPORTS ONLY" in ctx or "wrote REPORTS but did NOT modify" in ctx:
                no_progress_count += 1
        if no_progress_count >= 3:
            logger.warning(
                f"[{mgr.project_id}] Stuck detected (no file progress): "
                f"{no_progress_count} rounds without file changes"
            )
            return {
                "signal": "no_file_progress",
                "severity": "warning",
                "strategy": "force_implementation",
                "details": (
                    f"{no_progress_count} rounds without any file changes. "
                    "Agents are producing reports but not implementing. "
                    "Try: (1) give developer a very specific file+function to create, "
                    "(2) provide example code in the context, "
                    "(3) reduce the scope to a single file."
                ),
            }

    # --- Signal 5: cost runaway without progress ---
    if mgr._current_loop >= 5 and mgr.total_cost_usd > 0:
        effective = getattr(mgr, "_effective_budget", MAX_BUDGET_USD) or MAX_BUDGET_USD
        budget_used_pct = mgr.total_cost_usd / effective
        progress_pct = mgr._current_loop / MAX_ORCHESTRATOR_LOOPS
        if budget_used_pct > 0.5 and progress_pct < 0.25:
            logger.warning(
                f"[{mgr.project_id}] Stuck detected (cost runaway): "
                f"{budget_used_pct:.0%} budget used at {progress_pct:.0%} progress"
            )
            return {
                "signal": "cost_runaway",
                "severity": "critical",
                "strategy": "reduce_scope",
                "details": (
                    f"Spent {budget_used_pct:.0%} of budget but only {progress_pct:.0%} through rounds. "
                    "Cost is accelerating without proportional progress. "
                    "Try: (1) reduce task scope, (2) use fewer agents per round, "
                    "(3) give agents shorter, more focused tasks."
                ),
            }

    return None


# ── Task complexity estimation ───────────────────────────────────────────


def estimate_task_complexity(task: str) -> str:
    """Classify task complexity to set the right orchestrator expectations.

    Returns: 'SIMPLE' | 'MEDIUM' | 'LARGE' | 'EPIC'

    Delegates to the unified classifier in blackboard.classify_complexity().
    """
    from blackboard import classify_complexity

    result = classify_complexity(text=task)
    return result.level


# ── Premature completion check ───────────────────────────────────────────


def check_premature_completion(mgr: OrchestratorManager, loop_count: int, task: str) -> str | None:
    """Validate whether TASK_COMPLETE is appropriate.

    Returns a reason string if premature, None if completion is acceptable.
    Uses the project manifest (persistent) AND conversation log (not just
    shared_context which is trimmed) to decide.
    """
    complexity = estimate_task_complexity(task)

    # Minimum rounds before TASK_COMPLETE is allowed (by complexity)
    min_rounds = {"SIMPLE": 2, "MEDIUM": 3, "LARGE": 4, "EPIC": 8}
    required = min_rounds.get(complexity, 2)

    if loop_count < required:
        return (
            f"Task complexity is **{complexity}** but only {loop_count} round(s) completed "
            f"(minimum {required} required). Continue working through the remaining phases."
        )

    # Check if any agent crashed or reported errors
    crashed_agents = []
    for ctx in mgr.shared_context:
        if "FAILED" in ctx or "crashed" in ctx.lower() or "session crashed" in ctx.lower():
            if ctx.startswith("["):
                agent_name = ctx[1 : ctx.find("]")] if "]" in ctx else "unknown"
                crashed_agents.append(agent_name)
    if crashed_agents:
        return (
            f"Agent(s) {', '.join(set(crashed_agents))} crashed or failed during execution. "
            f"You MUST retry their tasks before declaring TASK_COMPLETE. "
            f"Delegate the failed work again with a fresh approach."
        )

    # Check that actual file changes were made (for tasks that require code work)
    task_lower = task.lower()
    is_code_task = not all(
        keyword in task_lower
        for keyword in ("research", "report", "document", "explain", "summarize")
    )
    if is_code_task:
        try:
            has_file_changes = any(
                "Files changed:" in ctx and "(none)" not in ctx for ctx in mgr.shared_context
            )
            if not has_file_changes:
                has_success = any("OK" in r for r in mgr._completed_rounds)
                if not has_success:
                    return (
                        "No file changes detected and no successful agent rounds recorded. "
                        "The task requires actual code changes. Delegate the implementation work."
                    )
        except Exception as _exc:
            logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)

    # For any non-trivial task, require at least one writer + one reviewer
    _reviewer_roles = {"reviewer", "security_auditor", "ux_critic"}
    if complexity != "SIMPLE":
        if not mgr._agents_used & mgr._WRITER_ROLES:
            return "No code-writing agent has been used yet. Delegate implementation work."
        if not mgr._agents_used & _reviewer_roles:
            return "No review agent has been used yet. Delegate a code review before completing."

    # Require at least 2 different agents
    if len(mgr._agents_used) < 2:
        return (
            f"Only {len(mgr._agents_used)} agent(s) used ({', '.join(mgr._agents_used) or 'none'}). "
            f"A proper workflow requires at least 2 agents (e.g., developer + reviewer). "
            f"Delegate more work before completing."
        )

    # Check for unresolved issues
    _issue_keywords = ("CRITICAL", "HIGH", "VULNERABILITY", "FAIL", "FAILED", "BROKEN")
    recent_issues = []
    for ctx in mgr.shared_context[-6:]:
        if any(kw in ctx.upper() for kw in _issue_keywords):
            if any(role in ctx.lower() for role in ("reviewer", "tester", "researcher")):
                recent_issues.append(ctx[:100])
    if recent_issues and loop_count < required + 2:
        return (
            f"Agents reported {len(recent_issues)} issue(s) with CRITICAL/HIGH/FAIL severity "
            f"in recent rounds. These must be FIXED (not just reported) before TASK_COMPLETE. "
            f"Delegate developer to fix the issues found by reviewer/tester."
        )

    # Check for report-only agents
    report_only_agents = []
    for ctx in mgr.shared_context[-6:]:
        if "REPORTS ONLY" in ctx or "wrote REPORTS but did NOT modify" in ctx:
            report_only_agents.append(ctx[:80])
    if report_only_agents and loop_count < required + 2:
        return (
            "Agents produced reports/reviews but no actual code fixes were implemented. "
            "Reports are INPUT for the next round — delegate developer to implement the fixes."
        )

    # Any agents blocked or needing followup?
    outstanding = [ctx for ctx in mgr.shared_context if "BLOCKED" in ctx or "NEEDS_FOLLOWUP" in ctx]
    if outstanding:
        return (
            f"{len(outstanding)} agent(s) are BLOCKED or have NEEDS_FOLLOWUP items. "
            f"Resolve all outstanding items before declaring complete."
        )

    # For MEDIUM+: require reviewer
    if complexity in ("MEDIUM", "LARGE", "EPIC"):
        reviewer_ran = "reviewer" in mgr._agents_used
        manifest = _read_project_manifest(mgr)
        if manifest:
            manifest_lower = manifest.lower()
            if "## issues log" in manifest_lower and len(manifest_lower) > 100:
                reviewer_ran = True
        if not reviewer_ran:
            return "Code has not been reviewed. Delegate reviewer before completing."

    # For LARGE and EPIC: require both reviewer and tester
    if complexity in ("LARGE", "EPIC"):
        tester_ran = "tester" in mgr._agents_used
        reviewer_ran = "reviewer" in mgr._agents_used

        manifest = _read_project_manifest(mgr)
        if manifest:
            manifest_lower = manifest.lower()
            if "## test results" in manifest_lower and (
                "passed" in manifest_lower or "failed" in manifest_lower
            ):
                tester_ran = True
            if "## issues log" in manifest_lower and len(manifest_lower) > 100:
                reviewer_ran = True

        if not tester_ran and not reviewer_ran:
            return (
                "For a task of this complexity, tests must be run AND code must be "
                "reviewed before TASK_COMPLETE. Delegate reviewer + tester now."
            )
        if not tester_ran:
            return "Tests have not been run. Delegate tester to verify the implementation works."
        if not reviewer_ran:
            return "Code has not been reviewed. Delegate reviewer before completing."

    return None  # Completion is acceptable


# ── Helpers ──────────────────────────────────────────────────────────────


def _read_project_manifest(mgr: OrchestratorManager) -> str:
    """Read .hivemind/PROJECT_MANIFEST.md — the team's persistent shared memory."""
    manifest_path = Path(mgr.project_dir) / ".hivemind" / "PROJECT_MANIFEST.md"
    if manifest_path.exists():
        try:
            content = manifest_path.read_text(encoding="utf-8").strip()
            if content:
                truncated = content[:3000]
                if len(content) > 3000:
                    truncated += "\n... (manifest truncated — read the full file for details)"
                return truncated
        except Exception as _exc:
            logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)
    return ""
