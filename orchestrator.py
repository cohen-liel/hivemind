from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Awaitable

from config import (
    MAX_BUDGET_USD,
    MAX_TURNS_PER_CYCLE,
    ORCHESTRATOR_SYSTEM_PROMPT,
    SDK_MAX_TURNS_PER_QUERY,
    SDK_MAX_BUDGET_PER_QUERY,
    STUCK_SIMILARITY_THRESHOLD,
    STUCK_WINDOW_SIZE,
    SUB_AGENT_PROMPTS,
)
from sdk_client import ClaudeSDKManager, SDKResponse
from session_manager import SessionManager

logger = logging.getLogger(__name__)

# Regex to parse <delegate> blocks from orchestrator output
_DELEGATE_RE = re.compile(
    r"<delegate>\s*(\{.*?\})\s*</delegate>",
    re.DOTALL,
)


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
        multi_agent: bool = True,
    ):
        self.project_name = project_name
        self.project_dir = project_dir
        self.sdk = sdk
        self.session_mgr = session_mgr
        self.user_id = user_id
        self.project_id = project_id
        self.on_update = on_update
        self.multi_agent = multi_agent

        self.conversation_log: list[Message] = []
        self.is_running = False
        self.is_paused = False
        self.total_cost_usd = 0.0
        self.turn_count = 0

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
        if self.on_update:
            try:
                await self.on_update(text)
            except Exception as e:
                logger.error(f"Update callback error: {e}")

    def _get_workspace_context(self) -> str:
        """Scan project directory and return a short file listing."""
        try:
            entries = []
            for item in sorted(os.listdir(self.project_dir)):
                if item.startswith("."):
                    continue
                full = os.path.join(self.project_dir, item)
                if os.path.isdir(full):
                    entries.append(f"  {item}/")
                else:
                    size = os.path.getsize(full)
                    entries.append(f"  {item} ({size} bytes)")
            if not entries:
                return ""
            return "Current workspace files:\n" + "\n".join(entries[:30])
        except Exception:
            return ""

    async def start_session(self, user_message: str):
        """Start processing a user message."""
        if self.is_running:
            await self._notify("Session is already running.")
            return

        self.is_running = True
        self._stop_event.clear()
        self._pause_event.set()
        self.turn_count = 0

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
            await self._notify(f"💬 *orchestrator*{cost_str}\n\n{summary}")
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
        await self._notify(
            f"🛑 Project *{self.project_name}* stopped.\n"
            f"📊 Turns: {self.turn_count} | 💰 Cost: ${self.total_cost_usd:.4f}"
        )

    # --- Core orchestration loop ---

    async def _run_orchestrator(self, user_message: str):
        """Main orchestrator loop."""
        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=user_message)
        )
        await self.session_mgr.add_message(
            self.project_id, "user", "User", user_message
        )

        # Build initial prompt
        workspace = self._get_workspace_context()
        prompt = (
            f"Project: {self.project_name}\n"
            f"Working directory: {self.project_dir}\n\n"
        )
        if workspace:
            prompt += f"{workspace}\n\n"
        prompt += f"User request:\n{user_message}"

        try:
            # Main loop: orchestrator responds, optionally delegates, then loops
            orchestrator_input = prompt
            loop_count = 0
            max_loops = 10  # Safety limit on orchestrator iterations

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

                await self._notify(
                    f"🔄 Turn {self.turn_count}/{MAX_TURNS_PER_CYCLE} — "
                    f"*orchestrator* is working..."
                )

                # Query orchestrator
                response = await self._query_agent("orchestrator", orchestrator_input)
                self._record_response("orchestrator", "Orchestrator", response)

                if response.is_error:
                    await self._notify(
                        f"⚠️ Orchestrator error: {response.error_message}\n"
                        f"Use /resume to retry or /stop to end."
                    )
                    self.is_paused = True
                    self._pause_event.clear()
                    continue

                # Show orchestrator response
                display_text = self._strip_delegate_blocks(response.text)
                if display_text.strip():
                    summary = display_text[:2000]
                    if len(display_text) > 2000:
                        summary += "\n... (truncated)"
                    await self._notify(
                        f"✅ *orchestrator* — Turn {self.turn_count}\n"
                        f"💰 ${response.cost_usd:.4f} (total: ${self.total_cost_usd:.4f})\n\n"
                        f"{summary}"
                    )

                # Check completion
                if "TASK_COMPLETE" in response.text:
                    await self._notify(
                        f"🎉 Project *{self.project_name}* completed!\n\n"
                        f"📊 Total turns: {self.turn_count}\n"
                        f"💰 Total cost: ${self.total_cost_usd:.4f}\n"
                        f"📁 Files: `{self.project_dir}`"
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

                if not delegations:
                    # No delegations — orchestrator handled it directly, done for now
                    break

                if not self.multi_agent:
                    # Single-agent mode — ignore delegations
                    break

                # Execute sub-agents
                sub_results = await self._run_sub_agents(delegations)

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
        finally:
            if not self.is_paused:
                self.is_running = False

    async def _run_sub_agents(self, delegations: list[Delegation]) -> dict[str, SDKResponse]:
        """Execute sub-agent tasks and return their responses."""
        results: dict[str, SDKResponse] = {}

        for delegation in delegations:
            if self._stop_event.is_set():
                break

            agent_role = delegation.agent
            if agent_role not in SUB_AGENT_PROMPTS:
                logger.warning(f"Unknown sub-agent role: {agent_role}, skipping")
                continue

            self.turn_count += 1

            await self._notify(
                f"🔧 *{agent_role}* is working on: {delegation.task[:200]}..."
            )

            # Build sub-agent prompt
            sub_prompt = f"Task: {delegation.task}"
            if delegation.context:
                sub_prompt += f"\n\nContext: {delegation.context}"

            workspace = self._get_workspace_context()
            if workspace:
                sub_prompt += f"\n\n{workspace}"

            response = await self._query_agent(agent_role, sub_prompt)
            self._record_response(agent_role, agent_role.capitalize(), response)
            results[agent_role] = response

            # Show sub-agent response
            summary = response.text[:1500]
            if len(response.text) > 1500:
                summary += "\n... (truncated)"

            status = "✅" if not response.is_error else "⚠️"
            await self._notify(
                f"{status} *{agent_role}* — Turn {self.turn_count}\n"
                f"💰 ${response.cost_usd:.4f} (total: ${self.total_cost_usd:.4f})\n\n"
                f"{summary}"
            )

        return results

    async def _query_agent(self, agent_role: str, prompt: str) -> SDKResponse:
        """Query a specific agent (orchestrator or sub-agent) using the SDK."""
        # Get system prompt
        if agent_role == "orchestrator":
            system_prompt = ORCHESTRATOR_SYSTEM_PROMPT
        else:
            system_prompt = SUB_AGENT_PROMPTS.get(agent_role, "You are a helpful coding assistant.")

        # Try to resume session
        session_id = await self.session_mgr.get_session(
            self.user_id, self.project_id, agent_role
        )

        response = await self.sdk.query_with_retry(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=self.project_dir,
            session_id=session_id,
            max_turns=SDK_MAX_TURNS_PER_QUERY,
            max_budget_usd=SDK_MAX_BUDGET_PER_QUERY,
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
            try:
                data = json.loads(match.group(1))
                delegations.append(Delegation(
                    agent=data.get("agent", "developer"),
                    task=data.get("task", ""),
                    context=data.get("context", ""),
                ))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to parse delegation block: {e}")
        return delegations

    def _strip_delegate_blocks(self, text: str) -> str:
        """Remove <delegate> blocks from text for display purposes."""
        return _DELEGATE_RE.sub("", text).strip()

    def _build_review_prompt(self, sub_results: dict[str, SDKResponse]) -> str:
        """Build a prompt for the orchestrator to review sub-agent results."""
        parts = ["Sub-agent results:\n"]
        for agent, response in sub_results.items():
            status = "SUCCESS" if not response.is_error else "ERROR"
            # Include a reasonable chunk of the response
            content = response.text[:3000]
            if len(response.text) > 3000:
                content += "\n... (truncated)"
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
