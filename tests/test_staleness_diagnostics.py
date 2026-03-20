"""
tests/test_staleness_diagnostics.py — Staleness diagnostic accuracy tests.

Scope
-----
Verifies that seconds_since_progress resets correctly on ALL tracked event types
in EventBus.publish(). Previously only 5 completion events triggered
record_progress(), causing false staleness readings while agents were actively
working. After the fix (task_002), 14+ event types reset staleness.

Covers:
- Original completion events still reset progress timer
- New active-work events (agent_update, agent_started, tool_start, agent_thinking) reset timer
- DAG activity events (dag_task_update, task_progress, dag_progress) reset timer
- Orchestration events (delegation, self_healing, plan_delta) reset timer
- project_status with status='running' resets timer
- project_status with status='idle' does NOT reset timer
- Error events with is_error=True do NOT reset progress timer
- Health score transitions: healthy → degraded → critical based on staleness
- Stuckness detection updates last_stuckness, does NOT reset progress
- record_progress resets warnings_count

Naming: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from dashboard.events import EventBus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bus():
    """Fresh EventBus with no session manager (skip DB writes)."""
    b = EventBus()
    return b


PROJECT_ID = "proj_staleness_test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def publish_event(bus: EventBus, event_type: str, project_id: str = PROJECT_ID, **extra):
    """Publish an event and return it."""
    event = {"type": event_type, "project_id": project_id, **extra}
    await bus.publish(event)
    return event


# ---------------------------------------------------------------------------
# Original completion events should reset progress
# ---------------------------------------------------------------------------


class TestOriginalCompletionEventsResetProgress:
    """The original 5 completion events must still call record_progress."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", [
        "agent_finished",
        "task_complete",
        "tool_end",
        "agent_result",
        "agent_final",
    ])
    async def test_completion_event_when_published_should_reset_seconds_since_progress(
        self, bus, event_type
    ):
        # Record old progress to establish a baseline
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]

        # Small delay to ensure timestamps differ
        await asyncio.sleep(0.01)

        await publish_event(bus, event_type)

        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts > old_ts, f"{event_type} should reset progress timer"

    @pytest.mark.asyncio
    async def test_agent_finished_when_is_error_should_not_reset_progress(self, bus):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, "agent_finished", is_error=True)

        # is_error agent_finished triggers record_error, not record_progress
        # (it matches the error branch first)
        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts == old_ts, "agent_finished with is_error should not reset progress"


# ---------------------------------------------------------------------------
# New active-work events should reset progress
# ---------------------------------------------------------------------------


class TestActiveWorkEventsResetProgress:
    """Active work events added in task_002 fix must reset staleness."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", [
        "agent_update",
        "agent_started",
        "tool_start",
        "agent_thinking",
    ])
    async def test_active_work_event_when_published_should_reset_progress(
        self, bus, event_type
    ):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, event_type)

        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts > old_ts, f"{event_type} should reset progress timer"


# ---------------------------------------------------------------------------
# DAG activity events should reset progress
# ---------------------------------------------------------------------------


class TestDagActivityEventsResetProgress:
    """DAG-level events must reset the staleness timer."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", [
        "dag_task_update",
        "task_progress",
        "dag_progress",
    ])
    async def test_dag_event_when_published_should_reset_progress(
        self, bus, event_type
    ):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, event_type)

        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts > old_ts, f"{event_type} should reset progress timer"


# ---------------------------------------------------------------------------
# Orchestration events should reset progress
# ---------------------------------------------------------------------------


class TestOrchestrationEventsResetProgress:
    """Orchestration events (delegation, self_healing, plan_delta) must reset timer."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", [
        "delegation",
        "self_healing",
        "plan_delta",
    ])
    async def test_orchestration_event_when_published_should_reset_progress(
        self, bus, event_type
    ):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, event_type)

        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts > old_ts, f"{event_type} should reset progress timer"


# ---------------------------------------------------------------------------
# project_status event — only 'running' resets progress
# ---------------------------------------------------------------------------


class TestProjectStatusEventProgressReset:
    """project_status events should only reset on status='running'."""

    @pytest.mark.asyncio
    async def test_project_status_when_running_should_reset_progress(self, bus):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, "project_status", status="running")

        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts > old_ts, "project_status running should reset progress"

    @pytest.mark.asyncio
    async def test_project_status_when_idle_should_not_reset_progress(self, bus):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, "project_status", status="idle")

        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts == old_ts, "project_status idle should not reset progress"

    @pytest.mark.asyncio
    async def test_project_status_when_paused_should_not_reset_progress(self, bus):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, "project_status", status="paused")

        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts == old_ts, "project_status paused should not reset progress"


# ---------------------------------------------------------------------------
# Error events should record error, NOT reset progress
# ---------------------------------------------------------------------------


class TestErrorEventsDoNotResetProgress:
    """Error events track separately from progress."""

    @pytest.mark.asyncio
    async def test_task_error_when_published_should_record_error_not_progress(self, bus):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, "task_error", is_error=True)

        assert PROJECT_ID in bus._last_error
        assert bus._last_progress[PROJECT_ID] == old_ts

    @pytest.mark.asyncio
    async def test_agent_finished_error_when_published_should_record_error(self, bus):
        await publish_event(bus, "agent_finished", is_error=True)

        assert PROJECT_ID in bus._last_error


# ---------------------------------------------------------------------------
# Stuckness event behavior
# ---------------------------------------------------------------------------


class TestStucknessEvent:
    """stuckness_detected events must update stuckness state, not progress."""

    @pytest.mark.asyncio
    async def test_stuckness_detected_when_published_should_record_stuckness(self, bus):
        await publish_event(bus, "stuckness_detected")

        assert PROJECT_ID in bus._last_stuckness

    @pytest.mark.asyncio
    async def test_stuckness_detected_when_published_should_not_reset_progress(self, bus):
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await publish_event(bus, "stuckness_detected")

        assert bus._last_progress[PROJECT_ID] == old_ts

    @pytest.mark.asyncio
    async def test_stuckness_detected_when_published_should_increment_warnings(self, bus):
        await publish_event(bus, "stuckness_detected")
        assert bus._warnings_count[PROJECT_ID] == 1

        await publish_event(bus, "stuckness_detected")
        assert bus._warnings_count[PROJECT_ID] == 2


# ---------------------------------------------------------------------------
# record_progress resets warnings_count
# ---------------------------------------------------------------------------


class TestProgressResetsWarnings:
    """Progress events should reset the warnings counter."""

    @pytest.mark.asyncio
    async def test_progress_when_after_warnings_should_reset_count(self, bus):
        # Simulate some stuckness warnings
        bus.record_stuckness(PROJECT_ID)
        bus.record_stuckness(PROJECT_ID)
        assert bus._warnings_count[PROJECT_ID] == 2

        # Progress should clear warnings
        bus.record_progress(PROJECT_ID)
        assert bus._warnings_count[PROJECT_ID] == 0


# ---------------------------------------------------------------------------
# Health score transitions based on staleness
# ---------------------------------------------------------------------------


class TestHealthScoreTransitions:
    """get_diagnostics() health_score must reflect staleness correctly."""

    def test_health_score_when_no_data_should_be_healthy(self, bus):
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "healthy"
        assert diag["seconds_since_progress"] is None

    def test_health_score_when_recent_progress_should_be_healthy(self, bus):
        bus.record_progress(PROJECT_ID)
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "healthy"
        assert diag["seconds_since_progress"] is not None
        assert diag["seconds_since_progress"] < 5

    def test_health_score_when_stale_46s_should_be_degraded(self, bus):
        # Simulate 46 seconds since last progress
        bus._last_progress[PROJECT_ID] = time.time() - 46
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "degraded"

    def test_health_score_when_stale_91s_should_be_critical(self, bus):
        # Simulate 91 seconds since last progress
        bus._last_progress[PROJECT_ID] = time.time() - 91
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "critical"

    def test_health_score_when_recent_stuckness_should_be_critical(self, bus):
        bus.record_stuckness(PROJECT_ID)
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "critical"

    def test_health_score_when_recent_error_should_be_degraded(self, bus):
        bus.record_error(PROJECT_ID)
        bus.record_progress(PROJECT_ID)  # Progress is fresh, but error exists
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "degraded"

    def test_health_score_when_error_old_should_be_healthy(self, bus):
        bus._last_error[PROJECT_ID] = time.time() - 121  # >120s ago
        bus.record_progress(PROJECT_ID)
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "healthy"


# ---------------------------------------------------------------------------
# Integration: publish triggers correct diagnostic pathway
# ---------------------------------------------------------------------------


class TestPublishDiagnosticIntegration:
    """Full integration: publish → diagnostics path verification."""

    @pytest.mark.asyncio
    async def test_all_tracked_events_when_published_should_keep_health_healthy(self, bus):
        """Publishing any tracked event should keep seconds_since_progress low."""
        all_tracked = [
            "agent_finished", "task_complete", "tool_end", "agent_result", "agent_final",
            "agent_update", "agent_started", "tool_start", "agent_thinking",
            "dag_task_update", "task_progress", "dag_progress",
            "delegation", "self_healing", "plan_delta",
        ]
        for evt in all_tracked:
            await publish_event(bus, evt)

        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "healthy"
        assert diag["seconds_since_progress"] < 2

    @pytest.mark.asyncio
    async def test_untracked_event_when_published_should_not_affect_diagnostics(self, bus):
        """Events not in the tracked list should not affect diagnostics."""
        await publish_event(bus, "some_random_event")

        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["seconds_since_progress"] is None  # no progress recorded

    @pytest.mark.asyncio
    async def test_rapid_events_when_published_should_update_progress_each_time(self, bus):
        """Multiple rapid events should each update the progress timestamp."""
        timestamps = []
        for _ in range(5):
            await publish_event(bus, "agent_update")
            timestamps.append(bus._last_progress[PROJECT_ID])
            await asyncio.sleep(0.005)

        # Each should be >= previous (monotonically non-decreasing)
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]
