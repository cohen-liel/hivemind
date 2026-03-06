from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass

from config import AGENT_TIMEOUT_SECONDS, CLAUDE_CLI_PATH

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    text: str
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    session_id: str = ""
    is_error: bool = False


class ClaudeAgent:
    def __init__(
        self,
        name: str,
        role: str,
        system_prompt: str,
        project_dir: str,
        session_id: str | None = None,
    ):
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.project_dir = project_dir
        self.session_id = session_id or str(uuid.uuid4())
        self._has_session = False

    def _build_command(self, message: str, stream: bool = False) -> list[str]:
        cmd = [
            CLAUDE_CLI_PATH,
            "-p", message,
            "--output-format", "stream-json" if stream else "json",
            "--dangerously-skip-permissions",
        ]

        # System prompt is passed every time to ensure consistency
        cmd.extend(["--system-prompt", self.system_prompt])

        if self._has_session:
            cmd.extend(["--resume", self.session_id])
        else:
            cmd.extend(["--session-id", self.session_id])

        return cmd

    async def send_message(self, message: str) -> AgentResponse:
        cmd = self._build_command(message, stream=False)
        logger.info(f"[{self.name}] Sending message ({len(message)} chars)")
        logger.debug(f"[{self.name}] Command: {' '.join(cmd[:6])}...")

        proc = None
        try:
            env = os.environ.copy()
            # Remove vars that prevent nested Claude sessions
            for var in ("CLAUDE_CODE_CLIENT_CERT", "CLAUDECODE", "CLAUDE_CODE"):
                env.pop(var, None)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_dir,
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=AGENT_TIMEOUT_SECONDS,
            )

            self._has_session = True

            if proc.returncode != 0:
                error_text = stderr.decode("utf-8", errors="replace").strip()
                logger.error(f"[{self.name}] CLI error (rc={proc.returncode}): {error_text}")
                return AgentResponse(
                    text=f"Error: {error_text}",
                    session_id=self.session_id,
                    is_error=True,
                )

            return self._parse_json_response(stdout.decode("utf-8", errors="replace"))

        except asyncio.TimeoutError:
            logger.warning(f"[{self.name}] Timed out after {AGENT_TIMEOUT_SECONDS}s")
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return AgentResponse(
                text=f"Error: Agent timed out after {AGENT_TIMEOUT_SECONDS} seconds",
                session_id=self.session_id,
                is_error=True,
            )
        except Exception as e:
            logger.error(f"[{self.name}] Unexpected error: {e}", exc_info=True)
            return AgentResponse(
                text=f"Error: {e}",
                session_id=self.session_id,
                is_error=True,
            )

    async def send_message_streaming(self, message: str):
        cmd = self._build_command(message, stream=True)
        logger.info(f"[{self.name}] Streaming message ({len(message)} chars)")

        env = os.environ.copy()
        for var in ("CLAUDE_CODE_CLIENT_CERT", "CLAUDECODE", "CLAUDE_CODE"):
            env.pop(var, None)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.project_dir,
            env=env,
        )

        accumulated_text = ""
        try:
            async for line in proc.stdout:
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("type") == "assistant" and "message" in event:
                        content = event["message"]
                        for block in content.get("content", []):
                            if block.get("type") == "text":
                                new_text = block["text"]
                                delta = new_text[len(accumulated_text):]
                                accumulated_text = new_text
                                if delta:
                                    yield delta
                    elif event.get("type") == "result":
                        cost = event.get("cost_usd", 0.0) or event.get("total_cost_usd", 0.0)
                        duration = event.get("duration_ms", 0) / 1000.0
                        self._has_session = True
                        yield AgentResponse(
                            text=accumulated_text,
                            cost_usd=cost,
                            duration_seconds=duration,
                            session_id=self.session_id,
                        )
                        return
                except json.JSONDecodeError:
                    continue

            await proc.wait()
            self._has_session = True

        except asyncio.TimeoutError:
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            yield AgentResponse(
                text=f"Error: Agent timed out after {AGENT_TIMEOUT_SECONDS} seconds",
                session_id=self.session_id,
                is_error=True,
            )

    def _parse_json_response(self, raw: str) -> AgentResponse:
        try:
            data = json.loads(raw)
            text = ""
            if isinstance(data.get("result"), str):
                text = data["result"]
            elif isinstance(data.get("content"), list):
                text = "\n".join(
                    block.get("text", "")
                    for block in data["content"]
                    if block.get("type") == "text"
                )
            else:
                text = raw

            cost = data.get("cost_usd", 0.0) or data.get("total_cost_usd", 0.0)
            duration = data.get("duration_ms", 0) / 1000.0
            session_id = data.get("session_id", self.session_id)

            return AgentResponse(
                text=text.strip(),
                cost_usd=cost,
                duration_seconds=duration,
                session_id=session_id,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[{self.name}] Failed to parse response: {e}")
            return AgentResponse(
                text=raw.strip(),
                session_id=self.session_id,
            )

    def reset_session(self):
        self.session_id = str(uuid.uuid4())
        self._has_session = False

    def __repr__(self):
        return f"ClaudeAgent(name={self.name!r}, role={self.role!r}, session={self.session_id[:8]})"
