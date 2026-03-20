"""Central pub/sub EventBus for broadcasting agent activity to WebSocket clients.

Enhanced with:
- Activity persistence: every event is saved to DB for cross-device sync
- Sequence IDs: monotonic per-project counter for gap-free replay
- In-memory ring buffer: fast replay for recent reconnects without DB hit
- Batch write queue: non-blocking DB writes to avoid slowing the publisher
- Event throttling: rate-limits high-frequency events (agent_text_chunk)
- Granular event types: tool_start/tool_end, agent_thinking, agent_eta
- Request-ID propagation: events carry the originating request_id for tracing
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import itertools
import logging
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from config import EVENT_QUEUE_TIMEOUT

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

# How many consecutive publish failures before a subscriber is considered dead
_MAX_CONSECUTIVE_FAILURES = 10

# Heartbeat interval in seconds — how often the status heartbeat fires
_HEARTBEAT_INTERVAL_SECONDS = 5

# Ring buffer size per project (in-memory, for fast replay)
# Increased from 500 to handle higher event volume from granular streaming
_RING_BUFFER_SIZE = 1000

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


class _TrackedQueue:
    """Wrapper around asyncio.Queue that tracks consecutive publish failures."""

    __slots__ = ("failures", "last_success", "queue")

    def __init__(self, maxsize: int = 256):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.failures: int = 0
        self.last_success: float = time.time()

    def put_nowait(self, event: dict) -> bool:
        """Try to enqueue an event. Returns True on success."""
        try:
            self.queue.put_nowait(event)
            self.failures = 0
            self.last_success = time.time()
            return True
        except asyncio.QueueFull:
            # Slow consumer — drop oldest event and try again
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
                self.failures = 0
                self.last_success = time.time()
                return True
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                self.failures += 1
                return False

    @property
    def is_dead(self) -> bool:
        """A subscriber is considered dead if it has too many consecutive failures."""
        return self.failures >= _MAX_CONSECUTIVE_FAILURES


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

        # Per-project sequence counters — itertools.count gives lock-free atomic increments
        # (next() on a C-level count object is GIL-protected and never returns duplicates).
        self._sequence_counters: dict[str, itertools.count[int]] = {}
        self._sequence_latest: dict[str, int] = {}

        # Per-project ring buffers for fast in-memory replay
        self._ring_buffers: dict[str, collections.deque] = {}

        # Async write queue for DB persistence (non-blocking)
        self._write_queue: asyncio.Queue | None = None
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
                        if agent_state in ("working", "waiting"):
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
                                "last_activity_ts",
                                now if agent_state in ("working", "waiting") else 0,
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
        """
        if self._write_queue is not None:
            return  # Already started
        self._write_queue = asyncio.Queue(maxsize=5000)
        self._writer_task = asyncio.create_task(self._db_writer_loop())
        logger.info("EventBus: DB writer started")

    async def stop_writer(self):
        """Flush pending writes and stop the background writer."""
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            # Flush remaining items
            await self._flush_write_queue()
            self._writer_task = None
            self._write_queue = None
            logger.info("EventBus: DB writer stopped")

    async def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue and register it."""
        async with self._lock:
            tracked = _TrackedQueue(maxsize=256)
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

    def _buffer_event(self, project_id: str, event: dict):
        """Add event to the in-memory ring buffer for fast replay."""
        if project_id not in self._ring_buffers:
            self._ring_buffers[project_id] = collections.deque(maxlen=_RING_BUFFER_SIZE)
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
                "agent_finished",
                "task_complete",
                "tool_end",
                "agent_result",
                "agent_final",
            ) and not event.get("is_error"):
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

        # Queue for DB persistence (non-blocking)
        # Using put_nowait() directly (not guarded by full() check) because:
        # The prior pattern — `if not queue.full(): queue.put_nowait(...)` — is a
        # TOCTOU race: another coroutine can fill the queue between the check and
        # the put.  put_nowait() already raises QueueFull atomically, so we just
        # attempt the put and handle the exception.  This is safe because asyncio
        # Queue.put_nowait is synchronous and will raise immediately if full.
        if project_id and event_type in _PERSIST_EVENT_TYPES:
            if self._write_queue is not None:
                qsize = self._write_queue.qsize()
                # Warn when queue is >80% full (approaching capacity)
                if qsize > 4000:
                    logger.warning(
                        "EventBus: write queue at %d/5000 — DB writes may be falling behind",
                        qsize,
                    )
                try:
                    self._write_queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(
                        "EventBus: write queue full, dropping DB write for %s", event_type
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

    def get_latest_sequence(self, project_id: str) -> int:
        """Get the latest sequence_id for a project (0 if no events)."""
        return self._sequence_latest.get(project_id, 0)

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

                # Wait for first event
                try:
                    event = await asyncio.wait_for(
                        self._write_queue.get(), timeout=EVENT_QUEUE_TIMEOUT
                    )
                    batch.append(event)
                except TimeoutError:
                    continue

                # Collect more events (up to 50 or 2 seconds)
                deadline = time.time() + 2.0
                while len(batch) < 50 and time.time() < deadline:
                    if self._write_queue is None:
                        break
                    try:
                        event = self._write_queue.get_nowait()
                        batch.append(event)
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(0.1)
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
        """Write a batch of events to the activity_log table."""
        if not self._session_mgr:
            return
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

    async def _flush_write_queue(self):
        """Flush all remaining events in the write queue to DB."""
        if not self._write_queue or not self._session_mgr:
            return
        batch = []
        while not self._write_queue.empty():
            try:
                batch.append(self._write_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await self._write_batch(batch)
            logger.info("EventBus: flushed %d events to DB on shutdown", len(batch))

    @property
    def subscriber_count(self) -> int:
        """Return the current number of subscribers (useful for monitoring)."""
        return len(self._subscribers)


# Module-level singleton
event_bus = EventBus()
