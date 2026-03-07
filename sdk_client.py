from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

# Unset CLAUDECODE so the SDK can spawn claude subprocesses
# (otherwise it refuses with "cannot launch inside another Claude Code session")
os.environ.pop("CLAUDECODE", None)

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from config import AGENT_TIMEOUT_SECONDS, SDK_MAX_RETRIES

logger = logging.getLogger(__name__)

# Force the SDK to use the system-installed Claude CLI wrapper (/usr/local/bin/claude)
# instead of the bundled binary.  The wrapper sets up Meta-specific authentication
# (x2p proxy, CAT tokens, ANTHROPIC_BASE_URL, etc.) which the bundled binary skips.
import shutil as _shutil

SYSTEM_CLI_PATH = _shutil.which("claude") or "/usr/local/bin/claude"


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
        permission_mode: str | None = "bypassPermissions",
        on_stream=None,
        on_tool_use=None,
        allowed_tools: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> SDKResponse:
        """Send a query to Claude Agent SDK.

        Args:
            prompt: The user/agent prompt.
            system_prompt: System prompt for the agent role.
            cwd: Working directory for the agent.
            session_id: If set, resumes a previous session.
            max_turns: Max agentic turns.
            max_budget_usd: Max budget for this query.
            permission_mode: Permission mode — "bypassPermissions" for full
                access, None for default (no auto-tool-approval).
            on_stream: Optional async callback for real-time text updates.
            tools: Base tool set — [] disables ALL tools (passes --tools ""),
                None uses default tools. Different from allowed_tools.

        Returns:
            SDKResponse with text, session_id, cost, etc.
        """
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            cwd=cwd,
            cli_path=SYSTEM_CLI_PATH,
            include_partial_messages=on_stream is not None,
        )

        if permission_mode:
            options.permission_mode = permission_mode

        if allowed_tools is not None:
            options.allowed_tools = allowed_tools

        if tools is not None:
            options.tools = tools

        if session_id:
            options.resume = session_id

        logger.info(
            f"SDK query: max_turns={max_turns}, budget=${max_budget_usd}, "
            f"session={'resume' if session_id else 'new'}, "
            f"tools={'disabled' if tools is not None and len(tools) == 0 else 'all'}, "
            f"prompt={prompt[:80]}..."
        )

        try:
            # Run the stream consumption as a task so we can apply a timeout
            result = await asyncio.wait_for(
                self._consume_stream(prompt, options, on_stream, on_tool_use),
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

    async def _consume_stream(self, prompt, options, on_stream=None, on_tool_use=None) -> SDKResponse:
        """Consume the SDK async stream and return the final SDKResponse.

        With include_partial_messages=True, we get intermediate AssistantMessage
        objects showing tool use and partial text in real time.
        """
        text_parts: list[str] = []
        result_session_id = ""
        cost_usd = 0.0
        duration_ms = 0
        num_turns = 0
        last_seen_text = ""  # Track to avoid duplicate stream callbacks

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                # Extract content from this message (may be partial or complete)
                turn_text = ""
                tool_info = ""
                for block in message.content:
                    if isinstance(block, TextBlock):
                        turn_text += block.text
                    elif isinstance(block, ToolUseBlock):
                        # Show which tool the agent is using
                        tool_name = block.name
                        tool_input = block.input
                        # Format tool use for display
                        if tool_name in ("Read", "read_file"):
                            path = tool_input.get("file_path") or tool_input.get("path", "")
                            tool_info = f"📄 Reading: {path}"
                        elif tool_name in ("Write", "write_file", "create_file"):
                            path = tool_input.get("file_path") or tool_input.get("path", "")
                            tool_info = f"✏️ Writing: {path}"
                        elif tool_name in ("Edit", "edit_file"):
                            path = tool_input.get("file_path") or tool_input.get("path", "")
                            tool_info = f"🔧 Editing: {path}"
                        elif tool_name in ("Bash", "execute_bash", "bash"):
                            cmd = str(tool_input.get("command", ""))[:100]
                            tool_info = f"💻 Running: `{cmd}`"
                        elif tool_name in ("Glob", "glob", "ListFiles"):
                            pattern = tool_input.get("pattern", "")
                            tool_info = f"🔍 Searching: {pattern}"
                        elif tool_name in ("Grep", "grep", "SearchFiles"):
                            pattern = tool_input.get("pattern", "")
                            tool_info = f"🔎 Grep: {pattern}"
                        else:
                            tool_info = f"🔧 {tool_name}"

                        # Fire on_tool_use callback with structured data
                        if on_tool_use:
                            try:
                                # Truncate tool_input for the callback
                                truncated_input = {
                                    k: (str(v)[:200] if isinstance(v, str) and len(str(v)) > 200 else v)
                                    for k, v in (tool_input or {}).items()
                                }
                                await on_tool_use(tool_name, tool_info, truncated_input)
                            except Exception as e:
                                logger.error(f"on_tool_use callback error: {e}")

                # Call stream callback with meaningful updates
                if on_stream and (turn_text != last_seen_text or tool_info):
                    try:
                        update = ""
                        if tool_info:
                            update = tool_info
                        if turn_text and turn_text != last_seen_text:
                            # Show last 300 chars of new text
                            new_text = turn_text[len(last_seen_text):]
                            preview = new_text[-300:] if len(new_text) > 300 else new_text
                            if update:
                                update += f"\n\n{preview}"
                            else:
                                update = preview
                        if update:
                            await on_stream(update)
                    except Exception as e:
                        logger.error(f"Stream callback error: {e}")
                    last_seen_text = turn_text

                # Collect final text — each turn may extend or replace the previous
                if turn_text:
                    if text_parts and turn_text.startswith(text_parts[-1]):
                        # This turn's text extends the last one (streaming partial)
                        text_parts[-1] = turn_text
                    elif not text_parts or turn_text != text_parts[-1]:
                        # New distinct turn text
                        text_parts.append(turn_text)

            elif isinstance(message, ResultMessage):
                result_session_id = message.session_id or ""
                cost_usd = message.total_cost_usd or 0.0
                duration_ms = message.duration_ms or 0
                num_turns = message.num_turns or 0

                # ResultMessage may also carry a result text
                if message.result and message.result not in text_parts:
                    text_parts.append(message.result)

                combined = "\n\n".join(text_parts).strip()

                # If agent did work via tools but produced no text output,
                # provide a meaningful fallback so the orchestrator knows work was done.
                if not combined and not message.is_error:
                    combined = (
                        f"✅ Task completed via tool use ({num_turns} turn(s)). "
                        "No text output — work was done directly. "
                        "Verify results in the workspace files."
                    )

                return SDKResponse(
                    text=combined,
                    session_id=result_session_id,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    num_turns=num_turns,
                    is_error=message.is_error,
                    error_message="" if not message.is_error else (message.result or "Unknown error"),
                )

        # Stream ended without ResultMessage — treat as success if we got any text,
        # otherwise flag as a real error (SDK failed to complete the stream).
        combined = "\n\n".join(text_parts).strip()
        return SDKResponse(
            text=combined or "⚠️ Agent produced no text output (stream ended unexpectedly).",
            session_id=result_session_id,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            num_turns=num_turns,
            is_error=True,  # No ResultMessage = definitely an SDK-level error
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
        permission_mode: str | None = "bypassPermissions",
        on_stream=None,
        on_tool_use=None,
        allowed_tools: list[str] | None = None,
        tools: list[str] | None = None,
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
                permission_mode=permission_mode,
                on_stream=on_stream if attempt == 1 else None,
                on_tool_use=on_tool_use if attempt == 1 else None,
                allowed_tools=allowed_tools,
                tools=tools,
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

            # Timeout: retry once with a wake-up nudge
            if "timeout" in error_msg:
                logger.warning(f"Timeout, retrying with wake-up nudge: {response.error_message}")
                prompt = (
                    "[SYSTEM: Previous attempt timed out. Please complete the task efficiently. "
                    "Focus on the most important parts first.]\n\n" + prompt
                )
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
