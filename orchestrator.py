from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Awaitable

from config import (
    AGENT_TIMEOUT_SECONDS,
    MAX_BUDGET_USD,
    MAX_ORCHESTRATOR_LOOPS,
    MAX_TURNS_PER_CYCLE,
    ORCHESTRATOR_SYSTEM_PROMPT,
    SDK_MAX_TURNS_PER_QUERY,
    SDK_MAX_BUDGET_PER_QUERY,
    SESSION_TIMEOUT_SECONDS,
    SOLO_AGENT_PROMPT,
    STUCK_SIMILARITY_THRESHOLD,
    STUCK_WINDOW_SIZE,
    SUB_AGENT_PROMPTS,
    RATE_LIMIT_SECONDS,
    BUDGET_WARNING_THRESHOLD,
    SPECIALIST_PROMPTS,
    get_specialist_prompt,
)
from sdk_client import ClaudeSDKManager, SDKResponse
from isolated_query import isolated_query
from session_manager import SessionManager
from skills_registry import get_skills_for_agent, select_skills_for_task, build_skill_prompt

# --- Typed Contract Protocol (new DAG-based system) ---
# Imported lazily inside _run_dag_session to avoid circular imports
# (orchestrator → pm_agent → state → orchestrator)
from contracts import TaskGraph, TaskInput, TaskOutput, AgentRole

logger = logging.getLogger(__name__)

# Agent emoji map for clear visual identification
AGENT_EMOJI = {
    # Layer 1: Brain
    "pm":                 "🧠",
    "orchestrator":       "🎯",
    "memory":             "📚",
    # Layer 2: Execution
    "frontend_developer": "🎨",
    "backend_developer":  "⚡",
    "database_expert":    "🗄️",
    "devops":             "🚀",
    # Layer 3: Quality
    "security_auditor":   "🔐",
    "test_engineer":      "🧪",
    "reviewer":           "🔍",
    "researcher":         "🔎",
    "ux_critic":          "🎭",
    # Legacy aliases
    "developer":          "💻",
    "tester":             "🧪",
    "user":               "👤",
}

# Regex to parse <delegate> blocks from orchestrator output
# Match everything between <delegate> and </delegate> tags, then parse JSON separately
_DELEGATE_RE = re.compile(
    r"<delegate>\s*(.*?)\s*</delegate>",
    re.DOTALL,
)


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from text, handling nested braces properly."""
    # Find the first { and match to its closing }
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':  # escape is always False here (handled above with continue)
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


@dataclass
class Message:
    agent_name: str
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    cost_usd: float = 0.0


@dataclass
class Delegation:
    agent: str
    task: str
    context: str = ""
    skills: list[str] = field(default_factory=list)


class OrchestratorManager:
    """Orchestrator-based agent management, replacing the round-robin AgentManager.

    The orchestrator agent receives user tasks and decides whether to handle
    directly or delegate to sub-agents via <delegate> blocks.
    """

    def __init__(
        self,
        project_name: str,
        project_dir: str,
        sdk: ClaudeSDKManager,
        session_mgr: SessionManager,
        user_id: int,
        project_id: str,
        on_update: Callable[[str], Awaitable[None]] | None = None,
        on_result: Callable[[str], Awaitable[None]] | None = None,
        on_final: Callable[[str], Awaitable[None]] | None = None,
        on_event: Callable[[dict], Awaitable[None]] | None = None,
        multi_agent: bool = True,
    ):
        self.project_name = project_name
        self.project_dir = project_dir
        self.sdk = sdk
        self.session_mgr = session_mgr
        self.user_id = user_id
        self.project_id = project_id
        self.on_update = on_update
        self.on_result = on_result
        self.on_final = on_final
        self.on_event = on_event
        self.multi_agent = multi_agent

        self.conversation_log: list[Message] = []
        self._agents_used: set[str] = set()  # All agent roles that have run — persists even if log is trimmed
        self.is_running = False
        self.is_paused = False
        self.total_cost_usd = 0.0
        self.turn_count = 0

        # Live state tracking for dashboard
        self.current_agent: str | None = None
        self.current_tool: str | None = None
        self.agent_states: dict[str, dict] = {}  # agent_name -> {state, task, cost, turns, ...}

        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

        # Message queue — replaces single-slot _user_injection to prevent lost messages
        # when multiple agents or the user send messages concurrently.
        self._message_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        # HITL approval mechanism
        self._approval_event = asyncio.Event()
        self._approval_result: bool = True  # True = approved, False = rejected
        self._pending_approval: str | None = None

        # Shared context accumulator — passes summary of previous rounds to sub-agents
        self.shared_context: list[str] = []

        # Track completed rounds for summaries and final reporting
        self._completed_rounds: list[str] = []

        # Track fire-and-forget tasks to prevent GC and log errors
        self._background_tasks: set[asyncio.Task] = set()

        # Current orchestrator loop count (readable by /live endpoint)
        self._current_loop: int = 0
        # Effective budget (respects per-project override)
        self._effective_budget: float = MAX_BUDGET_USD

    @property
    def agent_names(self) -> list[str]:
        names = ["orchestrator"]
        if self.multi_agent:
            # Include both base roles and specialist roles
            all_roles = set(SUB_AGENT_PROMPTS.keys()) | set(SPECIALIST_PROMPTS.keys())
            names.extend(sorted(all_roles))
        return names

    @property
    def is_multi_agent(self) -> bool:
        return self.multi_agent

    def _create_background_task(self, coro) -> asyncio.Task:
        """Create a background task with proper lifecycle management.

        - Prevents GC from collecting the task (strong reference in self._background_tasks)
        - Logs errors instead of silently swallowing them
        - Auto-removes from the set when done
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _on_done(t: asyncio.Task):
            self._background_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error(f"[{self.project_id}] Background task failed: {exc}", exc_info=exc)

        task.add_done_callback(_on_done)
        return task

    def _on_task_done(self, task: asyncio.Task):
        """Callback attached to the main _run_orchestrator task.

        Catches silent crashes that would otherwise go unnoticed and
        auto-restarts if there are pending messages in the queue.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(
                f"[{self.project_id}] Orchestrator task crashed: {exc}",
                exc_info=exc,
            )
        # If messages arrived while the task was finishing, restart
        if not self._message_queue.empty() and not self._stop_event.is_set():
            pending_parts: list[str] = []
            while not self._message_queue.empty():
                try:
                    _target, msg = self._message_queue.get_nowait()
                    pending_parts.append(msg)
                except asyncio.QueueEmpty:
                    break
            if pending_parts:
                combined = "\n\n---\n\n".join(pending_parts)
                logger.info(
                    f"[{self.project_id}] {len(pending_parts)} pending message(s) "
                    f"found after task ended — auto-restarting"
                )
                # Schedule a new session (can't await from a sync callback)
                self._create_background_task(self.start_session(combined))

    async def _notify(self, text: str):
        """Send a progress/status update to the client."""
        if self.on_update:
            try:
                await self.on_update(text)
            except Exception as e:
                logger.error(f"Update callback error: {e}")

    async def _send_result(self, text: str):
        """Send a final result message to the client."""
        if self.on_result:
            try:
                await self.on_result(text)
            except Exception as e:
                logger.error(f"Result callback error: {e}")
        elif self.on_update:
            # Fallback to on_update if on_result not set
            try:
                await self.on_update(text)
            except Exception as e:
                logger.error(f"Update callback error: {e}")

    async def _send_final(self, text: str):
        """Send the final clean message (deletes all intermediates, stays forever).

        Also persists to SQLite so the message survives a browser refresh —
        without this, a page reload during/after task completion shows a blank result
        because the event_bus has no subscribers to catch the WS-only event.
        """
        # Persist to DB first so it survives regardless of WS connectivity
        if self.session_mgr and self.project_id:
            self._create_background_task(
                self.session_mgr.add_message(
                    self.project_id, "system", "System", text, 0.0,
                )
            )

        if self.on_final:
            try:
                await self.on_final(text)
            except Exception as e:
                logger.error(f"Final callback error: {e}")
        else:
            # Fallback to on_result
            await self._send_result(text)

    async def _emit_event(self, event_type: str, **data):
        """Emit a structured event for the dashboard."""
        if self.on_event:
            try:
                event = {"type": event_type, "timestamp": time.time(), **data}
                await self.on_event(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

    def _detect_stuck(self) -> dict | None:
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
            m.content for m in list(self.conversation_log)
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
                    f"[{self.project_id}] Stuck detected (text similarity): "
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
        if len(self.shared_context) >= 3:
            recent_ctx = self.shared_context[-6:]
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
                    SequenceMatcher(None, first, sig).ratio() > 0.70
                    for sig in error_signatures[1:]
                ):
                    logger.warning(
                        f"[{self.project_id}] Stuck detected (repeated errors): "
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
        # Check if the same agent is getting the same task pattern 3+ times
        if len(self._completed_rounds) >= 3:
            recent_rounds = self._completed_rounds[-6:]
            # Extract agent patterns from round summaries like "Round 5: developer(OK), reviewer(ERR)"
            patterns = []
            for r in recent_rounds:
                # Normalize: extract just the agent names and statuses
                parts = r.split(": ", 1)
                if len(parts) == 2:
                    patterns.append(parts[1].strip().lower())
            if len(patterns) >= 3:
                # Check if last 3 patterns are identical
                if patterns[-1] == patterns[-2] == patterns[-3]:
                    logger.warning(
                        f"[{self.project_id}] Stuck detected (circular delegations): "
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
        # If 3+ rounds passed with no file changes, we're spinning wheels
        if len(self._completed_rounds) >= 4:
            no_progress_count = 0
            for ctx in self.shared_context[-8:]:
                if "Files changed:" in ctx and "(none)" in ctx:
                    no_progress_count += 1
                elif "REPORTS ONLY" in ctx or "wrote REPORTS but did NOT modify" in ctx:
                    no_progress_count += 1
            if no_progress_count >= 3:
                logger.warning(
                    f"[{self.project_id}] Stuck detected (no file progress): "
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
        # If we've spent >50% of budget but completed <25% of expected rounds
        if self._current_loop >= 5 and self.total_cost_usd > 0:
            from config import MAX_BUDGET_USD
            budget_used_pct = self.total_cost_usd / MAX_BUDGET_USD
            progress_pct = self._current_loop / MAX_ORCHESTRATOR_LOOPS
            if budget_used_pct > 0.5 and progress_pct < 0.25:
                logger.warning(
                    f"[{self.project_id}] Stuck detected (cost runaway): "
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

    def _read_project_manifest(self) -> str:
        """Read .nexus/PROJECT_MANIFEST.md — the team's persistent shared memory.

        Returns the manifest content (truncated to 3000 chars) or empty string if not found.
        Called in every review round so the orchestrator always has the current project state.
        """
        manifest_path = Path(self.project_dir) / ".nexus" / "PROJECT_MANIFEST.md"
        if manifest_path.exists():
            try:
                content = manifest_path.read_text(encoding="utf-8").strip()
                if content:
                    truncated = content[:3000]
                    if len(content) > 3000:
                        truncated += "\n... (manifest truncated — read the full file for details)"
                    return truncated
            except Exception:
                pass
        return ""

    def _estimate_task_complexity(self, task: str) -> str:
        """Classify task complexity to set the right orchestrator expectations.

        Returns: 'SIMPLE' | 'MEDIUM' | 'LARGE' | 'EPIC'
        """
        t = task.lower()

        # EPIC: building entire apps / systems / platforms
        epic_patterns = [
            "build an app", "build a app", "create an app", "develop an app",
            "build a system", "create a system", "full application", "complete app",
            "full stack", "fullstack", "from scratch", "entire system",
            "build a website", "create a website", "build a platform",
            "saas", "e-commerce", "ecommerce", "real-time app",
            "microservice", "full implementation", "complete system",
            "build me", "create me", "write me a complete",
            # Hebrew
            "תבנה אפליקציה", "צור אפליקציה", "פלטפורמה", "מערכת שלמה",
            "תכתוב לי מערכת", "תבנה לי", "אפליקציה מאפס",
        ]
        if any(p in t for p in epic_patterns):
            return "EPIC"

        # LARGE: significant features / services
        large_patterns = [
            "authentication", "auth system", "new feature", "add feature",
            "refactor", "add module", "create service", "implement",
            "integrate", "database schema", "api endpoint", "rest api",
            "graphql", "user management", "payment", "notification",
        ]
        # Long detailed task description also signals LARGE
        word_count = len(task.split())
        if word_count > 60 or any(p in t for p in large_patterns):
            return "LARGE"

        # MEDIUM: adding things, updates, moderate work
        medium_patterns = [
            "add", "update", "change", "modify", "improve", "enhance",
            "create", "write", "make", "implement", "migrate",
        ]
        if any(p in t for p in medium_patterns):
            return "MEDIUM"

        # Default: simple bug fixes, config changes, explanations
        return "SIMPLE"

    def _check_premature_completion(self, loop_count: int, task: str) -> str | None:
        """Validate whether TASK_COMPLETE is appropriate. Returns a reason string if premature.

        Uses the project manifest (persistent) AND conversation log (not just shared_context
        which is trimmed) to decide — so agents that ran 30+ rounds ago are still detected.
        Returns None if completion is acceptable, or a non-empty string explaining why not.
        """
        complexity = self._estimate_task_complexity(task)

        # Minimum rounds before TASK_COMPLETE is allowed (by complexity)
        # Raised minimums: even SIMPLE tasks need at least 2 rounds (implement + verify)
        min_rounds = {"SIMPLE": 2, "MEDIUM": 3, "LARGE": 4, "EPIC": 8}
        required = min_rounds.get(complexity, 2)

        if loop_count < required:
            return (
                f"Task complexity is **{complexity}** but only {loop_count} round(s) completed "
                f"(minimum {required} required). Continue working through the remaining phases."
            )

        # Check if any agent crashed or reported errors — must retry before completing
        crashed_agents = []
        for ctx in self.shared_context:
            if "FAILED" in ctx or "crashed" in ctx.lower() or "session crashed" in ctx.lower():
                # Extract agent name from context entry like "[developer] Round 1 | Task: ..."
                if ctx.startswith("["):
                    agent_name = ctx[1:ctx.find("]")] if "]" in ctx else "unknown"
                    crashed_agents.append(agent_name)
        if crashed_agents:
            return (
                f"Agent(s) {', '.join(set(crashed_agents))} crashed or failed during execution. "
                f"You MUST retry their tasks before declaring TASK_COMPLETE. "
                f"Delegate the failed work again with a fresh approach."
            )

        # Check that actual file changes were made (for tasks that require code work)
        # Skip this check only for pure research/documentation tasks
        task_lower = task.lower()
        is_code_task = not all(
            keyword in task_lower
            for keyword in ("research", "report", "document", "explain", "summarize")
        )
        if is_code_task:
            try:
                import asyncio
                # Use a synchronous check — we're in a sync method
                # Check the last round's results in shared_context for file changes
                has_file_changes = any(
                    "Files changed:" in ctx and "(none)" not in ctx
                    for ctx in self.shared_context
                )
                if not has_file_changes:
                    # Also check _completed_rounds for any successful work
                    has_success = any("OK" in r for r in self._completed_rounds)
                    if not has_success:
                        return (
                            "No file changes detected and no successful agent rounds recorded. "
                            "The task requires actual code changes. Delegate the implementation work."
                        )
            except Exception:
                pass  # Don't block completion on detection failure

        # For any non-trivial task, require at least developer + reviewer to have run
        if complexity != "SIMPLE":
            if "developer" not in self._agents_used:
                return "Developer agent has not been used yet. Delegate implementation work."
            if "reviewer" not in self._agents_used:
                return "Reviewer agent has not been used yet. Delegate a code review before completing."

        # For all tasks: require at least 2 different agents to have been used
        if len(self._agents_used) < 2:
            return (
                f"Only {len(self._agents_used)} agent(s) used ({', '.join(self._agents_used) or 'none'}). "
                f"A proper workflow requires at least 2 agents (e.g., developer + reviewer). "
                f"Delegate more work before completing."
            )

        # Check if agents found issues (CRITICAL/HIGH/BUG/FAIL) that haven't been addressed
        # Look at the most recent round's context for unresolved action items
        _issue_keywords = ("CRITICAL", "HIGH", "VULNERABILITY", "FAIL", "FAILED", "BROKEN")
        recent_issues = []
        for ctx in self.shared_context[-6:]:
            if any(kw in ctx.upper() for kw in _issue_keywords):
                # Check if this is from a reviewer/tester (finding issues) vs developer (fixing)
                if any(role in ctx.lower() for role in ("reviewer", "tester", "researcher")):
                    recent_issues.append(ctx[:100])
        # Only block if there are recent unfixed issues AND we haven't done many rounds
        if recent_issues and loop_count < required + 2:
            return (
                f"Agents reported {len(recent_issues)} issue(s) with CRITICAL/HIGH/FAIL severity "
                f"in recent rounds. These must be FIXED (not just reported) before TASK_COMPLETE. "
                f"Delegate developer to fix the issues found by reviewer/tester."
            )

        # Check if agents only wrote reports without actual code changes
        report_only_agents = []
        for ctx in self.shared_context[-6:]:
            if "REPORTS ONLY" in ctx or "wrote REPORTS but did NOT modify" in ctx:
                report_only_agents.append(ctx[:80])
        if report_only_agents and loop_count < required + 2:
            return (
                "Agents produced reports/reviews but no actual code fixes were implemented. "
                "Reports are INPUT for the next round — delegate developer to implement the fixes."
            )

        # Any agents blocked or needing followup? (check shared_context — most recent)
        outstanding = [
            ctx for ctx in self.shared_context
            if "BLOCKED" in ctx or "NEEDS_FOLLOWUP" in ctx
        ]
        if outstanding:
            return (
                f"{len(outstanding)} agent(s) are BLOCKED or have NEEDS_FOLLOWUP items. "
                f"Resolve all outstanding items before declaring complete."
            )

        # For MEDIUM and above: require reviewer to have run
        if complexity in ("MEDIUM", "LARGE", "EPIC"):
            reviewer_ran = "reviewer" in self._agents_used
            manifest = self._read_project_manifest()
            if manifest:
                manifest_lower = manifest.lower()
                if "## issues log" in manifest_lower and len(manifest_lower) > 100:
                    reviewer_ran = True
            if not reviewer_ran:
                return "Code has not been reviewed. Delegate reviewer before completing."

        # For LARGE and EPIC: require both reviewer and tester to have run.
        # Use _agents_used (persists across log trimming) + manifest as belt-and-suspenders.
        if complexity in ("LARGE", "EPIC"):
            tester_ran = "tester" in self._agents_used
            reviewer_ran = "reviewer" in self._agents_used

            # Also check the manifest for test results (belt-and-suspenders)
            manifest = self._read_project_manifest()
            if manifest:
                manifest_lower = manifest.lower()
                if "## test results" in manifest_lower and ("passed" in manifest_lower or "failed" in manifest_lower):
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

    async def _build_final_summary(self, user_message: str, start_time: float, status: str = "Done") -> str:
        """Build a clean final status message."""
        duration = time.monotonic() - start_time
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        duration_str = f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"

        task_preview = user_message[:100]
        if len(user_message) > 100:
            task_preview += "..."

        agents_used = list(dict.fromkeys(
            m.agent_name for m in self.conversation_log if m.agent_name != "user"
        ))
        # Also include any agents from _agents_used that were trimmed from conversation_log
        for a in sorted(self._agents_used - set(agents_used)):
            if a != "user":
                agents_used.append(a)
        agents_str = " → ".join(agents_used) if agents_used else "orchestrator"

        file_changes = await self._detect_file_changes()
        changes_str = ""
        if file_changes and "(no file" not in file_changes:
            changes_str = f"\n\n📝 Changes:\n```\n{file_changes}\n```"

        # Show what was accomplished each round
        rounds_str = ""
        if self._completed_rounds:
            rounds_str = "\n\n🔄 Rounds:\n" + "\n".join(f"  {r}" for r in self._completed_rounds)

        return (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ {self.project_name} — {status}\n\n"
            f"📋 Task: {task_preview}\n"
            f"🤖 Agents: {agents_str}\n"
            f"⏱ {duration_str} | 📊 {self.turn_count} turns | 💰 ${self.total_cost_usd:.2f}"
            f"{rounds_str}"
            f"{changes_str}\n\n"
            f"Send another message to continue.\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

    def _get_workspace_context(self) -> str:
        """Scan project directory and return a short file listing (2 levels deep)."""
        entries = []
        try:
            for item in sorted(Path(self.project_dir).iterdir()):
                if item.name.startswith('.') or item.name in ('__pycache__', 'node_modules', '.git', 'venv', '.venv'):
                    continue
                if item.is_dir():
                    entries.append(f"  {item.name}/")
                    try:
                        for sub in sorted(item.iterdir()):
                            if sub.name.startswith('.') or sub.name == '__pycache__':
                                continue
                            entries.append(f"    {sub.name}{'/' if sub.is_dir() else ''}")
                            if len(entries) >= 50:
                                break
                    except PermissionError:
                        pass
                else:
                    entries.append(f"  {item.name}")
                if len(entries) >= 50:
                    entries.append("  ... (truncated)")
                    break
        except Exception:
            entries = ["  (unable to list files)"]

        if not entries:
            return ""
        return "Current workspace files:\n" + "\n".join(entries)

    async def start_session(self, user_message: str):
        """Start processing a user message.

        Routing:
        - is_multi_agent=True + USE_DAG_EXECUTOR env var → new Typed Contract / DAG system
        - is_multi_agent=True (no env var) → legacy regex-delegate system
        - is_multi_agent=False → solo mode
        """
        if self.is_running:
            logger.warning(f"[{self.project_id}] start_session called but already running")
            await self._notify("Session is already running.")
            return

        use_dag = self.multi_agent and os.getenv("USE_DAG_EXECUTOR", "false").lower() == "true"
        logger.info(
            f"[{self.project_id}] Starting session: "
            f"multi_agent={self.multi_agent} dag={use_dag} message={user_message[:80]}"
        )
        self.is_running = True
        self._stop_event.clear()
        self._pause_event.set()
        self.turn_count = 0

        await self._emit_event("project_status", status="running")
        # Immediate feedback so the UI shows activity right away
        self.agent_states["orchestrator"] = {
            "state": "working",
            "task": "preparing workspace...",
        }
        await self._emit_event(
            "agent_started",
            agent="orchestrator",
            task="preparing workspace...",
        )
        await self.session_mgr.invalidate_session(self.user_id, self.project_id, "orchestrator")

        if use_dag:
            self._task = asyncio.create_task(
                self._run_dag_session(user_message)
            )
        else:
            self._task = asyncio.create_task(
                self._run_orchestrator(user_message)
            )
        self._task.add_done_callback(self._on_task_done)

    async def inject_user_message(self, agent_name: str, message: str):
        """Inject a user message into the orchestrator or a sub-agent.

        Uses an asyncio.Queue so multiple concurrent messages are never lost.
        """
        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=f"[to {agent_name}] {message}")
        )
        self._create_background_task(
            self.session_mgr.add_message(
                self.project_id, "user", "User", f"[to {agent_name}] {message}"
            )
        )

        if not self.is_running:
            # Not running — send directly to the requested agent (or orchestrator if unknown)
            target = agent_name if (agent_name in SUB_AGENT_PROMPTS or agent_name == "orchestrator") else "orchestrator"
            await self._notify(f"📨 Sending to *{target}*...")
            response = await self._query_agent(target, message)
            self._record_response(target, target.title(), response)
            self.turn_count += 1  # Track this turn for cost/limit accounting

            summary = response.text[:3000]
            if len(response.text) > 3000:
                summary += "\n... (truncated)"
            cost_str = f" | ${response.cost_usd:.4f}" if response.cost_usd > 0 else ""
            await self._send_final(f"💬 *{target}*{cost_str}\n\n{summary}")
        else:
            # Enqueue — the orchestrator loop will drain all pending messages
            await self._message_queue.put((agent_name, message))
            logger.info(f"[{self.project_id}] Queued message for {agent_name} (queue size: {self._message_queue.qsize()})")
            if self.is_paused:
                self.resume()

    def pause(self):
        if self.is_running and not self.is_paused:
            self.is_paused = True
            self._pause_event.clear()
            logger.info("Session paused")

    async def _self_pause(self, reason: str = "paused"):
        """Pause from within the orchestrator loop — emits project_status so frontend updates."""
        self.is_paused = True
        self._pause_event.clear()
        logger.info(f"[{self.project_id}] Self-paused: {reason}")
        await self._emit_event("project_status", status="paused", reason=reason)

    def resume(self):
        if self.is_paused:
            self.is_paused = False
            self._pause_event.set()
            logger.info("Session resumed")

    async def stop(self):
        import traceback
        caller = ''.join(traceback.format_stack(limit=4))
        logger.info(f"[{self.project_id}] stop() called. Caller:\n{caller}")
        self._stop_event.set()
        self.is_running = False
        self.is_paused = False
        self._pause_event.set()
        self._approval_event.set()  # Unblock any pending approval

        # Cancel main orchestrator task
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Wait for background tasks to finish (with timeout)
        if self._background_tasks:
            logger.info(f"[{self.project_id}] Waiting for {len(self._background_tasks)} background tasks...")
            pending = list(self._background_tasks)
            done, still_pending = await asyncio.wait(pending, timeout=5.0)
            for t in still_pending:
                t.cancel()
            if still_pending:
                logger.warning(f"[{self.project_id}] Cancelled {len(still_pending)} stuck background tasks")

        # Drain any remaining queued messages
        drained = 0
        while not self._message_queue.empty():
            try:
                self._message_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.info(f"[{self.project_id}] Drained {drained} queued messages on stop")

        await self._send_final(
            f"🛑 Project *{self.project_name}* stopped.\n"
            f"📊 Turns: {self.turn_count} | 💰 Cost: ${self.total_cost_usd:.4f}"
        )

    async def request_approval(self, description: str) -> bool:
        """Request human approval before proceeding. Blocks until approved/rejected."""
        self._pending_approval = description
        self._approval_event.clear()
        self._approval_result = True

        await self._emit_event(
            "approval_request",
            description=description,
        )
        await self._notify(f"⏸️ Approval needed: {description}")

        # Wait for approval or stop
        await self._approval_event.wait()
        self._pending_approval = None
        return self._approval_result

    def approve(self):
        """Approve the pending request."""
        self._approval_result = True
        self._approval_event.set()

    def reject(self):
        """Reject the pending request."""
        self._approval_result = False
        self._approval_event.set()

    @property
    def pending_approval(self) -> str | None:
        return self._pending_approval

    # --- DAG-based session (new Typed Contract system) ---

    async def _run_dag_session(self, user_message: str):
        """
        Execution path using the Typed Contract Protocol v2:

        1. Load Memory  → read existing MemorySnapshot for context continuity
        2. PM Agent     → creates TaskGraph (structured, typed, artifact-aware)
        3. DAG Executor → runs tasks with self-healing, artifact passing, smart retry
        4. Memory Agent → updates project memory from all task outputs
        5. Summary      → returned to user as final output

        Falls back to legacy _run_orchestrator if PM fails.
        """
        # Lazy imports to avoid circular dependency
        from pm_agent import create_task_graph, fallback_single_task_graph
        from dag_executor import execute_graph, build_execution_summary, ExecutionResult
        from memory_agent import update_project_memory

        try:
            # Ensure .nexus/ directory exists for memory persistence
            nexus_dir = Path(self.project_dir) / ".nexus"
            nexus_dir.mkdir(parents=True, exist_ok=True)

            await self._notify("🧠 **Loading project memory...**")

            # Step 0: Load project memory for context continuity
            manifest = await self._load_manifest()
            memory_snapshot = await self._load_memory_snapshot()
            file_tree = self._list_workspace_files()

            if memory_snapshot:
                await self._notify("📚 Found existing project memory — PM will use it for context.")

            await self._notify("🗺️ **PM Agent** is creating the execution plan...")

            # Step 1: PM Agent → TaskGraph (now with memory context)
            try:
                graph = await create_task_graph(
                    user_message=user_message,
                    project_id=self.project_id,
                    manifest=manifest,
                    file_tree=file_tree,
                    memory_snapshot=memory_snapshot,
                )
            except Exception as pm_err:
                logger.warning(f"[{self.project_id}] PM Agent failed: {pm_err}. Using fallback.")
                graph = fallback_single_task_graph(user_message, self.project_id)

            # Report the plan with artifact requirements
            artifact_count = sum(len(t.required_artifacts) for t in graph.tasks)
            await self._notify(
                f"📋 **Plan ready:** {graph.vision}\n"
                f"Tasks: {len(graph.tasks)} | "
                f"Epics: {len(graph.epic_breakdown)} | "
                f"Required artifacts: {artifact_count}"
            )

            # Emit the full graph to the frontend for visualization
            await self._emit_event("task_graph", graph=graph.model_dump())

            # Session IDs shared across tasks (agent resume)
            session_id_store: dict[str, str] = {}

            # Step 2: DAG Executor (now with self-healing + artifact passing)
            result: ExecutionResult = await execute_graph(
                graph=graph,
                project_dir=self.project_dir,
                specialist_prompts=SPECIALIST_PROMPTS,
                sdk_client=self.sdk,
                on_task_start=self._on_dag_task_start,
                on_task_done=self._on_dag_task_done,
                on_remediation=self._on_dag_remediation,
                max_budget_usd=self._effective_budget,
                session_id_store=session_id_store,
            )

            # Step 3: Memory Agent — update project knowledge
            try:
                await self._notify("🧠 **Memory Agent** is updating project knowledge...")
                await update_project_memory(
                    project_dir=self.project_dir,
                    project_id=self.project_id,
                    graph=graph,
                    outputs=result.outputs,
                    use_llm=len(result.outputs) >= 3,
                )
                await self._notify("📝 Project memory updated successfully.")
            except Exception as mem_err:
                logger.warning(f"[{self.project_id}] Memory Agent failed (non-fatal): {mem_err}")

            # Step 4: Final summary with healing history
            summary = build_execution_summary(graph, result)
            if result.healing_history:
                summary += f"\n\n🔧 **Self-healing activated:** {result.remediation_count} auto-fixes applied."
            await self._send_final(summary)

            # Record outputs in conversation log for persistence
            for output in result.outputs:
                self._record_dag_output(output)

            # Update total cost
            self.total_cost_usd += result.total_cost

        except asyncio.CancelledError:
            logger.info(f"[{self.project_id}] DAG session cancelled")
        except Exception as exc:
            logger.exception(f"[{self.project_id}] DAG session error: {exc}")
            await self._send_final(f"❌ DAG execution error: {exc}")
        finally:
            self.is_running = False
            self.turn_count += 1
            await self._emit_event("project_status", status="idle")

    async def _on_dag_task_start(self, task: "TaskInput"):
        """Callback: fired when DAG executor starts a task."""
        prefix = "🔧 " if task.is_remediation else ""
        required = ""
        if task.required_artifacts:
            art_names = [a.value for a in task.required_artifacts]
            required = f" | Artifacts: {', '.join(art_names)}"
        await self._emit_event(
            "agent_update",
            agent=task.role.value,
            status="working",
            task=task.goal[:120],
            is_remediation=task.is_remediation,
        )
        await self._notify(
            f"🔄 {prefix}**{task.role.value}** starting: {task.goal[:80]}...{required}"
        )

    async def _on_dag_task_done(self, task: "TaskInput", output: "TaskOutput"):
        """Callback: fired when DAG executor completes a task."""
        icon = "✅" if output.is_successful() else "❌"
        prefix = "🔧 " if task.is_remediation else ""
        artifact_info = ""
        if output.structured_artifacts:
            art_names = [a.title for a in output.structured_artifacts[:3]]
            artifact_info = f" | Artifacts: {', '.join(art_names)}"
        progress = getattr(output, '_progress', '')
        progress_str = f" ({progress})" if progress else ""
        await self._emit_event(
            "agent_update",
            agent=task.role.value,
            status="done" if output.is_successful() else "error",
            summary=output.summary[:200],
            cost=output.cost_usd,
            artifacts_count=len(output.structured_artifacts),
            is_remediation=task.is_remediation,
            progress=progress,
        )
        await self._notify(
            f"{icon} {prefix}**{task.role.value}** [{output.status.value}]{progress_str} "
            f"${output.cost_usd:.4f} — {output.summary[:100]}{artifact_info}"
        )

    async def _on_dag_remediation(
        self,
        failed_task: "TaskInput",
        failed_output: "TaskOutput",
        remediation_task: "TaskInput",
    ):
        """Callback: fired when DAG executor creates a self-healing remediation task."""
        category = failed_output.failure_category
        cat_str = category.value if category else "unknown"
        await self._emit_event(
            "self_healing",
            failed_task=failed_task.id,
            failure_category=cat_str,
            remediation_task=remediation_task.id,
            remediation_role=remediation_task.role.value,
        )
        await self._notify(
            f"🔧 **Self-healing:** Task {failed_task.id} failed ({cat_str}). "
            f"Auto-created fix task {remediation_task.id} ({remediation_task.role.value})."
        )

    def _record_dag_output(self, output: "TaskOutput"):
        """Store a TaskOutput in the conversation log for persistence."""
        artifact_lines = []
        if output.structured_artifacts:
            for art in output.structured_artifacts:
                artifact_lines.append(f"  [{art.type.value}] {art.title}: {art.summary}")
        artifacts_str = "\n".join(artifact_lines) if artifact_lines else "none"

        content = (
            f"[{output.task_id}] {output.status.value.upper()}\n"
            f"{output.summary}\n"
            f"Files: {', '.join(output.artifacts[:10]) or 'none'}\n"
            f"Structured Artifacts:\n{artifacts_str}\n"
            f"Confidence: {output.confidence:.2f} | Cost: ${output.cost_usd:.4f}"
        )
        if output.failure_category:
            content += f"\nFailure: {output.failure_category.value}"
        if output.issues:
            content += f"\nIssues: {'; '.join(output.issues[:3])}"

        self.conversation_log.append(
            Message(
                agent_name=output.task_id,
                role="Agent",
                content=content,
                cost_usd=output.cost_usd,
            )
        )

    async def _load_manifest(self) -> str:
        """Load .nexus/PROJECT_MANIFEST.md if it exists."""
        manifest_path = Path(self.project_dir) / ".nexus" / "PROJECT_MANIFEST.md"
        if manifest_path.exists():
            try:
                return manifest_path.read_text(encoding="utf-8")[:4000]
            except Exception:
                pass
        return ""

    async def _load_memory_snapshot(self) -> str:
        """Load .nexus/memory_snapshot.json if it exists (structured memory for PM)."""
        snapshot_path = Path(self.project_dir) / ".nexus" / "memory_snapshot.json"
        if snapshot_path.exists():
            try:
                content = snapshot_path.read_text(encoding="utf-8")
                if len(content) <= 8000:
                    return content
                # Truncate-safe: parse and re-serialize with size limit
                import json as _json
                data = _json.loads(content)
                # Remove large fields first to fit
                for key in ["file_map", "key_decisions", "known_issues"]:
                    result = _json.dumps(data)
                    if len(result) <= 8000:
                        break
                    if key in data and isinstance(data[key], (dict, list)):
                        if isinstance(data[key], dict):
                            items = list(data[key].items())[:20]
                            data[key] = dict(items)
                        else:
                            data[key] = data[key][:10]
                return _json.dumps(data)[:8000]
            except Exception:
                pass
        return ""

    def _list_workspace_files(self, max_files: int = 200) -> str:
        """Return a concise file tree of the project directory for the PM Agent."""
        root = Path(self.project_dir)
        if not root.exists():
            return ""
        lines: list[str] = []
        # Directories to skip
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                     "dist", "build", ".pytest_cache", ".mypy_cache"}
        try:
            for path in sorted(root.rglob("*")):
                if any(part in skip_dirs for part in path.parts):
                    continue
                if path.is_file():
                    rel = path.relative_to(root)
                    lines.append(str(rel))
                    if len(lines) >= max_files:
                        lines.append(f"... (truncated at {max_files} files)")
                        break
        except Exception:
            pass
        return "\n".join(lines)

    # --- Core orchestration loop (legacy) ---

    async def _run_orchestrator(self, user_message: str, *, _retry_count: int = 0):
        """Main orchestrator loop (legacy regex-delegate system).

        Uses a cumulative retry count (_retry_count) to bound retries on
        spurious anyio CancelledErrors.  Previous implementation used
        unbounded tail-call recursion which reset the counter each time.
        """
        start_time = time.monotonic()
        self._last_user_message = user_message  # Track for state persistence

        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=user_message)
        )
        await self.session_mgr.add_message(
            self.project_id, "user", "User", user_message
        )

        # Build initial prompt with conversation history for context
        workspace = await asyncio.to_thread(self._get_workspace_context)

        # Pre-flight check: verify project directory exists
        if not Path(self.project_dir).exists():
            logger.error(f"[{self.project_id}] Project directory does not exist: {self.project_dir}")
            await self._send_final(
                f"❌ Project directory not found: `{self.project_dir}`\n\n"
                f"Create the directory or update the project settings."
            )
            self.is_running = False
            await self._emit_event("project_status", status="idle")
            return

        # Include recent conversation history so the orchestrator has context
        # even without session resume
        recent_msgs = await self.session_mgr.get_recent_messages(self.project_id, count=10)
        history = ""
        if recent_msgs:
            history_lines = []
            for msg in recent_msgs:
                role = msg.get("agent_name", "unknown")
                content = msg.get("content", "")[:500]
                history_lines.append(f"[{role}]: {content}")
            history = "Recent conversation history:\n" + "\n".join(history_lines) + "\n\n"

        prompt = (
            f"Project: {self.project_name}\n"
            f"Working directory: {self.project_dir}\n\n"
        )
        if workspace:
            prompt += f"{workspace}\n\n"
        if history:
            prompt += history

        # Inject task complexity hint so the orchestrator sets the right expectations upfront
        complexity = self._estimate_task_complexity(user_message)

        # If the manifest already exists, this is a continuation — inject it and override complexity
        existing_manifest = await asyncio.to_thread(self._read_project_manifest)
        if existing_manifest:
            # Keep EPIC if already detected — manifest only sets a *minimum* of LARGE
            complexity = complexity if complexity == "EPIC" else "LARGE"
            prompt += (
                f"\n\n📋 EXISTING PROJECT MANIFEST FOUND (.nexus/PROJECT_MANIFEST.md):\n"
                f"This is a CONTINUATION of previous work. Read the manifest carefully before delegating.\n\n"
                f"{existing_manifest}\n"
            )

        complexity_hints = {
            "SIMPLE": (
                "⚡ TASK COMPLEXITY: SIMPLE — Handle efficiently in 1-2 rounds. "
                "Fix → verify → TASK_COMPLETE."
            ),
            "MEDIUM": (
                "⚡ TASK COMPLEXITY: MEDIUM — Plan 3-5 rounds. "
                "Implement → review → test → TASK_COMPLETE."
            ),
            "LARGE": (
                "⚡ TASK COMPLEXITY: LARGE — Plan 6-10 rounds. "
                "Explore → implement phase by phase → review → test → TASK_COMPLETE. "
                "Do NOT rush to completion."
            ),
            "EPIC": (
                "⚡ TASK COMPLEXITY: EPIC — This is a large system build. Plan 10-25 rounds.\n"
                "You MUST work through ALL 6 phases:\n"
                "  Phase 1: Architecture + explore existing code (rounds 1-3)\n"
                "  Phase 2: Core foundation — models, DB, config (rounds 4-8)\n"
                "  Phase 3: Feature implementation — one feature at a time (rounds 9-13)\n"
                "  Phase 4: Integration + error handling (rounds 14-17)\n"
                "  Phase 5: Testing — write + run tests, fix failures (rounds 18-22)\n"
                "  Phase 6: Polish + deployment config (rounds 23+)\n"
                "TASK_COMPLETE only when: all features work + tests pass + app starts."
            ),
        }
        prompt += f"\n\n{complexity_hints.get(complexity, '')}\n\nUser request:\n{user_message}"

        # Initialize the task ledger (todo.md) — persistent file-system context
        self._init_todo(user_message, complexity)
        todo_content = self._read_todo()
        if todo_content:
            prompt += (
                f"\n\n📋 TASK LEDGER (.nexus/todo.md):\n"
                f"This file tracks your progress. You can read it with your tools. "
                f"Update it by delegating developer to edit .nexus/todo.md when phases complete.\n\n"
                f"{todo_content[:2000]}\n"
            )

        # ── Experience Memory Injection ──
        # Retrieve relevant lessons from past tasks and inject them into the prompt.
        # This gives the orchestrator "memory" of what worked and what failed before.
        try:
            experience_context = await self._inject_experience_context(user_message)
            if experience_context:
                prompt += experience_context
                logger.info(f"[{self.project_id}] Injected experience context ({len(experience_context)} chars)")
        except Exception as e:
            logger.debug(f"[{self.project_id}] Experience injection failed (non-fatal): {e}")

        task_history_id = None  # Guard: prevents NameError in except blocks
        _anyio_retries = _retry_count  # Cumulative across retries
        _MAX_ANYIO_RETRIES = 3  # Max times to auto-retry on spurious CancelledError
        _should_retry = False
        try:
            # Record task history
            task_history_id = await self.session_mgr.add_task_history(
                project_id=self.project_id,
                user_id=self.user_id,
                task_description=user_message[:500],
                status="running",
            )

            # Main loop: orchestrator responds, optionally delegates, then loops
            orchestrator_input = prompt
            loop_count = 0
            self._current_loop = 0
            max_loops = MAX_ORCHESTRATOR_LOOPS  # Safety limit on orchestrator iterations
            self._completed_rounds = []  # Track what has been done each round (instance-level for final summary)
            self._agents_used = set()    # Reset agent participation tracking for new session
            self.shared_context = []  # Reset shared context for new session (prevents leaking from previous task)
            self._budget_warning_sent = False  # Reset budget warning flag for new session

            while self.is_running and loop_count < max_loops:
                if self._stop_event.is_set():
                    break

                # Wait until un-paused — poll every second so stop_event is respected
                while not self._pause_event.is_set():
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1.0)
                if self._stop_event.is_set():
                    break

                # Check session timeout (60 min default)
                elapsed = time.monotonic() - start_time
                if elapsed >= SESSION_TIMEOUT_SECONDS:
                    logger.warning(
                        f"[{self.project_id}] Session timeout after {elapsed:.0f}s "
                        f"(limit: {SESSION_TIMEOUT_SECONDS}s)"
                    )
                    await self._send_final(
                        await self._build_final_summary(
                            user_message, start_time,
                            status=f"Stopped (session timeout after {int(elapsed // 60)}m)"
                        )
                    )
                    break

                # Drain all pending messages from the queue (no message is lost)
                injected_parts = []
                while not self._message_queue.empty():
                    try:
                        target_name, injected_msg = self._message_queue.get_nowait()
                        injected_parts.append(f"[User message to {target_name}]:\n{injected_msg}")
                        logger.info(f"[{self.project_id}] Drained queued message for {target_name}")
                    except asyncio.QueueEmpty:
                        break
                if injected_parts:
                    orchestrator_input = "\n\n---\n\n".join(injected_parts)
                    await self._notify(f"📨 {len(injected_parts)} message(s) injected")

                self.turn_count += 1
                loop_count += 1
                self._current_loop = loop_count

                # Emit loop progress event
                await self._emit_event(
                    "loop_progress",
                    loop=loop_count, max_loops=max_loops,
                    turn=self.turn_count, max_turns=MAX_TURNS_PER_CYCLE,
                    cost=self.total_cost_usd, max_budget=MAX_BUDGET_USD,
                )

                await self._notify(
                    f"{AGENT_EMOJI.get('orchestrator', '🔄')} Turn {self.turn_count}/{MAX_TURNS_PER_CYCLE} — "
                    f"*orchestrator* is {'planning & delegating' if self.multi_agent else 'working'}..."
                )

                # Query orchestrator
                self.current_agent = "orchestrator"
                self.agent_states["orchestrator"] = {
                    "state": "working",
                    "task": "planning & delegating" if self.multi_agent else "working",
                }
                await self._emit_event(
                    "agent_started",
                    agent="orchestrator",
                    task="planning & delegating" if self.multi_agent else "working",
                )
                agent_start = time.monotonic()

                # Rate limiting: enforce minimum gap between orchestrator calls
                # to avoid overwhelming the API on fast loops (stuck detection may be slow)
                _last = getattr(self, "_last_orch_call_time", 0.0)
                _gap = time.monotonic() - _last
                if _gap < RATE_LIMIT_SECONDS and loop_count > 0:
                    await asyncio.sleep(RATE_LIMIT_SECONDS - _gap)
                self._last_orch_call_time = time.monotonic()

                # Orchestrator heartbeat — emit periodic updates while it's thinking
                # so the UI knows it's not stuck (it has no tools, so no tool_use events)
                async def _orch_heartbeat():
                    phases = [
                        "analyzing request...",
                        "reviewing context...",
                        "deciding delegation strategy...",
                        "composing agent instructions...",
                        "finalizing plan...",
                    ]
                    tick = 0
                    while True:
                        await asyncio.sleep(4)
                        tick += 1
                        elapsed = int(time.monotonic() - agent_start)
                        phase = phases[min(tick - 1, len(phases) - 1)]
                        self.agent_states["orchestrator"] = {
                            "state": "working",
                            "task": phase,
                            "current_tool": f"thinking ({elapsed}s)",
                        }
                        await self._emit_event(
                            "agent_update",
                            agent="orchestrator",
                            text=f"🎯 {phase} ({elapsed}s)",
                            timestamp=time.time(),
                        )

                orch_hb = asyncio.create_task(_orch_heartbeat())
                try:
                    response = await self._query_agent("orchestrator", orchestrator_input)
                finally:
                    orch_hb.cancel()
                    try:
                        await orch_hb
                    except asyncio.CancelledError:
                        pass

                agent_duration = time.monotonic() - agent_start
                logger.info(
                    f"[{self.project_id}] Orchestrator response: "
                    f"len={len(response.text)}, cost=${response.cost_usd:.4f}, "
                    f"turns={response.num_turns}, error={response.is_error}, "
                    f"has_delegate={'<delegate>' in response.text}, "
                    f"has_complete={'TASK_COMPLETE' in response.text}, "
                    f"duration={agent_duration:.1f}s"
                )
                self._record_response("orchestrator", "Orchestrator", response)
                self.current_agent = None
                self.current_tool = None
                self.agent_states["orchestrator"] = {
                    "state": "error" if response.is_error else "done",
                    "cost": response.cost_usd,
                    "turns": response.num_turns,
                    "duration": agent_duration,
                }
                await self._emit_event(
                    "agent_finished",
                    agent="orchestrator",
                    cost=response.cost_usd,
                    turns=response.num_turns,
                    duration=round(agent_duration, 1),
                    is_error=response.is_error,
                )

                if response.is_error:
                    error_msg = response.error_message.lower()
                    # Provide actionable messages for common errors
                    if "api key" in error_msg or "invalid api" in error_msg or "authentication" in error_msg:
                        await self._send_result(
                            "🔑 *Authentication Error*\n\n"
                            "The Claude agent can't authenticate.\n"
                            "Make sure the Claude CLI is installed and logged in.\n"
                            "Run: claude login\n\n"
                            "Docs: https://docs.anthropic.com/claude-code/getting-started"
                        )
                    elif "exit code 71" in error_msg or "exit code: 71" in error_msg:
                        await self._send_result(
                            "🔒 *macOS Sandbox Restriction (Exit Code 71)*\n\n"
                            "macOS is blocking the agent from accessing files in this directory.\n"
                            "This commonly happens with ~/Downloads.\n\n"
                            "Fix: Move the project folder:\n"
                            "  mv ~/Downloads/web-claude-bot ~/web-claude-bot\n\n"
                            "Then restart the server from the new location."
                        )
                    else:
                        # Transient error — retry with exponential backoff (up to 3 times)
                        _orch_retries = getattr(self, '_orch_error_retries', 0)
                        if _orch_retries < 3:
                            self._orch_error_retries = _orch_retries + 1
                            wait_time = min(5 * (2 ** _orch_retries), 30)
                            await self._notify(
                                f"⚠️ Orchestrator error (attempt {_orch_retries + 1}/3): "
                                f"{response.error_message[:200]}\n"
                                f"Retrying in {wait_time}s..."
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            self._orch_error_retries = 0
                            await self._send_result(
                                f"⚠️ Orchestrator error after 3 retries: {response.error_message}\n\n"
                                f"Use /resume to retry or /stop to end."
                            )
                    await self._self_pause("orchestrator error")
                    continue

                # Show orchestrator response as intermediate
                display_text = self._strip_delegate_blocks(response.text)
                if display_text.strip():
                    summary = display_text[:2000]
                    if len(display_text) > 2000:
                        summary += "\n... (truncated)"
                    await self._send_result(
                        f"🎯 *orchestrator* — Turn {self.turn_count}\n"
                        f"💰 ${response.cost_usd:.4f} (total: ${self.total_cost_usd:.4f})\n\n"
                        f"{summary}"
                    )

                # Check completion — validate TASK_COMPLETE is actually appropriate
                if "TASK_COMPLETE" in response.text:
                    premature_reason = self._check_premature_completion(loop_count, user_message)
                    if premature_reason:
                        logger.warning(
                            f"[{self.project_id}] TASK_COMPLETE rejected (premature): {premature_reason}"
                        )
                        await self._notify(f"⚠️ *orchestrator* tried to finish early — pushing to continue...")
                        # Still run any delegate blocks that accompanied the TASK_COMPLETE
                        early_delegations = self._parse_delegations(response.text)
                        if early_delegations:
                            sub_results = await self._run_sub_agents(early_delegations)
                            review = await self._build_review_prompt(sub_results, self._completed_rounds)
                            orchestrator_input = review
                        else:
                            # No delegates — inject rejection and force planning
                            current_changes = await self._detect_file_changes()
                            orchestrator_input = (
                                f"⛔ TASK_COMPLETE REJECTED — the task is not fully complete yet.\n\n"
                                f"Reason: {premature_reason}\n\n"
                                f"Current file changes:\n{current_changes}\n\n"
                                f"Rounds completed so far:\n"
                                + ("\n".join(f"  • {r}" for r in self._completed_rounds) or "  • (none yet)") +
                                f"\n\nYou MUST keep working. What specific work is still needed?\n"
                                f"Delegate the next phase of work now using <delegate> blocks."
                            )
                        continue
                    # Completion validated — accept it
                    if task_history_id is not None:
                        await self.session_mgr.update_task_history(
                            task_history_id, "completed",
                            cost_usd=self.total_cost_usd,
                            turns_used=self.turn_count,
                            summary=display_text[:500] if display_text.strip() else "Task completed",
                        )

                    # ── Reflection Step (Reflexion pattern) ──
                    # Generate lessons learned from this task execution
                    # and store them for future tasks.
                    try:
                        reflection = await self._generate_reflection(
                            task=user_message, outcome="success", start_time=start_time
                        )
                        if reflection:
                            await self._store_lessons(
                                task=user_message, reflection=reflection, outcome="success"
                            )
                            logger.info(f"[{self.project_id}] Reflection stored after successful completion")
                    except Exception as e:
                        logger.warning(f"[{self.project_id}] Reflection step failed (non-fatal): {e}")

                    # Clear persisted state (task completed successfully)
                    try:
                        if self.session_mgr:
                            await self.session_mgr.clear_orchestrator_state(self.project_id)
                    except Exception:
                        pass

                    await self._send_final(
                        await self._build_final_summary(user_message, start_time)
                    )
                    break

                # Check budget (global + per-project)
                effective_budget = MAX_BUDGET_USD
                try:
                    project_budget = await self.session_mgr.get_project_budget(self.project_id)
                    if project_budget > 0:
                        effective_budget = min(effective_budget, project_budget)
                except Exception:
                    pass
                self._effective_budget = effective_budget  # Store for sub-agent budget checks

                if self.total_cost_usd >= effective_budget:
                    await self._notify(
                        f"💰 Budget limit reached (${self.total_cost_usd:.4f} / ${effective_budget:.2f}).\n"
                        f"Use /resume to continue or /stop to end."
                    )
                    await self._self_pause("budget limit")
                    continue

                # Progressive budget warning at BUDGET_WARNING_THRESHOLD (default 80%)
                warning_threshold = effective_budget * BUDGET_WARNING_THRESHOLD
                if (
                    self.total_cost_usd >= warning_threshold
                    and not getattr(self, "_budget_warning_sent", False)
                ):
                    self._budget_warning_sent = True
                    pct = int(self.total_cost_usd / effective_budget * 100)
                    await self._notify(
                        f"⚠️ Budget at {pct}% — ${self.total_cost_usd:.4f} of ${effective_budget:.2f} used.\n"
                        f"Will auto-pause at 100%. Use /stop to end early."
                    )

                # Check turn limit
                if self.turn_count >= MAX_TURNS_PER_CYCLE:
                    await self._notify(
                        f"⏰ Reached max turns ({MAX_TURNS_PER_CYCLE}).\n"
                        f"Use /resume to continue or /stop to end."
                    )
                    await self._self_pause("turn limit")
                    continue

                # Parse delegations
                delegations = self._parse_delegations(response.text)
                logger.info(f"[{self.project_id}] Parsed {len(delegations)} delegations: {[f'{d.agent}:{d.task[:40]}' for d in delegations]}")

                # Mark delegated agents as queued (state=working) immediately so the
                # frontend never shows STANDBY between delegation and agent_started events.
                for d in delegations:
                    self.agent_states[d.agent] = {
                        "state": "working",
                        "task": d.task[:300],
                    }
                # Emit delegation events (frontend will confirm 'working' state)
                for d in delegations:
                    await self._emit_event(
                        "delegation",
                        from_agent="orchestrator",
                        to_agent=d.agent,
                        task=d.task[:300],
                    )

                if not delegations:
                    if self.multi_agent:
                        # No delegations in multi-agent mode — nudge to delegate or complete
                        logger.warning(
                            f"Orchestrator produced no parseable delegations. "
                            f"Response length: {len(response.text)}, "
                            f"contains '<delegate>': {'<delegate>' in response.text}"
                        )
                        # Build context-aware nudge — tell orchestrator what was done so far
                        rounds_so_far = (
                            "\n".join(f"  • {r}" for r in self._completed_rounds)
                            if self._completed_rounds
                            else "  • (no rounds completed yet — this is round 1)"
                        )
                        current_changes = await self._detect_file_changes()
                        changes_line = (
                            f"\nCurrent file changes:\n{current_changes}"
                            if current_changes and "(no file" not in current_changes
                            else "\nNo file changes detected yet."
                        )
                        orchestrator_input = (
                            "⚠️ No <delegate> blocks found in your response.\n\n"
                            "You MUST either:\n"
                            "A) Delegate work using <delegate> blocks:\n"
                            "<delegate>\n"
                            '{"agent": "developer", "task": "specific task description", "context": "relevant file paths and details"}\n'
                            "</delegate>\n\n"
                            "B) Say TASK_COMPLETE if the task is 100% verified done.\n\n"
                            f"═══ PROGRESS SO FAR ═══\n"
                            f"{rounds_so_far}"
                            f"{changes_line}\n\n"
                            "═══ BEFORE DECIDING, CHECK ═══\n"
                            "1. Was code actually written/modified? (see file changes above)\n"
                            "2. Was it reviewed by the reviewer agent?\n"
                            "3. Were tests run and did they pass?\n"
                            "4. Are there any BLOCKED or NEEDS_FOLLOWUP items to address?\n\n"
                            f"Original user request:\n{user_message}"
                        )
                        continue
                    else:
                        # Solo mode — orchestrator handled it directly, done
                        await self._send_final(
                            await self._build_final_summary(user_message, start_time)
                        )
                        break

                if not self.multi_agent:
                    # Single-agent mode — ignore delegations
                    await self._send_final(
                        await self._build_final_summary(user_message, start_time)
                    )
                    break

                # Execute sub-agents
                logger.info(f"[{self.project_id}] Running {len(delegations)} sub-agent tasks...")
                # Mark orchestrator as "waiting" while sub-agents work
                self.agent_states["orchestrator"] = {
                    "state": "idle",
                    "task": f"waiting for {len(delegations)} sub-agent(s)",
                }
                # Execute sub-agents.  Each sub-agent query runs in an
                # isolated event loop (via isolated_query), so the anyio
                # cancel-scope bug is contained.  We still keep the retry
                # guard as a safety net for edge cases.
                try:
                    sub_results = await self._run_sub_agents(delegations)
                except asyncio.CancelledError:
                    if self._stop_event.is_set():
                        raise  # Real cancellation — propagate up
                    # Spurious anyio cancel-scope leak (should be rare now
                    # with event-loop isolation) — uncancel and retry
                    ct = asyncio.current_task()
                    if ct is not None and hasattr(ct, 'uncancel'):
                        ct.uncancel()
                    _anyio_retries += 1
                    logger.warning(
                        f"[{self.project_id}] Spurious CancelledError before/during sub-agents "
                        f"(retry {_anyio_retries}/{_MAX_ANYIO_RETRIES})"
                    )
                    if _anyio_retries <= _MAX_ANYIO_RETRIES:
                        await self._notify("⚠️ Internal hiccup — retrying automatically...")
                        continue  # Retry the while loop iteration
                    else:
                        raise  # Too many retries — propagate up
                logger.info(
                    f"[{self.project_id}] Sub-agents finished: "
                    f"{', '.join(f'{k}({len(v)} tasks)' for k, v in sub_results.items())}"
                )

                # Auto-commit safety net: ensure agents' work is saved
                # even if they forgot to commit (prevents work loss on crash)
                try:
                    commit_result = await asyncio.wait_for(
                        asyncio.create_subprocess_exec(
                            "git", "-C", self.project_dir,
                            "diff", "--quiet",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        ),
                        timeout=10.0,
                    )
                    await commit_result.wait()
                    if commit_result.returncode != 0:
                        # There are uncommitted changes — auto-commit them
                        add_proc = await asyncio.create_subprocess_exec(
                            "git", "-C", self.project_dir,
                            "add", "-A",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await add_proc.wait()
                        agents_in_round = ", ".join(sub_results.keys())
                        commit_proc = await asyncio.create_subprocess_exec(
                            "git", "-C", self.project_dir,
                            "commit", "-m", f"auto: save work from round {loop_count} ({agents_in_round})",
                            "--no-verify",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await commit_proc.wait()
                        if commit_proc.returncode == 0:
                            logger.info(f"[{self.project_id}] Auto-committed uncommitted changes after round {loop_count}")
                except Exception as e:
                    logger.debug(f"[{self.project_id}] Auto-commit check failed (non-critical): {e}")

                # Check stuck detection (enhanced with auto-escalation)
                stuck_info = self._detect_stuck()
                if stuck_info:
                    severity = stuck_info['severity']
                    signal = stuck_info['signal']
                    details = stuck_info['details']
                    strategy = stuck_info['strategy']

                    if severity == 'critical':
                        # Critical: pause and notify user
                        await self._notify(
                            f"🔁 **Stuck detected** ({signal})\n\n"
                            f"{details}\n\n"
                            f"Suggested strategy: **{strategy}**\n"
                            f"Use /talk orchestrator <message> to intervene, or /stop to end."
                        )
                        await self._self_pause(f"stuck detection: {signal}")
                        continue
                    else:
                        # Warning: inject escalation hint into the review prompt
                        # but don't pause — let the orchestrator try to self-correct
                        self._stuck_escalation_hint = (
                            f"⚠️ STUCK WARNING ({signal}): {details}\n"
                            f"Suggested strategy: {strategy}. "
                            f"You MUST change your approach this round — do NOT repeat the same delegations."
                        )
                        logger.warning(
                            f"[{self.project_id}] Stuck warning ({signal}): "
                            f"injecting escalation hint into review prompt"
                        )

                # ═══ EVALUATOR-REFLECT-REFINE LOOP ═══
                # Before sending results to the orchestrator, automatically run
                # verification (tests/build) if code was changed. If tests fail,
                # send the developer back to fix WITHOUT wasting an orchestrator turn.
                eval_result = await self._auto_evaluate(sub_results, loop_count)
                if eval_result and eval_result.get("auto_fixed"):
                    # Developer was auto-retried and the fix results are in sub_results
                    sub_results = eval_result["updated_results"]
                    logger.info(f"[{self.project_id}] Evaluator auto-fix applied in round {loop_count}")

                # Track what was done this round
                _round_summary = ", ".join(
                    f"{role}({'OK' if all(not r.is_error for r in resps) else 'ERR'})"
                    for role, resps in sub_results.items()
                )
                self._completed_rounds.append(f"Round {loop_count}: {_round_summary}")

                # Update the persistent task ledger with this round's results
                self._update_todo_after_round(loop_count, _round_summary)

                # Persist orchestrator state for crash recovery (every round)
                try:
                    if self.session_mgr:
                        await self.session_mgr.save_orchestrator_state(
                            project_id=self.project_id,
                            user_id=self.user_id,
                            status="running",
                            current_loop=loop_count,
                            turn_count=self.turn_count,
                            total_cost_usd=self.total_cost_usd,
                            shared_context=self.shared_context[-20:],  # last 20 entries
                            agent_states=self.agent_states,
                            last_user_message=getattr(self, '_last_user_message', ''),
                        )
                except Exception as e:
                    logger.warning(f"[{self.project_id}] Failed to persist state: {e}")

                # Inject evaluation results into the review prompt context
                eval_context = ""
                if eval_result:
                    eval_context = eval_result.get("summary", "")

                # Feed results back to orchestrator with round history
                orchestrator_input = await self._build_review_prompt(sub_results, self._completed_rounds)

                # Add evaluation results if available
                if eval_context:
                    orchestrator_input += f"\n\n═══ AUTO-EVALUATION RESULTS ═══\n{eval_context}\n"

                # Inject current task ledger into the review prompt so the
                # orchestrator always has the persistent goal + progress visible
                todo_content = await asyncio.to_thread(self._read_todo)
                if todo_content:
                    orchestrator_input += (
                        f"\n\n📋 TASK LEDGER (.nexus/todo.md):\n"
                        f"{todo_content[:2000]}\n"
                    )

        except asyncio.CancelledError:
            # Distinguish real cancellation (user pressed Stop) from spurious
            # anyio cancel-scope leaks.  The SDK's anyio TaskGroup cleanup can
            # propagate CancelledError to the event loop when a generator is
            # GC'd in a different task.  If _stop_event is NOT set, this is a
            # spurious cancellation — we should NOT exit.
            if self._stop_event.is_set():
                logger.info(f"Orchestrator loop cancelled (stop requested) for {self.project_name}")
                if task_history_id is not None:
                    try:
                        await self.session_mgr.update_task_history(
                            task_history_id, "cancelled",
                            cost_usd=self.total_cost_usd, turns_used=self.turn_count,
                            summary="Task was cancelled",
                        )
                    except Exception:
                        pass
                await self._send_final(
                    f"🛑 *{self.project_name}* — Task cancelled.\n"
                    f"📊 Turns: {self.turn_count} | 💰 ${self.total_cost_usd:.4f}"
                )
            else:
                # Spurious — uncancel so we can keep running
                ct = asyncio.current_task()
                if ct is not None and hasattr(ct, 'uncancel'):
                    ct.uncancel()
                logger.warning(
                    f"Orchestrator loop got SPURIOUS CancelledError for {self.project_name} "
                    f"(stop_event not set — likely anyio cancel-scope leak). "
                    f"Retrying the round ({_anyio_retries + 1}/{_MAX_ANYIO_RETRIES})."
                )
                await self._notify(
                    f"⚠️ Internal hiccup (anyio bug) — retrying automatically..."
                )
                _anyio_retries += 1
                if _anyio_retries <= _MAX_ANYIO_RETRIES:
                    _should_retry = True
                else:
                    await self._send_final(
                        f"⚠️ *{self.project_name}* — Repeated anyio errors ({_anyio_retries}x).\n"
                        f"Send your message again to retry.\n"
                        f"📊 Turns: {self.turn_count} | 💰 ${self.total_cost_usd:.4f}"
                    )
        except Exception as e:
            logger.error(f"Orchestrator loop error: {e}", exc_info=True)
            # Use _send_final (not _notify) so the frontend receives an agent_final event
            await self._send_final(
                f"❌ *{self.project_name}* — Error in orchestrator:\n{e}\n\n"
                f"📊 Turns: {self.turn_count} | 💰 ${self.total_cost_usd:.4f}\n"
                f"Send another message to retry."
            )
            if task_history_id is not None:
                try:
                    await self.session_mgr.update_task_history(
                        task_history_id, "error",
                        cost_usd=self.total_cost_usd, turns_used=self.turn_count,
                        summary=f"Error: {e}",
                    )
                except Exception:
                    pass

            # ── Reflection on failed task ──
            try:
                reflection = await self._generate_reflection(
                    task=user_message, outcome=f"failure: {str(e)[:200]}", start_time=start_time
                )
                if reflection:
                    await self._store_lessons(
                        task=user_message, reflection=reflection, outcome="failure"
                    )
            except Exception:
                pass  # Don't let reflection failure mask the original error
        else:
            # Loop exited normally (not via exception).
            # If we hit the safety limit without a clean exit, send a final summary.
            if loop_count >= max_loops:
                if task_history_id is not None:
                    try:
                        await self.session_mgr.update_task_history(
                            task_history_id, "completed",
                            cost_usd=self.total_cost_usd, turns_used=self.turn_count,
                            summary="Stopped (loop limit reached)",
                        )
                    except Exception:
                        pass

                # ── Reflection on incomplete task ──
                try:
                    reflection = await self._generate_reflection(
                        task=user_message, outcome="partial (loop limit reached)", start_time=start_time
                    )
                    if reflection:
                        await self._store_lessons(
                            task=user_message, reflection=reflection, outcome="partial"
                        )
                        logger.info(f"[{self.project_id}] Reflection stored after loop-limit exit")
                except Exception as e:
                    logger.warning(f"[{self.project_id}] Reflection on loop-limit failed (non-fatal): {e}")

                await self._send_final(
                    await self._build_final_summary(user_message, start_time, status="Stopped (loop limit)")
                )
        finally:
            # Save final agent states to DB before clearing — so refresh can show
            # the last-known state (done/error) for each agent that participated.
            try:
                if self.session_mgr:
                    await self.session_mgr.save_orchestrator_state(
                        project_id=self.project_id,
                        user_id=self.user_id,
                        status="completed",
                        current_loop=getattr(self, '_current_loop', 0),
                        turn_count=self.turn_count,
                        total_cost_usd=self.total_cost_usd,
                        agent_states=self.agent_states,
                        last_user_message=user_message[:500] if user_message else "",
                    )
            except Exception:
                pass
            if not self.is_paused:
                self.is_running = False
            # Always emit project_status so frontend knows the state changed
            await self._emit_event("project_status", status="paused" if self.is_paused else "idle")
            # Reset all agent states to idle — clear task so page-refresh doesn't
            # show STANDBY with a stale task description from the previous round.
            for agent_name in list(self.agent_states.keys()):
                prev = self.agent_states.get(agent_name, {})
                self.agent_states[agent_name] = {
                    "state": "idle",
                    "current_tool": None,
                    # Preserve accumulated cost/turns for the stats display
                    "cost": prev.get("cost", 0),
                    "turns": prev.get("turns", 0),
                }
            # NOTE: _on_task_done callback handles auto-restart if queue has pending messages

        # If we set the retry flag due to spurious anyio CancelledError,
        # re-enter the orchestrator loop with cumulative retry count (bounded).
        if _should_retry:
            return await self._run_orchestrator(user_message, _retry_count=_anyio_retries)

    async def _run_sub_agents(self, delegations: list[Delegation]) -> dict[str, list[SDKResponse]]:
        """Execute sub-agent tasks with smart scheduling.

        Code-modifying agents (developer, devops) run SEQUENTIALLY to avoid
        conflicting file changes (the Cognition/Devin insight). Read-only
        agents (reviewer, tester, researcher) run in PARALLEL after writers finish.

        If the orchestrator delegates multiple tasks to the same role,
        those run sequentially (they share a session).

        Failed agents are automatically retried once with extra context.
        Exceptions from parallel execution are caught and reported properly.
        """
        # Group delegations by agent role
        by_role: dict[str, list[Delegation]] = {}
        results: dict[str, list[SDKResponse]] = {}
        for d in delegations:
            if d.agent not in SUB_AGENT_PROMPTS and d.agent not in SPECIALIST_PROMPTS:
                logger.warning(f"Unknown sub-agent role: {d.agent}, skipping")
                all_roles = set(SUB_AGENT_PROMPTS.keys()) | set(SPECIALIST_PROMPTS.keys())
                # Feed back to orchestrator so it can retry with a valid role name
                results.setdefault("⚠ Invalid Role", []).append(SDKResponse(
                    text=(
                        f"Delegation to unknown role '{d.agent}' was skipped.\n"
                        f"Valid roles are: {', '.join(sorted(all_roles))}.\n"
                        f"Task was: {d.task[:200]}"
                    ),
                    is_error=True,
                    error_message=f"Unknown agent role: {d.agent}",
                ))
                continue
            by_role.setdefault(d.agent, []).append(d)

        lock = asyncio.Lock()  # Protect shared state updates
        # Track files touched by each agent for conflict detection
        files_touched: dict[str, set[str]] = {}  # agent_role -> set of file paths

        async def run_role(agent_role: str, role_delegations: list[Delegation]):
            """Run all delegations for a single role (sequentially)."""
            for delegation in role_delegations:
                if self._stop_event.is_set():
                    break

                # Check limits under lock, then notify OUTSIDE the lock
                # (never await inside a lock — it blocks all sibling agents)
                _limit_msg: str | None = None
                async with lock:
                    if self.turn_count >= MAX_TURNS_PER_CYCLE:
                        _limit_msg = (
                            f"⏰ Turn limit reached ({MAX_TURNS_PER_CYCLE}) — "
                            f"skipping remaining sub-agents.\n"
                            f"Use /resume to continue."
                        )
                    elif self.total_cost_usd >= self._effective_budget:
                        _limit_msg = (
                            f"💰 Budget limit reached (${self.total_cost_usd:.4f} / ${self._effective_budget:.2f}) — "
                            f"skipping remaining sub-agents.\n"
                            f"Use /resume to continue."
                        )
                    else:
                        self.turn_count += 1
                if _limit_msg:
                    await self._notify(_limit_msg)
                    return

                await self._notify(
                    f"{AGENT_EMOJI.get(agent_role, '🔧')} *{agent_role}* is working on:\n_{delegation.task[:200]}_"
                )

                # Emit agent_started event
                self.current_agent = agent_role
                self.agent_states[agent_role] = {
                    "state": "working",
                    "task": delegation.task[:300],
                }
                await self._emit_event(
                    "agent_started",
                    agent=agent_role,
                    task=delegation.task[:300],
                )
                agent_start = time.monotonic()

                # Build sub-agent prompt
                sub_prompt = (
                    f"Project: {self.project_name}\n"
                    f"Working directory: {self.project_dir}\n\n"
                    f"Task: {delegation.task}"
                )
                if delegation.context:
                    sub_prompt += f"\n\nContext: {delegation.context}"

                # Include smart context from previous rounds (read under lock)
                async with lock:
                    agent_context = self._get_context_for_agent(agent_role)
                    if agent_context:
                        sub_prompt += f"\n\n{agent_context}"

                workspace = await asyncio.to_thread(self._get_workspace_context)
                if workspace:
                    sub_prompt += f"\n\n{workspace}"

                # Warn about file conflicts if multiple agents are running in parallel
                async with lock:
                    conflicts = self._detect_file_conflicts(files_touched)
                if conflicts:
                    conflict_lines = [
                        f"  {f}: touched by {', '.join(agents)}"
                        for f, agents in conflicts.items()
                    ]
                    sub_prompt += (
                        "\n\n⚠️ FILE CONFLICT WARNING: The following files were already "
                        "modified by another agent this session. Read the CURRENT version "
                        "of these files before making any changes:\n" +
                        "\n".join(conflict_lines)
                    )

                # Sub-agent heartbeat — emit periodic updates while SDK call is pending
                # so the UI shows the agent is alive and working
                async def _sub_heartbeat(role=agent_role, start=agent_start):
                    phases = [
                        "reading codebase...",
                        "analyzing code...",
                        "writing changes...",
                        "testing & verifying...",
                        "finalizing...",
                    ]
                    tick = 0
                    while True:
                        await asyncio.sleep(5)
                        tick += 1
                        elapsed = int(time.monotonic() - start)
                        phase = phases[min(tick - 1, len(phases) - 1)]
                        self.agent_states[role] = {
                            **self.agent_states.get(role, {}),
                            "state": "working",
                            "current_tool": f"{phase} ({elapsed}s)",
                        }
                        await self._emit_event(
                            "agent_update",
                            agent=role,
                            text=f"{AGENT_EMOJI.get(role, '🔧')} {phase} ({elapsed}s)",
                            summary=f"{phase} ({elapsed}s)",
                        )
                _hb_task = asyncio.create_task(_sub_heartbeat())

                try:
                    response = await self._query_agent(agent_role, sub_prompt, skill_names=delegation.skills)
                finally:
                    _hb_task.cancel()
                    try:
                        await _hb_task
                    except asyncio.CancelledError:
                        pass

                # Auto-retry once on failure with enriched context
                if response.is_error and not self._stop_event.is_set():
                    error_msg = response.error_message
                    logger.warning(
                        f"[{self.project_id}] Agent '{agent_role}' failed: {error_msg}. "
                        f"Retrying with enriched context..."
                    )
                    await self._notify(
                        f"🔄 *{agent_role}* failed, retrying with more context..."
                    )
                    # Emit agent_started again so frontend shows the retry
                    await self._emit_event(
                        "agent_started",
                        agent=agent_role,
                        task=f"[RETRY] {delegation.task[:250]}",
                    )
                    # Invalidate stale session and retry with error context
                    await self.session_mgr.invalidate_session(
                        self.user_id, self.project_id, agent_role
                    )
                    # Build enriched retry prompt — smart diagnostics based on error type
                    workspace_now = await asyncio.to_thread(self._get_workspace_context)
                    error_lower = error_msg.lower()

                    # Tailor guidance to the error type
                    if "permission" in error_lower or "eperm" in error_lower:
                        hint = "Check file permissions. Try reading the file first to confirm it exists and is accessible."
                    elif "not found" in error_lower or "no such file" in error_lower or "enoent" in error_lower:
                        hint = "The file or path does not exist. List the directory first (ls) to see what's actually there."
                    elif "syntax" in error_lower or "parse" in error_lower or "invalid" in error_lower:
                        hint = "There is a syntax or parsing error. Read the file carefully before editing. Check line numbers in the error."
                    elif "timeout" in error_lower or "timed out" in error_lower:
                        hint = "The operation timed out. Try a simpler/faster approach, or break it into smaller steps."
                    elif "import" in error_lower or "module" in error_lower or "package" in error_lower:
                        hint = "A dependency is missing. Check requirements.txt or package.json. Try pip install or npm install first."
                    elif "connection" in error_lower or "network" in error_lower:
                        hint = "Network or connection issue. Check if the service is running. Try a local alternative."
                    else:
                        hint = "Try a completely different approach. The previous method did not work."

                    retry_prompt = (
                        f"[RETRY — previous attempt failed]\n"
                        f"Error: {error_msg}\n\n"
                        f"Diagnosis: {hint}\n\n"
                        f"Before retrying:\n"
                        f"1. Read the error message carefully — understand WHY it failed\n"
                        f"2. Check your assumptions (file exists? correct path? right syntax?)\n"
                        f"3. Try the simplest possible fix first\n\n"
                        f"Original task: {delegation.task}\n"
                    )
                    if delegation.context:
                        retry_prompt += f"\nContext: {delegation.context}\n"
                    if workspace_now:
                        retry_prompt += f"\n{workspace_now}\n"
                    response = await self._query_agent(agent_role, retry_prompt, skill_names=delegation.skills)

                async with lock:
                    self._record_response(agent_role, agent_role.capitalize(), response)
                    results.setdefault(agent_role, []).append(response)
                    # Accumulate richer shared context under the same lock
                    await self._accumulate_context(agent_role, delegation.task, response)
                    # Track files this agent touched (for conflict detection)
                    touched = self._extract_touched_files(response.text)
                    files_touched.setdefault(agent_role, set()).update(touched)

                # Emit agent_finished event
                agent_duration = time.monotonic() - agent_start
                self.agent_states[agent_role] = {
                    "state": "error" if response.is_error else "done",
                    "task": delegation.task[:300],
                    "cost": response.cost_usd,
                    "turns": response.num_turns,
                    "duration": agent_duration,
                }
                await self._emit_event(
                    "agent_finished",
                    agent=agent_role,
                    cost=response.cost_usd,
                    turns=response.num_turns,
                    duration=round(agent_duration, 1),
                    is_error=response.is_error,
                )

                # Show sub-agent response
                summary = response.text[:2500]
                if len(response.text) > 2500:
                    summary += "\n... (truncated)"

                # If agent did tool-only work with no text, show what files changed
                if "tool use" in summary.lower() and "no text output" in summary.lower():
                    changed = await self._detect_file_changes()
                    if changed:
                        summary += f"\n\nFiles changed:\n{changed}"

                # ── Record agent performance for analytics ──
                try:
                    await self.session_mgr.record_agent_performance(
                        project_id=self.project_id,
                        agent_role=agent_role,
                        status="error" if response.is_error else "success",
                        duration_seconds=agent_duration,
                        cost_usd=response.cost_usd,
                        turns_used=response.num_turns,
                        task_description=delegation.task[:500],
                        error_message=response.error_message[:500] if response.is_error else "",
                        round_number=self._current_loop,
                    )
                except Exception as perf_err:
                    logger.debug(f"[{self.project_id}] Failed to record agent perf: {perf_err}")

                status_icon = "✅" if not response.is_error else "⚠️"
                emoji = AGENT_EMOJI.get(agent_role, "🔧")
                dur_str = f" ({response.duration_ms // 1000}s)" if response.duration_ms > 0 else ""
                await self._send_result(
                    f"{status_icon}{emoji} *{agent_role}* finished{dur_str}\n"
                    f"💰 ${response.cost_usd:.4f} | Turns: {response.num_turns}\n\n"
                    f"{summary}"
                )

        # ═══ SMART SCHEDULING: Sequential writers, then parallel readers ═══
        # Code-modifying agents (developer, devops) run FIRST and SEQUENTIALLY
        # to avoid conflicting file changes. Read-only agents (reviewer, tester,
        # researcher) run AFTER in PARALLEL.
        _WRITER_ROLES = {"developer", "devops"}  # Agents that modify files
        _READER_ROLES = {"reviewer", "tester", "researcher"}  # Agents that read/verify

        writer_roles = {r: d for r, d in by_role.items() if r in _WRITER_ROLES}
        reader_roles = {r: d for r, d in by_role.items() if r in _READER_ROLES}
        # Any unknown roles go to readers (safer default)
        for r, d in by_role.items():
            if r not in _WRITER_ROLES and r not in _READER_ROLES:
                reader_roles[r] = d

        # Run different roles with proper exception handling
        # Also run a heartbeat task that emits periodic status events
        async def _heartbeat():
            """Emit periodic status events with REAL info about what each agent is doing."""
            elapsed = 0
            while True:
                await asyncio.sleep(8)
                elapsed += 8
                # Build detailed status of currently working agents
                working_details = []
                for name, info in list(self.agent_states.items()):  # snapshot prevents RuntimeError during concurrent writes
                    if info.get("state") != "working":
                        continue
                    detail = f"{AGENT_EMOJI.get(name, '🔧')} {name}"
                    tool = info.get("current_tool")
                    task = info.get("task", "")
                    if tool:
                        detail += f" → {tool}"
                    elif task:
                        detail += f": {task[:80]}"
                    else:
                        detail += f" (running {elapsed}s)"
                    working_details.append(detail)

                if working_details:
                    # Emit per-agent updates so the frontend can show each one
                    for name, info in self.agent_states.items():
                        if info.get("state") == "working":
                            tool = info.get("current_tool", "")
                            task = info.get("task", "")
                            status_text = tool if tool else (f"Working on: {task[:100]}" if task else f"Running ({elapsed}s)")
                            await self._emit_event(
                                "agent_update",
                                agent=name,
                                text=status_text,
                                timestamp=time.time(),
                            )

                    # Also emit loop_progress to keep the progress bar alive
                    await self._emit_event(
                        "loop_progress",
                        loop=self._current_loop, max_loops=MAX_ORCHESTRATOR_LOOPS,
                        turn=self.turn_count, max_turns=MAX_TURNS_PER_CYCLE,
                        cost=self.total_cost_usd, max_budget=MAX_BUDGET_USD,
                    )

        async def _isolated_run_role(role, dels):
            """Run a role with full isolation from sibling failures."""
            try:
                await run_role(role, dels)
            except asyncio.CancelledError:
                # Only propagate if we were explicitly stopped
                if self._stop_event.is_set():
                    raise
                logger.warning(
                    f"[{self.project_id}] Agent '{role}' was cancelled "
                    f"(not by stop). Treating as error."
                )
                async with lock:
                    results.setdefault(role, []).append(SDKResponse(
                        text=f"Agent '{role}' was cancelled unexpectedly.",
                        is_error=True,
                        error_message="Cancelled unexpectedly",
                    ))
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.warning(
                        f"[{self.project_id}] Agent '{role}' hit anyio "
                        f"cancel scope bug (contained by isolation): {e}"
                    )
                    # Don't crash — the agent may have produced results
                else:
                    raise

        async def _run_roles_parallel(roles_dict: dict[str, list[Delegation]]):
            """Run multiple roles in parallel using independent tasks."""
            if not roles_dict:
                return
            if len(roles_dict) == 1:
                role, dels = next(iter(roles_dict.items()))
                await _isolated_run_role(role, dels)
                return

            _role_tasks: dict[str, asyncio.Task] = {}
            for role, dels in roles_dict.items():
                task = asyncio.create_task(
                    _isolated_run_role(role, dels),
                    name=f"agent-{role}",
                )
                _role_tasks[role] = task

            _wait_timeout = AGENT_TIMEOUT_SECONDS + 60
            remaining = set(_role_tasks.values())
            still_pending = set()
            while remaining:
                try:
                    done, still_pending = await asyncio.wait(
                        remaining,
                        return_when=asyncio.ALL_COMPLETED,
                        timeout=_wait_timeout,
                    )
                    remaining = set()
                except asyncio.CancelledError:
                    if self._stop_event.is_set():
                        raise
                    remaining = {t for t in remaining if not t.done()}
                    if not remaining:
                        break
                    logger.warning(
                        f"[{self.project_id}] Spurious CancelledError in asyncio.wait — "
                        f"{len(remaining)} agent(s) still running, re-waiting..."
                    )
                    continue

            if still_pending:
                logger.warning(
                    f"[{self.project_id}] {len(still_pending)} agent task(s) timed out "
                    f"after {_wait_timeout}s — cancelling"
                )
                for t in still_pending:
                    t.cancel()
                await asyncio.wait(still_pending, timeout=5.0)

            for role_name, task in _role_tasks.items():
                if task.cancelled():
                    logger.warning(
                        f"[{self.project_id}] Agent role '{role_name}' was cancelled"
                    )
                    async with lock:
                        results.setdefault(role_name, []).append(SDKResponse(
                            text=f"Agent '{role_name}' was cancelled.",
                            is_error=True,
                            error_message="Task cancelled",
                        ))
                elif task.exception() is not None:
                    exc = task.exception()
                    logger.error(
                        f"[{self.project_id}] Agent role '{role_name}' raised exception: {exc}",
                        exc_info=exc,
                    )
                    await self._send_result(
                        f"⚠️ *{role_name}* crashed unexpectedly: {exc}\n"
                        f"The orchestrator will be notified to handle this."
                    )
                    async with lock:
                        results.setdefault(role_name, []).append(SDKResponse(
                            text=f"Agent crashed with exception: {exc}",
                            is_error=True,
                            error_message=str(exc),
                        ))

        role_tasks: dict[str, asyncio.Task] = {}  # Keep for compatibility
        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            # Phase 1: Run WRITER agents SEQUENTIALLY
            # This prevents file conflicts (the Cognition/Devin insight)
            if writer_roles:
                writer_count = sum(len(d) for d in writer_roles.values())
                await self._notify(
                    f"📝 Running {writer_count} code-modifying task(s) sequentially "
                    f"({', '.join(writer_roles.keys())})..."
                )
                for role, dels in writer_roles.items():
                    if self._stop_event.is_set():
                        break
                    await _isolated_run_role(role, dels)

            # Phase 2: Run READER agents IN PARALLEL
            # They only read/verify — safe to run concurrently
            if reader_roles and not self._stop_event.is_set():
                reader_count = sum(len(d) for d in reader_roles.values())
                await self._notify(
                    f"🔍 Running {reader_count} verification task(s) in parallel "
                    f"({', '.join(reader_roles.keys())})..."
                )
                await _run_roles_parallel(reader_roles)


        except asyncio.CancelledError:
            if self._stop_event.is_set():
                raise
            else:
                # Spurious cancellation — uncancel so we don't re-raise.
                ct = asyncio.current_task()
                if ct is not None and hasattr(ct, 'uncancel'):
                    ct.uncancel()
                logger.warning(
                    f"[{self.project_id}] _run_sub_agents got SPURIOUS CancelledError "
                    f"(anyio cancel-scope leak). Continuing..."
                )

        finally:
            heartbeat_task.cancel()
            try:
                await asyncio.wait_for(heartbeat_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Reset current_agent after all sub-agents finish
        self.current_agent = None
        self.current_tool = None

        # Detect file conflicts between parallel agents
        if len(files_touched) > 1:
            conflicts = self._detect_file_conflicts(files_touched)
            if conflicts:
                conflict_msg = "⚠️ FILE CONFLICTS DETECTED:\n"
                for file_path, agents in conflicts.items():
                    conflict_msg += f"  • {file_path} — modified by: {', '.join(agents)}\n"
                conflict_msg += (
                    "\nThe orchestrator will be informed to resolve these conflicts."
                )
                await self._notify(conflict_msg)
                logger.warning(f"[{self.project_id}] File conflicts: {conflicts}")
                # Inject conflict info as a readable agent entry so orchestrator can resolve
                results.setdefault("⚠ File Conflicts", []).append(SDKResponse(
                    text=conflict_msg,
                    is_error=True,
                    error_message=f"File conflicts in: {', '.join(conflicts.keys())}",
                ))

        return results

    def _extract_touched_files(self, text: str) -> set[str]:
        """Extract file paths that an agent likely modified from its output."""
        touched = set()
        for line in text.split('\n'):
            lower = line.lower().strip()
            # Match common patterns from tool use output
            if any(w in lower for w in (
                'writing:', 'editing:', 'created:', 'modified:', 'wrote to',
                '✏️', '🔧 editing', 'updated file', 'created file',
            )):
                for token in line.split():
                    cleaned = token.strip('`"\',;:()[]{}')
                    if ('/' in cleaned or '.' in cleaned) and len(cleaned) > 3:
                        # Filter out URLs, log lines, etc.
                        if not cleaned.startswith('http') and not cleaned.startswith('//'):
                            touched.add(cleaned)
        return touched

    def _detect_file_conflicts(self, files_touched: dict[str, set[str]]) -> dict[str, list[str]]:
        """Detect files modified by multiple agents (potential conflicts)."""
        file_to_agents: dict[str, list[str]] = {}
        for agent, files in files_touched.items():
            for f in files:
                file_to_agents.setdefault(f, []).append(agent)
        # Only return files touched by 2+ agents
        return {f: agents for f, agents in file_to_agents.items() if len(agents) > 1}

    async def _accumulate_context(self, agent_role: str, task: str, response: SDKResponse):
        """Build rich shared context from an agent's response.

        Called under lock. Creates structured context entries that help
        other agents and the orchestrator understand what was done.
        Uses git diff --stat for reliable file tracking instead of regex.
        """
        text = response.text

        # Use git for reliable file change detection instead of fragile regex
        files_changed_git = []
        git_diff_snippet = ""
        try:
            async def _git_acc(*args: str) -> str:
                proc = await asyncio.create_subprocess_exec(
                    "git", *args,
                    cwd=self.project_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                return stdout.decode("utf-8", errors="replace")

            diff_names = await _git_acc("diff", "--name-only", "HEAD")
            if diff_names.strip():
                files_changed_git = [f.strip() for f in diff_names.strip().split('\n') if f.strip()]
            # Also check untracked (new) files
            untracked = await _git_acc("ls-files", "--others", "--exclude-standard")
            if untracked.strip():
                files_changed_git.extend([f"(new) {f.strip()}" for f in untracked.strip().split('\n') if f.strip()])
            # Get a short diff snippet for context — helps agents know WHAT changed
            if files_changed_git:
                stat_out = await _git_acc("diff", "--stat", "HEAD")
                if stat_out.strip():
                    git_diff_snippet = stat_out.strip()[:300]
        except Exception:
            pass

        # Fallback: parse text for file operations if git didn't find anything
        files_from_text = []
        commands_run = []
        test_results = []
        for line in text.split('\n'):
            lower = line.lower().strip()
            if any(w in lower for w in ('created file', 'wrote to', 'writing:', '✏️ writing', 'created:')):
                for token in line.split():
                    if '/' in token or ('.' in token and len(token) > 3):
                        cleaned = token.strip('`"\',;:()[]')
                        if cleaned and not cleaned.startswith('http'):
                            files_from_text.append(cleaned)
            elif any(w in lower for w in ('edited', 'modified', 'updated', '🔧 editing')):
                for token in line.split():
                    if '/' in token or ('.' in token and len(token) > 3):
                        cleaned = token.strip('`"\',;:()[]')
                        if cleaned and not cleaned.startswith('http'):
                            files_from_text.append(cleaned)
            elif any(w in lower for w in ('running:', '💻 running', 'executed:', '$ ')):
                cmd = line.strip()[:80]
                if cmd:
                    commands_run.append(cmd)
            # Capture test outcomes
            elif any(w in lower for w in ('test passed', 'tests passed', 'all tests', 'test failed', 'tests failed', 'assertion')):
                test_results.append(line.strip()[:120])

        # Build structured context entry — tagged with round number for temporal tracking
        ctx_parts = [f"[{agent_role}] Round {self._current_loop} | Task: {task[:200]}"]
        if response.is_error:
            ctx_parts.append(f"  Status: FAILED — {response.error_message[:200]}")
        else:
            ctx_parts.append(f"  Status: SUCCESS ({response.num_turns} turns, ${response.cost_usd:.4f})")

        # Prefer git-based file info (more reliable)
        files_info = files_changed_git if files_changed_git else files_from_text
        if files_info:
            ctx_parts.append(f"  Files changed: {', '.join(files_info[:12])}")
        if git_diff_snippet:
            ctx_parts.append(f"  Diff summary: {git_diff_snippet[:200]}")
        if commands_run:
            ctx_parts.append(f"  Commands: {'; '.join(commands_run[:5])}")
        if test_results:
            ctx_parts.append(f"  Test results: {'; '.join(test_results[:3])}")

        # Include key output summary — look for structured sections first
        # Parse new ## SUMMARY/STATUS/ISSUES FOUND format (set by updated sub-agent prompts)
        summary = ""
        status_line = ""
        issues = ""
        for marker in ["## SUMMARY", "## Summary", "### Summary", "## Result", "### Changes"]:
            idx = text.find(marker)
            if idx >= 0:
                end = text.find("\n## ", idx + len(marker))
                summary = text[idx: end if end > idx else idx + 400].strip()
                break
        for sm in ["## STATUS", "## Status"]:
            idx = text.find(sm)
            if idx >= 0:
                for line in text[idx:idx + 200].strip().split("\n")[1:]:
                    if line.strip():
                        status_line = line.strip()[:150]
                        break
                break
        for im in ["## ISSUES FOUND", "## Issues Found"]:
            idx = text.find(im)
            if idx >= 0:
                lines = [l.strip() for l in text[idx:idx + 400].strip().split("\n")[1:]
                         if l.strip() and l.strip() not in ("(or: none)", "none")]
                if lines:
                    issues = "; ".join(lines[:3])
                break
        if not summary:
            summary = text[:400].strip()
        if summary:
            ctx_parts.append(f"  Output: {summary[:400]}")
        if status_line:
            ctx_parts.append(f"  Status: {status_line}")
        if issues:
            ctx_parts.append(f"  Issues: {issues}")

        self.shared_context.append("\n".join(ctx_parts))

        # Smart context compression: instead of just truncating, compress older entries
        if len(self.shared_context) > 20:
            # Keep last 10 entries full, compress older ones into a summary
            old_entries = self.shared_context[:-10]
            recent_entries = self.shared_context[-10:]

            # Compress old entries: extract just role, status, and key findings
            compressed_lines = []
            for entry in old_entries:
                lines = entry.split('\n')
                # Keep only the header line (role + status) and issues
                for line in lines:
                    ls = line.strip()
                    if ls.startswith('[') or 'FAILED' in ls or 'Issues:' in ls or 'Status:' in ls:
                        compressed_lines.append(f"  {ls[:150]}")
                        break

            if compressed_lines:
                summary = (
                    f"[COMPRESSED HISTORY] Rounds 1-{len(old_entries)} summary:\n"
                    + "\n".join(compressed_lines)
                )
                self.shared_context = [summary] + recent_entries
            else:
                self.shared_context = recent_entries

    def _get_context_for_agent(self, agent_role: str) -> str:
        """Build a smart context summary for a sub-agent.

        Instead of dumping all shared_context, prioritize:
        1. Most recent entries (last 3)
        2. Entries from agents with related roles
        3. Error entries (always include so agents don't repeat mistakes)
        """
        if not self.shared_context:
            return ""

        priority_entries = []
        recent_entries = []
        error_entries = []
        followup_entries = []

        for ctx in self.shared_context:
            if "FAILED" in ctx or "ERROR" in ctx or "BLOCKED" in ctx:
                error_entries.append(ctx)
            elif "NEEDS_FOLLOWUP" in ctx:
                followup_entries.append(ctx)
            elif f"[{agent_role}]" in ctx:
                # Same agent's previous work — always relevant
                priority_entries.append(ctx)
            else:
                recent_entries.append(ctx)

        # Build context: errors + followups first, then own history, then recent
        selected = []
        selected.extend(error_entries[-3:])
        selected.extend(followup_entries[-2:])
        selected.extend(priority_entries[-3:])
        remaining_slots = max(0, 8 - len(selected))
        selected.extend(recent_entries[-remaining_slots:])

        if not selected:
            return ""

        # Compress each entry — preserve structured sections, truncate raw output
        compressed = []
        for entry in selected:
            lines = entry.split('\n')
            essential = []
            for line in lines:
                ls = line.strip()
                # Always keep role/status/files/issues/status-line headers
                if ls.startswith(('[', 'Status:', 'Files changed:', 'Issues:', 'Commands:')):
                    essential.append(line)
                elif ls.startswith('Output:'):
                    # Truncate long output but keep it
                    essential.append(line[:200])
                elif len(essential) < 8:
                    essential.append(line)
            compressed.append('\n'.join(essential))

        # Build the context block — manifest first (persistent truth), then round history
        sections: list[str] = []
        manifest = self._read_project_manifest()
        if manifest:
            sections.append(
                "─── PROJECT MANIFEST (team's persistent memory) ───\n"
                + manifest +
                "\n───────────────────────────────────────────────────"
            )
        if compressed:
            sections.append("Context from previous rounds:\n" + "\n---\n".join(compressed))

        return "\n\n".join(sections) if sections else ""

    async def _query_agent(self, agent_role: str, prompt: str, skill_names: list[str] | None = None) -> SDKResponse:
        """Query a specific agent (orchestrator or sub-agent) using the SDK."""
        # Get system prompt and resource limits based on role and mode
        allowed_tools = None  # None = all tools available (default)
        tools = None  # None = default tool set; [] = disable ALL tools

        if agent_role == "orchestrator" and self.multi_agent:
            # Build orchestrator prompt with available skills summary + actual skill content
            system_prompt = ORCHESTRATOR_SYSTEM_PROMPT
            available_skills = self._get_available_skills_summary()
            if available_skills:
                system_prompt += f"\n\n{available_skills}"
            # Also inject full content of orchestrator-mapped skills (planning, summarize, etc.)
            orch_skills = get_skills_for_agent("orchestrator")
            if orch_skills:
                skill_content = build_skill_prompt(orch_skills)
                if skill_content:
                    system_prompt += skill_content
            max_turns = 10  # Orchestrator needs turns to: read files, analyze, then produce delegate blocks
            max_budget = 5.0  # Orchestrator processes large context across many rounds — needs headroom
            permission_mode = "bypassPermissions"
            # Read-only tools: orchestrator can inspect the project but NOT modify it
            allowed_tools = [
                "Read",       # Read file contents
                "Glob",       # List/find files by pattern
                "Grep",       # Search file contents
                "LS",         # List directory
                "Bash(git log*)",   # Git history (read-only)
                "Bash(git diff*)",  # Git diff (read-only)
                "Bash(git status*)",  # Git status (read-only)
                "Bash(cat *)",       # Cat files (read-only)
                "Bash(head *)",      # Head files (read-only)
                "Bash(tail *)",      # Tail files (read-only)
                "Bash(wc *)",        # Word count (read-only)
                "Bash(find *)",      # Find files (read-only)
                "Bash(pytest*)",     # Run tests (read-only verification)
                "Bash(python*-m*pytest*)",  # Run tests via python
                "Bash(npm test*)",   # Run JS tests
                "Bash(npx jest*)",   # Run Jest tests
            ]
            tools = None  # Use default tool set (filtered by allowed_tools)
            logger.info(f"[{self.project_id}] Querying orchestrator (coordinator mode, read-only tools, max_turns=10)")
        elif agent_role == "orchestrator" and not self.multi_agent:
            system_prompt = SOLO_AGENT_PROMPT
            max_turns = SDK_MAX_TURNS_PER_QUERY
            max_budget = SDK_MAX_BUDGET_PER_QUERY
            permission_mode = "bypassPermissions"
            logger.info(f"[{self.project_id}] Querying orchestrator (solo mode, full tools)")
        else:
            system_prompt = SUB_AGENT_PROMPTS.get(agent_role, "You are a helpful coding assistant.")
            # Append skill content if requested or auto-mapped
            # Smart skill selection: pick top 5 relevant skills from task text (not all ~48)
            task_hint = prompt[:1000]  # Use first 1000 chars of prompt as task signal
            auto_skills = select_skills_for_task(agent_role, task_hint, max_skills=5)
            all_skills = list(dict.fromkeys(list(skill_names or []) + auto_skills))  # explicit first, then auto
            if all_skills:
                skill_suffix = build_skill_prompt(list(dict.fromkeys(all_skills)))  # deduplicate
                if skill_suffix:
                    system_prompt += skill_suffix
            max_turns = SDK_MAX_TURNS_PER_QUERY
            max_budget = SDK_MAX_BUDGET_PER_QUERY
            permission_mode = "bypassPermissions"
            logger.info(f"[{self.project_id}] Querying sub-agent '{agent_role}' (max_turns={max_turns}, budget=${max_budget}, skills={all_skills or 'none'})")

        # Try to resume session
        session_id = await self.session_mgr.get_session(
            self.user_id, self.project_id, agent_role
        )

        # Stream callback: show live agent activity in the progress message
        async def on_stream(text: str):
            emoji = AGENT_EMOJI.get(agent_role, "🔧")
            await self._notify(f"{emoji} *{agent_role}*\n{text[-500:]}")

        # Tool use callback: emit tool_use events for dashboard
        async def on_tool_use(tool_name: str, tool_info: str, tool_input: dict):
            self.current_tool = tool_info
            if agent_role in self.agent_states:
                self.agent_states[agent_role]["current_tool"] = tool_info
                # Track tool count for progress insight
                count = self.agent_states[agent_role].get("tool_count", 0) + 1
                self.agent_states[agent_role]["tool_count"] = count
            await self._emit_event(
                "tool_use",
                agent=agent_role,
                tool_name=tool_name,
                description=tool_info,
                input=tool_input,
                timestamp=time.time(),
            )
            # Also emit an agent_update so the ticker shows what's happening NOW
            await self._emit_event(
                "agent_update",
                agent=agent_role,
                text=tool_info,
                timestamp=time.time(),
            )

        # Use isolated_query for sub-agents (they run in parallel and are
        # vulnerable to the anyio cancel-scope bug).  The orchestrator itself
        # runs alone, so it can use the direct SDK call safely.
        use_isolation = (agent_role != "orchestrator")

        if use_isolation:
            response = await isolated_query(
                self.sdk,
                prompt=prompt,
                system_prompt=system_prompt,
                cwd=self.project_dir,
                session_id=session_id,
                max_turns=max_turns,
                max_budget_usd=max_budget,
                permission_mode=permission_mode,
                on_stream=on_stream,
                on_tool_use=on_tool_use,
                allowed_tools=allowed_tools,
                tools=tools,
            )
        else:
            response = await self.sdk.query_with_retry(
                prompt=prompt,
                system_prompt=system_prompt,
                cwd=self.project_dir,
                session_id=session_id,
                max_turns=max_turns,
                max_budget_usd=max_budget,
                permission_mode=permission_mode,
                on_stream=on_stream,
                on_tool_use=on_tool_use,
                allowed_tools=allowed_tools,
                tools=tools,
            )

        # Save session for future resume
        if response.session_id and not response.is_error:
            await self.session_mgr.save_session(
                self.user_id, self.project_id, agent_role,
                response.session_id, response.cost_usd, response.num_turns,
            )
        elif response.is_error and session_id:
            # Session may be stale, invalidate it
            error_lower = response.error_message.lower()
            if "session" in error_lower or "resume" in error_lower:
                await self.session_mgr.invalidate_session(
                    self.user_id, self.project_id, agent_role
                )

        return response

    def _record_response(self, agent_name: str, role: str, response: SDKResponse):
        """Record an agent response in the conversation log and update costs."""
        self.total_cost_usd += response.cost_usd
        self._agents_used.add(agent_name)  # Track participation for premature-completion checks
        self.conversation_log.append(
            Message(
                agent_name=agent_name,
                role=role,
                content=response.text,
                cost_usd=response.cost_usd,
            )
        )
        # Cap log at 2000 entries — drop oldest to prevent memory blowup in EPIC 50-round tasks.
        # Agent participation is tracked separately in _agents_used so it survives trimming.
        if len(self.conversation_log) > 2000:
            self.conversation_log = self.conversation_log[-2000:]
        # Persist to SQLite in background (safe: tracked reference, errors logged)
        self._create_background_task(
            self.session_mgr.add_message(
                self.project_id, agent_name, role, response.text, response.cost_usd,
            )
        )

    # --- Delegation parsing ---

    def _get_available_skills_summary(self) -> str:
        """Build a summary of available skills for the orchestrator to reference.

        This lets the orchestrator know which skills exist and which agent to
        assign them to, enabling smarter delegation.
        """
        from skills_registry import list_skills, SKILL_AGENT_MAP
        skills = list_skills()
        if not skills:
            return ""

        lines = ["AVAILABLE SKILLS — you can request these via the 'skills' field in delegation:"]
        for skill_name in skills:
            mapped_agent = SKILL_AGENT_MAP.get(skill_name, "developer")
            lines.append(f"  - {skill_name} (best suited for: {mapped_agent})")
        lines.append(
            "\nTo use a skill, add a 'skills' array to your delegation JSON:\n"
            '<delegate>\n'
            '{"agent": "developer", "task": "...", "skills": ["frontend-design"]}\n'
            '</delegate>'
        )
        return "\n".join(lines)

    def _parse_delegations(self, text: str) -> list[Delegation]:
        """Parse <delegate> blocks from orchestrator output."""
        delegations = []
        for match in _DELEGATE_RE.finditer(text):
            raw = match.group(1)
            try:
                # First try simple json.loads
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Fall back to robust JSON extraction (handles nested braces, etc.)
                data = _extract_json(raw)
            if data and isinstance(data, dict):
                agent = data.get("agent", "developer")
                task = data.get("task", "")
                if task:  # Only add if there's an actual task
                    skills_list = data.get("skills", [])
                    if isinstance(skills_list, str):
                        skills_list = [skills_list]
                    delegations.append(Delegation(
                        agent=agent,
                        task=task,
                        context=data.get("context", ""),
                        skills=skills_list,
                    ))
                    logger.info(f"Parsed delegation: {agent} -> {task[:80]}")
                else:
                    logger.warning(f"Delegation block missing 'task': {raw[:200]}")
            else:
                logger.warning(f"Failed to parse delegation JSON: {raw[:200]}")

        # Fallback: if we found <delegate> tags but got no valid delegations, log it
        if not delegations and "<delegate>" in text:
            logger.error(
                f"Found <delegate> tags but failed to parse any delegations! "
                f"Raw text around tags: {text[text.find('<delegate>'):text.find('</delegate>') + 20][:500]}"
            )
        return delegations

    def _strip_delegate_blocks(self, text: str) -> str:
        """Remove <delegate>...</delegate> blocks from text for display purposes."""
        # Use a broader regex that catches everything between the tags
        return re.sub(r"<delegate>.*?</delegate>", "", text, flags=re.DOTALL).strip()

    async def _build_review_prompt(self, sub_results: dict[str, list[SDKResponse]], completed_rounds: list[str] | None = None) -> str:
        """Build a structured prompt for the orchestrator to review sub-agent results.

        This is the CRITICAL method that drives the orchestration loop forward.
        Instead of dumping raw agent output and hoping the orchestrator figures
        out what to do next, we:
        1. Parse agent outputs to extract specific findings (issues, bugs, failures)
        2. Build ready-made <delegate> blocks the orchestrator can use directly
        3. Truncate raw output to keep the prompt focused and actionable
        4. Present a clear decision: use these blocks, modify them, or TASK_COMPLETE
        """
        parts: list[str] = []

        # ── Budget / rounds context (compact) ──
        budget_used = self.total_cost_usd
        budget_cap = self._effective_budget
        budget_left = max(0.0, budget_cap - budget_used)
        loops_done = self._current_loop
        loops_max = MAX_ORCHESTRATOR_LOOPS
        loops_left = max(0, loops_max - loops_done)
        burn_rate = budget_used / max(loops_done, 1)
        budget_rounds_left = int(budget_left / burn_rate) if burn_rate > 0 else loops_left
        effective_rounds_left = min(loops_left, budget_rounds_left)
        parts.append(
            f"Round {loops_done}/{loops_max} | Budget ${budget_used:.2f}/${budget_cap:.0f} "
            f"(~{effective_rounds_left} rounds left)"
        )
        if effective_rounds_left < 5:
            parts.append("⚠️ BUDGET LOW — prioritize critical work only!")

        # ── Parse each agent's output ──
        has_errors = False
        successful_agents: list[str] = []
        failed_agents: list[str] = []
        crashed_agents: list[str] = []

        # Structured findings extracted from agent outputs
        _findings: list[dict] = []  # {agent, type, description, file, severity}
        _agent_summaries: dict[str, str] = {}  # agent -> compact summary
        _agents_only_reports: list[str] = []
        _agents_wrote_code: list[str] = []
        _test_results: list[dict] = []  # {agent, passed, failed, errors}

        _CRASH_INDICATORS = (
            "session crashed", "session was interrupted", "pick up where I left off",
            "was in the middle of", "got cancelled", "timed out",
            "anyio bug", "cancel scope", "RuntimeError",
        )

        for agent, responses in sub_results.items():
            for resp_idx, response in enumerate(responses):
                # Detect soft crashes
                is_soft_crash = False
                if not response.is_error and response.text:
                    text_lower = response.text[:1000].lower()
                    if any(ind in text_lower for ind in _CRASH_INDICATORS):
                        is_soft_crash = True

                if response.is_error or is_soft_crash:
                    has_errors = True
                    if is_soft_crash:
                        crashed_agents.append(agent)
                    else:
                        failed_agents.append(agent)
                else:
                    successful_agents.append(agent)

                text = response.text or ""

                # ── Extract structured sections from agent output ──
                summary = self._extract_section(text, ["## SUMMARY", "## Summary", "### Summary", "## Result"])
                status_line = self._extract_section(text, ["## STATUS", "## Status"], max_lines=2)
                issues_text = self._extract_section(text, ["## ISSUES FOUND", "## Issues Found", "## Issues", "## Findings"])
                files_section = self._extract_section(text, ["## FILES CHANGED", "## Files Changed"])

                # Build compact agent summary (max 600 chars)
                if response.is_error:
                    agent_summary = f"FAILED: {response.error_message[:300]}"
                elif is_soft_crash:
                    agent_summary = f"CRASHED: {text[:300]}"
                elif summary:
                    agent_summary = summary[:600]
                else:
                    agent_summary = text[:600]
                _agent_summaries[agent] = agent_summary

                # ── Extract specific findings (issues, bugs, test failures) ──
                if issues_text:
                    for line in issues_text.split("\n"):
                        line = line.strip()
                        if not line or line.startswith("#") or line in ("(or: none)", "none", "None"):
                            continue
                        # Parse severity if present
                        severity = "MEDIUM"
                        for sev in ["CRITICAL", "HIGH", "LOW"]:
                            if sev in line.upper():
                                severity = sev
                                break
                        # Extract file path if mentioned
                        file_path = ""
                        for token in line.split():
                            cleaned = token.strip("`\"',;:()[]")
                            if ("/" in cleaned or "." in cleaned) and len(cleaned) > 3:
                                if not cleaned.startswith("http") and any(
                                    cleaned.endswith(ext) for ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".yml", ".yaml", ".toml", ".md")
                                ):
                                    file_path = cleaned
                                    break
                        _findings.append({
                            "agent": agent,
                            "type": "issue",
                            "description": line[:200],
                            "file": file_path,
                            "severity": severity,
                        })

                # ── Extract test results ──
                text_upper = text.upper()
                if agent in ("tester",) or "TEST" in text_upper:
                    passed = failed = errors = 0
                    for line in text.split("\n"):
                        ll = line.lower().strip()
                        # Parse "N passed" or "passed: N" patterns
                        # Be careful not to double-count: "12 passed, 0 failed" should NOT set failed=12
                        tokens = ll.split()
                        for ti, tok in enumerate(tokens):
                            if tok.isdigit():
                                num = int(tok)
                                # Check the NEXT token for the keyword
                                next_tok = tokens[ti + 1] if ti + 1 < len(tokens) else ""
                                if "passed" in next_tok or next_tok.startswith("pass"):
                                    passed = max(passed, num)
                                elif "failed" in next_tok or "failure" in next_tok or next_tok.startswith("fail"):
                                    failed = max(failed, num)
                                elif "error" in next_tok:
                                    errors = max(errors, num)
                            elif tok in ("passed:", "passed") and ti + 1 < len(tokens) and tokens[ti + 1].isdigit():
                                passed = max(passed, int(tokens[ti + 1]))
                            elif tok in ("failed:", "failed", "failures:") and ti + 1 < len(tokens) and tokens[ti + 1].isdigit():
                                failed = max(failed, int(tokens[ti + 1]))
                            elif tok in ("errors:", "error:") and ti + 1 < len(tokens) and tokens[ti + 1].isdigit():
                                errors = max(errors, int(tokens[ti + 1]))
                        # Capture specific failure messages
                        if any(kw in ll for kw in ("fail:", "failed:", "error:", "assertion")):
                            _findings.append({
                                "agent": agent,
                                "type": "test_failure",
                                "description": line.strip()[:200],
                                "file": "",
                                "severity": "HIGH",
                            })
                    if passed or failed or errors:
                        _test_results.append({"agent": agent, "passed": passed, "failed": failed, "errors": errors})

                # ── Detect report-only vs code changes ──
                _code_extensions = (".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".yml", ".yaml", ".toml")
                _report_extensions = (".md", ".txt", ".log", ".json")
                if files_section:
                    has_code = any(ext in files_section for ext in _code_extensions)
                    has_only_reports = all(
                        any(ext in line for ext in _report_extensions)
                        for line in files_section.split("\n")[1:]
                        if line.strip().startswith("-") or line.strip().startswith("*")
                    ) if files_section.strip() else False
                    if has_only_reports and not has_code:
                        _agents_only_reports.append(agent)
                    elif has_code:
                        _agents_wrote_code.append(agent)

                # ── Detect NEEDS_FOLLOWUP / BLOCKED ──
                if status_line:
                    if "NEEDS_FOLLOWUP" in status_line:
                        _findings.append({
                            "agent": agent, "type": "followup",
                            "description": status_line[:200],
                            "file": "", "severity": "HIGH",
                        })
                    elif "BLOCKED" in status_line:
                        _findings.append({
                            "agent": agent, "type": "blocked",
                            "description": status_line[:200],
                            "file": "", "severity": "CRITICAL",
                        })

        # ── WORKSPACE CHANGES (git) ──
        file_changes = await self._detect_file_changes()
        has_file_changes = file_changes and "(no file" not in file_changes

        # ═══════════════════════════════════════════════════════
        # BUILD THE PROMPT — compact, actionable, with ready-made blocks
        # ═══════════════════════════════════════════════════════

        # Section 1: Agent summaries (compact — NOT raw output)
        parts.append("\n═══ AGENT RESULTS (this round) ═══")
        for agent, summary in _agent_summaries.items():
            status_tag = "✅" if agent in successful_agents else "❌ FAILED" if agent in failed_agents else "💥 CRASHED"
            parts.append(f"{status_tag} {agent}: {summary[:500]}")
        parts.append("")

        # Section 2: File changes
        if has_file_changes:
            parts.append(f"═══ FILES CHANGED ═══\n{file_changes}\n")

        # Section 3: Findings summary (if any)
        critical_findings = [f for f in _findings if f["severity"] in ("CRITICAL", "HIGH")]
        medium_findings = [f for f in _findings if f["severity"] == "MEDIUM"]
        if critical_findings or medium_findings:
            parts.append("═══ ISSUES REQUIRING ACTION ═══")
            for f in critical_findings:
                file_hint = f" in {f['file']}" if f['file'] else ""
                parts.append(f"  🔴 [{f['severity']}] {f['description']}{file_hint} (found by {f['agent']})")
            for f in medium_findings[:5]:  # Cap medium findings
                file_hint = f" in {f['file']}" if f['file'] else ""
                parts.append(f"  🟡 [{f['severity']}] {f['description']}{file_hint} (found by {f['agent']})")
            parts.append("")

        # Section 4: Test results summary
        if _test_results:
            parts.append("═══ TEST RESULTS ═══")
            for tr in _test_results:
                parts.append(f"  {tr['agent']}: {tr['passed']} passed, {tr['failed']} failed, {tr['errors']} errors")
            parts.append("")

        # Section 5: Round history (compact)
        if completed_rounds:
            parts.append(f"═══ HISTORY ({len(completed_rounds)} rounds) ═══")
            for r in completed_rounds[-5:]:  # Only show last 5 rounds
                parts.append(f"  {r}")
            parts.append("")

        # ═══════════════════════════════════════════════════════
        # GENERATE READY-MADE <delegate> BLOCKS
        # This is the key innovation: instead of telling the orchestrator
        # "figure out what to do", we BUILD the blocks for it.
        # ═══════════════════════════════════════════════════════
        suggested_blocks: list[str] = []

        # Priority 1: Retry crashed/failed agents
        _retried = set()
        for agent in crashed_agents + failed_agents:
            if agent in _retried:
                continue
            _retried.add(agent)
            # Find the original task for this agent from the delegation
            error_ctx = _agent_summaries.get(agent, "unknown error")[:200]
            suggested_blocks.append(
                f'<delegate>\n'
                f'{{"agent": "{agent}", "task": "RETRY: Your previous attempt failed/crashed. '
                f'Please retry the same task with a fresh approach.", '
                f'"context": "Previous error: {self._escape_json_str(error_ctx)}"}}\n'
                f'</delegate>'
            )

        # Priority 2: Fix issues found by reviewer/tester
        if critical_findings and not failed_agents:
            # Group findings by file for efficient fixing
            files_with_issues: dict[str, list[str]] = {}
            general_issues: list[str] = []
            for f in critical_findings:
                if f["file"]:
                    files_with_issues.setdefault(f["file"], []).append(f["description"])
                else:
                    general_issues.append(f["description"])

            # Build fix tasks — group by file
            fix_descriptions: list[str] = []
            for fpath, descs in list(files_with_issues.items())[:5]:
                fix_descriptions.append(f"In {fpath}: {'; '.join(d[:80] for d in descs[:3])}")
            if general_issues:
                fix_descriptions.append(f"General: {'; '.join(d[:80] for d in general_issues[:3])}")

            if fix_descriptions:
                fix_task = "Fix the following issues found by reviewer/tester: " + " | ".join(fix_descriptions[:4])
                fix_context = f"Issues found in round {loops_done}. Fix the actual code, don't just report."
                suggested_blocks.append(
                    f'<delegate>\n'
                    f'{{"agent": "developer", "task": "{self._escape_json_str(fix_task[:500])}", '
                    f'"context": "{self._escape_json_str(fix_context)}"}}\n'
                    f'</delegate>'
                )

        # Priority 3: Test failures need developer fixes
        test_failures = [f for f in _findings if f["type"] == "test_failure"]
        if test_failures and not failed_agents and not critical_findings:
            failure_descs = "; ".join(f["description"][:80] for f in test_failures[:5])
            suggested_blocks.append(
                f'<delegate>\n'
                f'{{"agent": "developer", "task": "Fix failing tests: {self._escape_json_str(failure_descs[:400])}", '
                f'"context": "Tests were run and some failed. Fix the code (not the tests) to make them pass."}}\n'
                f'</delegate>'
            )

        # Priority 4: Reports written but no code changes → developer must implement
        if _agents_only_reports and not _agents_wrote_code and not failed_agents:
            report_agents = ", ".join(_agents_only_reports)
            suggested_blocks.append(
                f'<delegate>\n'
                f'{{"agent": "developer", "task": "Implement the fixes/changes described in the reports written by {report_agents}. '
                f'Read their output above and make the actual code changes.", '
                f'"context": "Previous round produced reports/analysis but no code changes. Now implement."}}\n'
                f'</delegate>'
            )

        # Priority 5: Code was written but not reviewed/tested
        all_agents_set = {"developer", "reviewer", "tester", "devops", "researcher"}
        unused_agents = all_agents_set - self._agents_used
        roles_this_round = set(successful_agents)

        if _agents_wrote_code or has_file_changes:
            if "reviewer" not in roles_this_round and "reviewer" not in failed_agents:
                changed_files = file_changes[:200] if has_file_changes else "check git diff"
                suggested_blocks.append(
                    f'<delegate>\n'
                    f'{{"agent": "reviewer", "task": "Review the code changes from this round for bugs, security issues, and best practices.", '
                    f'"context": "Files changed: {self._escape_json_str(changed_files)}"}}\n'
                    f'</delegate>'
                )
            if "tester" not in roles_this_round and "tester" not in failed_agents:
                suggested_blocks.append(
                    f'<delegate>\n'
                    f'{{"agent": "tester", "task": "Write and run tests for the code changes made this round. Report PASS/FAIL with details.", '
                    f'"context": "Code was modified — verify it works correctly."}}\n'
                    f'</delegate>'
                )

        # Priority 6: Blocked/followup items
        blocked_findings = [f for f in _findings if f["type"] == "blocked"]
        followup_findings = [f for f in _findings if f["type"] == "followup"]
        for bf in blocked_findings:
            suggested_blocks.append(
                f'<delegate>\n'
                f'{{"agent": "{bf["agent"]}", "task": "UNBLOCK: {self._escape_json_str(bf["description"][:300])}", '
                f'"context": "This agent was blocked in the previous round. Provide what they need to proceed."}}\n'
                f'</delegate>'
            )
        for ff in followup_findings:
            suggested_blocks.append(
                f'<delegate>\n'
                f'{{"agent": "{ff["agent"]}", "task": "FOLLOWUP: {self._escape_json_str(ff["description"][:300])}", '
                f'"context": "This agent needs follow-up work from the previous round."}}\n'
                f'</delegate>'
            )

        # ═══════════════════════════════════════════════════════
        # FINAL DECISION SECTION
        # ═══════════════════════════════════════════════════════
        if suggested_blocks:
            parts.append("═══ SUGGESTED NEXT DELEGATIONS (ready to use) ═══")
            parts.append("Use these <delegate> blocks as-is, modify them, or add more:")
            parts.append("")
            parts.extend(suggested_blocks)
            parts.append("")
            parts.append(
                "INSTRUCTIONS: Copy the <delegate> blocks above into your response. "
                "You may modify the task/context or add additional blocks. "
                "Do NOT say TASK_COMPLETE — there is work to do."
            )
        else:
            # No suggested blocks — either everything is done or we can't determine next steps
            all_success = not has_errors
            has_review = "reviewer" in self._agents_used
            has_tests = "tester" in self._agents_used
            code_changed = bool(_agents_wrote_code) or has_file_changes

            if all_success and has_review and has_tests and code_changed:
                # Genuinely looks complete
                parts.append(
                    "═══ DECISION ═══\n"
                    "All agents succeeded, code was reviewed and tested.\n"
                    "If the original task is fully addressed, respond with TASK_COMPLETE.\n"
                    "If there's more work needed, create <delegate> blocks for the next phase."
                )
            elif all_success and code_changed:
                # Code changed but missing review/tests
                missing = []
                if not has_review:
                    missing.append("code review (reviewer)")
                if not has_tests:
                    missing.append("testing (tester)")
                parts.append(
                    f"═══ DECISION ═══\n"
                    f"Code was changed but still needs: {', '.join(missing)}.\n"
                    f"Delegate the missing steps before TASK_COMPLETE."
                )
                # Generate blocks for missing steps
                if not has_review:
                    parts.append(
                        f'\n<delegate>\n'
                        f'{{"agent": "reviewer", "task": "Review all code changes for bugs, security, and best practices.", '
                        f'"context": "Code was written but not yet reviewed."}}\n'
                        f'</delegate>'
                    )
                if not has_tests:
                    parts.append(
                        f'\n<delegate>\n'
                        f'{{"agent": "tester", "task": "Write and run tests for the implementation. Report PASS/FAIL.", '
                        f'"context": "Code was written but not yet tested."}}\n'
                        f'</delegate>'
                    )
            elif not code_changed and all_success:
                parts.append(
                    "═══ DECISION ═══\n"
                    "No code changes detected. Either:\n"
                    "A) The task doesn't require code changes → TASK_COMPLETE if done\n"
                    "B) Agents didn't do the work → delegate with more specific instructions"
                )
            else:
                parts.append(
                    "═══ DECISION ═══\n"
                    "Review the results above and decide what to do next.\n"
                    "Create <delegate> blocks for any remaining work."
                )

        # ── Inject stuck escalation hint if detected ──
        hint = getattr(self, '_stuck_escalation_hint', None)
        if hint:
            parts.append(f"\n{'='*50}")
            parts.append(hint)
            parts.append(f"{'='*50}")
            self._stuck_escalation_hint = None  # Clear after injection

        return "\n".join(parts)

    def _extract_section(self, text: str, markers: list[str], max_lines: int = 10) -> str:
        """Extract a section from agent output text by looking for markdown headers."""
        for marker in markers:
            idx = text.find(marker)
            if idx >= 0:
                # Find end of section (next ## header or end of text)
                end = text.find("\n## ", idx + len(marker))
                section = text[idx + len(marker): end if end > idx else idx + 800].strip()
                # Limit lines
                lines = section.split("\n")[:max_lines]
                return "\n".join(lines).strip()
        return ""

    @staticmethod
    def _escape_json_str(s: str) -> str:
        """Escape a string for safe inclusion in a JSON string value."""
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "").replace("\t", " ")

    # --- Stuck detection ---

    async def _detect_file_changes(self) -> str:
        """Run git status in the project dir to show what files the agent changed."""
        try:
            async def _git(*args: str) -> str:
                proc = await asyncio.create_subprocess_exec(
                    "git", *args,
                    cwd=self.project_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                return stdout.decode("utf-8", errors="replace")

            diff_out = await _git("diff", "--stat", "HEAD")
            if diff_out.strip():
                return diff_out.strip()
            # Also check untracked files
            status_out = await _git("status", "--short")
            return status_out.strip() or "(no file changes detected)"
        except Exception:
            return "(unable to detect changes)"

    # ── Evaluator-Reflect-Refine Loop ──
    # Automatically runs verification (tests/build/lint) after code changes.
    # If tests fail, auto-retries the developer with the error output—
    # saving an entire orchestrator round.

    async def _auto_evaluate(self, sub_results: dict[str, list[SDKResponse]], round_num: int) -> dict | None:
        """Run automatic evaluation after a round of sub-agent work.

        Detects if code was changed, runs tests/build if available,
        and optionally auto-retries the developer if tests fail.

        Returns:
            None if no evaluation was needed or possible.
            dict with keys:
                - summary: str — human-readable evaluation result
                - tests_passed: bool | None
                - auto_fixed: bool — whether developer was auto-retried
                - updated_results: dict — updated sub_results if auto-fixed
        """
        # Only evaluate if developer was in this round (code changes likely)
        if "developer" not in sub_results:
            return None

        # Check if there are actual file changes
        file_changes = await self._detect_file_changes()
        if not file_changes or "(no file" in file_changes:
            return None

        # Detect test framework and run tests
        test_output = await self._run_project_tests()
        if test_output is None:
            # No test framework detected
            return {"summary": "No test framework detected — skipping auto-evaluation.", "tests_passed": None, "auto_fixed": False, "updated_results": sub_results}

        test_passed = test_output["passed"]
        test_summary = test_output["output"][:1500]

        if test_passed:
            return {
                "summary": f"✅ Auto-evaluation: Tests PASSED\n{test_summary[:500]}",
                "tests_passed": True,
                "auto_fixed": False,
                "updated_results": sub_results,
            }

        # Tests failed — auto-retry developer with the error
        await self._notify(
            f"🔄 Auto-evaluator detected test failures in round {round_num}. "
            f"Sending developer back to fix..."
        )
        logger.info(f"[{self.project_id}] Auto-evaluator: tests failed, auto-retrying developer")

        # Build a focused fix prompt with the test output
        fix_prompt = (
            f"Project: {self.project_name}\n"
            f"Working directory: {self.project_dir}\n\n"
            f"URGENT: Tests are failing after your changes. Fix the code to make tests pass.\n\n"
            f"Test output:\n```\n{test_summary}\n```\n\n"
            f"Instructions:\n"
            f"1. Read the test output carefully to understand what's failing\n"
            f"2. Fix the SOURCE CODE (not the tests) to resolve the failures\n"
            f"3. Run the tests again to verify your fix works\n"
            f"4. Report what you changed\n"
        )

        try:
            fix_response = await self._query_agent("developer", fix_prompt)
            await self._accumulate_context("developer", "Auto-fix: resolve test failures", fix_response)

            # Run tests again to verify
            retest = await self._run_project_tests()
            retest_passed = retest["passed"] if retest else False
            retest_summary = retest["output"][:500] if retest else "(retest failed)"

            # Update sub_results with the fix response
            updated = dict(sub_results)
            updated.setdefault("developer", []).append(fix_response)

            return {
                "summary": (
                    f"🔄 Auto-evaluator: Tests failed after round {round_num}.\n"
                    f"Developer was auto-retried to fix.\n"
                    f"Retest result: {'PASSED ✅' if retest_passed else 'STILL FAILING ❌'}\n"
                    f"Retest output: {retest_summary}"
                ),
                "tests_passed": retest_passed,
                "auto_fixed": True,
                "updated_results": updated,
            }
        except Exception as e:
            logger.warning(f"[{self.project_id}] Auto-fix failed: {e}")
            return {
                "summary": f"❌ Auto-evaluator: Tests failed. Auto-fix attempt also failed: {str(e)[:200]}",
                "tests_passed": False,
                "auto_fixed": False,
                "updated_results": sub_results,
            }

    async def _run_project_tests(self) -> dict | None:
        """Detect and run the project's test suite.

        Returns:
            None if no test framework detected.
            dict with keys: passed (bool), output (str)
        """
        project = Path(self.project_dir)

        # Detect test framework by checking for config files and test directories
        test_commands = []

        # Python: pytest
        if (project / "pytest.ini").exists() or (project / "setup.cfg").exists() or \
           (project / "pyproject.toml").exists() or (project / "tests").is_dir() or \
           (project / "test").is_dir():
            # Check if pytest is available
            test_commands.append(["python3", "-m", "pytest", "--tb=short", "-q", "--no-header", "--timeout=30"])

        # Node.js: npm test / jest
        if (project / "package.json").exists():
            try:
                pkg = json.loads((project / "package.json").read_text())
                scripts = pkg.get("scripts", {})
                if "test" in scripts and scripts["test"] != 'echo "Error: no test specified" && exit 1':
                    test_commands.append(["npm", "test", "--", "--watchAll=false"])
            except Exception:
                pass

        if not test_commands:
            return None

        # Try each test command until one works
        for cmd in test_commands:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=self.project_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env={**os.environ, "CI": "true", "FORCE_COLOR": "0"},
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120.0)
                output = stdout.decode("utf-8", errors="replace")
                passed = proc.returncode == 0
                return {"passed": passed, "output": output}
            except asyncio.TimeoutError:
                return {"passed": False, "output": f"Tests timed out after 120s (command: {' '.join(cmd)})"}
            except FileNotFoundError:
                continue  # Try next command
            except Exception as e:
                return {"passed": False, "output": f"Test execution error: {str(e)}"}

        return None

    # ── File-System Context: Task Ledger (.nexus/todo.md) ──
    # Instead of relying solely on shared_context (which grows and gets trimmed),
    # we maintain a persistent todo.md file that tracks the current task state.
    # This is inspired by Manus's context engineering approach: the file system
    # IS the memory, and the todo.md keeps goals inside the model's "attention window".

    def _get_todo_path(self) -> Path:
        """Get the path to the task ledger file."""
        nexus_dir = Path(self.project_dir) / ".nexus"
        nexus_dir.mkdir(parents=True, exist_ok=True)
        return nexus_dir / "todo.md"

    def _read_todo(self) -> str:
        """Read the current task ledger. Returns empty string if not found."""
        todo_path = self._get_todo_path()
        if todo_path.exists():
            try:
                return todo_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return ""

    def _write_todo(self, content: str):
        """Write the task ledger. Creates .nexus/ dir if needed."""
        try:
            todo_path = self._get_todo_path()
            todo_path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.warning(f"[{self.project_id}] Failed to write todo.md: {e}")

    def _init_todo(self, user_message: str, complexity: str):
        """Initialize the task ledger at the start of a new session.

        Creates a structured todo.md with the original goal, phases,
        and a checklist that the orchestrator updates each round.
        """
        existing = self._read_todo()
        if existing:
            # Don't overwrite — this is a continuation
            return

        phase_templates = {
            "SIMPLE": (
                "- [ ] Phase 1: Implement the fix/change\n"
                "- [ ] Phase 2: Verify it works\n"
            ),
            "MEDIUM": (
                "- [ ] Phase 1: Understand the codebase and plan\n"
                "- [ ] Phase 2: Implement the changes\n"
                "- [ ] Phase 3: Review the code\n"
                "- [ ] Phase 4: Test and verify\n"
            ),
            "LARGE": (
                "- [ ] Phase 1: Architecture and planning\n"
                "- [ ] Phase 2: Core implementation\n"
                "- [ ] Phase 3: Feature implementation\n"
                "- [ ] Phase 4: Integration\n"
                "- [ ] Phase 5: Review and testing\n"
                "- [ ] Phase 6: Polish and deployment\n"
            ),
            "EPIC": (
                "- [ ] Phase 1: Architecture + read existing code + plan file structure (rounds 1-3)\n"
                "- [ ] Phase 2: Core foundation — models, DB, config (rounds 4-8)\n"
                "- [ ] Phase 3: Feature implementation — one feature at a time (rounds 9-13)\n"
                "- [ ] Phase 4: Integration — connect all pieces, error handling (rounds 14-17)\n"
                "- [ ] Phase 5: Testing — comprehensive tests, fix failures (rounds 18-22)\n"
                "- [ ] Phase 6: Polish — error handling, docs, deployment config (rounds 23+)\n"
            ),
        }

        phases = phase_templates.get(complexity, phase_templates["MEDIUM"])
        content = (
            f"# Task Ledger\n\n"
            f"## Goal\n{user_message[:1000]}\n\n"
            f"## Complexity\n{complexity}\n\n"
            f"## Phases\n{phases}\n"
            f"## Current Phase\nPhase 1\n\n"
            f"## Completed Work\n(none yet)\n\n"
            f"## Open Issues\n(none yet)\n\n"
            f"## Blocked Items\n(none yet)\n"
        )
        self._write_todo(content)

    def _update_todo_after_round(self, round_num: int, round_summary: str, findings: list[dict] | None = None):
        """Update the task ledger after a round completes.

        Appends the round summary to 'Completed Work' and updates
        'Open Issues' based on findings from the review prompt.
        """
        current = self._read_todo()
        if not current:
            return  # No ledger to update

        # Append to Completed Work section
        completed_marker = "## Completed Work"
        if completed_marker in current:
            idx = current.find(completed_marker) + len(completed_marker)
            # Find the next section
            next_section = current.find("\n## ", idx)
            before = current[:idx]
            existing_work = current[idx:next_section].strip() if next_section > idx else current[idx:].strip()
            after = current[next_section:] if next_section > idx else ""

            if existing_work == "(none yet)":
                existing_work = ""
            new_entry = f"- Round {round_num}: {round_summary[:200]}"
            updated_work = f"{existing_work}\n{new_entry}".strip()
            current = f"{before}\n{updated_work}\n{after}"

        # Update Open Issues if we have findings
        if findings:
            issues_marker = "## Open Issues"
            if issues_marker in current:
                idx = current.find(issues_marker) + len(issues_marker)
                next_section = current.find("\n## ", idx)
                before = current[:idx]
                after = current[next_section:] if next_section > idx else ""

                issue_lines = []
                for f in findings[:10]:  # Cap at 10 issues
                    severity = f.get("severity", "MEDIUM")
                    desc = f.get("description", "")[:150]
                    file_hint = f" in {f['file']}" if f.get("file") else ""
                    issue_lines.append(f"- [{severity}] {desc}{file_hint}")
                issues_text = "\n".join(issue_lines) if issue_lines else "(none)"
                current = f"{before}\n{issues_text}\n{after}"

        self._write_todo(current)

    # ── Experience Ledger (.nexus/.experience.md) ──
    # Cross-session memory: stores lessons learned from past task executions.
    # Inspired by Reflexion (Shinn et al., 2023) — verbal reinforcement learning.
    # The orchestrator reflects on completed tasks and stores insights that are
    # injected into future task prompts, enabling learning without weight updates.

    def _get_experience_path(self) -> Path:
        """Get the path to the experience ledger file."""
        nexus_dir = Path(self.project_dir) / ".nexus"
        nexus_dir.mkdir(parents=True, exist_ok=True)
        return nexus_dir / ".experience.md"

    def _read_experience(self) -> str:
        """Read the experience ledger. Returns empty string if not found."""
        exp_path = self._get_experience_path()
        if exp_path.exists():
            try:
                content = exp_path.read_text(encoding="utf-8").strip()
                # Cap at 3000 chars to avoid bloating the prompt
                if len(content) > 3000:
                    # Keep the header + most recent lessons
                    lines = content.split("\n")
                    header = "\n".join(lines[:5])
                    # Find lesson entries (start with '### Lesson')
                    lesson_starts = [i for i, l in enumerate(lines) if l.startswith("### Lesson")]
                    if lesson_starts:
                        # Keep the last 5 lessons
                        keep_from = lesson_starts[-5] if len(lesson_starts) >= 5 else lesson_starts[0]
                        recent = "\n".join(lines[keep_from:])
                        content = f"{header}\n\n... (older lessons trimmed)\n\n{recent}"
                return content
            except Exception:
                pass
        return ""

    def _write_experience(self, content: str):
        """Write the experience ledger."""
        try:
            exp_path = self._get_experience_path()
            exp_path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.warning(f"[{self.project_id}] Failed to write experience ledger: {e}")

    def _append_experience(self, lesson: str):
        """Append a new lesson to the experience ledger."""
        existing = self._read_experience()
        if not existing:
            existing = (
                "# Experience Ledger\n\n"
                "This file stores lessons learned from past task executions.\n"
                "The orchestrator uses these to avoid repeating mistakes.\n"
            )
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        # Count existing lessons to number the new one
        lesson_count = existing.count("### Lesson ")
        new_entry = (
            f"\n### Lesson {lesson_count + 1} ({timestamp})\n"
            f"{lesson}\n"
        )
        self._write_experience(existing + new_entry)

    async def _generate_reflection(self, task: str, outcome: str, start_time: float) -> str | None:
        """Generate a reflection on the task execution using the LLM.

        This is the core of the Reflexion pattern: after a task completes,
        the orchestrator analyzes what happened and extracts reusable lessons.

        Returns the reflection text, or None if generation fails.
        """
        duration = time.monotonic() - start_time
        rounds_summary = "\n".join(f"  {r}" for r in self._completed_rounds[-15:]) if self._completed_rounds else "  (no rounds)"
        agents_used = sorted(self._agents_used)

        # Read the current todo to see what was accomplished
        todo = self._read_todo()

        reflection_prompt = (
            f"You are reflecting on a completed task to extract lessons for future tasks.\n\n"
            f"TASK: {task[:500]}\n"
            f"OUTCOME: {outcome}\n"
            f"DURATION: {int(duration)}s ({len(self._completed_rounds)} rounds)\n"
            f"COST: ${self.total_cost_usd:.4f}\n"
            f"AGENTS USED: {', '.join(agents_used)}\n\n"
            f"ROUND HISTORY:\n{rounds_summary}\n\n"
        )
        if todo:
            reflection_prompt += f"TASK LEDGER:\n{todo[:1000]}\n\n"

        reflection_prompt += (
            "Based on this execution, extract 2-4 CONCRETE lessons. Focus on:\n"
            "1. What strategy worked well? (e.g., 'sequential developer→tester pipeline was effective')\n"
            "2. What went wrong? (e.g., 'developer kept failing on X because Y')\n"
            "3. What should be done differently next time? (e.g., 'always run lint before tests')\n"
            "4. Any project-specific knowledge? (e.g., 'this project uses pnpm not npm')\n\n"
            "Format each lesson as a single line starting with '- '.\n"
            "Be specific and actionable. Do NOT be vague.\n"
            "Output ONLY the lessons, nothing else."
        )

        try:
            # Use a lightweight query — this is a background task, not user-facing
            response = await self._query_agent("orchestrator", reflection_prompt)
            if response and not response.is_error and response.text.strip():
                return response.text.strip()
        except Exception as e:
            logger.warning(f"[{self.project_id}] Reflection generation failed: {e}")
        return None

    async def _store_lessons(self, task: str, reflection: str, outcome: str):
        """Store lessons from a reflection in both the file system and the database.

        Dual storage ensures:
        - File system (.experience.md): always available to the orchestrator via read tools
        - Database (lessons table): searchable, queryable, cross-project
        """
        # 1. Append to the experience ledger file
        self._append_experience(reflection)

        # 2. Parse individual lessons and store in DB
        lessons = []
        for line in reflection.split("\n"):
            line = line.strip()
            if line.startswith("- ") and len(line) > 10:
                lessons.append(line[2:].strip())

        if not lessons:
            # If no bullet points, store the whole reflection as one lesson
            lessons = [reflection[:500]]

        # Extract tags from the task description for future retrieval
        task_lower = task.lower()
        tags = []
        tag_keywords = [
            "react", "python", "typescript", "javascript", "node", "fastapi",
            "django", "flask", "next", "vue", "angular", "docker", "postgres",
            "sqlite", "redis", "api", "auth", "test", "deploy", "css", "html",
            "database", "websocket", "graphql", "rest", "frontend", "backend",
        ]
        for kw in tag_keywords:
            if kw in task_lower:
                tags.append(kw)

        # Determine lesson type based on content
        for lesson_text in lessons:
            lesson_lower = lesson_text.lower()
            if any(w in lesson_lower for w in ["error", "fail", "crash", "bug", "wrong"]):
                lesson_type = "error_pattern"
            elif any(w in lesson_lower for w in ["strategy", "pipeline", "approach", "pattern"]):
                lesson_type = "strategy"
            elif any(w in lesson_lower for w in ["tool", "command", "npm", "pip", "git"]):
                lesson_type = "tool_usage"
            else:
                lesson_type = "general"

            try:
                await self.session_mgr.add_lesson(
                    project_id=self.project_id,
                    user_id=self.user_id,
                    task_description=task[:500],
                    lesson=lesson_text[:500],
                    lesson_type=lesson_type,
                    tags=",".join(tags[:10]),
                    outcome=outcome,
                    rounds_used=len(self._completed_rounds),
                    cost_usd=self.total_cost_usd,
                )
            except Exception as e:
                logger.warning(f"[{self.project_id}] Failed to store lesson in DB: {e}")

        logger.info(
            f"[{self.project_id}] Stored {len(lessons)} lessons "
            f"(outcome={outcome}, tags={tags})"
        )

    async def _inject_experience_context(self, task: str) -> str:
        """Build an experience context block to inject into the orchestrator's initial prompt.

        Retrieves relevant lessons from both the project-specific experience ledger
        and the cross-project lessons database.
        """
        sections = []

        # 1. Project-specific experience (from .experience.md)
        experience = self._read_experience()
        if experience:
            sections.append(
                "📚 PROJECT EXPERIENCE (lessons from previous tasks in this project):\n"
                f"{experience[:1500]}"
            )

        # 2. Cross-project lessons from DB (keyword search based on current task)
        try:
            # Extract keywords from the task for searching
            task_words = re.sub(r"[^a-zA-Z0-9 ]", " ", task.lower()).split()
            # Filter to meaningful words (>3 chars, not stop words)
            stop_words = {"the", "and", "for", "that", "this", "with", "from", "have", "will", "should", "would", "could"}
            keywords = [w for w in task_words if len(w) > 3 and w not in stop_words][:8]

            if keywords:
                db_lessons = await self.session_mgr.search_lessons(
                    user_id=self.user_id,
                    keywords=keywords,
                    limit=5,
                )
                if db_lessons:
                    lesson_lines = []
                    for l in db_lessons:
                        project_name = l.get("project_name", "unknown")
                        lesson_text = l.get("lesson", "")
                        outcome = l.get("outcome", "")
                        icon = "✅" if outcome == "success" else "⚠️" if outcome == "partial" else "❌"
                        lesson_lines.append(f"  {icon} [{project_name}] {lesson_text[:200]}")
                    if lesson_lines:
                        sections.append(
                            "📚 CROSS-PROJECT LESSONS (relevant experience from other projects):\n"
                            + "\n".join(lesson_lines)
                        )
        except Exception as e:
            logger.debug(f"[{self.project_id}] Failed to search lessons DB: {e}")

        if not sections:
            return ""

        return (
            "\n\n═══ EXPERIENCE MEMORY ═══\n"
            "These are lessons learned from previous tasks. Use them to avoid repeating mistakes.\n\n"
            + "\n\n".join(sections)
            + "\n═══════════════════════\n"
        )

    # _detect_stuck is defined earlier in the class (line ~245)
