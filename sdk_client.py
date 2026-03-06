from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from config import AGENT_TIMEOUT_SECONDS, SDK_MAX_RETRIES

logger = logging.getLogger(__name__)


@dataclass
class SDKResponse:
    text: str
    session_id: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    is_error: bool = False
    error_message: str = ""


class ClaudeSDKManager:
    """Thin wrapper over claude-agent-sdk's query() with retry and error handling."""

    async def query(
        self,
        prompt: str,
        system_prompt: str,
        cwd: str,
        session_id: str | None = None,
        max_turns: int = 10,
        max_budget_usd: float = 2.0,
        on_stream=None,
    ) -> SDKResponse:
        """Send a query to Claude Agent SDK.

        Args:
            prompt: The user/agent prompt.
            system_prompt: System prompt for the agent role.
            cwd: Working directory for the agent.
            session_id: If set, resumes a previous session.
            max_turns: Max agentic turns.
            max_budget_usd: Max budget for this query.
            on_stream: Optional async callback for real-time text updates.

        Returns:
            SDKResponse with text, session_id, cost, etc.
        """
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            cwd=cwd,
            permission_mode="bypassPermissions",
        )

        if session_id:
            options.resume = session_id

        try:
            # Run the stream consumption as a task so we can apply a timeout
            result = await asyncio.wait_for(
                self._consume_stream(prompt, options, on_stream),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"SDK query timed out after {AGENT_TIMEOUT_SECONDS}s")
            return SDKResponse(
                text=f"Error: Agent timed out after {AGENT_TIMEOUT_SECONDS} seconds",
                session_id=session_id or "",
                is_error=True,
                error_message=f"Timeout after {AGENT_TIMEOUT_SECONDS}s",
            )
        except Exception as e:
            logger.error(f"SDK query error: {e}", exc_info=True)
            return SDKResponse(
                text=f"Error: {e}",
                session_id=session_id or "",
                is_error=True,
                error_message=str(e),
            )

    async def _consume_stream(self, prompt, options, on_stream=None) -> SDKResponse:
        """Consume the SDK async stream and return the final SDKResponse."""
        text_parts: list[str] = []
        result_session_id = ""
        cost_usd = 0.0
        duration_ms = 0
        num_turns = 0

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                # Each AssistantMessage is a complete assistant turn.
                # Extract text from its content blocks.
                turn_text = ""
                for block in message.content:
                    if isinstance(block, TextBlock):
                        turn_text += block.text
                if turn_text:
                    text_parts.append(turn_text)
                    if on_stream:
                        try:
                            await on_stream(turn_text)
                        except Exception as e:
                            logger.error(f"Stream callback error: {e}")

            elif isinstance(message, ResultMessage):
                result_session_id = message.session_id or ""
                cost_usd = message.total_cost_usd or 0.0
                duration_ms = message.duration_ms or 0
                num_turns = message.num_turns or 0

                # ResultMessage may also carry a result text
                if message.result and message.result not in text_parts:
                    text_parts.append(message.result)

                return SDKResponse(
                    text="\n\n".join(text_parts).strip(),
                    session_id=result_session_id,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    num_turns=num_turns,
                    is_error=message.is_error,
                    error_message="" if not message.is_error else (message.result or "Unknown error"),
                )

        # Stream ended without ResultMessage
        combined = "\n\n".join(text_parts).strip()
        return SDKResponse(
            text=combined or "No response received",
            session_id=result_session_id,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            num_turns=num_turns,
            is_error=not combined,
        )

    async def query_with_retry(
        self,
        prompt: str,
        system_prompt: str,
        cwd: str,
        session_id: str | None = None,
        max_turns: int = 10,
        max_budget_usd: float = 2.0,
        max_retries: int = SDK_MAX_RETRIES,
        on_stream=None,
    ) -> SDKResponse:
        """Query with automatic retry on transient errors.

        Retry strategy:
        - Timeout → retry once
        - Connection errors → retry with 1s/3s backoff
        - Stale session errors → invalidate session_id, retry fresh
        """
        last_response: SDKResponse | None = None
        current_session = session_id

        for attempt in range(1, max_retries + 1):
            logger.info(f"SDK query attempt {attempt}/{max_retries}")

            response = await self.query(
                prompt=prompt,
                system_prompt=system_prompt,
                cwd=cwd,
                session_id=current_session,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                on_stream=on_stream if attempt == 1 else None,
            )

            if not response.is_error:
                return response

            last_response = response
            error_msg = response.error_message.lower()

            # Session-related errors: invalidate and retry fresh
            if "session" in error_msg or "resume" in error_msg:
                logger.warning(f"Session error, retrying fresh: {response.error_message}")
                current_session = None
                await asyncio.sleep(1)
                continue

            # Timeout: retry once
            if "timeout" in error_msg:
                logger.warning(f"Timeout, retrying: {response.error_message}")
                await asyncio.sleep(1)
                continue

            # Connection errors: retry with backoff
            if "connection" in error_msg or "process" in error_msg:
                backoff = [1, 3][min(attempt - 1, 1)]
                logger.warning(f"Connection error, retrying in {backoff}s: {response.error_message}")
                await asyncio.sleep(backoff)
                continue

            # Other errors: don't retry
            logger.error(f"Non-retryable error: {response.error_message}")
            return response

        logger.error(f"All {max_retries} retry attempts failed")
        return last_response or SDKResponse(
            text="Error: All retry attempts failed",
            is_error=True,
            error_message="All retry attempts failed",
        )
