"""Central pub/sub EventBus for broadcasting agent activity to WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class EventBus:
    """Simple async pub/sub for real-time event broadcasting.

    WebSocket handlers subscribe (get a Queue), and any part of the
    application can publish events that fan out to all subscribers.
    """

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue and register it."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """Remove a subscriber queue."""
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    async def publish(self, event: dict):
        """Broadcast an event dict to all subscribers.

        Adds a timestamp if not present. Drops events for full queues
        (slow consumers) rather than blocking the publisher.
        """
        if "timestamp" not in event:
            event["timestamp"] = time.time()

        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — drop oldest event and try again
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


# Module-level singleton
event_bus = EventBus()
