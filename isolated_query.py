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
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from config import ASYNC_WAIT_TIMEOUT, POLL_RETRY_DELAY
from sdk_client import ErrorCategory, SDKResponse, classify_error

logger = logging.getLogger(__name__)

# Thread pool for isolated queries.  Each thread gets its own event loop.
# Size matches max concurrent agents (10 roles).  This IS the concurrency
# limiter — no need for the asyncio.Semaphore pool inside the thread.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=10,
    thread_name_prefix="isolated-sdk",
)


@dataclass
class _StreamEvent:
    """A message passed from the isolated thread back to the caller."""

    kind: str  # "stream" | "tool_use" | "done" | "error"
    payload: Any = None


def _run_in_fresh_loop(coro_factory: Callable[[], Awaitable[SDKResponse]]) -> SDKResponse:
    """Run *coro_factory()* in a brand-new event loop on the current thread.

    This is the function that executes inside the thread pool.  It creates
    a fresh ``asyncio.run()`` so the anyio cancel-scope leak cannot infect
    the caller's event loop.

    ROOT CAUSE FIX (BUG-25b): We disable the async generator finalizer on
    the isolated loop.  Without this, Python's GC can finalize the SDK's
    async generator *after* the isolated loop has closed, which triggers
    anyio's cancel-scope cleanup on the MAIN event loop — injecting a
    CancelledError into unrelated tasks.  By disabling the finalizer,
    the generator is simply discarded without triggering anyio cleanup.
    ``asyncio.run()`` still calls ``shutdown_asyncgens()`` as part of its
    normal teardown, which handles cleanup safely within the same task
    context.
    """

    async def _inner():
        # Disable async generator finalizer for THIS loop.
        # This prevents Python's GC from calling aclose() on generators
        # that outlive this loop — which is the root cause of the anyio
        # cancel-scope CancelledError leak.
        # NOTE: set_asyncgen_hooks is on sys, NOT on the event loop.
        # Passing finalizer=None means "don't change" — use a no-op lambda instead.
        import sys

        sys.set_asyncgen_hooks(firstiter=None, finalizer=lambda agen: None)
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
    per_message_timeout: int | None = None,
    model: str | None = None,
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
        f"max_turns={max_turns}, budget=${max_budget_usd}, "
        f"per_message_timeout={per_message_timeout}s"
    )

    # Queue for streaming events from isolated thread → caller.
    # Bounded to prevent unbounded memory growth if the drain task falls behind.
    stream_queue: asyncio.Queue[_StreamEvent] = asyncio.Queue(maxsize=500)

    def _safe_enqueue(event: _StreamEvent):
        """Enqueue without raising QueueFull — drops events under backpressure."""
        try:
            stream_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # Drop event under backpressure

    def _make_bridged_stream_cb():
        """Create a stream callback that bridges to the caller's loop."""
        if on_stream is None:
            return None

        async def _bridged_stream(text: str):
            try:
                caller_loop.call_soon_threadsafe(
                    _safe_enqueue, _StreamEvent(kind="stream", payload=text)
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
                    _safe_enqueue,
                    _StreamEvent(kind="tool_use", payload=(tool_name, tool_info, tool_input)),
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
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.types import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
        )

        # Import the CLI path resolution from sdk_client
        from sdk_client import SYSTEM_CLI_PATH, _make_project_guard

        # Use role-specific timeout if provided, otherwise fall back to global

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
                    # can_use_tool requires streaming mode — always True.
                    include_partial_messages=True,
                    sandbox={"enabled": False},
                    can_use_tool=_make_project_guard(cwd),
                )
                if permission_mode:
                    options.permission_mode = permission_mode
                if allowed_tools is not None:
                    options.allowed_tools = allowed_tools
                if tools is not None:
                    options.tools = tools
                if current_session:
                    options.resume = current_session
                if model:
                    options.model = model

                query_start = time.monotonic()
                text_parts: list[str] = []
                result_session_id = ""
                cost_usd = 0.0
                duration_ms = 0
                num_turns = 0
                last_seen_text = ""
                tool_uses: list[str] = []

                try:
                    from claude_agent_sdk import ClaudeSDKClient
                    from claude_agent_sdk._internal.message_parser import parse_message

                    logger.info(f"[{request_id}] Creating ClaudeSDKClient...")
                    client = ClaudeSDKClient(options)
                    try:
                        logger.info(f"[{request_id}] Calling client.connect()...")
                        await client.connect()
                        logger.info(
                            f"[{request_id}] client.connect() done, calling client.query()..."
                        )
                        await client.query(prompt)
                        logger.info(f"[{request_id}] client.query() done, starting message loop...")

                        async for raw_data in client._query.receive_messages():
                            try:
                                message = parse_message(raw_data)
                            except Exception:
                                continue
                            if message is None:
                                continue

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
                                            path = tool_input_data.get(
                                                "file_path"
                                            ) or tool_input_data.get("path", "")
                                            tool_info = f"📄 Reading: {path}"
                                        elif tool_name in ("Write", "write_file", "create_file"):
                                            path = tool_input_data.get(
                                                "file_path"
                                            ) or tool_input_data.get("path", "")
                                            tool_info = f"✏️ Writing: {path}"
                                        elif tool_name in ("Edit", "edit_file"):
                                            path = tool_input_data.get(
                                                "file_path"
                                            ) or tool_input_data.get("path", "")
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
                                                logger.debug(
                                                    "bridged_tool callback failed", exc_info=True
                                                )

                                if bridged_stream and (turn_text != last_seen_text or tool_info):
                                    try:
                                        update = ""
                                        if tool_info:
                                            update = tool_info
                                        if turn_text and turn_text != last_seen_text:
                                            new_text = turn_text[len(last_seen_text) :]
                                            preview = (
                                                new_text[-300:] if len(new_text) > 300 else new_text
                                            )
                                            update = f"{update}\n\n{preview}" if update else preview
                                        if update:
                                            await bridged_stream(update)
                                    except Exception:
                                        logger.debug(
                                            "bridged_stream callback failed", exc_info=True
                                        )
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
                                    tools_summary = (
                                        ", ".join(set(tool_uses)) if tool_uses else "unknown"
                                    )
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
                                    error_message=""
                                    if not message.is_error
                                    else (message.result or "Unknown error"),
                                    error_category=classify_error(message.result or "")
                                    if message.is_error
                                    else ErrorCategory.UNKNOWN,
                                    retry_count=attempt - 1,
                                    tool_uses=list(tool_uses) if tool_uses else None,
                                )

                                if not response.is_error:
                                    return response

                                # Check if retryable
                                last_response = response
                                cat = response.error_category
                                if cat in (
                                    ErrorCategory.AUTH,
                                    ErrorCategory.BUDGET,
                                    ErrorCategory.PERMANENT,
                                ):
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
                                    await asyncio.sleep(POLL_RETRY_DELAY)
                                break  # Break inner while to retry outer for

                    finally:
                        try:
                            await client.disconnect()
                        except Exception as _dc_err:
                            logger.debug("[%s] Client disconnect error: %s", request_id, _dc_err)
                        last_tool = tool_uses[-1] if tool_uses else "none"
                        logger.info(
                            f"[{request_id}] SDK client finished. "
                            f"Last tool={last_tool}, turns={num_turns}, "
                            f"elapsed={time.monotonic() - query_start:.1f}s"
                        )

                except TimeoutError:
                    elapsed = time.monotonic() - query_start
                    total_cost += cost_usd
                    last_response = SDKResponse(
                        text=f"Error: Agent timed out after {elapsed:.0f}s",
                        is_error=True,
                        error_message=f"Timeout after {elapsed:.0f}s",
                        error_category=ErrorCategory.TRANSIENT,
                        cost_usd=total_cost,
                        tool_uses=list(tool_uses) if tool_uses else None,
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
                            tool_uses=list(tool_uses) if tool_uses else None,
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
                        tool_uses=list(tool_uses) if tool_uses else None,
                    )
                    if attempt > max_retries:
                        return last_response
                    await asyncio.sleep(POLL_RETRY_DELAY)
                    continue

                else:
                    # If we already got a ResultMessage error and broke out for retry,
                    # skip this else clause and continue to the next attempt.
                    if last_response is not None and last_response.is_error:
                        continue
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
                            tool_uses=list(tool_uses) if tool_uses else None,
                        )
                    # No text and no ResultMessage — retry
                    last_response = SDKResponse(
                        text="Agent produced no output (stream ended unexpectedly).",
                        is_error=True,
                        error_message="No ResultMessage received",
                        error_category=ErrorCategory.TRANSIENT,
                        cost_usd=total_cost,
                        tool_uses=list(tool_uses) if tool_uses else None,
                    )
                    if attempt > max_retries:
                        return last_response
                    await asyncio.sleep(POLL_RETRY_DELAY)
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
                except TimeoutError:
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
            logger.warning(f"[{request_id}] Callback dispatch error: {e}")

    drain_task = asyncio.create_task(_drain_stream_queue())

    # Non-cancellable wait via asyncio.Event (not shield — shield doesn't
    # protect the outer coroutine from CancelledError).

    _done_event = asyncio.Event()
    _executor_result: list[SDKResponse] = []  # mutable container for thread result
    _executor_error: list[BaseException] = []  # mutable container for thread error

    def _on_cf_done(cf_fut: concurrent.futures.Future) -> None:  # type: ignore[type-arg]
        """CF.Future callback (executor thread). Always signals _done_event."""
        try:
            _executor_result.append(cf_fut.result())
        except BaseException as exc:  # ← catches CancelledError (BaseException)
            _executor_error.append(exc)
        finally:
            # Always signal, even on error — the while-loop below must exit.
            caller_loop.call_soon_threadsafe(_done_event.set)

    cf_future = _executor.submit(_run_in_fresh_loop, _query_factory)
    cf_future.add_done_callback(_on_cf_done)

    # Overall timeout: if per_message_timeout is set, use it as the hard
    # wall-clock limit for the entire isolated query.  Without this, the
    # executor thread can run forever (the root cause of 18-minute hangs).
    _overall_timeout = per_message_timeout or 300  # default 5 min
    _timeout_hit = False
    _wait_start = time.monotonic()

    try:
        # Wait for executor with timeout, limited cancel retries
        max_cancel_retries = 10
        cancel_hits = 0
        while True:
            if time.monotonic() - _wait_start > _overall_timeout:
                _timeout_hit = True
                logger.warning(f"[{request_id}] Isolated query TIMEOUT after {_overall_timeout}s")
                cf_future.cancel()
                break
            try:
                await asyncio.wait_for(_done_event.wait(), timeout=5.0)
                break
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                cancel_hits += 1
                if cancel_hits > max_cancel_retries:
                    logger.error(
                        f"[{request_id}] Too many CancelledErrors ({cancel_hits}) — giving up"
                    )
                    cf_future.cancel()
                    _timeout_hit = True
                    break
                if _done_event.is_set():
                    break
                logger.warning(f"[{request_id}] CancelledError during wait (hit {cancel_hits}x)")
                await asyncio.sleep(0.1)

        # ── Timeout hit — return error immediately ──
        if _timeout_hit:
            return SDKResponse(
                text=f"Error: Isolated query timed out after {_overall_timeout}s",
                is_error=True,
                error_message=f"Timeout after {_overall_timeout}s",
                error_category=ErrorCategory.TRANSIENT,
            )

        # ── Executor finished — extract result ──
        if cancel_hits:
            logger.info(
                f"[{request_id}] Executor completed despite {cancel_hits} "
                f"CancelledError interruptions"
            )

        if _executor_error:
            exc = _executor_error[0]
            logger.error(f"[{request_id}] Isolated query executor error: {exc}", exc_info=exc)
            return SDKResponse(
                text=f"Error in isolated query: {exc}",
                is_error=True,
                error_message=str(exc),
                error_category=ErrorCategory.UNKNOWN,
            )

        result = (
            _executor_result[0]
            if _executor_result
            else SDKResponse(
                text="Executor completed with no result",
                is_error=True,
                error_message="No result from executor",
                error_category=ErrorCategory.UNKNOWN,
            )
        )

        logger.info(
            f"[{request_id}] Isolated query finished: "
            f"ok={not result.is_error}, cost=${result.cost_usd:.4f}, "
            f"turns={result.num_turns}"
        )
        return result

    except Exception as e:
        logger.error(f"[{request_id}] Isolated query unexpected error: {e}", exc_info=True)
        return SDKResponse(
            text=f"Error in isolated query: {e}",
            is_error=True,
            error_message=str(e),
            error_category=ErrorCategory.UNKNOWN,
        )
    finally:
        # Signal drain task to stop, then wait for it to finish.
        # Drain pending cancellations (bounded) so our awaits don't fail.
        ct = asyncio.current_task()
        _fin_drained = 0
        if ct and hasattr(ct, "uncancel"):
            while ct.cancelling() > 0 and _fin_drained < 10:
                ct.uncancel()
                _fin_drained += 1
        drain_done.set()
        try:
            await asyncio.wait_for(drain_task, timeout=ASYNC_WAIT_TIMEOUT)
        except (TimeoutError, asyncio.CancelledError):
            drain_task.cancel()
            try:
                await drain_task
            except (asyncio.CancelledError, Exception):
                pass
