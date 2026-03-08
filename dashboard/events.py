"""Central pub/sub EventBus for broadcasting agent activity to WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# How many consecutive publish failures before a subscriber is considered dead
_MAX_CONSECUTIVE_FAILURES = 10


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
    """Simple async pub/sub for real-time event broadcasting.

    WebSocket handlers subscribe (get a Queue), and any part of the
    application can publish events that fan out to all subscribers.

    Dead subscribers (those with persistently full queues) are automatically
    cleaned up during publish to prevent memory leaks.
    """

    def __init__(self):
        self._subscribers: list[_TrackedQueue] = []
        self._lock = asyncio.Lock()

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

    async def publish(self, event: dict):
        """Broadcast an event dict to all subscribers.

        Adds a timestamp if not present. Drops events for full queues
        (slow consumers) rather than blocking the publisher.
        Automatically removes dead subscribers.
        """
        if "timestamp" not in event:
            event["timestamp"] = time.time()

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

    @property
    def subscriber_count(self) -> int:
        """Return the current number of subscribers (useful for monitoring)."""
        return len(self._subscribers)


# Module-level singleton
event_bus = EventBus()
