from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Awaitable

from claude_agent import ClaudeAgent, AgentResponse
from config import (
    DEFAULT_AGENTS,
    MAX_BUDGET_USD,
    MAX_TURNS_PER_CYCLE,
    PROJECTS_BASE_DIR,
    STUCK_SIMILARITY_THRESHOLD,
    STUCK_WINDOW_SIZE,
)

logger = logging.getLogger(__name__)


@dataclass
class Message:
    agent_name: str
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    cost_usd: float = 0.0


class AgentManager:
    def __init__(
        self,
        project_name: str,
        project_dir: str,
        agents_config: list[dict] | None = None,
        on_update: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.project_name = project_name
        self.project_dir = project_dir
        self.agents_config = agents_config or DEFAULT_AGENTS[:2]
        self.on_update = on_update

        self.agents: dict[str, ClaudeAgent] = {}
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

        self._init_agents()

    def _init_agents(self):
        os.makedirs(self.project_dir, exist_ok=True)
        for cfg in self.agents_config:
            agent = ClaudeAgent(
                name=cfg["name"],
                role=cfg["role"],
                system_prompt=cfg["system_prompt"],
                project_dir=self.project_dir,
            )
            self.agents[cfg["name"]] = agent
            logger.info(f"Initialized agent: {agent}")

    @property
    def agent_names(self) -> list[str]:
        return list(self.agents.keys())

    @property
    def is_multi_agent(self) -> bool:
        return len(self.agents) > 1

    async def _notify(self, text: str):
        if self.on_update:
            try:
                await self.on_update(text)
            except Exception as e:
                logger.error(f"Update callback error: {e}")

    async def start_session(self, user_instructions: str):
        if self.is_running:
            await self._notify("Session is already running.")
            return

        self.is_running = True
        self._stop_event.clear()
        self._pause_event.set()
        # Reset turn count for new session
        self.turn_count = 0

        if self.is_multi_agent:
            self._task = asyncio.create_task(
                self._run_multi_agent_loop(user_instructions)
            )
        else:
            # Single agent: just process and respond, no loop
            self._task = asyncio.create_task(
                self._run_single_agent(user_instructions)
            )

    async def _run_single_agent(self, user_message: str):
        """Handle single-agent mode: send message, get response, done."""
        agent = list(self.agents.values())[0]

        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=user_message)
        )

        self.turn_count += 1

        await self._notify(
            f"🤖 {agent.name} ({agent.role}) is working on your request..."
        )

        try:
            response = await agent.send_message(user_message)

            self.total_cost_usd += response.cost_usd
            self.conversation_log.append(
                Message(
                    agent_name=agent.name,
                    role=agent.role,
                    content=response.text,
                    cost_usd=response.cost_usd,
                )
            )

            summary = response.text[:3000]
            if len(response.text) > 3000:
                summary += "\n... (truncated, use /log for full)"

            cost_str = f"${response.cost_usd:.4f}" if response.cost_usd > 0 else ""
            header = f"💬 *{agent.name}*"
            if cost_str:
                header += f" | {cost_str}"

            await self._notify(f"{header}\n\n{summary}")

            if response.is_error:
                await self._notify(
                    f"⚠️ Error occurred. Try sending your message again or check the logs."
                )
        except Exception as e:
            logger.error(f"Single agent error: {e}", exc_info=True)
            await self._notify(f"❌ Error: {e}")
        finally:
            self.is_running = False

    async def _run_multi_agent_loop(self, user_instructions: str):
        """Handle multi-agent mode: agents pass messages to each other."""
        agent_list = list(self.agents.values())
        if not agent_list:
            await self._notify("No agents configured.")
            self.is_running = False
            return

        current_message = user_instructions
        current_agent_idx = 0

        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=user_instructions)
        )

        await self._notify(
            f"🚀 Starting *{self.project_name}* with {len(agent_list)} agents: "
            f"{', '.join(a.name for a in agent_list)}\n\n"
            f"📋 Task: {user_instructions[:300]}"
        )

        try:
            while self.is_running and self.turn_count < MAX_TURNS_PER_CYCLE:
                # Check stop
                if self._stop_event.is_set():
                    break

                # Wait if paused
                await self._pause_event.wait()

                # Check for user injection
                if self._user_injection:
                    target_name, injected_msg = self._user_injection
                    self._user_injection = None
                    if target_name in self.agents:
                        current_agent_idx = list(self.agents.keys()).index(target_name)
                        current_message = injected_msg
                        # Log user injection
                        self.conversation_log.append(
                            Message(agent_name="user", role="User", content=f"[to {target_name}] {injected_msg}")
                        )
                        await self._notify(f"📨 User message injected to *{target_name}*")

                agent = agent_list[current_agent_idx]
                self.turn_count += 1

                # Build prompt with context
                if agent == agent_list[0] and self.turn_count == 1:
                    prompt = (
                        f"Project: {self.project_name}\n\n"
                        f"User requirements:\n{current_message}\n\n"
                        f"You are working with {len(agent_list) - 1} other agent(s): "
                        f"{', '.join(f'{a.name} ({a.role})' for a in agent_list if a != agent)}.\n\n"
                        f"Create a detailed plan and send your first instructions to the next agent."
                    )
                else:
                    prev_agent = agent_list[(current_agent_idx - 1) % len(agent_list)]
                    prompt = (
                        f"[Message from {prev_agent.name} ({prev_agent.role})]:\n\n"
                        f"{current_message}"
                    )

                await self._notify(
                    f"🔄 Turn {self.turn_count}/{MAX_TURNS_PER_CYCLE} — "
                    f"*{agent.name}* ({agent.role}) is working..."
                )

                response = await agent.send_message(prompt)

                self.total_cost_usd += response.cost_usd
                self.conversation_log.append(
                    Message(
                        agent_name=agent.name,
                        role=agent.role,
                        content=response.text,
                        cost_usd=response.cost_usd,
                    )
                )

                # Truncate response for Telegram update
                summary = response.text[:2000]
                if len(response.text) > 2000:
                    summary += "\n... (truncated)"

                await self._notify(
                    f"✅ *{agent.name}* ({agent.role}) — Turn {self.turn_count}\n"
                    f"💰 ${response.cost_usd:.4f} (total: ${self.total_cost_usd:.4f})\n\n"
                    f"{summary}"
                )

                if response.is_error:
                    await self._notify(
                        f"⚠️ {agent.name} returned an error. "
                        f"Use /talk {agent.name} <message> to intervene, or /stop to end."
                    )
                    # Don't pass errors forward — pause and let user intervene
                    self.is_paused = True
                    self._pause_event.clear()
                    continue

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

                # Check for stuck loop
                if self._detect_stuck():
                    await self._notify(
                        f"🔁 Agents appear stuck in a loop.\n"
                        f"Use /talk <agent> <message> to intervene, or /stop to end."
                    )
                    self.is_paused = True
                    self._pause_event.clear()
                    continue

                # Move to next agent
                current_message = response.text
                current_agent_idx = (current_agent_idx + 1) % len(agent_list)

            if self.turn_count >= MAX_TURNS_PER_CYCLE:
                await self._notify(
                    f"⏰ Reached max turns ({MAX_TURNS_PER_CYCLE}).\n"
                    f"Use /resume to continue or /stop to end."
                )
                self.is_paused = True
                self._pause_event.clear()

        except asyncio.CancelledError:
            logger.info(f"Multi-agent loop cancelled for {self.project_name}")
        except Exception as e:
            logger.error(f"Multi-agent loop error: {e}", exc_info=True)
            await self._notify(f"❌ Unexpected error in agent loop: {e}")
        finally:
            if not self.is_paused:
                self.is_running = False

    def _detect_stuck(self) -> bool:
        if len(self.conversation_log) < STUCK_WINDOW_SIZE:
            return False

        # Only check agent messages, not user messages
        agent_messages = [m.content for m in self.conversation_log[-STUCK_WINDOW_SIZE:] if m.agent_name != "user"]
        if len(agent_messages) < STUCK_WINDOW_SIZE:
            return False

        for i in range(len(agent_messages)):
            for j in range(i + 1, len(agent_messages)):
                similarity = SequenceMatcher(None, agent_messages[i][:500], agent_messages[j][:500]).ratio()
                if similarity > STUCK_SIMILARITY_THRESHOLD:
                    logger.warning(f"Stuck detected: similarity={similarity:.2f}")
                    return True
        return False

    async def inject_user_message(self, agent_name: str, message: str):
        if agent_name not in self.agents:
            await self._notify(f"Unknown agent: {agent_name}. Available: {', '.join(self.agent_names)}")
            return

        # Log user message
        self.conversation_log.append(
            Message(agent_name="user", role="User", content=f"[to {agent_name}] {message}")
        )

        if not self.is_running:
            # Send directly if not running
            agent = self.agents[agent_name]
            await self._notify(f"📨 Sending to *{agent_name}*...")
            response = await agent.send_message(message)
            self.total_cost_usd += response.cost_usd
            self.conversation_log.append(
                Message(agent_name=agent_name, role=agent.role, content=response.text, cost_usd=response.cost_usd)
            )
            summary = response.text[:3000]
            if len(response.text) > 3000:
                summary += "\n... (truncated)"

            cost_str = f" | ${response.cost_usd:.4f}" if response.cost_usd > 0 else ""
            await self._notify(f"💬 *{agent_name}*{cost_str}\n\n{summary}")
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
            # If the task is done (e.g., max turns reached), restart isn't needed
            # because the loop is still running (waiting on _pause_event)

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

    def get_status(self) -> str:
        state = "running" if self.is_running else ("paused" if self.is_paused else "stopped")
        lines = [
            f"Project: {self.project_name}",
            f"State: {state}",
            f"Turn: {self.turn_count}/{MAX_TURNS_PER_CYCLE}",
            f"Cost: ${self.total_cost_usd:.4f} / ${MAX_BUDGET_USD:.2f}",
            f"Agents: {', '.join(self.agent_names)}",
            f"Directory: {self.project_dir}",
        ]
        if self.conversation_log:
            last = self.conversation_log[-1]
            lines.append(f"Last message from: {last.agent_name} ({last.role})")
        return "\n".join(lines)
