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
    SOLO_AGENT_PROMPT,
    STUCK_SIMILARITY_THRESHOLD,
    STUCK_WINDOW_SIZE,
    SUB_AGENT_PROMPTS,
)
from sdk_client import ClaudeSDKManager, SDKResponse
from session_manager import SessionManager

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
        self._user_injection: tuple[str, str] | None = None

    @property
    def agent_names(self) -> list[str]:
        names = ["orchestrator"]
        if self.multi_agent:
            names.extend(SUB_AGENT_PROMPTS.keys())
        return names

    @property
    def is_multi_agent(self) -> bool:
        return self.multi_agent

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

        # Invalidate ALL sessions (orchestrator + sub-agents) so the new
        # system prompt takes full effect. Without this, the resumed
        # orchestrator session carries old context/instructions and ignores
        # the current system prompt — causing it to answer directly instead
        # of delegating to sub-agents.
        await self.session_mgr.invalidate_session(self.user_id, self.project_id, "orchestrator")
        for role in SUB_AGENT_PROMPTS:
            await self.session_mgr.invalidate_session(self.user_id, self.project_id, role)

        self._task = asyncio.create_task(
            self._run_orchestrator(user_message)
        )

    async def inject_user_message(self, agent_name: str, message: str):
        """Inject a user message into the orchestrator or a sub-agent."""
        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=f"[to {agent_name}] {message}")
        )
        await self.session_mgr.add_message(
            self.project_id, "user", "User", f"[to {agent_name}] {message}"
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
            self._user_injection = (agent_name, message)
            if self.is_paused:
                self.resume()

    def pause(self):
        if self.is_running and not self.is_paused:
            self.is_paused = True
            self._pause_event.clear()
            logger.info("Session paused")

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
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._send_final(
            f"🛑 Project *{self.project_name}* stopped.\n"
            f"📊 Turns: {self.turn_count} | 💰 Cost: ${self.total_cost_usd:.4f}"
        )

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

        try:
            # Main loop: orchestrator responds, optionally delegates, then loops
            orchestrator_input = prompt
            loop_count = 0
            max_loops = MAX_ORCHESTRATOR_LOOPS  # Safety limit on orchestrator iterations

            while self.is_running and loop_count < max_loops:
                if self._stop_event.is_set():
                    break

                await self._pause_event.wait()

                # Check for user injection
                if self._user_injection:
                    target_name, injected_msg = self._user_injection
                    self._user_injection = None
                    orchestrator_input = (
                        f"[User message to {target_name}]:\n{injected_msg}"
                    )
                    await self._notify(f"📨 User message injected")

                self.turn_count += 1
                loop_count += 1

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
                    self.is_paused = True
                    self._pause_event.clear()
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
                    await self._send_final(
                        self._build_final_summary(user_message, start_time)
                    )
                    break

                # Check budget
                if self.total_cost_usd >= MAX_BUDGET_USD:
                    await self._notify(
                        f"💰 Budget limit reached (${self.total_cost_usd:.4f} / ${MAX_BUDGET_USD:.2f}).\n"
                        f"Use /resume to continue or /stop to end."
                    )
                    self.is_paused = True
                    self._pause_event.clear()
                    continue

                # Check turn limit
                if self.turn_count >= MAX_TURNS_PER_CYCLE:
                    await self._notify(
                        f"⏰ Reached max turns ({MAX_TURNS_PER_CYCLE}).\n"
                        f"Use /resume to continue or /stop to end."
                    )
                    self.is_paused = True
                    self._pause_event.clear()
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
                    f"{', '.join(f'{k}(${v.cost_usd:.3f}, err={v.is_error})' for k, v in sub_results.items())}"
                )

                # Check stuck detection
                if self._detect_stuck():
                    await self._notify(
                        f"🔁 Agents appear stuck in a loop.\n"
                        f"Use /talk orchestrator <message> to intervene, or /stop to end."
                    )
                    self.is_paused = True
                    self._pause_event.clear()
                    continue

                # Feed results back to orchestrator
                orchestrator_input = self._build_review_prompt(sub_results)

        except asyncio.CancelledError:
            logger.info(f"Orchestrator loop cancelled for {self.project_name}")
        except Exception as e:
            logger.error(f"Orchestrator loop error: {e}", exc_info=True)
            await self._notify(f"❌ Unexpected error in orchestrator: {e}")
        else:
            # Loop exited normally (not via exception).
            # If we hit the safety limit without a clean exit, send a final summary.
            if loop_count >= max_loops:
                await self._send_final(
                    self._build_final_summary(user_message, start_time, status="Stopped (loop limit)")
                )
        finally:
            if not self.is_paused:
                self.is_running = False
    async def _run_sub_agents(self, delegations: list[Delegation]) -> dict[str, SDKResponse]:
        """Execute sub-agent tasks, running different agent roles in parallel.

        Agents with different roles run concurrently via asyncio.gather().
        If the orchestrator delegates multiple tasks to the same role,
        those run sequentially (they share a session).
        """
        # Group delegations by agent role
        by_role: dict[str, list[Delegation]] = {}
        for d in delegations:
            if d.agent not in SUB_AGENT_PROMPTS:
                logger.warning(f"Unknown sub-agent role: {d.agent}, skipping")
                continue
            by_role.setdefault(d.agent, []).append(d)

        results: dict[str, SDKResponse] = {}
        lock = asyncio.Lock()  # Protect shared state updates

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
                    if self.total_cost_usd >= MAX_BUDGET_USD:
                        await self._notify(
                            f"💰 Budget limit reached (${self.total_cost_usd:.4f}) — "
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

                workspace = self._get_workspace_context()
                if workspace:
                    sub_prompt += f"\n\n{workspace}"

                response = await self._query_agent(agent_role, sub_prompt)

                async with lock:
                    self._record_response(agent_role, agent_role.capitalize(), response)
                    results[agent_role] = response

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

        # Run different roles in parallel
        if len(by_role) > 1:
            await asyncio.gather(
                *(run_role(role, dels) for role, dels in by_role.items()),
                return_exceptions=True,
            )
        elif by_role:
            # Single role — run directly (no overhead)
            role, dels = next(iter(by_role.items()))
            await run_role(role, dels)

        # Reset current_agent after all sub-agents finish
        self.current_agent = None
        self.current_tool = None

        return results

    async def _query_agent(self, agent_role: str, prompt: str) -> SDKResponse:
        """Query a specific agent (orchestrator or sub-agent) using the SDK."""
        # Get system prompt and resource limits based on role and mode
        allowed_tools = None  # None = all tools available (default)

        if agent_role == "orchestrator" and self.multi_agent:
            system_prompt = ORCHESTRATOR_SYSTEM_PROMPT
            max_turns = 1
            max_budget = 0.5
            permission_mode = "bypassPermissions"
            allowed_tools = []  # No tools — text-only output
            logger.info(f"[{self.project_id}] Querying orchestrator (coordinator mode, no tools, max_turns=1)")
        elif agent_role == "orchestrator" and not self.multi_agent:
            system_prompt = SOLO_AGENT_PROMPT
            max_turns = SDK_MAX_TURNS_PER_QUERY
            max_budget = SDK_MAX_BUDGET_PER_QUERY
            permission_mode = "bypassPermissions"
            logger.info(f"[{self.project_id}] Querying orchestrator (solo mode, full tools)")
        else:
            system_prompt = SUB_AGENT_PROMPTS.get(agent_role, "You are a helpful coding assistant.")
            max_turns = SDK_MAX_TURNS_PER_QUERY
            max_budget = SDK_MAX_BUDGET_PER_QUERY
            permission_mode = "bypassPermissions"
            logger.info(f"[{self.project_id}] Querying sub-agent '{agent_role}' (max_turns={max_turns}, budget=${max_budget})")

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
            await self._emit_event(
                "tool_use",
                agent=agent_role,
                tool_name=tool_name,
                description=tool_info,
                input=tool_input,
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
        # Fire-and-forget persist to SQLite
        asyncio.create_task(
            self.session_mgr.add_message(
                self.project_id, agent_name, role, response.text, response.cost_usd,
            )
        )

    # --- Delegation parsing ---

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
                    delegations.append(Delegation(
                        agent=agent,
                        task=task,
                        context=data.get("context", ""),
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

    def _build_review_prompt(self, sub_results: dict[str, SDKResponse]) -> str:
        """Build a prompt for the orchestrator to review sub-agent results."""
        parts = ["Sub-agent results:\n"]
        for agent, response in sub_results.items():
            status = "SUCCESS" if not response.is_error else "ERROR"
            # Include a reasonable chunk of the response
            content = response.text[:3000]
            if len(response.text) > 3000:
                content += "\n... (truncated)"
            # Provide explicit fallback so the orchestrator knows work was attempted
            if not content.strip():
                content = (
                    "[Agent produced no text output — it may have completed the task "
                    "using tools (file writes, shell commands). Check the workspace files "
                    "to verify what was done before deciding if more work is needed.]"
                )
            parts.append(
                f"--- {agent} [{status}] ---\n"
                f"{content}\n"
            )
        parts.append(
            "\nReview the results above. If all tasks are complete and correct, "
            "respond with TASK_COMPLETE. If more work is needed, delegate again."
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

    def _detect_stuck(self) -> bool:
        if len(self.conversation_log) < STUCK_WINDOW_SIZE:
            return False

        agent_messages = [
            m.content for m in self.conversation_log[-STUCK_WINDOW_SIZE:]
            if m.agent_name != "user"
        ]
        if len(agent_messages) < STUCK_WINDOW_SIZE:
            return False

        for i in range(len(agent_messages)):
            for j in range(i + 1, len(agent_messages)):
                similarity = SequenceMatcher(
                    None, agent_messages[i][:500], agent_messages[j][:500]
                ).ratio()
                if similarity > STUCK_SIMILARITY_THRESHOLD:
                    logger.warning(f"Stuck detected: similarity={similarity:.2f}")
                    return True
        return False
