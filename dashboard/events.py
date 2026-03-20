"""Central pub/sub EventBus for broadcasting agent activity to WebSocket clients.

Enhanced with:
- Activity persistence: every event is saved to DB for cross-device sync
- Sequence IDs: monotonic per-project counter for gap-free replay
- In-memory ring buffer: fast replay for recent reconnects without DB hit
- Batch write queue: non-blocking DB writes to avoid slowing the publisher
- Event throttling: rate-limits high-frequency events (agent_text_chunk)
- Granular event types: tool_start/tool_end, agent_thinking, agent_eta
- Request-ID propagation: events carry the originating request_id for tracing
- Critical event overflow buffer: never silently drop critical events
- Subscriber health monitoring: per-client delivery latency tracking
- Dynamic ring buffer sizing: scales with active project count (500–5000)
- Reconnect-replay protocol: sequence_id range replay for clients
- DB write fallback: local file persistence when DB writes fail
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import itertools
import json as _json
import logging
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config import (
    CORRELATION_ID_HEADER,
    EVENTBUS_FLUSH_TIMEOUT,
    EVENTBUS_MAX_BACKLOG_SIZE,
    EVENT_QUEUE_TIMEOUT,
    FALLBACK_MAX_AGE_HOURS,
    FALLBACK_MAX_FILES,
)

if TYPE_CHECKING:
    from src.storage.platform_session import PlatformSessionManager as SessionManager

# Type alias for the async status callback used by heartbeat.
# Must return a dict with at least {"status": str, "active_agents": int}.
# Enhanced: may also return "agents" (list of per-agent dicts) and
# "last_progress_ts" (float timestamp of last meaningful progress).
StatusFn = Callable[[], Coroutine[Any, Any, dict]]

logger = logging.getLogger(__name__)

# ContextVar for request-ID tracing.
# Set this in HTTP request middleware so all EventBus.publish() calls made
# during that request automatically carry the originating request_id.
current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_request_id", default=""
)

# ContextVar for correlation-ID distributed tracing.
# Propagated across all EventBus publish/subscribe boundaries and logged
# in structured JSON format for end-to-end request tracing in ELK/Datadog.
current_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_correlation_id", default=""
)

# How many consecutive publish failures before a subscriber is considered dead
_MAX_CONSECUTIVE_FAILURES = 10

# Heartbeat interval in seconds — how often the status heartbeat fires
_HEARTBEAT_INTERVAL_SECONDS = 5

# Ring buffer size bounds — dynamically scaled between min and max
# based on the number of active projects (see _compute_ring_buffer_size)
_RING_BUFFER_MIN = 500
_RING_BUFFER_MAX = 5000

# Critical event types that must NEVER be silently dropped.
# When a subscriber queue overflows, these are retained in a per-subscriber
# overflow buffer so they can still be delivered.
CRITICAL_EVENT_TYPES = frozenset({
    "agent_finished",
    "task_graph",
    "dag_task_update",
    "execution_error",
    "plan_delta",
    "task_error",
    "project_status",
})

# Max critical events kept in overflow buffer per subscriber
_OVERFLOW_BUFFER_SIZE = 200

# Directory for DB write fallback files
_FALLBACK_DIR = Path(__file__).resolve().parent.parent / "data" / "event_fallback"

# Events that should be persisted to DB (skip ephemeral ones like ping, text_chunk)
_PERSIST_EVENT_TYPES = frozenset(
    {
        "agent_update",
        "agent_result",
        "agent_final",
        "project_status",
        "tool_use",
        "agent_started",
        "agent_finished",
        "delegation",
        "loop_progress",
        "approval_request",
        "history_cleared",
        "task_complete",
        "task_error",
        # DAG execution plan — critical for state reconstruction on reconnect
        "task_graph",
        "dag_task_update",
        "self_healing",
        "stuckness_detected",
        # Granular streaming events (persisted for replay/analytics)
        "tool_start",
        "tool_end",
        "agent_thinking",
        "agent_eta",
        # Agent activity logs — structured per-task completion records
        "agent_activity",
        # Pre-task question surfaced to the user before agent dispatch
        "pre_task_question",
        # Message ingestion pipeline — queued message acknowledgement
        "message_queued",
        "task_queued",
        # Granular DAG progress — task milestones and aggregate completion
        "task_progress",
        "dag_progress",
        # Incremental plan updates — must be persisted for reconnect state reconstruction
        "plan_delta",
        # NOTE: agent_text_chunk intentionally excluded — too frequent for DB
    }
)

# High-frequency event types that should be skipped in the ring buffer
# to prevent memory pressure from very chatty events
_SKIP_BUFFER_EVENT_TYPES = frozenset(
    {
        "agent_text_chunk",
    }
)


class _BacklogQueue:
    """Bounded queue for DB write backlog with oldest-event eviction and file fallback.

    When the queue reaches max_backlog_size, the oldest events are evicted
    to make room for new ones, and a structured warning is logged with the
    count of dropped events.

    If DB writes fail, events are persisted to a local JSONL file so they
    can be recovered and replayed after DB recovery.
    """

    def __init__(self, maxsize: int = EVENTBUS_MAX_BACKLOG_SIZE):
        self._queue: collections.deque[dict] = collections.deque(maxlen=maxsize)
        self._maxsize = maxsize
        self._total_evicted: int = 0
        self._event = asyncio.Event()
        self._fallback_path: Path | None = None
        self._consecutive_db_failures: int = 0
        self._total_fallback_writes: int = 0

    def _ensure_fallback_dir(self) -> Path:
        """Lazily create and return the fallback file path."""
        if self._fallback_path is None:
            _FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
            self._fallback_path = _FALLBACK_DIR / f"events_{int(time.time())}.jsonl"
        return self._fallback_path

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def total_evicted(self) -> int:
        return self._total_evicted

    @property
    def consecutive_db_failures(self) -> int:
        return self._consecutive_db_failures

    @property
    def total_fallback_writes(self) -> int:
        return self._total_fallback_writes

    def qsize(self) -> int:
        return len(self._queue)

    def empty(self) -> bool:
        return len(self._queue) == 0

    def put_nowait(self, event: dict) -> int:
        """Enqueue an event. Returns the number of events evicted (0 or 1+).

        When the deque is at capacity, the oldest event is automatically
        evicted by the deque's maxlen constraint.
        """
        was_full = len(self._queue) >= self._maxsize
        evicted = 0
        if was_full:
            evicted = 1
            self._total_evicted += 1
        self._queue.append(event)
        self._event.set()
        return evicted

    def get_nowait(self) -> dict:
        """Dequeue the oldest event. Raises IndexError if empty."""
        if not self._queue:
            raise IndexError("BacklogQueue is empty")
        item = self._queue.popleft()
        if not self._queue:
            self._event.clear()
        return item

    async def wait_for_item(self, timeout: float) -> bool:
        """Wait until an item is available or timeout expires. Returns True if item available."""
        if self._queue:
            return True
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return bool(self._queue)
        except TimeoutError:
            return False

    def drain(self, max_items: int = 0) -> list[dict]:
        """Remove and return up to max_items events (0 = all)."""
        if max_items <= 0:
            items = list(self._queue)
            self._queue.clear()
        else:
            items = []
            for _ in range(min(max_items, len(self._queue))):
                items.append(self._queue.popleft())
        if not self._queue:
            self._event.clear()
        return items

    def record_db_success(self) -> None:
        """Reset the DB failure counter after a successful write."""
        self._consecutive_db_failures = 0

    def write_to_fallback(self, events: list[dict]) -> int:
        """Write events to local JSONL file as fallback. Returns count written."""
        if not events:
            return 0
        self._consecutive_db_failures += 1
        try:
            path = self._ensure_fallback_dir()
            with open(path, "a") as f:
                for ev in events:
                    f.write(_json.dumps(ev, default=str) + "\n")
            written = len(events)
            self._total_fallback_writes += written
            logger.warning(
                "EventBus: wrote %d events to fallback file %s "
                "(consecutive_db_failures=%d, total_fallback=%d)",
                written, path.name, self._consecutive_db_failures,
                self._total_fallback_writes,
            )
            return written
        except OSError as e:
            logger.error(
                "EventBus: fallback file write FAILED: %s — %d events lost",
                e, len(events), exc_info=True,
            )
            return 0

    def recover_fallback_events(self) -> list[dict]:
        """Read and remove all events from fallback files. Returns recovered events."""
        recovered: list[dict] = []
        if not _FALLBACK_DIR.exists():
            return recovered
        for path in sorted(_FALLBACK_DIR.glob("events_*.jsonl")):
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            recovered.append(_json.loads(line))
                path.unlink()
                logger.info("EventBus: recovered %d events from %s", len(recovered), path.name)
            except (OSError, _json.JSONDecodeError) as e:
                logger.error("EventBus: failed to recover fallback file %s: %s", path.name, e)
        return recovered


class _TrackedQueue:
    """Wrapper around asyncio.Queue that tracks consecutive publish failures.

    Includes an overflow buffer for critical events that must never be dropped,
    and per-subscriber health metrics for monitoring delivery latency.
    """

    __slots__ = (
        "failures", "last_success", "queue", "subscriber_id",
        "_overflow_buffer", "_delivery_latencies", "_total_delivered",
        "_total_dropped", "_created_at",
    )

    def __init__(self, maxsize: int = 256, subscriber_id: str = ""):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.failures: int = 0
        self.last_success: float = time.time()
        self.subscriber_id: str = subscriber_id
        self._overflow_buffer: collections.deque[dict] = collections.deque(
            maxlen=_OVERFLOW_BUFFER_SIZE
        )
        # Rolling window of delivery latencies (last 100)
        self._delivery_latencies: collections.deque[float] = collections.deque(maxlen=100)
        self._total_delivered: int = 0
        self._total_dropped: int = 0
        self._created_at: float = time.time()

    def put_nowait(self, event: dict) -> bool:
        """Try to enqueue an event. Returns True on success.

        If the queue is full and the event is critical, it is stored in the
        overflow buffer instead of being dropped.
        """
        t0 = time.monotonic()
        try:
            self.queue.put_nowait(event)
            self.failures = 0
            self.last_success = time.time()
            self._total_delivered += 1
            self._delivery_latencies.append(time.monotonic() - t0)
            return True
        except asyncio.QueueFull:
            event_type = event.get("type", "")
            if event_type in CRITICAL_EVENT_TYPES:
                # Critical events go to overflow buffer — never silently dropped
                self._overflow_buffer.append(event)
                self._delivery_latencies.append(time.monotonic() - t0)
                return True

            # Non-critical: try drop-oldest strategy
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
                self.failures = 0
                self.last_success = time.time()
                self._total_delivered += 1
                self._total_dropped += 1
                self._delivery_latencies.append(time.monotonic() - t0)
                return True
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                self.failures += 1
                self._total_dropped += 1
                return False

    def drain_overflow(self) -> list[dict]:
        """Drain all critical events from the overflow buffer."""
        items = list(self._overflow_buffer)
        self._overflow_buffer.clear()
        return items

    @property
    def overflow_count(self) -> int:
        """Number of critical events waiting in overflow buffer."""
        return len(self._overflow_buffer)

    @property
    def is_dead(self) -> bool:
        """A subscriber is considered dead if it has too many consecutive failures."""
        return self.failures >= _MAX_CONSECUTIVE_FAILURES

    def get_health_metrics(self) -> dict:
        """Return subscriber health metrics for monitoring.

        Designed to be lightweight (<1ms) — only reads from pre-computed
        deques and counters, no I/O or heavy computation.
        """
        latencies = list(self._delivery_latencies)
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        max_latency = max(latencies) if latencies else 0.0
        return {
            "subscriber_id": self.subscriber_id,
            "queue_size": self.queue.qsize(),
            "overflow_size": len(self._overflow_buffer),
            "total_delivered": self._total_delivered,
            "total_dropped": self._total_dropped,
            "consecutive_failures": self.failures,
            "avg_latency_ms": round(avg_latency * 1000, 3),
            "max_latency_ms": round(max_latency * 1000, 3),
            "seconds_since_last_success": round(time.time() - self.last_success, 1),
            "uptime_seconds": round(time.time() - self._created_at, 1),
            "is_dead": self.is_dead,
        }


class EventThrottler:
    """Rate-limits event emission per (agent, event_type) key.

    Designed for high-frequency events like agent_text_chunk that would
    flood WebSocket connections if emitted at full streaming rate.

    The throttler enforces a minimum interval between emissions for each key.
    When an event is throttled, it is stored as a "pending" event so the
    most recent state is never lost — the caller can flush pending events
    at the end of a stream.

    Thread-safe: uses only simple dict operations with monotonic timestamps
    (no locks needed since dict operations are atomic in CPython).
    """

    def __init__(self, max_per_second: float = 4.0, max_keys: int = 10_000):
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self._min_interval: float = 1.0 / max_per_second
        self._max_keys: int = max_keys  # Hard cap: evict oldest keys when exceeded
        self._last_emit: dict[str, float] = {}
        self._pending: dict[str, dict] = {}
        # Per-key drop counter for back-pressure warning
        self._drop_count: dict[str, int] = {}

    @property
    def min_interval(self) -> float:
        """Minimum interval between emissions in seconds."""
        return self._min_interval

    def should_emit(self, key: str) -> bool:
        """Check if an event for this key can be emitted now.

        Returns True if enough time has passed since the last emission,
        and updates the last-emit timestamp. Returns False if the event
        should be throttled.

        Memory safety: if the number of tracked keys exceeds max_keys, the
        oldest half is evicted before adding a new key. This prevents unbounded
        dict growth in pathological workloads with many unique keys.

        Thread-safety: asyncio is single-threaded — this method contains no
        ``await`` points, so it runs atomically with respect to the event loop.
        No two coroutines can interleave inside ``should_emit()``, meaning the
        size check and the subsequent dict write form an atomic unit from the
        scheduler's perspective.
        """
        now = time.monotonic()
        last = self._last_emit.get(key, 0.0)
        if now - last >= self._min_interval:
            # Guard against unbounded growth: evict oldest keys when limit hit
            if key not in self._last_emit and len(self._last_emit) >= self._max_keys:
                # Sort by last-emit time and drop the oldest half
                sorted_keys = sorted(self._last_emit, key=lambda k: self._last_emit[k])
                for old_key in sorted_keys[: len(sorted_keys) // 2]:
                    self._last_emit.pop(old_key, None)
                    self._pending.pop(old_key, None)
                    self._drop_count.pop(old_key, None)
                logger.warning(
                    "EventThrottler: max_keys=%d exceeded — evicted %d stale keys",
                    self._max_keys,
                    len(sorted_keys) // 2,
                )
            self._last_emit[key] = now
            return True
        return False

    def set_pending(self, key: str, event: dict) -> None:
        """Store a throttled event as pending. The latest event wins.

        When an event is throttled, store it so it can be flushed later.
        This ensures the final state is never lost even when throttled.
        Logs a warning every 100 dropped events per key to surface back-pressure.

        Both ``_pending`` and ``_last_emit`` are bounded by ``_max_keys``.
        If ``_pending`` is at capacity and this is a new key, the oldest pending
        entry is evicted to stay within bounds, preventing unbounded dict growth
        under pathological workloads (many unique throttle keys).

        Thread-safety: asyncio is single-threaded within one event loop; there
        is no OS-level thread interleaving between dict reads and writes in any of
        these methods.  ``should_emit()`` has no ``await`` points, so it runs
        atomically from the asyncio scheduler's perspective.
        """
        # Enforce _max_keys on _pending to mirror the bound in should_emit().
        # If the pending dict is full and this is a new key, evict one arbitrary
        # entry (the dict's insertion-order first item) to stay within bounds.
        if key not in self._pending and len(self._pending) >= self._max_keys:
            try:
                evict_key = next(iter(self._pending))
                del self._pending[evict_key]
                logger.warning(
                    "EventThrottler: _pending at max_keys=%d — evicted key=%r to make room",
                    self._max_keys,
                    evict_key,
                )
            except StopIteration:
                pass  # dict was empty (race-free in asyncio single-thread model)

        # Track how many events are being dropped per key (back-pressure counter)
        count = self._drop_count.get(key, 0) + 1
        self._drop_count[key] = count
        # Warn every 100 drops so operators can see sustained back-pressure
        if count % 100 == 0:
            logger.warning(
                "EventThrottler: back-pressure on key=%r — %d events dropped/pending "
                "(throttle interval=%.3fs). Consider reducing event rate.",
                key,
                count,
                self._min_interval,
            )
        self._pending[key] = event

    def pop_pending(self, key: str) -> dict | None:
        """Retrieve and remove the pending event for a key.

        Returns None if no pending event exists. Used to flush the last
        throttled event at the end of a stream.
        """
        self._drop_count.pop(key, None)  # Reset drop counter on flush
        return self._pending.pop(key, None)

    def reset(self, key: str) -> None:
        """Reset throttle state for a key (e.g., when an agent finishes)."""
        self._last_emit.pop(key, None)
        self._pending.pop(key, None)
        self._drop_count.pop(key, None)

    def cleanup(self, max_age: float = 60.0) -> None:
        """Remove stale entries older than max_age seconds.

        Call periodically (e.g., every minute) to prevent unbounded growth
        of the throttle dictionaries from agents that have finished.
        """
        now = time.monotonic()
        stale = [k for k, t in self._last_emit.items() if now - t > max_age]
        for k in stale:
            del self._last_emit[k]
            self._pending.pop(k, None)
            self._drop_count.pop(k, None)


# Module-level throttler for text chunk events (max 4 per second per agent)
text_chunk_throttler = EventThrottler(max_per_second=4.0)

# Module-level throttler for task progress events (max 2 per second per task)
task_progress_throttler = EventThrottler(max_per_second=2.0)


class EventBus:
    """Async pub/sub for real-time event broadcasting with persistence.

    WebSocket handlers subscribe (get a Queue), and any part of the
    application can publish events that fan out to all subscribers.

    Events are also persisted to the database (via a non-blocking write
    queue) so that clients reconnecting from another device can catch up
    on missed events.
    """

    def __init__(self):
        self._subscribers: list[_TrackedQueue] = []
        self._lock = asyncio.Lock()
        self._subscriber_counter = itertools.count(1)

        # Per-project sequence counters — itertools.count gives lock-free atomic increments
        # (next() on a C-level count object is GIL-protected and never returns duplicates).
        self._sequence_counters: dict[str, itertools.count[int]] = {}
        self._sequence_latest: dict[str, int] = {}

        # Per-project ring buffers for fast in-memory replay
        self._ring_buffers: dict[str, collections.deque] = {}
        # Current ring buffer size — dynamically adjusted
        self._ring_buffer_size: int = _RING_BUFFER_MIN

        # Async write queue for DB persistence (non-blocking, bounded with eviction)
        self._write_queue: _BacklogQueue | None = None
        self._writer_task: asyncio.Task | None = None

        # Reference to session manager (set via set_session_manager)
        self._session_mgr: SessionManager | None = None

        # Per-project heartbeat background tasks
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}

        # ------------------------------------------------------------------
        # Diagnostics state — tracked per-project for health scoring
        # ------------------------------------------------------------------
        # Timestamp of the last stuckness event per project
        self._last_stuckness: dict[str, float] = {}
        # Timestamp of the last error event per project
        self._last_error: dict[str, float] = {}
        # Timestamp of the last meaningful progress per project
        self._last_progress: dict[str, float] = {}
        # Count of active warnings per project
        self._warnings_count: dict[str, int] = {}

    def set_session_manager(self, session_mgr: SessionManager):
        """Connect the EventBus to the session manager for DB persistence.

        Must be called once during app initialization.
        """
        self._session_mgr = session_mgr

    # ------------------------------------------------------------------
    # Diagnostics tracking — record events that affect health scoring
    # ------------------------------------------------------------------

    def record_stuckness(self, project_id: str) -> None:
        """Record that a stuckness event occurred for a project."""
        now = time.time()
        self._last_stuckness[project_id] = now
        self._warnings_count[project_id] = self._warnings_count.get(project_id, 0) + 1

    def record_error(self, project_id: str) -> None:
        """Record that an error event occurred for a project."""
        self._last_error[project_id] = time.time()

    def record_progress(self, project_id: str) -> None:
        """Record that meaningful progress occurred for a project."""
        self._last_progress[project_id] = time.time()
        # Reset warning count on progress
        self._warnings_count[project_id] = 0

    def get_diagnostics(self, project_id: str) -> dict:
        """Compute diagnostics for a project.

        Returns a dict with:
        - health_score: 'healthy' | 'degraded' | 'critical'
        - warnings_count: int — number of active warnings
        - last_stuckness: float | None — timestamp of last stuckness event
        - seconds_since_progress: float | None — seconds since last progress

        Health score logic:
        - 'critical' if any stuckness in last 60s or agent silent >90s
        - 'degraded' if agent silent >45s or error in last 120s
        - 'healthy' otherwise
        """
        now = time.time()

        last_stuck_ts = self._last_stuckness.get(project_id)
        last_error_ts = self._last_error.get(project_id)
        last_progress_ts = self._last_progress.get(project_id)

        seconds_since_progress: float | None = None
        if last_progress_ts is not None:
            seconds_since_progress = round(now - last_progress_ts, 1)

        warnings_count = self._warnings_count.get(project_id, 0)

        # Compute health_score
        health_score = "healthy"

        # Critical: stuckness in last 60s
        if last_stuck_ts is not None and (now - last_stuck_ts) < 60:
            health_score = "critical"
        # Critical: agent silent >90s (no progress in 90s when we have progress data)
        elif last_progress_ts is not None and (now - last_progress_ts) > 90:
            health_score = "critical"
        # Degraded: agent silent >45s
        elif last_progress_ts is not None and (now - last_progress_ts) > 45:
            health_score = "degraded"
        # Degraded: error in last 120s
        elif last_error_ts is not None and (now - last_error_ts) < 120:
            health_score = "degraded"

        return {
            "health_score": health_score,
            "warnings_count": warnings_count,
            "last_stuckness": last_stuck_ts,
            "seconds_since_progress": seconds_since_progress,
        }

    # ------------------------------------------------------------------
    # Heartbeat — periodic status broadcast
    # ------------------------------------------------------------------

    async def start_heartbeat(self, project_id: str, status_fn: StatusFn) -> None:
        """Start a periodic status heartbeat for a project.

        The heartbeat fires every ``_HEARTBEAT_INTERVAL_SECONDS`` (5 s) and
        publishes a lightweight ``status_heartbeat`` event via the normal
        ``publish()`` pipeline so all WebSocket subscribers receive it.

        Args:
            project_id: The project to heartbeat for.
            status_fn:  An async callable that returns a dict with at least
                        ``{"status": str, "active_agents": int}``.
                        It is invoked every tick to get the *current* truth.
        """
        # Stop any existing heartbeat for this project first
        await self.stop_heartbeat(project_id)

        task = asyncio.create_task(
            self._heartbeat_loop(project_id, status_fn),
            name=f"heartbeat-{project_id}",
        )
        self._heartbeat_tasks[project_id] = task
        logger.info("EventBus: heartbeat started for project %s", project_id)

    async def stop_heartbeat(self, project_id: str) -> None:
        """Stop the heartbeat for a project (idempotent).

        Cancels the background task and awaits its completion so there are
        no dangling coroutines after the project session ends.
        """
        task = self._heartbeat_tasks.pop(project_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("EventBus: heartbeat stopped for project %s", project_id)

    async def stop_all_heartbeats(self) -> None:
        """Stop heartbeats for every project. Called during app shutdown."""
        project_ids = list(self._heartbeat_tasks.keys())
        for pid in project_ids:
            await self.stop_heartbeat(pid)

    async def _heartbeat_loop(self, project_id: str, status_fn: StatusFn) -> None:
        """Background loop that publishes status_heartbeat events.

        Runs until cancelled (via ``stop_heartbeat``) or until
        ``status_fn`` raises an exception.  Each tick publishes:
        - ``project_id``, ``status``, ``active_agents``, ``timestamp`` (original)
        - ``agents``: array of per-agent diagnostic dicts (new)
        - ``diagnostics``: system health summary (new)

        All new fields are additive — existing consumers that only read
        the original fields continue to work unchanged.
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
                try:
                    info = await status_fn()

                    # Build per-agent diagnostics array from status_fn data.
                    # status_fn may return an "agents" dict keyed by agent name,
                    # or "agent_states" (the orchestrator's naming convention).
                    raw_agents = info.get("agents") or info.get("agent_states") or {}
                    agents_array: list[dict] = []
                    now = time.time()
                    has_working_agent = False

                    for agent_name, agent_info in raw_agents.items():
                        if not isinstance(agent_info, dict):
                            continue
                        agent_state = agent_info.get("state", "idle")
                        if agent_state == "working":
                            has_working_agent = True

                        # Compute elapsed seconds from duration or started_at
                        elapsed: float = 0.0
                        if "duration" in agent_info:
                            elapsed = float(agent_info["duration"])
                        elif "started_at" in agent_info:
                            elapsed = round(now - float(agent_info["started_at"]), 1)

                        # Last activity timestamp — use last_stream_at if available
                        last_activity_ts = agent_info.get(
                            "last_stream_at",
                            agent_info.get(
                                "last_activity_ts", now if agent_state == "working" else 0
                            ),
                        )

                        agents_array.append(
                            {
                                "name": agent_name,
                                "state": agent_state,
                                "elapsed_seconds": elapsed,
                                "last_activity": last_activity_ts,
                                "current_tool": agent_info.get("current_tool", ""),
                                "task": agent_info.get("task", ""),
                            }
                        )

                    # Track progress: any working agent counts as progress
                    if has_working_agent:
                        self.record_progress(project_id)

                    # Fetch diagnostics for health scoring
                    diagnostics = self.get_diagnostics(project_id)

                    event = {
                        "type": "status_heartbeat",
                        "project_id": project_id,
                        "status": info.get("status", "unknown"),
                        "active_agents": info.get("active_agents", 0),
                        "timestamp": now,
                        # New fields — backward-compatible additions
                        "agents": agents_array,
                        "diagnostics": diagnostics,
                    }
                    await self.publish(event)
                except asyncio.CancelledError:
                    raise  # let the outer handler deal with it
                except Exception as exc:
                    logger.error(
                        "EventBus: heartbeat status_fn error for %s: %s",
                        project_id,
                        exc,
                        exc_info=True,
                    )
                    # Continue heartbeat even if one tick fails — resilience
        except asyncio.CancelledError:
            logger.debug("EventBus: heartbeat loop cancelled for %s", project_id)

    async def start_writer(self):
        """Start the background DB writer task.

        Call this after the event loop is running (e.g., in app startup).
        Also attempts to recover any events from fallback files left by
        previous sessions that experienced DB write failures.
        """
        if self._write_queue is not None:
            return  # Already started
        self._write_queue = _BacklogQueue(maxsize=EVENTBUS_MAX_BACKLOG_SIZE)
        # Recover any fallback events from previous sessions
        recovered = self._write_queue.recover_fallback_events()
        if recovered:
            for ev in recovered:
                self._write_queue.put_nowait(ev)
            logger.info(
                "EventBus: re-queued %d recovered fallback events for DB write",
                len(recovered),
            )
        self._writer_task = asyncio.create_task(self._db_writer_loop())
        logger.info(
            "EventBus: DB writer started (max_backlog_size=%d)", EVENTBUS_MAX_BACKLOG_SIZE
        )

    async def stop_writer(self):
        """Flush pending writes and stop the background writer.

        The flush is bounded by EVENTBUS_FLUSH_TIMEOUT to prevent
        indefinite blocking during shutdown.
        """
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            # Flush remaining items (with timeout)
            await self._flush_write_queue()
            self._writer_task = None
            self._write_queue = None
            logger.info("EventBus: DB writer stopped")

    async def subscribe(self, subscriber_id: str = "") -> asyncio.Queue:
        """Create a new subscriber queue and register it.

        Args:
            subscriber_id: Optional identifier for the subscriber (e.g. ws_id).
                Used for health monitoring and diagnostics.
        """
        async with self._lock:
            sid = subscriber_id or f"sub_{next(self._subscriber_counter)}"
            tracked = _TrackedQueue(maxsize=256, subscriber_id=sid)
            self._subscribers.append(tracked)
            return tracked.queue

    async def unsubscribe(self, queue: asyncio.Queue):
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers = [t for t in self._subscribers if t.queue is not queue]

    def _next_sequence(self, project_id: str) -> int:
        """Get and increment the sequence counter for a project.

        Uses itertools.count which is GIL-protected — next() is atomic and
        guaranteed to never return the same value twice, even under concurrent
        asyncio tasks.
        """
        if project_id not in self._sequence_counters:
            self._sequence_counters[project_id] = itertools.count(1)
        seq = next(self._sequence_counters[project_id])
        self._sequence_latest[project_id] = seq
        return seq

    def _compute_ring_buffer_size(self) -> int:
        """Dynamically compute ring buffer size based on active project count.

        Scales linearly: 500 base + 100 per project, clamped to [500, 5000].
        """
        active = len(self._ring_buffers)
        size = _RING_BUFFER_MIN + active * 100
        return max(_RING_BUFFER_MIN, min(size, _RING_BUFFER_MAX))

    def _buffer_event(self, project_id: str, event: dict):
        """Add event to the in-memory ring buffer for fast replay.

        Ring buffer size is dynamically adjusted based on active project count.
        """
        # Recalculate buffer size periodically
        new_size = self._compute_ring_buffer_size()
        if new_size != self._ring_buffer_size:
            self._ring_buffer_size = new_size

        if project_id not in self._ring_buffers:
            self._ring_buffers[project_id] = collections.deque(maxlen=self._ring_buffer_size)
        else:
            buf = self._ring_buffers[project_id]
            if buf.maxlen != self._ring_buffer_size:
                # Resize by creating a new deque with the updated maxlen
                self._ring_buffers[project_id] = collections.deque(
                    buf, maxlen=self._ring_buffer_size
                )
        self._ring_buffers[project_id].append(event)

        # Evict excess project buffers to prevent unbounded dict growth.
        # When more than 100 projects have accumulated, remove the one whose
        # ring buffer has the smallest (oldest) last sequence_id.
        if len(self._ring_buffers) > 100:
            oldest_pid = min(
                self._ring_buffers,
                key=lambda pid: (
                    self._ring_buffers[pid][-1].get("sequence_id", 0)
                    if self._ring_buffers[pid]
                    else 0
                ),
            )
            del self._ring_buffers[oldest_pid]

    async def publish(self, event: dict):
        """Broadcast an event dict to all subscribers and persist to DB.

        Adds a timestamp, sequence_id, and request_id (from ContextVar) if not
        already present. Drops events for full queues (slow consumers) rather
        than blocking the publisher. Automatically removes dead subscribers.

        Also tracks diagnostics-relevant events (stuckness, errors, progress)
        for health score computation in heartbeat diagnostics.
        """
        # Copy to prevent shared mutable state across subscribers
        event = {**event}
        if "timestamp" not in event:
            event["timestamp"] = time.time()

        # Propagate the originating request_id for end-to-end traceability.
        # The ContextVar is set by the HTTP request middleware; it defaults to ""
        # for events published outside a request context (e.g., background tasks).
        req_id = current_request_id.get("")
        if req_id and "request_id" not in event:
            event["request_id"] = req_id

        # Propagate correlation_id for distributed tracing (ELK/Datadog).
        corr_id = current_correlation_id.get("")
        if corr_id and "correlation_id" not in event:
            event["correlation_id"] = corr_id

        project_id = event.get("project_id", "")
        event_type = event.get("type", "")

        # Log important events so we can trace what the frontend receives
        if event_type in (
            "agent_started",
            "agent_finished",
            "project_status",
            "delegation",
            "task_graph",
            "self_healing",
            "plan_delta",
        ):
            agent = event.get("agent", "")
            extra = ""
            if event_type == "agent_finished":
                extra = f" is_error={event.get('is_error')} tokens={event.get('total_tokens', 0)} failure_reason={event.get('failure_reason', '')[:80]}"
            elif event_type == "project_status":
                extra = f" status={event.get('status')}"
            elif event_type == "agent_started":
                extra = f" task={str(event.get('task', ''))[:80]}"
            elif event_type == "delegation":
                extra = f" delegations={len(event.get('delegations', []))}"
            req_tag = f" req={req_id}" if req_id else ""
            logger.info(
                "[EventBus] PUBLISH %s agent=%s project=%s seq=%s%s%s",
                event_type,
                agent,
                project_id[:8] if project_id else "",
                event.get("sequence_id", "?"),
                req_tag,
                extra,
            )
        elif event_type == "agent_update":
            agent = event.get("agent", "")
            summary = str(event.get("summary", ""))[:60]
            logger.debug(
                "[EventBus] PUBLISH %s agent=%s summary='%s'",
                event_type,
                agent,
                summary,
            )

        # --- Diagnostics auto-tracking ---
        # Track events that affect health scoring (lightweight, no I/O)
        if project_id:
            if event_type == "stuckness_detected":  # matches orchestrator._emit_stuckness_event
                self.record_stuckness(project_id)
            elif event_type in ("task_error", "agent_finished") and event.get("is_error"):
                self.record_error(project_id)
            elif event_type in (
                # Completion events — original set
                "agent_finished",
                "task_complete",
                "tool_end",
                "agent_result",
                "agent_final",
                # Active work events — reset staleness during ongoing activity
                "agent_update",
                "agent_started",
                "tool_start",
                "agent_thinking",
                "dag_task_update",
                "task_progress",
                "dag_progress",
                "delegation",
                "self_healing",
                "plan_delta",
            ) and not event.get("is_error"):
                self.record_progress(project_id)
            elif event_type == "project_status" and event.get("status") == "running":
                self.record_progress(project_id)

        # Assign sequence ID for ordered replay
        if project_id:
            seq = self._next_sequence(project_id)
            event["sequence_id"] = seq
            # Skip ring buffer for high-frequency ephemeral events
            # to prevent memory pressure (text chunks are fire-and-forget)
            if event_type not in _SKIP_BUFFER_EVENT_TYPES:
                self._buffer_event(project_id, event)

        # Fan out to WebSocket subscribers
        async with self._lock:
            subscribers = list(self._subscribers)

        dead: list[_TrackedQueue] = []

        for tracked in subscribers:
            success = tracked.put_nowait(event)
            if not success and tracked.is_dead:
                dead.append(tracked)
                logger.warning(
                    "EventBus: removing dead subscriber (%d consecutive failures)",
                    tracked.failures,
                )

        # Clean up dead subscribers
        if dead:
            async with self._lock:
                for d in dead:
                    try:
                        self._subscribers.remove(d)
                    except ValueError as _ve:
                        logger.debug(
                            "EventBus: subscriber already removed (concurrent cleanup): %s", _ve
                        )
            logger.info(
                "EventBus: cleaned up %d dead subscriber(s), %d remaining",
                len(dead),
                len(self._subscribers),
            )

        # Queue for DB persistence (non-blocking, oldest-event eviction)
        if project_id and event_type in _PERSIST_EVENT_TYPES:
            if self._write_queue is not None:
                qsize = self._write_queue.qsize()
                capacity = self._write_queue.maxsize
                # Warn when queue is >80% full (approaching capacity)
                if qsize > int(capacity * 0.8):
                    logger.warning(
                        "EventBus: write queue at %d/%d — DB writes may be falling behind",
                        qsize,
                        capacity,
                    )
                evicted = self._write_queue.put_nowait(event)
                if evicted > 0:
                    logger.warning(
                        "EventBus: backlog full (%d/%d) — evicted %d oldest event(s) "
                        "to make room for %s (total evicted: %d)",
                        capacity,
                        capacity,
                        evicted,
                        event_type,
                        self._write_queue.total_evicted,
                    )

    async def publish_throttled(self, event: dict, throttle_key: str | None = None) -> bool:
        """Publish an event with optional rate-limiting.

        If throttle_key is provided, the event is rate-limited using the
        module-level text_chunk_throttler. Returns True if the event was
        published immediately, False if it was throttled (stored as pending).

        Throttled events are stored so the latest state is preserved.
        Call flush_throttled() to emit any pending events when a stream ends.
        """
        if throttle_key:
            if not text_chunk_throttler.should_emit(throttle_key):
                text_chunk_throttler.set_pending(throttle_key, event)
                return False
        await self.publish(event)
        return True

    async def flush_throttled(self, throttle_key: str) -> None:
        """Publish any pending throttled event for the given key.

        Call this at the end of an agent's stream to ensure the final
        text chunk is delivered even if it was throttled.
        """
        pending = text_chunk_throttler.pop_pending(throttle_key)
        if pending:
            await self.publish(pending)
        text_chunk_throttler.reset(throttle_key)

    def get_buffered_events(self, project_id: str, since_sequence: int = 0) -> list[dict]:
        """Get events from the in-memory ring buffer after a given sequence.

        Fast path for reconnects — avoids DB query if events are still in memory.
        Returns events in chronological order.
        """
        buf = self._ring_buffers.get(project_id)
        if not buf:
            return []
        return [e for e in buf if e.get("sequence_id", 0) > since_sequence]

    def get_buffered_events_range(
        self, project_id: str, from_seq: int, to_seq: int
    ) -> list[dict]:
        """Get events from the ring buffer within a sequence_id range [from_seq, to_seq].

        Used by the reconnect-replay protocol so clients can request a specific
        range of missed events. Returns events in chronological order.
        """
        buf = self._ring_buffers.get(project_id)
        if not buf:
            return []
        return [
            e for e in buf
            if from_seq <= e.get("sequence_id", 0) <= to_seq
        ]

    def get_latest_sequence(self, project_id: str) -> int:
        """Get the latest sequence_id for a project (0 if no events)."""
        return self._sequence_latest.get(project_id, 0)

    def get_subscriber_health(self) -> list[dict]:
        """Return health metrics for all active subscribers.

        Lightweight — reads pre-computed counters only. Suitable for
        periodic diagnostic emission or API endpoint exposure.
        """
        return [t.get_health_metrics() for t in self._subscribers]

    def get_subscriber_health_summary(self) -> dict:
        """Return aggregate subscriber health summary."""
        total = len(self._subscribers)
        if total == 0:
            return {"total_subscribers": 0, "healthy": 0, "degraded": 0, "dead": 0}
        healthy = sum(1 for t in self._subscribers if t.failures == 0)
        dead = sum(1 for t in self._subscribers if t.is_dead)
        degraded = total - healthy - dead
        total_overflow = sum(t.overflow_count for t in self._subscribers)
        return {
            "total_subscribers": total,
            "healthy": healthy,
            "degraded": degraded,
            "dead": dead,
            "total_overflow_events": total_overflow,
            "ring_buffer_size": self._ring_buffer_size,
            "active_projects": len(self._ring_buffers),
        }

    async def drain_overflow_to_subscriber(self, queue: asyncio.Queue) -> int:
        """Drain critical overflow events back into the subscriber's main queue.

        Called when the subscriber's queue has space again (e.g. after the
        WebSocket _sender pulls events). Returns the number of events drained.
        """
        tracked: _TrackedQueue | None = None
        for t in self._subscribers:
            if t.queue is queue:
                tracked = t
                break
        if not tracked or not tracked._overflow_buffer:
            return 0
        drained = 0
        while tracked._overflow_buffer:
            if tracked.queue.full():
                break
            ev = tracked._overflow_buffer.popleft()
            try:
                tracked.queue.put_nowait(ev)
                drained += 1
            except asyncio.QueueFull:
                tracked._overflow_buffer.appendleft(ev)
                break
        return drained

    def clear_project_events(self, project_id: str) -> None:
        """Clear all in-memory event data for a project.

        Resets the ring buffer, sequence counter, latest sequence, and
        diagnostics state so that after a history clear the frontend starts
        from a clean slate and old events cannot resurface from the
        in-memory cache.
        """
        self._ring_buffers.pop(project_id, None)
        self._sequence_counters.pop(project_id, None)
        self._sequence_latest.pop(project_id, None)
        # Clear diagnostics state
        self._last_stuckness.pop(project_id, None)
        self._last_error.pop(project_id, None)
        self._last_progress.pop(project_id, None)
        self._warnings_count.pop(project_id, None)
        # Also clear any pending throttled events for this project
        keys_to_remove = [k for k in text_chunk_throttler._pending if k.startswith(project_id)]
        for k in keys_to_remove:
            text_chunk_throttler._pending.pop(k, None)
        # Also clear stale last-emit timestamps so the first new chunk after a
        # clear is not incorrectly throttled (throttler saw a "recent" emit).
        emit_keys_to_remove = [
            k for k in text_chunk_throttler._last_emit if k.startswith(project_id)
        ]
        for k in emit_keys_to_remove:
            text_chunk_throttler._last_emit.pop(k, None)

    async def _db_writer_loop(self):
        """Background task that batches and writes events to DB.

        Collects events for up to 2 seconds or 50 events, then writes
        them all in a single transaction for efficiency.
        """
        batch: list[dict] = []
        _last_throttle_cleanup = time.monotonic()
        while True:
            try:
                # Periodically clean up stale throttler entries
                # to prevent unbounded memory growth from finished agents.
                _now = time.monotonic()
                if _now - _last_throttle_cleanup > 60.0:  # every 60 seconds
                    text_chunk_throttler.cleanup(max_age=60.0)
                    _last_throttle_cleanup = _now

                # Guard: _write_queue may be None if stop_writer was called
                if self._write_queue is None:
                    await asyncio.sleep(0.5)
                    continue

                # Wait for first event (with timeout)
                has_item = await self._write_queue.wait_for_item(
                    timeout=EVENT_QUEUE_TIMEOUT
                )
                if not has_item:
                    continue

                try:
                    batch.append(self._write_queue.get_nowait())
                except IndexError:
                    continue

                # Collect more events (up to 50 or 2 seconds)
                deadline = time.time() + 2.0
                while len(batch) < 50 and time.time() < deadline:
                    if self._write_queue is None:
                        break
                    if self._write_queue.empty():
                        await asyncio.sleep(0.1)
                        break
                    try:
                        batch.append(self._write_queue.get_nowait())
                    except IndexError:
                        break

                # Write batch to DB
                if batch and self._session_mgr:
                    await self._write_batch(batch)
                batch = []

            except asyncio.CancelledError:
                # Flush remaining
                if batch and self._session_mgr:
                    await self._write_batch(batch)
                raise
            except Exception as e:
                logger.error("EventBus DB writer error: %s", e, exc_info=True)
                batch = []
                await asyncio.sleep(1.0)

    async def _write_batch(self, batch: list[dict]):
        """Write a batch of events to the activity_log table.

        Falls back to local file persistence if DB writes fail.
        """
        if not self._session_mgr:
            return
        failed_events: list[dict] = []
        for event in batch:
            try:
                project_id = event.get("project_id", "")
                event_type = event.get("type", "")
                agent = event.get("agent", "")
                timestamp = event.get("timestamp", time.time())

                # Store the full event data (minus redundant fields)
                data = {
                    k: v
                    for k, v in event.items()
                    if k not in ("project_id", "type", "agent", "timestamp", "sequence_id")
                }

                await self._session_mgr.log_activity(
                    project_id=project_id,
                    event_type=event_type,
                    agent=agent,
                    data=data,
                    timestamp=timestamp,
                )
            except Exception as e:
                logger.error("EventBus: failed to persist event %s: %s", event.get("type"), e)
                failed_events.append(event)

        if failed_events and self._write_queue:
            self._write_queue.write_to_fallback(failed_events)
        elif not failed_events and self._write_queue:
            self._write_queue.record_db_success()

    async def _flush_write_queue(self):
        """Flush all remaining events in the write queue to DB.

        Uses EVENTBUS_FLUSH_TIMEOUT to prevent indefinite blocking
        during shutdown if the DB is slow or unresponsive.
        """
        if not self._write_queue or not self._session_mgr:
            return
        try:
            async with asyncio.timeout(EVENTBUS_FLUSH_TIMEOUT):
                batch = self._write_queue.drain()
                if batch:
                    await self._write_batch(batch)
                    logger.info("EventBus: flushed %d events to DB on shutdown", len(batch))
        except TimeoutError:
            remaining = self._write_queue.qsize()
            logger.warning(
                "EventBus: flush timed out after %.1fs — %d events remain unwritten",
                EVENTBUS_FLUSH_TIMEOUT,
                remaining,
            )

    @property
    def subscriber_count(self) -> int:
        """Return the current number of subscribers (useful for monitoring)."""
        return len(self._subscribers)

    def get_ring_buffer_utilization(self) -> dict:
        """Return ring buffer utilization stats per project.

        Lightweight — reads only deque lengths and maxlen attributes.
        Response time <1ms for typical project counts.
        """
        buffers: list[dict] = []
        total_events = 0
        total_capacity = 0
        for pid, buf in self._ring_buffers.items():
            size = len(buf)
            capacity = buf.maxlen or self._ring_buffer_size
            total_events += size
            total_capacity += capacity
            buffers.append({
                "project_id": pid,
                "size": size,
                "capacity": capacity,
                "utilization_pct": round(size / capacity * 100, 1) if capacity else 0,
            })
        return {
            "ring_buffer_size_setting": self._ring_buffer_size,
            "active_projects": len(self._ring_buffers),
            "total_buffered_events": total_events,
            "total_capacity": total_capacity,
            "overall_utilization_pct": round(
                total_events / total_capacity * 100, 1
            ) if total_capacity else 0,
            "per_project": buffers,
        }

    def get_ws_connection_states(self) -> dict:
        """Return WebSocket connection state summary from subscriber metrics.

        Categorizes subscribers as active, idle, or degraded based on
        delivery health metrics. Suitable for the health endpoint.
        """
        active = 0
        idle = 0
        degraded = 0
        dead_count = 0
        for t in self._subscribers:
            if t.is_dead:
                dead_count += 1
            elif t.failures > 0:
                degraded += 1
            elif time.time() - t.last_success > 30:
                idle += 1
            else:
                active += 1
        return {
            "total_connections": len(self._subscribers),
            "active": active,
            "idle": idle,
            "degraded": degraded,
            "dead": dead_count,
        }

    def rotate_fallback_files(self) -> dict:
        """Rotate fallback event files: enforce max file count and age.

        Removes oldest files beyond FALLBACK_MAX_FILES and files older
        than FALLBACK_MAX_AGE_HOURS. Idempotent — safe to call repeatedly.
        Returns a summary of rotation actions taken.
        """
        if not _FALLBACK_DIR.exists():
            return {"rotated": 0, "reason": "no_fallback_dir"}

        files = sorted(_FALLBACK_DIR.glob("events_*.jsonl"), key=lambda p: p.stat().st_mtime)
        removed_age = 0
        removed_count = 0
        now = time.time()
        max_age_seconds = FALLBACK_MAX_AGE_HOURS * 3600

        # Remove files older than max age
        remaining: list[Path] = []
        for f in files:
            try:
                age = now - f.stat().st_mtime
                if age > max_age_seconds:
                    f.unlink()
                    removed_age += 1
                else:
                    remaining.append(f)
            except OSError as e:
                logger.warning("EventBus: failed to rotate fallback file %s: %s", f.name, e)
                remaining.append(f)

        # Enforce max file count (keep newest)
        if len(remaining) > FALLBACK_MAX_FILES:
            excess = remaining[: len(remaining) - FALLBACK_MAX_FILES]
            for f in excess:
                try:
                    f.unlink()
                    removed_count += 1
                except OSError as e:
                    logger.warning("EventBus: failed to remove excess fallback file %s: %s", f.name, e)

        total_removed = removed_age + removed_count
        if total_removed > 0:
            logger.info(
                "EventBus: rotated %d fallback files (age=%d, count=%d, remaining=%d)",
                total_removed, removed_age, removed_count,
                len(remaining) - removed_count,
            )
        return {
            "rotated": total_removed,
            "removed_by_age": removed_age,
            "removed_by_count": removed_count,
            "remaining_files": max(0, len(remaining) - removed_count),
        }


class StructuredJsonFormatter(logging.Formatter):
    """JSON log formatter compatible with ELK/Datadog/CloudWatch.

    Emits one JSON object per log line with standard fields:
    timestamp, level, logger, message, and optional correlation_id,
    request_id, and extra fields from the log record.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Inject correlation_id and request_id from ContextVars if available
        corr_id = current_correlation_id.get("")
        if corr_id:
            log_entry["correlation_id"] = corr_id
        req_id = current_request_id.get("")
        if req_id:
            log_entry["request_id"] = req_id
        # Include exception info
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Include extra fields passed via logger.info("msg", extra={...})
        for key in ("project_id", "event_type", "agent", "subscriber_id", "ws_id"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return _json.dumps(log_entry, default=str)


def configure_structured_logging(log_format: str = "text", log_level: str = "INFO") -> None:
    """Configure root logger with structured JSON or plain text format.

    Args:
        log_format: "json" for ELK/Datadog-compatible JSON lines,
                    "text" for human-readable plain text.
        log_level: Standard Python log level name (DEBUG, INFO, WARNING, etc.).
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))
    # Remove existing handlers to prevent duplicate output
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    root.addHandler(handler)


# Module-level singleton
event_bus = EventBus()
