"""Per-project asyncio task queue for parallel message processing.

Each project gets its own ``ProjectTaskQueue`` backed by an ``asyncio.Queue``
and an ``asyncio.Semaphore`` that limits concurrency to
``PARALLEL_TASKS_LIMIT`` (env var, default 5).

Design principles
-----------------
- **No external broker** — pure asyncio, no Redis/Celery required for MVP.
- **Isolated workers** — each task is given its own agent context slice via a
  caller-supplied ``worker_fn`` coroutine; tasks cannot mutate each other's
  state.
- **Task_id first** — every enqueued message gets a UUID ``task_id`` before
  any work begins, so callers can return it to the frontend immediately.
- **Status lifecycle** — queued → running → done | failed, queryable by REST.

Usage::

    registry = TaskQueueRegistry.get_registry()
    queue = await registry.get_or_create_queue(project_id)
    record = await queue.enqueue(
        message="fix the login bug",
        worker_fn=process_message_task,
        project_name="my-api",
        project_dir="/home/user/my-api",
        user_id=0,
    )
    # record.task_id is ready immediately — return it in the HTTP response.
    # The worker runs in the background under the semaphore.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    pass  # forward refs only

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PARALLEL_TASKS_LIMIT: int = int(os.getenv("PARALLEL_TASKS_LIMIT", "5"))

# Max number of completed/failed tasks to retain in memory per project.
# Oldest tasks are pruned when the limit is exceeded.
_MAX_TASK_HISTORY: int = 200


# ---------------------------------------------------------------------------
# Task status & record
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """Lifecycle states for a queued task."""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


@dataclass
class TaskRecord:
    """Immutable identity + mutable status for one user-message task.

    ``task_id`` is a hex UUID generated at enqueue time.  It is safe to return
    to the client before any agent work has started.

    Attributes:
        task_id:         Hex UUID string — unique across the process lifetime.
        project_id:      Project this task belongs to.
        message:         Original user message (truncated to 500 chars in API
                         responses; full text stored here for the worker).
        status:          Current lifecycle stage.
        conversation_id: UUID of the isolated conversation created for this
                         task (populated by the worker after DB write).
        created_at:      Unix timestamp when the task was enqueued.
        started_at:      Unix timestamp when execution began (None if queued).
        completed_at:    Unix timestamp when done/failed (None if not finished).
        error:           Short error string if status==failed, else None.
    """

    task_id: str
    project_id: str
    message: str
    status: TaskStatus = TaskStatus.queued
    conversation_id: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None

    # Internal: worker callable and kwargs attached at enqueue time.
    # Not part of the public API — accessed only by _ProjectTaskQueue._run_task.
    _worker_fn: Any = field(default=None, repr=False, compare=False)
    _worker_kwargs: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict suitable for API responses."""
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "message": self.message[:500],
            "status": self.status.value,
            "conversation_id": self.conversation_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Per-project queue
# ---------------------------------------------------------------------------


class ProjectTaskQueue:
    """Bounded-concurrency task queue for a single project.

    Internally uses an ``asyncio.Queue`` for ordering and an
    ``asyncio.Semaphore`` to cap concurrent execution.  A background dispatch
    loop drains the queue and launches worker coroutines as semaphore slots
    become available.

    Args:
        project_id:     Project this queue belongs to.
        max_concurrent: Maximum number of tasks that may run simultaneously.
                        Defaults to ``PARALLEL_TASKS_LIMIT`` (env var).
    """

    def __init__(
        self,
        project_id: str,
        max_concurrent: int = PARALLEL_TASKS_LIMIT,
    ) -> None:
        self.project_id = project_id
        self.max_concurrent = max_concurrent

        self._queue: asyncio.Queue[TaskRecord] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, TaskRecord] = {}  # task_id → record
        self._tasks_lock = asyncio.Lock()
        self._dispatch_task: asyncio.Task[None] | None = None
        self._running = False
        self._active_tasks: set[asyncio.Task] = set()  # prevent GC of running tasks

        # Project-level write lock — ensures only one code-writing task runs
        # at a time within a project.  Multiple DAGs writing to the same
        # project_dir concurrently causes file conflicts and race conditions
        # (Google Research 2026: uncontrolled parallelism increases errors 17x).
        # All tasks acquire this lock since any user message may trigger code
        # changes via the orchestrator/DAG pipeline.
        self._project_write_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background dispatch loop (idempotent)."""
        if self._running:
            return
        self._running = True
        self._dispatch_task = asyncio.ensure_future(self._dispatch_loop())
        logger.info(
            "ProjectTaskQueue[%s]: started (max_concurrent=%d)",
            self.project_id,
            self.max_concurrent,
        )

    async def stop(self) -> None:
        """Stop the dispatch loop gracefully."""
        self._running = False
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        logger.info("ProjectTaskQueue[%s]: stopped", self.project_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        message: str,
        worker_fn: Callable[..., Coroutine[Any, Any, None]],
        **worker_kwargs: Any,
    ) -> TaskRecord:
        """Enqueue a new message task and return a ``TaskRecord`` immediately.

        The returned record has ``status=queued`` and a fresh ``task_id``.
        Actual execution begins once a semaphore slot is available.

        Args:
            message:    The user message text to process.
            worker_fn:  Async coroutine function to call as
                        ``await worker_fn(record, **worker_kwargs)``.
                        Must update ``record.conversation_id`` if it creates
                        one.
            **worker_kwargs: Extra keyword arguments forwarded to ``worker_fn``.

        Returns:
            The newly created ``TaskRecord``.
        """
        task_id = uuid.uuid4().hex
        record = TaskRecord(
            task_id=task_id,
            project_id=self.project_id,
            message=message,
            _worker_fn=worker_fn,
            _worker_kwargs=worker_kwargs,
        )
        async with self._tasks_lock:
            self._tasks[task_id] = record
            # Prune oldest completed tasks to cap memory usage
            if len(self._tasks) > _MAX_TASK_HISTORY:
                self._evict_oldest()
        await self._queue.put(record)
        logger.info(
            "ProjectTaskQueue[%s]: enqueued task_id=%s queue_depth=%d",
            self.project_id,
            task_id,
            self._queue.qsize(),
        )
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        """Return the ``TaskRecord`` for a ``task_id``, or ``None``."""
        async with self._tasks_lock:
            return self._tasks.get(task_id)

    async def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to ``limit`` tasks, newest first (by ``created_at``)."""
        async with self._tasks_lock:
            records = list(self._tasks.values())
        records.sort(key=lambda r: r.created_at, reverse=True)
        return [r.to_dict() for r in records[:limit]]

    @property
    def queue_depth(self) -> int:
        """Number of tasks currently waiting in the queue (not yet running)."""
        return self._queue.qsize()

    @property
    def running_count(self) -> int:
        """Number of tasks currently being executed (semaphore slots used)."""
        return self.max_concurrent - self._semaphore._value  # type: ignore[attr-defined]

    def queue_position_of(self, task_id: str) -> int:
        """Return the 1-indexed queue position of a task, or 0 if not queued.

        Position is computed from all tasks with status ``queued``, ordered by
        ``created_at``.  A task that is already running or finished returns 0.
        """
        queued = sorted(
            (r for r in self._tasks.values() if r.status == TaskStatus.queued),
            key=lambda r: r.created_at,
        )
        for idx, rec in enumerate(queued, 1):
            if rec.task_id == task_id:
                return idx
        return 0

    def estimated_wait_seconds(self, queue_position: int) -> float:
        """Estimate wait time based on queue position and recent task durations.

        Uses the average duration of the last 10 completed tasks as the per-task
        estimate.  Falls back to 30 seconds if no history is available.

        Args:
            queue_position: 1-indexed position in the queue.

        Returns:
            Estimated seconds until this task starts executing.
        """
        if queue_position <= 0:
            return 0.0

        # Calculate average task duration from recent completions
        completed = [
            r
            for r in self._tasks.values()
            if r.status in (TaskStatus.done, TaskStatus.failed)
            and r.started_at is not None
            and r.completed_at is not None
        ]
        if completed:
            completed.sort(key=lambda r: r.completed_at, reverse=True)  # type: ignore[arg-type]
            recent = completed[:10]
            avg_duration = sum(
                (r.completed_at - r.started_at)
                for r in recent  # type: ignore[operator]
            ) / len(recent)
        else:
            avg_duration = 30.0  # default estimate

        # Tasks ahead in queue / max_concurrent slots = batches to wait
        batches_ahead = max(0, (queue_position - 1)) / max(self.max_concurrent, 1)
        return round(batches_ahead * avg_duration, 1)

    async def get_queue_state(self) -> dict[str, Any]:
        """Return structured queue state for the /queue GET endpoint.

        Returns a dict with:
        - pending_tasks: list of queued task summaries
        - active_tasks: list of currently running task summaries
        - queue_depth: number of pending tasks
        - running_count: number of running tasks
        - max_concurrent: concurrency limit
        - estimated_drain_seconds: estimated time to clear the queue
        """
        async with self._tasks_lock:
            all_tasks = list(self._tasks.values())

        pending = sorted(
            (r for r in all_tasks if r.status == TaskStatus.queued),
            key=lambda r: r.created_at,
        )
        active = [r for r in all_tasks if r.status == TaskStatus.running]

        now = time.time()
        pending_summaries = [
            {
                "task_id": r.task_id,
                "message_preview": r.message[:100],
                "queue_position": idx,
                "created_at": r.created_at,
                "estimated_wait_seconds": self.estimated_wait_seconds(idx),
            }
            for idx, r in enumerate(pending, 1)
        ]
        active_summaries = [
            {
                "task_id": r.task_id,
                "message_preview": r.message[:100],
                "started_at": r.started_at,
                "elapsed_seconds": round(now - r.started_at, 1) if r.started_at else 0,
            }
            for r in active
        ]

        total_drain = self.estimated_wait_seconds(len(pending) + 1) if pending else 0.0

        return {
            "pending_tasks": pending_summaries,
            "active_tasks": active_summaries,
            "queue_depth": len(pending),
            "running_count": len(active),
            "max_concurrent": self.max_concurrent,
            "estimated_drain_seconds": total_drain,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_oldest(self) -> None:
        """Remove the oldest finished tasks until we are under the limit.

        Called under ``self._tasks_lock``.  Only removes ``done`` or ``failed``
        tasks — in-flight tasks are never evicted.
        """
        finished = sorted(
            (r for r in self._tasks.values() if r.status in (TaskStatus.done, TaskStatus.failed)),
            key=lambda r: r.created_at,
        )
        to_remove = len(self._tasks) - _MAX_TASK_HISTORY
        for record in finished[:to_remove]:
            del self._tasks[record.task_id]

    async def _dispatch_loop(self) -> None:
        """Drain the queue, launching each task under the concurrency semaphore."""
        while self._running:
            try:
                try:
                    record = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except TimeoutError:
                    continue  # poll again to check _running flag

                # Launch the task as an independent async coroutine.
                # The semaphore is acquired inside _run_task so that the
                # dispatch loop itself never blocks — it can keep picking up
                # new tasks even while slots are all occupied.
                _task = asyncio.create_task(self._run_task(record))
                self._active_tasks.add(_task)
                _task.add_done_callback(self._active_tasks.discard)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error(
                    "ProjectTaskQueue[%s]: dispatch loop error",
                    self.project_id,
                    exc_info=True,
                )

    async def _run_task(self, record: TaskRecord) -> None:
        """Execute a task under the concurrency semaphore + project write lock.

        The concurrency semaphore limits how many tasks are actively running,
        while the project write lock ensures only one task writes to the
        project directory at a time (preventing file conflicts between
        concurrent DAG executions).
        """
        # Acquire semaphore — blocks here if max_concurrent tasks are running
        async with self._semaphore:
            # Acquire project write lock — serialises code-writing within project
            async with self._project_write_lock:
                record.status = TaskStatus.running
                record.started_at = time.time()
                logger.info(
                    "ProjectTaskQueue[%s]: starting task_id=%s (write_lock acquired)",
                    self.project_id,
                    record.task_id,
                )
                try:
                    await record._worker_fn(record, **record._worker_kwargs)
                    record.status = TaskStatus.done
                    logger.info(
                        "ProjectTaskQueue[%s]: task_id=%s completed successfully",
                        self.project_id,
                        record.task_id,
                    )
                except asyncio.CancelledError:
                    record.status = TaskStatus.failed
                    record.error = "Task cancelled during execution"
                    logger.warning(
                        "ProjectTaskQueue[%s]: task_id=%s cancelled",
                        self.project_id,
                        record.task_id,
                    )
                    raise
                except Exception as exc:
                    record.status = TaskStatus.failed
                    record.error = str(exc)[:500]
                    logger.error(
                        "ProjectTaskQueue[%s]: task_id=%s failed — %s",
                        self.project_id,
                        record.task_id,
                        exc,
                        exc_info=True,
                    )
                finally:
                    record.completed_at = time.time()
                    self._queue.task_done()


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------


class TaskQueueRegistry:
    """Singleton registry mapping ``project_id`` → ``ProjectTaskQueue``.

    Use ``TaskQueueRegistry.get_registry()`` to obtain the singleton, then
    ``await registry.get_or_create_queue(project_id)`` to get (or lazily
    create) the queue for a project.

    The registry is process-scoped and survives across requests.  Queues are
    created with ``start()`` called automatically.
    """

    _instance: ClassVar[TaskQueueRegistry | None] = None

    def __init__(self) -> None:
        self._queues: dict[str, ProjectTaskQueue] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_registry(cls) -> TaskQueueRegistry:
        """Return the process-level singleton (creates it on first call)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_or_create_queue(self, project_id: str) -> ProjectTaskQueue:
        """Return the ``ProjectTaskQueue`` for ``project_id``, creating it if needed."""
        async with self._lock:
            if project_id not in self._queues:
                queue = ProjectTaskQueue(project_id)
                queue.start()
                self._queues[project_id] = queue
                logger.debug("TaskQueueRegistry: created queue for project=%s", project_id)
            return self._queues[project_id]

    async def get_queue(self, project_id: str) -> ProjectTaskQueue | None:
        """Return the ``ProjectTaskQueue`` for ``project_id``, or ``None``."""
        async with self._lock:
            return self._queues.get(project_id)

    async def list_queues(self) -> list[dict[str, Any]]:
        """Return a summary of all active queues (for debugging/monitoring)."""
        async with self._lock:
            queues = list(self._queues.values())
        return [
            {
                "project_id": q.project_id,
                "queue_depth": q.queue_depth,
                "running_count": q.running_count,
                "max_concurrent": q.max_concurrent,
            }
            for q in queues
        ]

    async def stop_all(self) -> None:
        """Stop all queues — call on server shutdown."""
        async with self._lock:
            queues = list(self._queues.values())
        for queue in queues:
            await queue.stop()
        async with self._lock:
            self._queues.clear()
        logger.info("TaskQueueRegistry: all queues stopped")
