"""Isolated async worker that processes a single user-message task.

Each task gets its own:
- ``conversation_id`` — a new DB conversation so state never leaks between tasks.
- ``OrchestratorManager`` instance — no shared mutable state with concurrent tasks.
- Wrapped event callbacks — every event published to the bus carries ``task_id``
  so the frontend can route responses to the correct UI slot.

Race-condition safety
---------------------
``_conv_write_locks`` (``asyncio.Lock`` per conversation_id) ensures that the
user message and the final assistant response are never written concurrently to
the same conversation row.  Because each task creates its own conversation, the
lock is almost never contended; it is there to guard against hypothetical future
retry or multi-write paths.

Completion detection
--------------------
``OrchestratorManager.start_session()`` launches internal asyncio tasks and
returns immediately.  We therefore use an ``asyncio.Future`` that is resolved
by the ``on_final`` callback — the manager calls it when it emits its last
message.  ``asyncio.wait_for`` gives us a hard timeout via the
``AGENT_TIMEOUT_SECONDS`` env var (default 3600 s).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from src.workers.task_queue import TaskRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_AGENT_TIMEOUT_SECONDS: float = float(os.getenv("AGENT_TIMEOUT_SECONDS", "3600"))

# ---------------------------------------------------------------------------
# Per-conversation write locks (race-condition safety for DB persistence)
# ---------------------------------------------------------------------------

_conv_write_locks: dict[str, asyncio.Lock] = {}
_conv_write_locks_registry_lock = asyncio.Lock()


async def _get_conv_lock(conversation_id: str) -> asyncio.Lock:
    """Return (or lazily create) the asyncio.Lock for ``conversation_id``."""
    async with _conv_write_locks_registry_lock:
        if conversation_id not in _conv_write_locks:
            _conv_write_locks[conversation_id] = asyncio.Lock()
        return _conv_write_locks[conversation_id]


# ---------------------------------------------------------------------------
# Manager factory
# ---------------------------------------------------------------------------


def _create_task_manager(
    task_id: str,
    project_id: str,
    project_name: str,
    project_dir: str,
    user_id: int,
    completion_future: asyncio.Future[str],
    mode: str | None = None,
) -> Any:  # returns OrchestratorManager | None
    """Create an ephemeral OrchestratorManager with task_id injected into all events.

    The manager is **not** registered in global ``state.active_sessions`` so it
    does not interfere with project-level status.  Events flow directly to the
    shared ``event_bus`` (which all WebSocket clients subscribe to), tagged with
    ``task_id`` so the frontend can route them.

    Args:
        task_id:           Hex UUID for this task — injected into every event.
        project_id:        Project the task belongs to.
        project_name:      Human-readable project name (passed to manager).
        project_dir:       Filesystem path to the project directory.
        user_id:           Owner user ID.
        completion_future: Future resolved (with the final text) when the
                           manager emits its ``on_final`` callback.
        mode:              Execution mode — ``"autonomous"`` or ``"interactive"``.

    Returns:
        An ``OrchestratorManager`` instance, or ``None`` if ``state.sdk_client``
        or ``state.session_mgr`` are not yet initialised.
    """
    import state
    from dashboard.events import event_bus
    from orchestrator import OrchestratorManager  # type: ignore[import]

    sdk = state.sdk_client
    smgr = state.session_mgr

    if not sdk or not smgr:
        logger.error(
            "task_worker[%s]: sdk_client or session_mgr not initialised — cannot create manager",
            task_id,
        )
        return None

    # ------------------------------------------------------------------
    # Task-aware callbacks — every event published to the bus carries task_id
    # ------------------------------------------------------------------

    async def on_update(text: str) -> None:
        """Handle a progress-update callback from the orchestrator."""
        await event_bus.publish(
            {
                "type": "agent_update",
                "project_id": project_id,
                "project_name": project_name,
                "agent": manager.current_agent or "orchestrator",
                "text": text,
                "task_id": task_id,
                "timestamp": time.time(),
            }
        )

    async def on_result(text: str) -> None:
        """Handle a result callback from the orchestrator."""
        await event_bus.publish(
            {
                "type": "agent_result",
                "project_id": project_id,
                "project_name": project_name,
                "text": text,
                "task_id": task_id,
                "timestamp": time.time(),
            }
        )

    async def on_final(text: str) -> None:
        """Publish the final result and resolve the completion future."""
        await event_bus.publish(
            {
                "type": "agent_final",
                "project_id": project_id,
                "project_name": project_name,
                "text": text,
                "task_id": task_id,
                "timestamp": time.time(),
            }
        )
        # Signal task completion so the worker can proceed to persistence/cleanup
        if not completion_future.done():
            completion_future.set_result(text)

    async def on_event(event: dict[str, Any]) -> None:
        """Enrich every orchestrator event with project_id and task_id."""
        enriched = {
            **event,
            "project_id": project_id,
            "project_name": project_name,
            "task_id": task_id,
        }
        await event_bus.publish(enriched)

    manager = OrchestratorManager(
        project_name=project_name,
        project_dir=project_dir,
        sdk=sdk,
        session_mgr=smgr,
        user_id=user_id,
        project_id=project_id,
        on_update=on_update,
        on_result=on_result,
        on_final=on_final,
        on_event=on_event,
        multi_agent=True,
        **({"mode": mode} if mode else {}),
    )
    return manager


# ---------------------------------------------------------------------------
# Main worker coroutine
# ---------------------------------------------------------------------------


async def process_message_task(
    record: TaskRecord,
    *,
    project_name: str,
    project_dir: str,
    user_id: int,
    mode: str | None = None,
) -> None:
    """Process a single user message task in complete isolation.

    This coroutine is the ``worker_fn`` passed to ``ProjectTaskQueue.enqueue``.
    It:

    1. Creates a dedicated ``conversation_id`` in the DB for full state
       isolation (no cross-task state leakage).
    2. Persists the user message under a per-conversation lock.
    3. Creates an ephemeral ``OrchestratorManager`` with ``task_id`` woven
       into all event callbacks.
    4. Publishes a ``task_started`` event so the frontend can open a result
       slot before the agent begins streaming.
    5. Calls ``manager.start_session(message)`` and waits for the
       ``on_final`` callback (or a hard timeout).
    6. Persists the collected assistant response.
    7. Publishes ``task_done`` or ``task_failed`` events.

    Args:
        record:       The ``TaskRecord`` created by the task queue (status
                      updated in-place by the queue wrapper; this function
                      updates ``conversation_id`` only).
        project_name: Human-readable project name.
        project_dir:  Filesystem path to the project directory.
        user_id:      Owner user ID.
        mode:         Execution mode — ``"autonomous"`` or ``"interactive"``.

    Raises:
        Re-raises any exception so the task queue can mark the record
        as ``failed`` and log the traceback.
    """
    task_id = record.task_id
    project_id = record.project_id
    message = record.message

    from dashboard.events import event_bus
    from src.db.database import get_session_factory
    from src.storage.conversation_store import ConversationStore

    conv_store = ConversationStore(get_session_factory())

    # ------------------------------------------------------------------
    # 1. Create an isolated conversation in the DB
    # ------------------------------------------------------------------
    conv_id: str | None = None
    try:
        title = f"Task {task_id[:8]}: {message[:60]}"
        conv_id = await conv_store.create_conversation(project_id=project_id, title=title)
        record.conversation_id = conv_id
        logger.debug(
            "task_worker[%s]: created isolated conversation %s for project=%s",
            task_id,
            conv_id,
            project_id,
        )
    except Exception:
        logger.error(
            "task_worker[%s]: failed to create conversation — continuing without DB persistence",
            task_id,
            exc_info=True,
        )

    # ------------------------------------------------------------------
    # 2. Persist user message (under conversation lock)
    # ------------------------------------------------------------------
    if conv_id:
        conv_lock = await _get_conv_lock(conv_id)
        async with conv_lock:
            try:
                await conv_store.append_message(
                    conversation_id=conv_id,
                    role="user",
                    content=message,
                    metadata={"task_id": task_id},
                )
            except Exception:
                logger.warning(
                    "task_worker[%s]: failed to persist user message (non-fatal)",
                    task_id,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # 3. Publish task_started so the frontend opens a result slot
    # ------------------------------------------------------------------
    await event_bus.publish(
        {
            "type": "task_started",
            "project_id": project_id,
            "task_id": task_id,
            "conversation_id": conv_id,
            "message": message[:200],
            "timestamp": time.time(),
        }
    )

    # ------------------------------------------------------------------
    # 4. Create ephemeral manager with task_id woven into all events
    # ------------------------------------------------------------------
    completion_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
    manager = _create_task_manager(
        task_id=task_id,
        project_id=project_id,
        project_name=project_name,
        project_dir=project_dir,
        user_id=user_id,
        completion_future=completion_future,
        mode=mode,
    )

    if manager is None:
        err_msg = "OrchestratorManager could not be created (sdk/session_mgr not ready)"
        logger.error("task_worker[%s]: %s", task_id, err_msg)
        await event_bus.publish(
            {
                "type": "task_failed",
                "project_id": project_id,
                "task_id": task_id,
                "error": err_msg,
                "timestamp": time.time(),
            }
        )
        raise RuntimeError(err_msg)

    # ------------------------------------------------------------------
    # 5. Run agent — start_session() launches internal asyncio tasks and
    #    returns quickly; we wait for on_final via the completion_future.
    # ------------------------------------------------------------------
    final_text: str = ""
    try:
        await manager.start_session(message)
        logger.debug(
            "task_worker[%s]: start_session returned — waiting for on_final (timeout=%ss)",
            task_id,
            _AGENT_TIMEOUT_SECONDS,
        )
        final_text = await asyncio.wait_for(
            asyncio.shield(completion_future),
            timeout=_AGENT_TIMEOUT_SECONDS,
        )
        logger.info(
            "task_worker[%s]: agent completed — response length=%d chars",
            task_id,
            len(final_text),
        )
    except TimeoutError:
        err = f"Agent timed out after {_AGENT_TIMEOUT_SECONDS:.0f}s"
        logger.error("task_worker[%s]: %s", task_id, err)
        # Stop the orchestrator — asyncio.shield prevents wait_for from
        # cancelling the future, so we must explicitly stop the manager
        # to clean up running SDK sessions and the DAG executor.
        try:
            await manager.stop()
        except Exception as stop_err:
            logger.warning("task_worker[%s]: manager.stop() failed: %s", task_id, stop_err)
        await event_bus.publish(
            {
                "type": "task_failed",
                "project_id": project_id,
                "task_id": task_id,
                "error": err,
                "timestamp": time.time(),
            }
        )
        raise TimeoutError(err)
    except Exception as exc:
        logger.error(
            "task_worker[%s]: agent execution error — %s",
            task_id,
            exc,
            exc_info=True,
        )
        await event_bus.publish(
            {
                "type": "task_failed",
                "project_id": project_id,
                "task_id": task_id,
                "error": str(exc)[:500],
                "timestamp": time.time(),
            }
        )
        raise

    # ------------------------------------------------------------------
    # 6. Persist assistant response (under conversation lock)
    # ------------------------------------------------------------------
    if conv_id and final_text:
        conv_lock = await _get_conv_lock(conv_id)
        async with conv_lock:
            try:
                await conv_store.append_message(
                    conversation_id=conv_id,
                    role="assistant",
                    content=final_text,
                    metadata={"task_id": task_id},
                )
            except Exception:
                logger.warning(
                    "task_worker[%s]: failed to persist assistant response (non-fatal)",
                    task_id,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # 7. Publish task_done so the frontend can close the streaming slot
    # ------------------------------------------------------------------
    await event_bus.publish(
        {
            "type": "task_done",
            "project_id": project_id,
            "task_id": task_id,
            "conversation_id": conv_id,
            "timestamp": time.time(),
        }
    )
    logger.info(
        "task_worker[%s]: task_done published for project=%s conv=%s",
        task_id,
        project_id,
        conv_id,
    )
