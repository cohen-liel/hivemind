"""Process-isolated SDK query runner.

This module solves the anyio cancel-scope bug (GitHub issue #454 in
claude-agent-sdk-python) by running each SDK ``query()`` call inside a
**dedicated asyncio event loop on a separate thread**.  If the SDK's
internal anyio cleanup leaks a cancel-scope into the event loop, only
that throwaway loop is poisoned — the main application loop stays clean.

Architecture
------------
Main event loop (FastAPI / orchestrator)
  └─ calls ``isolated_query()``
       └─ spawns a **thread** with its own ``asyncio.run()``
            └─ runs ``_inner_query()`` which calls the real SDK ``query()``
            └─ streams partial results back via a thread-safe ``asyncio.Queue``

**Critical fix (v2)**: Each isolated loop creates its OWN connection pool
semaphore.  The module-level ``_pool`` in ``sdk_client.py`` is bound to the
main event loop — using it from a thread's fresh loop causes
``RuntimeError: Semaphore is bound to a different event loop`` or silent
deadlocks.  We bypass the pool entirely inside isolated queries since each
thread can only run one query at a time anyway (the thread pool size IS
the concurrency limit).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from sdk_client import SDKResponse, ErrorCategory, classify_error

logger = logging.getLogger(__name__)

# Thread pool for isolated queries.  Each thread gets its own event loop.
# Size matches max concurrent agents (5 roles).  This IS the concurrency
# limiter — no need for the asyncio.Semaphore pool inside the thread.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=5,
    thread_name_prefix="isolated-sdk",
)


@dataclass
class _StreamEvent:
    """A message passed from the isolated thread back to the caller."""
    kind: str          # "stream" | "tool_use" | "done" | "error"
    payload: Any = None


def _run_in_fresh_loop(coro_factory: Callable[[], Awaitable[SDKResponse]],
                        stream_queue: asyncio.Queue | None,
                        caller_loop: asyncio.AbstractEventLoop) -> SDKResponse:
    """Run *coro_factory()* in a brand-new event loop on the current thread.

    This is the function that executes inside the thread pool.  It creates
    a fresh ``asyncio.run()`` so the anyio cancel-scope leak cannot infect
    the caller's event loop.
    """
    async def _inner():
        return await coro_factory()

    try:
        return asyncio.run(_inner())
    except RuntimeError as e:
        if "cancel scope" in str(e):
            logger.warning(f"Isolated query caught anyio cancel-scope error (contained): {e}")
            return SDKResponse(
                text="Agent completed but cleanup had an anyio error (contained in isolated loop).",
                is_error=True,
                error_message=f"anyio cancel scope (isolated): {e}",
                error_category=ErrorCategory.TRANSIENT,
            )
        raise
    except Exception as e:
        logger.error(f"Isolated query unexpected error: {e}", exc_info=True)
        return SDKResponse(
            text=f"Error in isolated query: {e}",
            is_error=True,
            error_message=str(e),
            error_category=ErrorCategory.UNKNOWN,
        )


async def isolated_query(
    sdk,  # ClaudeSDKManager — only used for type reference, not called directly
    *,
    prompt: str,
    system_prompt: str,
    cwd: str,
    session_id: str | None = None,
    max_turns: int = 10,
    max_budget_usd: float = 2.0,
    max_retries: int = 2,
    permission_mode: str | None = "bypassPermissions",
    on_stream: Callable | None = None,
    on_tool_use: Callable | None = None,
    allowed_tools: list[str] | None = None,
    tools: list[str] | None = None,
) -> SDKResponse:
    """Run an SDK query in a process-isolated event loop.

    This is a drop-in replacement for ``sdk.query_with_retry()`` that
    provides event-loop isolation.  The caller's event loop is never
    exposed to anyio's cancel-scope cleanup.

    **v2 fix**: We no longer create a ``ClaudeSDKManager`` inside the
    isolated loop (which would try to use the module-level ``_pool``
    semaphore from a different event loop).  Instead, we call the raw
    ``claude_agent_sdk.query()`` directly, bypassing the pool.  The
    thread pool executor size (5 workers) acts as the concurrency limiter.

    Callbacks (``on_stream``, ``on_tool_use``) are bridged back to the
    caller's event loop via ``call_soon_threadsafe``.
    """
    caller_loop = asyncio.get_running_loop()
    request_id = f"iso_{int(time.monotonic() * 1000) % 100000}"

    logger.info(
        f"[{request_id}] Starting isolated query: "
        f"max_turns={max_turns}, budget=${max_budget_usd}"
    )

    # Queue for streaming events from isolated thread → caller.
    # Bounded to prevent unbounded memory growth if the drain task falls behind.
    stream_queue: asyncio.Queue[_StreamEvent] = asyncio.Queue(maxsize=500)

    def _make_bridged_stream_cb():
        """Create a stream callback that bridges to the caller's loop."""
        if on_stream is None:
            return None

        async def _bridged_stream(text: str):
            try:
                caller_loop.call_soon_threadsafe(
                    lambda t=text: stream_queue.put_nowait(
                        _StreamEvent(kind="stream", payload=t)
                    ) if not stream_queue.full() else None,
                )
            except Exception:
                pass  # Caller loop may be closing, or queue full

        return _bridged_stream

    def _make_bridged_tool_cb():
        """Create a tool_use callback that bridges to the caller's loop."""
        if on_tool_use is None:
            return None

        async def _bridged_tool(tool_name: str, tool_info: str, tool_input: dict):
            try:
                caller_loop.call_soon_threadsafe(
                    lambda tn=tool_name, ti=tool_info, tinp=tool_input: stream_queue.put_nowait(
                        _StreamEvent(kind="tool_use", payload=(tn, ti, tinp))
                    ) if not stream_queue.full() else None,
                )
            except Exception:
                pass  # Caller loop may be closing, or queue full

        return _bridged_tool

    def _query_factory():
        """Factory that creates the coroutine to run in the isolated loop.

        CRITICAL: We call the raw SDK query() directly here instead of
        going through ClaudeSDKManager.  This avoids the cross-event-loop
        semaphore issue.  The thread pool size limits concurrency.
        """
        from claude_agent_sdk import query as sdk_query, ClaudeAgentOptions
        from claude_agent_sdk.types import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
        )
        from config import AGENT_TIMEOUT_SECONDS

        # Import the CLI path resolution from sdk_client
        from sdk_client import SYSTEM_CLI_PATH

        bridged_stream = _make_bridged_stream_cb()
        bridged_tool = _make_bridged_tool_cb()

        async def _do_query() -> SDKResponse:
            """Execute the SDK query with retry logic inside the isolated loop."""
            last_response: SDKResponse | None = None
            current_session = session_id
            total_cost = 0.0

            for attempt in range(1, max_retries + 2):
                if attempt > max_retries + 1:
                    break

                # Build options
                options = ClaudeAgentOptions(
                    system_prompt=system_prompt,
                    max_turns=max_turns,
                    max_budget_usd=max_budget_usd,
                    cwd=cwd,
                    cli_path=SYSTEM_CLI_PATH,
                    include_partial_messages=bridged_stream is not None,
                )
                if permission_mode:
                    options.permission_mode = permission_mode
                if allowed_tools is not None:
                    options.allowed_tools = allowed_tools
                if tools is not None:
                    options.tools = tools
                if current_session:
                    options.resume = current_session

                query_start = time.monotonic()
                text_parts: list[str] = []
                result_session_id = ""
                cost_usd = 0.0
                duration_ms = 0
                num_turns = 0
                last_seen_text = ""
                tool_uses: list[str] = []

                try:
                    gen = sdk_query(prompt=prompt, options=options).__aiter__()
                    try:
                        while True:
                            try:
                                message = await asyncio.wait_for(
                                    gen.__anext__(),
                                    timeout=AGENT_TIMEOUT_SECONDS,
                                )
                            except StopAsyncIteration:
                                break
                            except asyncio.TimeoutError:
                                logger.warning(f"[{request_id}] Isolated query timeout at {AGENT_TIMEOUT_SECONDS}s")
                                break

                            if isinstance(message, AssistantMessage):
                                turn_text = ""
                                tool_info = ""
                                for block in message.content:
                                    if isinstance(block, TextBlock):
                                        turn_text += block.text
                                    elif isinstance(block, ToolUseBlock):
                                        tool_name = block.name
                                        tool_input_data = block.input if block.input else {}
                                        if tool_name in ("Read", "read_file"):
                                            path = tool_input_data.get("file_path") or tool_input_data.get("path", "")
                                            tool_info = f"📄 Reading: {path}"
                                        elif tool_name in ("Write", "write_file", "create_file"):
                                            path = tool_input_data.get("file_path") or tool_input_data.get("path", "")
                                            tool_info = f"✏️ Writing: {path}"
                                        elif tool_name in ("Edit", "edit_file"):
                                            path = tool_input_data.get("file_path") or tool_input_data.get("path", "")
                                            tool_info = f"🔧 Editing: {path}"
                                        elif tool_name in ("Bash", "execute_bash", "bash"):
                                            cmd = str(tool_input_data.get("command", ""))[:100]
                                            tool_info = f"💻 Running: `{cmd}`"
                                        elif tool_name in ("Glob", "glob", "ListFiles"):
                                            pattern = tool_input_data.get("pattern", "")
                                            tool_info = f"🔍 Searching: {pattern}"
                                        elif tool_name in ("Grep", "grep", "SearchFiles"):
                                            pattern = tool_input_data.get("pattern", "")
                                            tool_info = f"🔎 Grep: {pattern}"
                                        else:
                                            tool_info = f"🔧 {tool_name}"
                                        tool_uses.append(tool_name)

                                        if bridged_tool:
                                            try:
                                                truncated = {}
                                                for k, v in (tool_input_data or {}).items():
                                                    if isinstance(v, str) and len(v) > 200:
                                                        truncated[k] = v[:200] + "..."
                                                    else:
                                                        truncated[k] = v
                                                await bridged_tool(tool_name, tool_info, truncated)
                                            except Exception:
                                                pass

                                if bridged_stream and (turn_text != last_seen_text or tool_info):
                                    try:
                                        update = ""
                                        if tool_info:
                                            update = tool_info
                                        if turn_text and turn_text != last_seen_text:
                                            new_text = turn_text[len(last_seen_text):]
                                            preview = new_text[-300:] if len(new_text) > 300 else new_text
                                            update = f"{update}\n\n{preview}" if update else preview
                                        if update:
                                            await bridged_stream(update)
                                    except Exception:
                                        pass
                                    last_seen_text = turn_text

                                if turn_text:
                                    if text_parts and turn_text.startswith(text_parts[-1]):
                                        text_parts[-1] = turn_text
                                    elif not text_parts or turn_text != text_parts[-1]:
                                        text_parts.append(turn_text)

                            elif isinstance(message, ResultMessage):
                                result_session_id = message.session_id or ""
                                cost_usd = message.total_cost_usd or 0.0
                                duration_ms = message.duration_ms or 0
                                num_turns = message.num_turns or 0

                                if message.result and message.result not in text_parts:
                                    text_parts.append(message.result)

                                combined = "\n\n".join(text_parts).strip()
                                if not combined and not message.is_error:
                                    tools_summary = ", ".join(set(tool_uses)) if tool_uses else "unknown"
                                    combined = (
                                        f"✅ Task completed via tool use ({num_turns} turn(s), "
                                        f"tools: {tools_summary}). No text output."
                                    )

                                total_cost += cost_usd
                                response = SDKResponse(
                                    text=combined,
                                    session_id=result_session_id,
                                    cost_usd=total_cost,
                                    duration_ms=duration_ms,
                                    num_turns=num_turns,
                                    is_error=message.is_error,
                                    error_message="" if not message.is_error else (message.result or "Unknown error"),
                                    error_category=classify_error(message.result or "") if message.is_error else ErrorCategory.UNKNOWN,
                                    retry_count=attempt - 1,
                                )

                                if not response.is_error:
                                    return response

                                # Check if retryable
                                last_response = response
                                cat = response.error_category
                                if cat in (ErrorCategory.AUTH, ErrorCategory.BUDGET, ErrorCategory.PERMANENT):
                                    return response
                                if attempt > max_retries:
                                    return response

                                # Backoff and retry
                                if cat == ErrorCategory.RATE_LIMIT:
                                    await asyncio.sleep(min(5 * (3 ** (attempt - 1)), 30))
                                elif cat == ErrorCategory.SESSION:
                                    current_session = None
                                    await asyncio.sleep(0.5)
                                elif cat == ErrorCategory.TRANSIENT:
                                    await asyncio.sleep(min(1 * (2 ** (attempt - 1)), 8))
                                else:
                                    await asyncio.sleep(2)
                                break  # Break inner while to retry outer for

                    finally:
                        # Explicitly close the generator in THIS task
                        try:
                            await gen.aclose()
                        except RuntimeError as e:
                            if "cancel scope" in str(e):
                                logger.debug(f"[{request_id}] Suppressed anyio cancel scope during close")
                            else:
                                logger.warning(f"[{request_id}] RuntimeError during close: {e}")
                        except Exception:
                            pass

                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - query_start
                    total_cost += cost_usd
                    last_response = SDKResponse(
                        text=f"Error: Agent timed out after {elapsed:.0f}s",
                        is_error=True,
                        error_message=f"Timeout after {elapsed:.0f}s",
                        error_category=ErrorCategory.TRANSIENT,
                        cost_usd=total_cost,
                    )
                    if attempt > max_retries:
                        return last_response
                    await asyncio.sleep(min(1 * (2 ** (attempt - 1)), 8))
                    continue

                except RuntimeError as e:
                    if "cancel scope" in str(e):
                        logger.warning(f"[{request_id}] anyio cancel scope in attempt {attempt}")
                        combined = "\n\n".join(text_parts).strip()
                        total_cost += cost_usd
                        last_response = SDKResponse(
                            text=combined or "Agent interrupted (anyio error).",
                            session_id=result_session_id,
                            cost_usd=total_cost,
                            is_error=True,
                            error_message="anyio cancel scope error",
                            error_category=ErrorCategory.TRANSIENT,
                        )
                        if attempt > max_retries:
                            return last_response
                        await asyncio.sleep(1)
                        continue
                    raise

                except Exception as e:
                    total_cost += cost_usd
                    last_response = SDKResponse(
                        text=f"Error: {e}",
                        is_error=True,
                        error_message=str(e),
                        error_category=classify_error(str(e)),
                        cost_usd=total_cost,
                    )
                    if attempt > max_retries:
                        return last_response
                    await asyncio.sleep(2)
                    continue

                else:
                    # Stream ended without ResultMessage
                    combined = "\n\n".join(text_parts).strip()
                    total_cost += cost_usd
                    if combined:
                        return SDKResponse(
                            text=combined,
                            session_id=result_session_id,
                            cost_usd=total_cost,
                            duration_ms=duration_ms,
                            num_turns=num_turns,
                            is_error=False,
                            retry_count=attempt - 1,
                        )
                    # No text and no ResultMessage — retry
                    last_response = SDKResponse(
                        text="Agent produced no output (stream ended unexpectedly).",
                        is_error=True,
                        error_message="No ResultMessage received",
                        error_category=ErrorCategory.TRANSIENT,
                        cost_usd=total_cost,
                    )
                    if attempt > max_retries:
                        return last_response
                    await asyncio.sleep(2)
                    continue

            # All retries exhausted
            if last_response:
                return last_response
            return SDKResponse(
                text="Error: All retry attempts failed",
                is_error=True,
                error_message="All retries exhausted",
                error_category=ErrorCategory.UNKNOWN,
                cost_usd=total_cost,
            )

        return _do_query()

    # Start a background task to drain the stream queue and forward events
    drain_done = asyncio.Event()

    async def _drain_stream_queue():
        """Forward stream events from the isolated thread to real callbacks."""
        try:
            while True:
                try:
                    event = await asyncio.wait_for(stream_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if drain_done.is_set():
                        while not stream_queue.empty():
                            event = stream_queue.get_nowait()
                            await _dispatch_event(event)
                        return
                    continue

                await _dispatch_event(event)
        except asyncio.CancelledError:
            pass

    async def _dispatch_event(event: _StreamEvent):
        try:
            if event.kind == "stream" and on_stream:
                await on_stream(event.payload)
            elif event.kind == "tool_use" and on_tool_use:
                tool_name, tool_info, tool_input = event.payload
                await on_tool_use(tool_name, tool_info, tool_input)
        except Exception as e:
            logger.debug(f"[{request_id}] Callback dispatch error: {e}")

    drain_task = asyncio.create_task(_drain_stream_queue())

    try:
        # Run the query in an isolated thread with its own event loop
        result = await caller_loop.run_in_executor(
            _executor,
            _run_in_fresh_loop,
            _query_factory,
            stream_queue,
            caller_loop,
        )

        logger.info(
            f"[{request_id}] Isolated query finished: "
            f"ok={not result.is_error}, cost=${result.cost_usd:.4f}, "
            f"turns={result.num_turns}"
        )
        return result

    except Exception as e:
        logger.error(f"[{request_id}] Isolated query executor error: {e}", exc_info=True)
        return SDKResponse(
            text=f"Error in isolated query: {e}",
            is_error=True,
            error_message=str(e),
            error_category=ErrorCategory.UNKNOWN,
        )
    finally:
        drain_done.set()
        drain_task.cancel()
        try:
            await asyncio.wait_for(drain_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
