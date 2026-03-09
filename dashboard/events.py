"""Central pub/sub EventBus for broadcasting agent activity to WebSocket clients.

Enhanced with:
- Activity persistence: every event is saved to DB for cross-device sync
- Sequence IDs: monotonic per-project counter for gap-free replay
- In-memory ring buffer: fast replay for recent reconnects without DB hit
- Batch write queue: non-blocking DB writes to avoid slowing the publisher
- Event throttling: rate-limits high-frequency events (agent_text_chunk)
- Granular event types: tool_start/tool_end, agent_thinking, agent_eta
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from typing import Any, Callable, Coroutine, TYPE_CHECKING

if TYPE_CHECKING:
    from session_manager import SessionManager

# Type alias for the async status callback used by heartbeat.
# Must return a dict with at least {"status": str, "active_agents": int}.
StatusFn = Callable[[], Coroutine[Any, Any, dict]]

logger = logging.getLogger(__name__)

# How many consecutive publish failures before a subscriber is considered dead
_MAX_CONSECUTIVE_FAILURES = 10

# Heartbeat interval in seconds — how often the status heartbeat fires
_HEARTBEAT_INTERVAL_SECONDS = 5

# Ring buffer size per project (in-memory, for fast replay)
# Increased from 500 to handle higher event volume from granular streaming
_RING_BUFFER_SIZE = 1000

# Events that should be persisted to DB (skip ephemeral ones like ping, text_chunk)
_PERSIST_EVENT_TYPES = frozenset({
    "agent_update", "agent_result", "agent_final", "project_status",
    "tool_use", "agent_started", "agent_finished", "delegation",
    "loop_progress", "approval_request", "history_cleared",
    "task_complete", "task_error",
    # Granular streaming events (persisted for replay/analytics)
    "tool_start", "tool_end", "agent_thinking", "agent_eta",
    # NOTE: agent_text_chunk intentionally excluded — too frequent for DB
})

# High-frequency event types that should be skipped in the ring buffer
# to prevent memory pressure from very chatty events
_SKIP_BUFFER_EVENT_TYPES = frozenset({
    "agent_text_chunk",
})


class _TrackedQueue:
    """Wrapper around asyncio.Queue that tracks consecutive publish failures."""

    __slots__ = ("queue", "failures", "last_success")

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

    def __init__(self, max_per_second: float = 4.0):
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self._min_interval: float = 1.0 / max_per_second
        self._last_emit: dict[str, float] = {}
        self._pending: dict[str, dict] = {}

    @property
    def min_interval(self) -> float:
        """Minimum interval between emissions in seconds."""
        return self._min_interval

    def should_emit(self, key: str) -> bool:
        """Check if an event for this key can be emitted now.

        Returns True if enough time has passed since the last emission,
        and updates the last-emit timestamp. Returns False if the event
        should be throttled.
        """
        now = time.monotonic()
        last = self._last_emit.get(key, 0.0)
        if now - last >= self._min_interval:
            self._last_emit[key] = now
            return True
        return False

    def set_pending(self, key: str, event: dict) -> None:
        """Store a throttled event as pending. The latest event wins.

        When an event is throttled, store it so it can be flushed later.
        This ensures the final state is never lost even when throttled.
        """
        self._pending[key] = event

    def pop_pending(self, key: str) -> dict | None:
        """Retrieve and remove the pending event for a key.

        Returns None if no pending event exists. Used to flush the last
        throttled event at the end of a stream.
        """
        return self._pending.pop(key, None)

    def reset(self, key: str) -> None:
        """Reset throttle state for a key (e.g., when an agent finishes)."""
        self._last_emit.pop(key, None)
        self._pending.pop(key, None)

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


# Module-level throttler for text chunk events (max 4 per second per agent)
text_chunk_throttler = EventThrottler(max_per_second=4.0)


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

        # Per-project sequence counters (project_id -> next_seq)
        self._sequence_counters: dict[str, int] = {}

        # Per-project ring buffers for fast in-memory replay
        self._ring_buffers: dict[str, collections.deque] = {}

        # Async write queue for DB persistence (non-blocking)
        self._write_queue: asyncio.Queue | None = None
        self._writer_task: asyncio.Task | None = None

        # Reference to session manager (set via set_session_manager)
        self._session_mgr: SessionManager | None = None

        # Per-project heartbeat background tasks
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}

    def set_session_manager(self, session_mgr: SessionManager):
        """Connect the EventBus to the session manager for DB persistence.

        Must be called once during app initialization.
        """
        self._session_mgr = session_mgr

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

    async def _heartbeat_loop(
        self, project_id: str, status_fn: StatusFn
    ) -> None:
        """Background loop that publishes status_heartbeat events.

        Runs until cancelled (via ``stop_heartbeat``) or until
        ``status_fn`` raises an exception.  Each tick is lightweight:
        only ``project_id``, ``status``, ``active_agents``, and
        ``timestamp`` are included in the event payload.
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
                try:
                    info = await status_fn()
                    event = {
                        "type": "status_heartbeat",
                        "project_id": project_id,
                        "status": info.get("status", "unknown"),
                        "active_agents": info.get("active_agents", 0),
                        "timestamp": time.time(),
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
            self._subscribers = [
                t for t in self._subscribers if t.queue is not queue
            ]

    def _next_sequence(self, project_id: str) -> int:
        """Get and increment the sequence counter for a project."""
        seq = self._sequence_counters.get(project_id, 0) + 1
        self._sequence_counters[project_id] = seq
        return seq

    def _buffer_event(self, project_id: str, event: dict):
        """Add event to the in-memory ring buffer for fast replay."""
        if project_id not in self._ring_buffers:
            self._ring_buffers[project_id] = collections.deque(maxlen=_RING_BUFFER_SIZE)
        self._ring_buffers[project_id].append(event)

    async def publish(self, event: dict):
        """Broadcast an event dict to all subscribers and persist to DB.

        Adds a timestamp and sequence_id if not present. Drops events for
        full queues (slow consumers) rather than blocking the publisher.
        Automatically removes dead subscribers.
        """
        # Copy to prevent shared mutable state across subscribers
        event = {**event}
        if "timestamp" not in event:
            event["timestamp"] = time.time()

        project_id = event.get("project_id", "")

        # Assign sequence ID for ordered replay
        if project_id:
            seq = self._next_sequence(project_id)
            event["sequence_id"] = seq
            # Skip ring buffer for high-frequency ephemeral events
            # to prevent memory pressure (text chunks are fire-and-forget)
            event_type = event.get("type", "")
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
                    except ValueError:
                        pass
            logger.info(
                "EventBus: cleaned up %d dead subscriber(s), %d remaining",
                len(dead),
                len(self._subscribers),
            )

        # Queue for DB persistence (non-blocking)
        event_type = event.get("type", "")
        if project_id and event_type in _PERSIST_EVENT_TYPES:
            if self._write_queue and not self._write_queue.full():
                try:
                    self._write_queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("EventBus: write queue full, dropping DB write for %s", event_type)

    async def publish_throttled(
        self, event: dict, throttle_key: str | None = None
    ) -> bool:
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
        return self._sequence_counters.get(project_id, 0)

    async def _db_writer_loop(self):
        """Background task that batches and writes events to DB.

        Collects events for up to 2 seconds or 50 events, then writes
        them all in a single transaction for efficiency.
        """
        batch: list[dict] = []
        while True:
            try:
                # Guard: _write_queue may be None if stop_writer was called
                if self._write_queue is None:
                    await asyncio.sleep(0.5)
                    continue

                # Wait for first event
                try:
                    event = await asyncio.wait_for(self._write_queue.get(), timeout=5.0)
                    batch.append(event)
                except asyncio.TimeoutError:
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
                data = {k: v for k, v in event.items()
                        if k not in ("project_id", "type", "agent", "timestamp", "sequence_id")}

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
