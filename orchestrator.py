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

    async def _notify(self, text: str):
        """Send a progress/status update (edited in-place in Telegram)."""
        if self.on_update:
            try:
                await self.on_update(text)
            except Exception as e:
                logger.error(f"Update callback error: {e}")

    async def _send_result(self, text: str):
        """Send a final result message (new Telegram message, not edited)."""
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
                event = {"type": event_type, **data}
                await self.on_event(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

    def _detect_stuck(self) -> bool:
        """Detect if the orchestrator is repeating itself (stuck in a loop).

        Compares the last STUCK_WINDOW_SIZE orchestrator responses using
        SequenceMatcher. If all pairwise similarities exceed
        STUCK_SIMILARITY_THRESHOLD, the agents are likely stuck.
        """
        # Collect recent orchestrator responses
        recent = [
            m.content for m in self.conversation_log[-STUCK_WINDOW_SIZE * 2:]
            if m.agent_name == "orchestrator" and m.content
        ][-STUCK_WINDOW_SIZE:]

        if len(recent) < STUCK_WINDOW_SIZE:
            return False  # Not enough data to detect a loop

        # Check pairwise similarity between consecutive responses
        for i in range(len(recent) - 1):
            ratio = SequenceMatcher(None, recent[i], recent[i + 1]).ratio()
            if ratio < STUCK_SIMILARITY_THRESHOLD:
                return False  # Found a sufficiently different response

        logger.warning(
            f"[{self.project_id}] Stuck detection triggered: "
            f"last {len(recent)} orchestrator responses are >{STUCK_SIMILARITY_THRESHOLD:.0%} similar"
        )
        return True

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

        return (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ {self.project_name} — {status}\n\n"
            f"📋 Task: {task_preview}\n"
            f"🤖 Agents: {agents_str}\n"
            f"⏱ {duration_str} | 📊 {self.turn_count} turns | 💰 ${self.total_cost_usd:.2f}"
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
        prompt += f"User request:\n{user_message}"

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

            while self.is_running and loop_count < max_loops:
                if self._stop_event.is_set():
                    break

                await self._pause_event.wait()

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
                response = await self._query_agent("orchestrator", orchestrator_input)
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

                # Check completion
                if "TASK_COMPLETE" in response.text:
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
                        orchestrator_input = (
                            "You didn't delegate any work. You MUST use <delegate> blocks with "
                            "valid JSON. Example:\n\n"
                            "<delegate>\n"
                            '{"agent": "developer", "task": "your task here", "context": "relevant context"}\n'
                            "</delegate>\n\n"
                            "If the task is fully complete and verified, respond with TASK_COMPLETE. "
                            "Otherwise, delegate the remaining work using <delegate> blocks.\n\n"
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

                # Feed results back to orchestrator
                orchestrator_input = self._build_review_prompt(sub_results)

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

                # Check limits (under lock since turn_count is shared)
                async with lock:
                    if self.turn_count >= MAX_TURNS_PER_CYCLE:
                        await self._notify(
                            f"⏰ Turn limit reached ({MAX_TURNS_PER_CYCLE}) — "
                            f"skipping remaining sub-agents.\n"
                            f"Use /resume to continue."
                        )
                        return
                    if self.total_cost_usd >= self._effective_budget:
                        await self._notify(
                            f"💰 Budget limit reached (${self.total_cost_usd:.4f} / ${self._effective_budget:.2f}) — "
                            f"skipping remaining sub-agents.\n"
                            f"Use /resume to continue."
                        )
                        return
                    self.turn_count += 1

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
                sub_prompt = f"Task: {delegation.task}"
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
                    # Invalidate stale session and retry with error context
                    await self.session_mgr.invalidate_session(
                        self.user_id, self.project_id, agent_role
                    )
                    retry_prompt = (
                        f"[RETRY — previous attempt failed with: {error_msg}]\n\n"
                        f"{sub_prompt}\n\n"
                        f"Please try a different approach if the previous one didn't work."
                    )
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
                for name, info in self.agent_states.items():
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

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            if len(by_role) > 1:
                gather_results = await asyncio.gather(
                    *(run_role(role, dels) for role, dels in by_role.items()),
                    return_exceptions=True,
                )
                # Log any unexpected exceptions from parallel execution
                for role_name, result in zip(by_role.keys(), gather_results):
                    if isinstance(result, Exception):
                        logger.error(
                            f"[{self.project_id}] Agent role '{role_name}' raised exception: {result}",
                            exc_info=result,
                        )
                        await self._send_result(
                            f"⚠️ *{role_name}* crashed unexpectedly: {result}\n"
                            f"The orchestrator will be notified to handle this."
                        )
                        # Create a synthetic error response so the orchestrator knows
                        async with lock:
                            results.setdefault(role_name, []).append(SDKResponse(
                                text=f"Agent crashed with exception: {result}",
                                is_error=True,
                                error_message=str(result),
                            ))
            elif by_role:
                # Single role — run directly (no overhead)
                role, dels = next(iter(by_role.items()))
                await run_role(role, dels)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
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
        """
        text = response.text

        # Detect file operations
        files_created = []
        files_modified = []
        files_read = []
        commands_run = []
        for line in text.split('\n'):
            lower = line.lower().strip()
            if any(w in lower for w in ('created file', 'wrote to', 'writing:', '✏️ writing', 'created:')):
                for token in line.split():
                    if '/' in token or ('.' in token and len(token) > 3):
                        cleaned = token.strip('`"\',;:()[]')
                        if cleaned:
                            files_created.append(cleaned)
            elif any(w in lower for w in ('edited', 'modified', 'updated', '🔧 editing')):
                for token in line.split():
                    if '/' in token or ('.' in token and len(token) > 3):
                        cleaned = token.strip('`"\',;:()[]')
                        if cleaned:
                            files_modified.append(cleaned)
            elif any(w in lower for w in ('reading:', '📄 reading', 'read file')):
                for token in line.split():
                    if '/' in token or ('.' in token and len(token) > 3):
                        cleaned = token.strip('`"\',;:()[]')
                        if cleaned:
                            files_read.append(cleaned)
            elif any(w in lower for w in ('running:', '💻 running', 'executed:')):
                cmd = line.strip()[:80]
                if cmd:
                    commands_run.append(cmd)

        # Build structured context entry
        ctx_parts = [f"[{agent_role}] Task: {task[:150]}"]
        if response.is_error:
            ctx_parts.append(f"  Status: FAILED — {response.error_message[:150]}")
        else:
            ctx_parts.append(f"  Status: SUCCESS ({response.num_turns} turns, ${response.cost_usd:.4f})")

        if files_created:
            ctx_parts.append(f"  Created: {', '.join(files_created[:8])}")
        if files_modified:
            ctx_parts.append(f"  Modified: {', '.join(files_modified[:8])}")
        if files_read:
            ctx_parts.append(f"  Read: {', '.join(files_read[:8])}")
        if commands_run:
            ctx_parts.append(f"  Commands: {'; '.join(commands_run[:3])}")

        # Include a more detailed summary of the output
        summary = text[:500].strip()
        if summary:
            ctx_parts.append(f"  Output: {summary}")

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

        for ctx in self.shared_context:
            if "FAILED" in ctx or "ERROR" in ctx:
                error_entries.append(ctx)
            elif f"[{agent_role}]" in ctx:
                # Same agent's previous work — always relevant
                priority_entries.append(ctx)
            else:
                recent_entries.append(ctx)

        # Build context: errors first, then own history, then recent others
        selected = []
        selected.extend(error_entries[-3:])
        selected.extend(priority_entries[-3:])
        remaining_slots = max(0, 8 - len(selected))
        selected.extend(recent_entries[-remaining_slots:])

        if not selected:
            return ""

        # Compress each entry to essential info only
        compressed = []
        for entry in selected:
            lines = entry.split('\n')
            # Keep first 3 lines (role/status/files) and skip long output
            essential = []
            for line in lines[:4]:
                if line.strip().startswith('Output:'):
                    # Truncate output to 100 chars
                    essential.append(line[:110])
                else:
                    essential.append(line)
            compressed.append('\n'.join(essential))

        return "Context from previous rounds:\n" + "\n---\n".join(compressed)

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
            max_budget = 0.5
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

    def _build_review_prompt(self, sub_results: dict[str, list[SDKResponse]]) -> str:
        """Build a structured prompt for the orchestrator to review sub-agent results.

        Includes clear status, cost, shared context, and actionable guidance.
        """
        parts = ["═══ SUB-AGENT RESULTS ═══\n"]
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

        # File changes summary
        file_changes = self._detect_file_changes()
        if file_changes and "(no file" not in file_changes:
            parts.append(f"─── WORKSPACE CHANGES ───\n{file_changes}\n")

        # Cost summary
        parts.append(
            f"─── SESSION TOTALS ───\n"
            f"This round: ${total_sub_cost:.4f} ({total_sub_turns} turns) | "
            f"Overall: ${self.total_cost_usd:.4f} ({self.turn_count} turns)\n"
            f"Successful: {', '.join(successful_agents) or 'none'} | "
            f"Failed: {', '.join(failed_agents) or 'none'}\n"
        )

        # Include accumulated shared context so orchestrator sees the full picture
        if self.shared_context:
            parts.append("─── ACCUMULATED CONTEXT (all rounds) ───")
            for ctx in self.shared_context[-8:]:
                parts.append(ctx)
            parts.append("")

        if has_errors:
            parts.append(
                "\nSome agents encountered errors. You MUST:\n"
                "1. Analyze each error carefully\n"
                "2. Retry failed tasks with the error details in 'context'\n"
                "3. If a different agent is better suited, delegate to them\n"
                "4. ONLY say TASK_COMPLETE if ALL critical work is done despite errors\n"
                "\nDo NOT give up after one failed attempt — retry with a different approach."
            )
        else:
            parts.append(
                "\nAll agents completed successfully. Now:\n"
                "- If ALL tasks are done and verified → respond with TASK_COMPLETE\n"
                "- If you need verification → delegate to reviewer or tester\n"
                "- If more work is needed → delegate with specific instructions\n"
                "- Pass relevant results from this round as 'context' to the next agent\n"
            )
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
