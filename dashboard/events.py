"""Central pub/sub EventBus for broadcasting agent activity to WebSocket clients.

Enhanced with:
- Activity persistence: every event is saved to DB for cross-device sync
- Sequence IDs: monotonic per-project counter for gap-free replay
- In-memory ring buffer: fast replay for recent reconnects without DB hit
- Batch write queue: non-blocking DB writes to avoid slowing the publisher
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from session_manager import SessionManager

logger = logging.getLogger(__name__)

# How many consecutive publish failures before a subscriber is considered dead
_MAX_CONSECUTIVE_FAILURES = 10

# Ring buffer size per project (in-memory, for fast replay)
_RING_BUFFER_SIZE = 500

# Events that should be persisted to DB (skip ephemeral ones like ping)
_PERSIST_EVENT_TYPES = frozenset({
    "agent_update", "agent_result", "agent_final", "project_status",
    "tool_use", "agent_started", "agent_finished", "delegation",
    "loop_progress", "approval_request", "history_cleared",
    "task_complete", "task_error",
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

    def set_session_manager(self, session_mgr: SessionManager):
        """Connect the EventBus to the session manager for DB persistence.

        Must be called once during app initialization.
        """
        self._session_mgr = session_mgr

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
