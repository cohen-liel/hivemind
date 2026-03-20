"""Tests for EventBus resilience — overflow buffer, health metrics, replay, throttler.

Covers: dashboard/events.py (task_003)
"""

from __future__ import annotations

import asyncio
import collections
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dashboard.events import (
    CRITICAL_EVENT_TYPES,
    EventBus,
    EventThrottler,
    _BacklogQueue,
    _TrackedQueue,
    _OVERFLOW_BUFFER_SIZE,
    _RING_BUFFER_MIN,
    _RING_BUFFER_MAX,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_event(
    event_type: str = "agent_update",
    project_id: str = "proj1",
    **kwargs,
) -> dict:
    ev = {"type": event_type, "project_id": project_id}
    ev.update(kwargs)
    return ev


# ── Tests: _BacklogQueue ───────────────────────────────────────────────────


class TestBacklogQueue:
    def test_put_and_get_when_normal_should_fifo(self):
        q = _BacklogQueue(maxsize=10)
        q.put_nowait({"id": 1})
        q.put_nowait({"id": 2})
        assert q.qsize() == 2
        assert q.get_nowait() == {"id": 1}
        assert q.get_nowait() == {"id": 2}

    def test_put_when_full_should_evict_oldest(self):
        q = _BacklogQueue(maxsize=2)
        q.put_nowait({"id": 1})
        q.put_nowait({"id": 2})
        evicted = q.put_nowait({"id": 3})
        assert evicted == 1
        assert q.total_evicted == 1
        assert q.get_nowait() == {"id": 2}

    def test_get_nowait_when_empty_should_raise(self):
        q = _BacklogQueue(maxsize=5)
        with pytest.raises(IndexError):
            q.get_nowait()

    def test_drain_when_items_present_should_return_all(self):
        q = _BacklogQueue(maxsize=10)
        for i in range(5):
            q.put_nowait({"id": i})
        items = q.drain()
        assert len(items) == 5
        assert q.empty()

    def test_drain_with_max_items_should_limit(self):
        q = _BacklogQueue(maxsize=10)
        for i in range(5):
            q.put_nowait({"id": i})
        items = q.drain(max_items=3)
        assert len(items) == 3
        assert q.qsize() == 2

    def test_write_to_fallback_should_increment_counters(self, tmp_path):
        q = _BacklogQueue(maxsize=10)
        # Patch the fallback dir
        with patch("dashboard.events._FALLBACK_DIR", tmp_path):
            written = q.write_to_fallback([{"id": 1}, {"id": 2}])
        assert written == 2
        assert q.total_fallback_writes == 2
        assert q.consecutive_db_failures == 1

    def test_write_to_fallback_when_empty_should_return_zero(self):
        q = _BacklogQueue(maxsize=10)
        assert q.write_to_fallback([]) == 0

    def test_record_db_success_should_reset_failure_counter(self, tmp_path):
        q = _BacklogQueue(maxsize=10)
        with patch("dashboard.events._FALLBACK_DIR", tmp_path):
            q.write_to_fallback([{"id": 1}])
        assert q.consecutive_db_failures == 1
        q.record_db_success()
        assert q.consecutive_db_failures == 0

    def test_recover_fallback_when_files_exist_should_recover(self, tmp_path):
        q = _BacklogQueue(maxsize=10)
        fallback_file = tmp_path / "events_123.jsonl"
        fallback_file.write_text(
            json.dumps({"id": 1}) + "\n" + json.dumps({"id": 2}) + "\n"
        )
        with patch("dashboard.events._FALLBACK_DIR", tmp_path):
            recovered = q.recover_fallback_events()
        assert len(recovered) == 2
        assert not fallback_file.exists()  # File should be removed

    def test_recover_fallback_when_no_dir_should_return_empty(self, tmp_path):
        q = _BacklogQueue(maxsize=10)
        with patch("dashboard.events._FALLBACK_DIR", tmp_path / "nonexistent"):
            recovered = q.recover_fallback_events()
        assert recovered == []

    @pytest.mark.asyncio
    async def test_wait_for_item_when_empty_should_timeout(self):
        q = _BacklogQueue(maxsize=10)
        result = await q.wait_for_item(timeout=0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_item_when_has_items_should_return_true(self):
        q = _BacklogQueue(maxsize=10)
        q.put_nowait({"id": 1})
        result = await q.wait_for_item(timeout=0.1)
        assert result is True


# ── Tests: _TrackedQueue — overflow buffer ─────────────────────────────────


class TestTrackedQueueOverflow:
    def test_put_when_full_and_critical_should_go_to_overflow(self):
        tq = _TrackedQueue(maxsize=1, subscriber_id="s1")
        tq.put_nowait(_make_event("agent_update"))  # Fills the queue
        assert tq.queue.qsize() == 1

        # Critical event should go to overflow when queue full
        result = tq.put_nowait(_make_event("plan_delta"))
        assert result is True
        assert tq.overflow_count == 1

    def test_put_when_full_and_noncritical_should_drop_oldest(self):
        tq = _TrackedQueue(maxsize=1, subscriber_id="s2")
        tq.put_nowait(_make_event("agent_update"))
        # Non-critical event drops oldest
        result = tq.put_nowait(_make_event("agent_update", data="new"))
        assert result is True
        assert tq._total_dropped >= 1

    def test_drain_overflow_should_return_and_clear(self):
        tq = _TrackedQueue(maxsize=1, subscriber_id="s3")
        tq.put_nowait(_make_event("agent_update"))
        tq.put_nowait(_make_event("task_graph"))  # Goes to overflow
        tq.put_nowait(_make_event("plan_delta"))  # Goes to overflow

        items = tq.drain_overflow()
        assert len(items) == 2
        assert tq.overflow_count == 0

    def test_overflow_buffer_should_respect_max_size(self):
        tq = _TrackedQueue(maxsize=1, subscriber_id="s4")
        tq.put_nowait(_make_event("agent_update"))  # Fill queue

        # Overflow more than buffer size
        for i in range(_OVERFLOW_BUFFER_SIZE + 10):
            tq.put_nowait(_make_event("task_graph", seq=i))

        assert tq.overflow_count <= _OVERFLOW_BUFFER_SIZE

    def test_is_dead_when_too_many_failures_should_be_true(self):
        tq = _TrackedQueue(maxsize=1, subscriber_id="s5")
        tq.failures = 10
        assert tq.is_dead is True

    def test_is_dead_when_few_failures_should_be_false(self):
        tq = _TrackedQueue(maxsize=1, subscriber_id="s6")
        tq.failures = 3
        assert tq.is_dead is False

    def test_get_health_metrics_should_return_all_fields(self):
        tq = _TrackedQueue(maxsize=10, subscriber_id="sub-1")
        tq.put_nowait(_make_event("agent_update"))
        metrics = tq.get_health_metrics()
        assert metrics["subscriber_id"] == "sub-1"
        assert "queue_size" in metrics
        assert "overflow_size" in metrics
        assert "total_delivered" in metrics
        assert "total_dropped" in metrics
        assert "avg_latency_ms" in metrics
        assert "is_dead" in metrics
        assert metrics["total_delivered"] == 1


# ── Tests: EventThrottler ─────────────────────────────────────────────────


class TestEventThrottler:
    def test_should_emit_when_first_call_should_return_true(self):
        throttler = EventThrottler(max_per_second=10.0)
        assert throttler.should_emit("key1") is True

    def test_should_emit_when_too_fast_should_return_false(self):
        throttler = EventThrottler(max_per_second=1.0)
        throttler.should_emit("key1")  # First: True
        assert throttler.should_emit("key1") is False  # Too fast

    def test_set_pending_and_pop_should_work(self):
        throttler = EventThrottler(max_per_second=1.0)
        event = {"type": "test", "data": "hello"}
        throttler.set_pending("key1", event)
        result = throttler.pop_pending("key1")
        assert result == event

    def test_pop_pending_when_empty_should_return_none(self):
        throttler = EventThrottler(max_per_second=1.0)
        assert throttler.pop_pending("nonexistent") is None

    def test_reset_should_clear_state(self):
        throttler = EventThrottler(max_per_second=1.0)
        throttler.should_emit("key1")
        throttler.set_pending("key1", {"data": "x"})
        throttler.reset("key1")
        # After reset, should_emit should allow immediately
        assert throttler.should_emit("key1") is True
        assert throttler.pop_pending("key1") is None

    def test_cleanup_should_remove_stale_entries(self):
        throttler = EventThrottler(max_per_second=10.0)
        throttler.should_emit("key1")
        # Force the timestamp to be old
        throttler._last_emit["key1"] = time.monotonic() - 120
        throttler.cleanup(max_age=60.0)
        # key1 should be cleaned up
        assert "key1" not in throttler._last_emit

    def test_init_when_invalid_params_should_raise(self):
        with pytest.raises(ValueError):
            EventThrottler(max_per_second=0)
        with pytest.raises(ValueError):
            EventThrottler(max_per_second=-1)
        with pytest.raises(ValueError):
            EventThrottler(max_per_second=1.0, max_keys=0)


# ── Tests: EventBus core ───────────────────────────────────────────────────


class TestEventBus:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish_should_deliver(self):
        bus = EventBus()
        queue = await bus.subscribe(subscriber_id="test-sub")
        event = _make_event("agent_update")
        await bus.publish(event)
        # subscribe() returns asyncio.Queue directly
        item = queue.get_nowait()
        assert item["type"] == "agent_update"
        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_unsubscribe_should_remove_subscriber(self):
        bus = EventBus()
        queue = await bus.subscribe(subscriber_id="test-sub-2")
        await bus.unsubscribe(queue)
        assert queue not in bus._subscribers

    @pytest.mark.asyncio
    async def test_publish_should_assign_sequence_id(self):
        bus = EventBus()
        queue = await bus.subscribe(subscriber_id="test-sub-3")
        await bus.publish(_make_event("agent_update", project_id="proj1"))
        await bus.publish(_make_event("agent_update", project_id="proj1"))
        item1 = queue.get_nowait()
        item2 = queue.get_nowait()
        assert item1.get("sequence_id", 0) < item2.get("sequence_id", 0)
        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_get_buffered_events_should_return_recent(self):
        bus = EventBus()
        await bus.publish(_make_event("agent_update", project_id="proj1"))
        await bus.publish(_make_event("tool_use", project_id="proj1"))
        events = bus.get_buffered_events("proj1", since_sequence=0)
        assert len(events) >= 2

    @pytest.mark.asyncio
    async def test_get_latest_sequence_should_return_highest(self):
        bus = EventBus()
        await bus.publish(_make_event("agent_update", project_id="proj1"))
        await bus.publish(_make_event("agent_update", project_id="proj1"))
        seq = bus.get_latest_sequence("proj1")
        assert seq >= 2

    @pytest.mark.asyncio
    async def test_get_buffered_events_range_should_filter(self):
        bus = EventBus()
        for i in range(10):
            await bus.publish(_make_event("agent_update", project_id="proj1"))
        events = bus.get_buffered_events_range("proj1", from_seq=3, to_seq=7)
        for ev in events:
            assert 3 <= ev.get("sequence_id", 0) <= 7

    @pytest.mark.asyncio
    async def test_clear_project_events_should_remove_buffer(self):
        bus = EventBus()
        await bus.publish(_make_event("agent_update", project_id="proj1"))
        bus.clear_project_events("proj1")
        events = bus.get_buffered_events("proj1", since_sequence=0)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_get_subscriber_health_should_return_list(self):
        bus = EventBus()
        queue = await bus.subscribe(subscriber_id="health-sub")
        health = bus.get_subscriber_health()
        assert len(health) >= 1
        assert health[0]["subscriber_id"] == "health-sub"
        await bus.unsubscribe(queue)

    @pytest.mark.asyncio
    async def test_get_subscriber_health_summary_should_aggregate(self):
        bus = EventBus()
        q1 = await bus.subscribe(subscriber_id="s1")
        q2 = await bus.subscribe(subscriber_id="s2")
        summary = bus.get_subscriber_health_summary()
        assert summary["total_subscribers"] >= 2
        assert "healthy" in summary
        assert "degraded" in summary
        assert "dead" in summary
        await bus.unsubscribe(q1)
        await bus.unsubscribe(q2)


# ── Tests: CRITICAL_EVENT_TYPES ────────────────────────────────────────────


class TestCriticalEventTypes:
    def test_critical_types_should_include_required_events(self):
        required = {"plan_delta", "task_graph", "execution_error", "agent_finished"}
        assert required.issubset(CRITICAL_EVENT_TYPES)

    def test_critical_types_should_be_frozenset(self):
        assert isinstance(CRITICAL_EVENT_TYPES, frozenset)


# ── Tests: Diagnostics ────────────────────────────────────────────────────


class TestEventBusDiagnostics:
    def test_record_stuckness_and_diagnostics_should_reflect(self):
        bus = EventBus()
        bus.record_stuckness("proj1")
        diag = bus.get_diagnostics("proj1")
        assert diag["health_score"] == "critical"
        assert diag["warnings_count"] == 1

    def test_record_progress_should_reset_warnings(self):
        bus = EventBus()
        bus.record_stuckness("proj1")
        bus.record_progress("proj1")
        diag = bus.get_diagnostics("proj1")
        assert diag["warnings_count"] == 0

    def test_diagnostics_when_no_activity_should_be_healthy(self):
        bus = EventBus()
        diag = bus.get_diagnostics("proj1")
        assert diag["health_score"] == "healthy"

    def test_record_error_should_mark_degraded(self):
        bus = EventBus()
        bus.record_error("proj1")
        diag = bus.get_diagnostics("proj1")
        assert diag["health_score"] == "degraded"
