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

This approach is lighter than subprocess isolation (no pickling, no IPC)
while still providing full event-loop isolation.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from sdk_client import ClaudeSDKManager, SDKResponse, ErrorCategory

logger = logging.getLogger(__name__)

# Thread pool for isolated queries.  Each thread gets its own event loop.
# Size matches the connection pool in sdk_client.py (max 5 concurrent).
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
    sdk: ClaudeSDKManager,
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

    Callbacks (``on_stream``, ``on_tool_use``) are bridged back to the
    caller's event loop via ``call_soon_threadsafe``.
    """
    caller_loop = asyncio.get_running_loop()
    request_id = f"iso_{int(time.monotonic() * 1000) % 100000}"

    logger.info(
        f"[{request_id}] Starting isolated query: "
        f"max_turns={max_turns}, budget=${max_budget_usd}"
    )

    # Bridge callbacks from the isolated thread back to the caller's loop.
    # We create simple wrappers that use call_soon_threadsafe to schedule
    # the real async callbacks on the caller's loop.

    # Queue for streaming events from isolated thread → caller
    stream_queue: asyncio.Queue[_StreamEvent] = asyncio.Queue()

    def _make_bridged_stream_cb():
        """Create a stream callback that bridges to the caller's loop."""
        if on_stream is None:
            return None

        async def _bridged_stream(text: str):
            # We're inside the isolated loop — push event to queue
            # and let the caller's loop pick it up
            try:
                caller_loop.call_soon_threadsafe(
                    stream_queue.put_nowait,
                    _StreamEvent(kind="stream", payload=text),
                )
            except Exception:
                pass  # Caller loop may be closing

        return _bridged_stream

    def _make_bridged_tool_cb():
        """Create a tool_use callback that bridges to the caller's loop."""
        if on_tool_use is None:
            return None

        async def _bridged_tool(tool_name: str, tool_info: str, tool_input: dict):
            try:
                caller_loop.call_soon_threadsafe(
                    stream_queue.put_nowait,
                    _StreamEvent(kind="tool_use", payload=(tool_name, tool_info, tool_input)),
                )
            except Exception:
                pass

        return _bridged_tool

    # Create a fresh SDK manager for the isolated loop (each has its own pool).
    # We don't reuse the caller's manager because its internal state (generators
    # set, pool semaphore) is tied to the caller's event loop.
    def _query_factory():
        """Factory that creates the coroutine to run in the isolated loop."""
        isolated_sdk = ClaudeSDKManager()

        return isolated_sdk.query_with_retry(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=cwd,
            session_id=session_id,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            max_retries=max_retries,
            permission_mode=permission_mode,
            on_stream=_make_bridged_stream_cb(),
            on_tool_use=_make_bridged_tool_cb(),
            allowed_tools=allowed_tools,
            tools=tools,
        )

    # Start a background task to drain the stream queue and forward events
    # to the real callbacks on the caller's loop.
    drain_done = asyncio.Event()

    async def _drain_stream_queue():
        """Forward stream events from the isolated thread to real callbacks."""
        try:
            while True:
                try:
                    event = await asyncio.wait_for(stream_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if drain_done.is_set():
                        # Drain remaining events
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
        # Signal the drain task to finish and wait for it
        drain_done.set()
        drain_task.cancel()
        try:
            await asyncio.wait_for(drain_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
