from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
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
)
from sdk_client import ClaudeSDKManager, SDKResponse
from session_manager import SessionManager
from skills_registry import get_skill_content, get_skills_for_agent, build_skill_prompt, scan_skills

logger = logging.getLogger(__name__)

# Agent emoji map for clear visual identification
AGENT_EMOJI = {
    "orchestrator": "🎯",
    "developer": "💻",
    "reviewer": "🔍",
    "tester": "🧪",
    "devops": "⚙️",
    "user": "👤",
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
        if ch == '"' and not escape:
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
            names.extend(SUB_AGENT_PROMPTS.keys())
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
        """Send the final clean message (deletes all intermediates, stays forever)."""
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

    def _detect_stuck(self) -> bool:
        """Detect if the orchestrator is repeating itself (stuck in a loop).

        Checks two signals:
        1. Orchestrator text similarity: last N responses are nearly identical
        2. Error-repeat: same agent failing with the same error 3+ consecutive times
        """
        # --- Signal 1: orchestrator response similarity ---
        recent = [
            m.content for m in self.conversation_log[-STUCK_WINDOW_SIZE * 2:]
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
                return True

        # --- Signal 2: repeated identical failures in shared context ---
        # If the last 3+ context entries are all FAILUREs with nearly the same message,
        # the team is stuck retrying the same broken approach.
        if len(self.shared_context) >= 3:
            recent_ctx = self.shared_context[-6:]
            error_signatures: list[str] = []
            for ctx in recent_ctx:
                for line in ctx.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("Status: FAILED") or stripped.startswith("BLOCKED"):
                        # Use first 80 chars of error as signature
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
                        f"same failure appearing {len(error_signatures)} times in a row"
                    )
                    return True

        return False

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
        min_rounds = {"SIMPLE": 1, "MEDIUM": 2, "LARGE": 4, "EPIC": 8}
        required = min_rounds.get(complexity, 2)

        if loop_count < required:
            return (
                f"Task complexity is **{complexity}** but only {loop_count} round(s) completed "
                f"(minimum {required} required). Continue working through the remaining phases."
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

        # For LARGE and EPIC: require both reviewer and tester to have run.
        # Check FULL conversation log (not just trimmed shared_context) so agents that ran
        # 30+ rounds ago are still detected correctly.
        if complexity in ("LARGE", "EPIC"):
            # Search full conversation log for agent names
            all_agent_names = {m.agent_name for m in self.conversation_log}
            tester_ran = "tester" in all_agent_names
            reviewer_ran = "reviewer" in all_agent_names

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

    def _build_final_summary(self, user_message: str, start_time: float, status: str = "Done") -> str:
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
        agents_str = " → ".join(agents_used) if agents_used else "orchestrator"

        file_changes = self._detect_file_changes()
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
        """Start processing a user message."""
        if self.is_running:
            logger.warning(f"[{self.project_id}] start_session called but already running")
            await self._notify("Session is already running.")
            return

        logger.info(f"[{self.project_id}] Starting session: multi_agent={self.multi_agent}, message={user_message[:80]}")
        self.is_running = True
        self._stop_event.clear()
        self._pause_event.set()
        self.turn_count = 0

        # Emit running status immediately so frontend updates
        await self._emit_event("project_status", status="running")

        # Invalidate the orchestrator session so the new system prompt takes
        # full effect. Sub-agent sessions are preserved so they accumulate
        # context across delegation rounds.
        await self.session_mgr.invalidate_session(self.user_id, self.project_id, "orchestrator")

        self._task = asyncio.create_task(
            self._run_orchestrator(user_message)
        )
        # Log uncaught errors from the task so they don't vanish silently
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
            # Not running — send directly to orchestrator
            await self._notify(f"📨 Sending to *orchestrator*...")
            response = await self._query_agent("orchestrator", message)
            self._record_response("orchestrator", "Orchestrator", response)

            summary = response.text[:3000]
            if len(response.text) > 3000:
                summary += "\n... (truncated)"
            cost_str = f" | ${response.cost_usd:.4f}" if response.cost_usd > 0 else ""
            await self._send_final(f"💬 *orchestrator*{cost_str}\n\n{summary}")
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

    # --- Core orchestration loop ---

    async def _run_orchestrator(self, user_message: str):
        """Main orchestrator loop."""
        start_time = time.monotonic()

        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=user_message)
        )
        await self.session_mgr.add_message(
            self.project_id, "user", "User", user_message
        )

        # Build initial prompt with conversation history for context
        workspace = self._get_workspace_context()

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
        existing_manifest = self._read_project_manifest()
        if existing_manifest:
            complexity = "LARGE"  # At minimum — manifest means prior work exists
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

        task_history_id = None  # Guard: prevents NameError in except blocks
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
                        self._build_final_summary(
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
                            "🔑 *API Key Error*\n\n"
                            "The Claude agent can't authenticate.\n"
                            "Make sure `ANTHROPIC_API_KEY` is set in your `.env` file.\n\n"
                            "Get your key at: https://console.anthropic.com/"
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
                        await self._send_result(
                            f"⚠️ Orchestrator error: {response.error_message}\n\n"
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
                        # Inject a rejection so the orchestrator keeps working
                        current_changes = self._detect_file_changes()
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
                    await self._send_final(
                        self._build_final_summary(user_message, start_time)
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

                # Emit delegation events
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
                        current_changes = self._detect_file_changes()
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
                            self._build_final_summary(user_message, start_time)
                        )
                        break

                if not self.multi_agent:
                    # Single-agent mode — ignore delegations
                    await self._send_final(
                        self._build_final_summary(user_message, start_time)
                    )
                    break

                # Execute sub-agents
                logger.info(f"[{self.project_id}] Running {len(delegations)} sub-agent tasks...")
                # Mark orchestrator as "waiting" while sub-agents work
                self.agent_states["orchestrator"] = {
                    "state": "idle",
                    "task": f"waiting for {len(delegations)} sub-agent(s)",
                }
                sub_results = await self._run_sub_agents(delegations)
                logger.info(
                    f"[{self.project_id}] Sub-agents finished: "
                    f"{', '.join(f'{k}({len(v)} tasks)' for k, v in sub_results.items())}"
                )

                # Check stuck detection
                if self._detect_stuck():
                    await self._notify(
                        f"🔁 Agents appear stuck in a loop.\n"
                        f"Use /talk orchestrator <message> to intervene, or /stop to end."
                    )
                    await self._self_pause("stuck detection")
                    continue

                # Track what was done this round
                _round_summary = ", ".join(
                    f"{role}({'OK' if all(not r.is_error for r in resps) else 'ERR'})"
                    for role, resps in sub_results.items()
                )
                self._completed_rounds.append(f"Round {loop_count}: {_round_summary}")

                # Feed results back to orchestrator with round history
                orchestrator_input = self._build_review_prompt(sub_results, self._completed_rounds)

        except asyncio.CancelledError:
            logger.info(f"Orchestrator loop cancelled for {self.project_name}")
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
                await self._send_final(
                    self._build_final_summary(user_message, start_time, status="Stopped (loop limit)")
                )
        finally:
            if not self.is_paused:
                self.is_running = False
            # Always emit project_status so frontend knows the state changed
            await self._emit_event("project_status", status="paused" if self.is_paused else "idle")
            # Reset all agent states to idle
            for agent_name in list(self.agent_states.keys()):
                self.agent_states[agent_name] = {
                    **self.agent_states.get(agent_name, {}),
                    "state": "idle",
                    "current_tool": None,
                }
            # NOTE: _on_task_done callback handles auto-restart if queue has pending messages

    async def _run_sub_agents(self, delegations: list[Delegation]) -> dict[str, list[SDKResponse]]:
        """Execute sub-agent tasks, running different agent roles in parallel.

        Agents with different roles run concurrently via asyncio.gather().
        If the orchestrator delegates multiple tasks to the same role,
        those run sequentially (they share a session).

        Failed agents are automatically retried once with extra context.
        Exceptions from parallel execution are caught and reported properly.
        """
        # Group delegations by agent role
        by_role: dict[str, list[Delegation]] = {}
        for d in delegations:
            if d.agent not in SUB_AGENT_PROMPTS:
                logger.warning(f"Unknown sub-agent role: {d.agent}, skipping")
                continue
            by_role.setdefault(d.agent, []).append(d)

        results: dict[str, list[SDKResponse]] = {}
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

                workspace = self._get_workspace_context()
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

                response = await self._query_agent(agent_role, sub_prompt, skill_names=delegation.skills)

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
                    workspace_now = self._get_workspace_context()
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
                    self._accumulate_context(agent_role, delegation.task, response)
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
                    changed = self._detect_file_changes()
                    if changed:
                        summary += f"\n\nFiles changed:\n{changed}"

                status_icon = "✅" if not response.is_error else "⚠️"
                emoji = AGENT_EMOJI.get(agent_role, "🔧")
                dur_str = f" ({response.duration_ms // 1000}s)" if response.duration_ms > 0 else ""
                await self._send_result(
                    f"{status_icon}{emoji} *{agent_role}* finished{dur_str}\n"
                    f"💰 ${response.cost_usd:.4f} | Turns: {response.num_turns}\n\n"
                    f"{summary}"
                )

        # Run different roles in parallel, with proper exception handling
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

        role_tasks: dict[str, asyncio.Task] = {}
        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            if len(by_role) > 1:
                # Run each role as a fully independent asyncio.Task.
                # We use asyncio.wait() instead of asyncio.gather() because
                # gather connects tasks — if the claude_agent_sdk's anyio
                # cancel-scope cleanup leaks into a sibling task, gather
                # propagates the cancellation to ALL tasks, killing them all.
                # asyncio.wait() treats each task independently: if one crashes
                # or gets cancelled, the others continue running unaffected.
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
                                f"cancel scope bug (suppressed): {e}"
                            )
                            # Don't crash — the agent may have produced results
                        else:
                            raise

                # Create independent tasks (not linked via gather)
                for role, dels in by_role.items():
                    task = asyncio.create_task(
                        _isolated_run_role(role, dels),
                        name=f"agent-{role}",
                    )
                    role_tasks[role] = task

                # Wait for all tasks to complete — with timeout to prevent infinite hangs
                if role_tasks:
                    _wait_timeout = AGENT_TIMEOUT_SECONDS + 60  # agent timeout + buffer
                    done, still_pending = await asyncio.wait(
                        role_tasks.values(),
                        return_when=asyncio.ALL_COMPLETED,
                        timeout=_wait_timeout,
                    )
                    if still_pending:
                        logger.warning(
                            f"[{self.project_id}] {len(still_pending)} agent task(s) timed out "
                            f"after {_wait_timeout}s — cancelling"
                        )
                        for t in still_pending:
                            t.cancel()
                        await asyncio.wait(still_pending, timeout=5.0)
                    # Check for unexpected exceptions
                    for role_name, task in role_tasks.items():
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
            elif by_role:
                # Single role — run directly (no overhead)
                role, dels = next(iter(by_role.items()))
                await run_role(role, dels)
        except asyncio.CancelledError:
            # Orchestrator was stopped — cancel any running agent tasks
            if role_tasks:
                for task in role_tasks.values():
                    if not task.done():
                        task.cancel()
                # Wait briefly for tasks to finish cancellation
                await asyncio.wait(role_tasks.values(), timeout=5.0)
            raise
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
                # Inject conflict info into results so orchestrator can resolve
                results.setdefault("_conflicts", []).append(SDKResponse(
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

    def _accumulate_context(self, agent_role: str, task: str, response: SDKResponse):
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
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.project_dir,
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                files_changed_git = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
            # Also check untracked (new) files
            result2 = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=self.project_dir,
                capture_output=True, text=True, timeout=5,
            )
            if result2.stdout.strip():
                files_changed_git.extend([f"(new) {f.strip()}" for f in result2.stdout.strip().split('\n') if f.strip()])
            # Get a short diff snippet for context — helps agents know WHAT changed
            if files_changed_git:
                result3 = subprocess.run(
                    ["git", "diff", "--stat", "HEAD"],
                    cwd=self.project_dir,
                    capture_output=True, text=True, timeout=5,
                )
                if result3.stdout.strip():
                    git_diff_snippet = result3.stdout.strip()[:300]
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

        # Keep shared_context from growing too large (max 20 entries)
        if len(self.shared_context) > 20:
            self.shared_context = self.shared_context[-15:]

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
            # Build orchestrator prompt with available skills info
            system_prompt = ORCHESTRATOR_SYSTEM_PROMPT
            available_skills = self._get_available_skills_summary()
            if available_skills:
                system_prompt += f"\n\n{available_skills}"
            max_turns = 1
            max_budget = 5.0  # Orchestrator processes large context across many rounds — needs headroom
            permission_mode = "bypassPermissions"
            tools = []  # Disable ALL tools — text-only coordinator
            logger.info(f"[{self.project_id}] Querying orchestrator (coordinator mode, no tools, max_turns=1)")
        elif agent_role == "orchestrator" and not self.multi_agent:
            system_prompt = SOLO_AGENT_PROMPT
            max_turns = SDK_MAX_TURNS_PER_QUERY
            max_budget = SDK_MAX_BUDGET_PER_QUERY
            permission_mode = "bypassPermissions"
            logger.info(f"[{self.project_id}] Querying orchestrator (solo mode, full tools)")
        else:
            system_prompt = SUB_AGENT_PROMPTS.get(agent_role, "You are a helpful coding assistant.")
            # Append skill content if requested or auto-mapped
            all_skills = list(skill_names or []) + get_skills_for_agent(agent_role)
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
        self.conversation_log.append(
            Message(
                agent_name=agent_name,
                role=role,
                content=response.text,
                cost_usd=response.cost_usd,
            )
        )
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

    def _build_review_prompt(self, sub_results: dict[str, list[SDKResponse]], completed_rounds: list[str] | None = None) -> str:
        """Build a structured prompt for the orchestrator to review sub-agent results.

        Includes manifest state, agent results, workspace changes, round history,
        budget/round estimate, and a concrete recommended next action.
        """
        parts = ["═══ SUB-AGENT RESULTS ═══\n"]

        # Always inject manifest at the top — this is the team's ground truth
        manifest = self._read_project_manifest()
        if manifest:
            parts.append(f"─── 📋 PROJECT MANIFEST (.nexus/PROJECT_MANIFEST.md) ───\n{manifest}\n")

        # Budget + rounds estimate so orchestrator can prioritize ruthlessly
        budget_used = self.total_cost_usd
        budget_cap = self._effective_budget
        budget_left = max(0.0, budget_cap - budget_used)
        loops_done = self._current_loop
        loops_max = MAX_ORCHESTRATOR_LOOPS
        loops_left = max(0, loops_max - loops_done)
        # Burn rate: avg cost per round so far → estimate rounds remaining within budget
        burn_rate = budget_used / max(loops_done, 1)
        budget_rounds_left = int(budget_left / burn_rate) if burn_rate > 0 else loops_left
        effective_rounds_left = min(loops_left, budget_rounds_left)
        parts.append(
            f"─── 📊 SESSION PROGRESS ───\n"
            f"Rounds: {loops_done}/{loops_max} ({loops_left} remaining) | "
            f"Budget: ${budget_used:.2f}/${budget_cap:.0f} (${budget_left:.2f} left)\n"
            f"Burn rate: ${burn_rate:.2f}/round → ~{effective_rounds_left} effective rounds left\n"
            f"{'⚠️ BUDGET RUNNING LOW — prioritize critical work only!' if effective_rounds_left < 5 else ''}\n"
        )
        has_errors = False
        total_sub_cost = 0.0
        total_sub_turns = 0
        successful_agents = []
        failed_agents = []

        for agent, responses in sub_results.items():
            for idx, response in enumerate(responses):
                status = "SUCCESS" if not response.is_error else "ERROR"
                if response.is_error:
                    has_errors = True
                    failed_agents.append(agent)
                else:
                    successful_agents.append(agent)
                total_sub_cost += response.cost_usd
                total_sub_turns += response.num_turns

                # Include a reasonable chunk of the response
                content = response.text[:4000]
                if len(response.text) > 4000:
                    content += "\n... (truncated)"

                # Provide explicit fallback so the orchestrator knows work was attempted
                if not content.strip():
                    if response.is_error:
                        content = (
                            f"[Agent FAILED with error: {response.error_message}. "
                            f"Consider retrying the task or using a different approach.]"
                        )
                    else:
                        content = (
                            "[Agent produced no text output — it may have completed the task "
                            "using tools (file writes, shell commands). Check the workspace files "
                            "to verify what was done before deciding if more work is needed.]"
                        )

                label = f"{agent}" if len(responses) == 1 else f"{agent} (task {idx + 1}/{len(responses)})"
                cost_str = f"${response.cost_usd:.4f}" if response.cost_usd > 0 else "—"
                parts.append(
                    f"─── {label} [{status}] (cost: {cost_str}, turns: {response.num_turns}) ───\n"
                    f"{content}\n"
                )

        # ALWAYS show current file changes — most reliable way to see what happened
        file_changes = self._detect_file_changes()
        if file_changes and "(no file" not in file_changes:
            parts.append(f"─── WORKSPACE CHANGES (git diff --stat) ───\n{file_changes}\n")

        # Extract and surface NEEDS_FOLLOWUP / BLOCKED statuses prominently
        followup_items = []
        blocked_items = []
        for agent, responses in sub_results.items():
            for response in responses:
                text = response.text
                for sm in ["## STATUS", "## Status"]:
                    idx = text.find(sm)
                    if idx >= 0:
                        for line in text[idx:idx + 200].strip().split("\n")[1:]:
                            if line.strip():
                                sl = line.strip()
                                if sl.startswith("NEEDS_FOLLOWUP"):
                                    followup_items.append(f"  {agent}: {sl}")
                                elif sl.startswith("BLOCKED"):
                                    blocked_items.append(f"  {agent}: {sl}")
                                break
                        break

        if blocked_items:
            parts.append("─── 🚫 BLOCKED AGENTS (address these first) ───")
            parts.extend(blocked_items)
            parts.append("")
        if followup_items:
            parts.append("─── 📋 NEEDS FOLLOWUP (must act on these) ───")
            parts.extend(followup_items)
            parts.append("")

        # Cost summary
        parts.append(
            f"─── SESSION TOTALS ───\n"
            f"This round: ${total_sub_cost:.4f} ({total_sub_turns} turns) | "
            f"Overall: ${self.total_cost_usd:.4f} ({self.turn_count} turns)\n"
            f"Successful: {', '.join(successful_agents) or 'none'} | "
            f"Failed: {', '.join(failed_agents) or 'none'}\n"
        )

        # Show round history so orchestrator knows what's been done
        if completed_rounds:
            parts.append(f"─── ROUNDS COMPLETED ({len(completed_rounds)}) ───")
            for r in completed_rounds:
                parts.append(f"  {r}")
            parts.append("")

        # Include accumulated shared context so orchestrator sees the full picture
        if self.shared_context:
            parts.append("─── ACCUMULATED CONTEXT (all rounds) ───")
            for ctx in self.shared_context[-8:]:
                parts.append(ctx)
            parts.append("")

        if has_errors:
            parts.append(
                "\n⚠️ SOME AGENTS FAILED. You MUST:\n"
                "1. Analyze each error carefully — read the exact error message\n"
                "2. Retry failed tasks: put the error in 'context', try a different approach\n"
                "3. If the same approach fails twice, try a completely different strategy\n"
                "4. If a different agent is better suited, delegate to them instead\n"
                "5. Do NOT say TASK_COMPLETE until all critical work is done\n"
                "\nIMPORTANT: Failure is normal — diagnose, adapt, retry. Never give up."
            )
        else:
            parts.append(
                "\n✅ ALL AGENTS COMPLETED. Now reason through these questions:\n"
                "1. Was the task FULLY implemented? Check FILES CHANGED above.\n"
                "2. Has the code been REVIEWED? If not, delegate reviewer now.\n"
                "3. Have TESTS been run and passed? If not, delegate tester now.\n"
                "4. Are there any ISSUES FOUND sections that need fixing?\n"
                "5. Only if all 4 are yes → respond with TASK_COMPLETE\n"
                "\nIf any answer is no → delegate the missing work before completing."
            )

        # --- Synthesize a concrete RECOMMENDED NEXT ACTION ---
        rna_parts: list[str] = []
        if blocked_items:
            rna_parts.append("ADDRESS BLOCKED AGENTS — provide missing context/tools so they can proceed.")
        elif failed_agents:
            rna_parts.append(
                f"RETRY FAILED AGENTS ({', '.join(set(failed_agents))}) — "
                "use a different approach, include the exact error in 'context'."
            )
        elif followup_items:
            rna_parts.append("ACT ON NEEDS_FOLLOWUP items listed above.")
        elif file_changes and "(no file" not in file_changes:
            # Work was done — check if review/tests are needed
            roles_done = set(successful_agents)
            if "reviewer" not in roles_done and "tester" not in roles_done:
                rna_parts.append(
                    "CODE WAS CHANGED but not reviewed or tested → "
                    "delegate reviewer + tester in parallel before TASK_COMPLETE."
                )
            elif "reviewer" not in roles_done:
                rna_parts.append("Code changed but NOT reviewed → delegate reviewer.")
            elif "tester" not in roles_done:
                rna_parts.append("Code changed but tests NOT run → delegate tester.")
            else:
                rna_parts.append("All checks done → evaluate if TASK_COMPLETE is appropriate.")
        else:
            rna_parts.append(
                "No file changes detected — verify agents actually completed their work. "
                "If task is done, say TASK_COMPLETE. If not, delegate with more specific instructions."
            )

        if rna_parts:
            parts.append(f"\n─── 🎯 RECOMMENDED NEXT ACTION ───\n" + "\n".join(rna_parts))

        return "\n".join(parts)

    # --- Stuck detection ---

    def _detect_file_changes(self) -> str:
        """Run git status in the project dir to show what files the agent changed."""
        try:
            result = subprocess.run(
                ["git", "diff", "--stat", "HEAD"],
                cwd=self.project_dir,
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                return result.stdout.strip()
            # Also check untracked files
            result2 = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.project_dir,
                capture_output=True, text=True, timeout=5,
            )
            return result2.stdout.strip() or "(no file changes detected)"
        except Exception:
            return "(unable to detect changes)"

    # _detect_stuck is defined earlier in the class (line ~245)
