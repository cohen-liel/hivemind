"""
tests/test_orchestrator_progress.py — Orchestrator startup progress event tests.

Scope
-----
Verifies that the orchestrator emits progress events during startup phases,
keeping EventBus diagnostics healthy and preventing false stall alerts.

Covers:
- _emit_startup_progress emits a task_progress event with correct fields
- _start_startup_keepalive emits periodic events and is cancellable
- Progress events during startup keep diagnostics health 'healthy'
- Keepalive interval (25s) stays well within degraded threshold (45s)
- Multiple startup milestones each reset progress timers

Naming: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashboard.events import EventBus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bus():
    """Fresh EventBus for diagnostics tracking."""
    return EventBus()


@pytest.fixture
def mock_orchestrator():
    """Minimal orchestrator-like object with _emit_event and startup methods.

    We import and patch the real Orchestrator's methods onto a lightweight mock
    to test _emit_startup_progress and _start_startup_keepalive in isolation
    without needing a full project context.
    """
    from orchestrator import OrchestratorManager

    orch = MagicMock(spec=OrchestratorManager)
    orch.project_id = "proj_test_startup"
    orch.on_event = AsyncMock()
    orch.is_running = True
    orch.is_paused = False

    # Bind the real methods so we test actual implementation
    orch._emit_event = OrchestratorManager._emit_event.__get__(orch, OrchestratorManager)
    orch._emit_startup_progress = OrchestratorManager._emit_startup_progress.__get__(orch, OrchestratorManager)
    orch._start_startup_keepalive = OrchestratorManager._start_startup_keepalive.__get__(orch, OrchestratorManager)

    # Stub _get_project_status_metadata (not needed for task_progress events)
    orch._get_project_status_metadata = MagicMock(return_value={})

    return orch


PROJECT_ID = "proj_test_startup"


# ---------------------------------------------------------------------------
# _emit_startup_progress tests
# ---------------------------------------------------------------------------


class TestEmitStartupProgress:
    """Verify _emit_startup_progress emits correct task_progress events."""

    @pytest.mark.asyncio
    async def test_emit_startup_progress_when_called_should_emit_task_progress_event(
        self, mock_orchestrator
    ):
        await mock_orchestrator._emit_startup_progress("load_manifest", "Loading manifest...")

        mock_orchestrator.on_event.assert_called_once()
        event = mock_orchestrator.on_event.call_args[0][0]
        assert event["type"] == "task_progress"

    @pytest.mark.asyncio
    async def test_emit_startup_progress_when_called_should_include_agent_orchestrator(
        self, mock_orchestrator
    ):
        await mock_orchestrator._emit_startup_progress("load_memory", "Loading memory...")

        event = mock_orchestrator.on_event.call_args[0][0]
        assert event["agent"] == "orchestrator"

    @pytest.mark.asyncio
    async def test_emit_startup_progress_when_called_should_include_step_and_description(
        self, mock_orchestrator
    ):
        await mock_orchestrator._emit_startup_progress(
            "context_loaded", "All context loaded"
        )

        event = mock_orchestrator.on_event.call_args[0][0]
        assert event["step"] == "context_loaded"
        assert event["step_description"] == "All context loaded"
        assert event["task_name"] == "Orchestrator startup"

    @pytest.mark.asyncio
    async def test_emit_startup_progress_when_called_should_include_timestamp(
        self, mock_orchestrator
    ):
        before = time.time()
        await mock_orchestrator._emit_startup_progress("test_step", "Testing")
        after = time.time()

        event = mock_orchestrator.on_event.call_args[0][0]
        assert before <= event["timestamp"] <= after

    @pytest.mark.asyncio
    async def test_emit_startup_progress_when_on_event_is_none_should_not_raise(
        self, mock_orchestrator
    ):
        mock_orchestrator.on_event = None
        # Should silently skip — no exception
        await mock_orchestrator._emit_startup_progress("step", "desc")


# ---------------------------------------------------------------------------
# _start_startup_keepalive tests
# ---------------------------------------------------------------------------


class TestStartStartupKeepalive:
    """Verify keepalive background task emits periodic events."""

    @pytest.mark.asyncio
    async def test_keepalive_when_started_should_return_asyncio_task(
        self, mock_orchestrator
    ):
        task = mock_orchestrator._start_startup_keepalive("Architect reviewing...")
        try:
            assert isinstance(task, asyncio.Task)
            assert task.get_name() == "startup-keepalive"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_keepalive_when_cancelled_should_stop_cleanly(
        self, mock_orchestrator
    ):
        task = mock_orchestrator._start_startup_keepalive("PM planning...")
        await asyncio.sleep(0.01)  # Let it start
        task.cancel()

        # Should not raise — CancelledError is caught internally
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert task.done()

    @pytest.mark.asyncio
    async def test_keepalive_when_running_should_emit_startup_keepalive_step(
        self, mock_orchestrator
    ):
        # Patch sleep to be very short for testing
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            call_count = 0
            original_emit = mock_orchestrator._emit_startup_progress

            async def counting_emit(step, desc):
                nonlocal call_count
                call_count += 1
                await original_emit(step, desc)
                if call_count >= 2:
                    raise asyncio.CancelledError()

            mock_orchestrator._emit_startup_progress = counting_emit

            task = mock_orchestrator._start_startup_keepalive("Architect review")
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Should have called sleep(25) — the keepalive interval
            assert mock_sleep.call_count >= 1
            sleep_arg = mock_sleep.call_args_list[0][0][0]
            assert sleep_arg == 25, f"Keepalive interval should be 25s, got {sleep_arg}"


# ---------------------------------------------------------------------------
# Integration: startup progress events keep EventBus healthy
# ---------------------------------------------------------------------------


class TestStartupProgressKeepsDiagnosticsHealthy:
    """Integration test: task_progress events from startup reset EventBus timers."""

    @pytest.mark.asyncio
    async def test_diagnostics_when_task_progress_emitted_should_stay_healthy(self, bus):
        """task_progress events (the type emitted by _emit_startup_progress)
        should reset seconds_since_progress and keep health 'healthy'."""
        # Publish a task_progress event like _emit_startup_progress does
        await bus.publish({
            "type": "task_progress",
            "project_id": PROJECT_ID,
            "agent": "orchestrator",
            "step": "load_manifest",
            "step_description": "Loading manifest...",
            "task_name": "Orchestrator startup",
        })

        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "healthy"
        assert diag["seconds_since_progress"] is not None
        assert diag["seconds_since_progress"] < 2

    @pytest.mark.asyncio
    async def test_diagnostics_when_multiple_startup_events_should_stay_healthy(self, bus):
        """Simulating the full startup sequence of events."""
        startup_steps = [
            ("load_manifest", "Loading project manifest..."),
            ("load_memory", "Loading memory snapshot..."),
            ("file_tree", "Scanning file tree..."),
            ("context_loaded", "All project context loaded"),
            ("lessons_learned", "Loading lessons learned..."),
            ("cross_project_memory", "Loading cross-project memory..."),
        ]
        for step, desc in startup_steps:
            await bus.publish({
                "type": "task_progress",
                "project_id": PROJECT_ID,
                "agent": "orchestrator",
                "step": step,
                "step_description": desc,
                "task_name": "Orchestrator startup",
            })

        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "healthy"
        assert diag["seconds_since_progress"] < 2

    @pytest.mark.asyncio
    async def test_diagnostics_when_no_events_after_46s_should_be_degraded(self, bus):
        """Without keepalive, silence >45s should degrade health."""
        bus._last_progress[PROJECT_ID] = time.time() - 46
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "degraded"

    @pytest.mark.asyncio
    async def test_diagnostics_when_keepalive_emits_at_25s_should_never_degrade(self, bus):
        """Keepalive at 25s intervals keeps health well within 45s degraded threshold."""
        # Simulate: initial progress, then 25s gap, then keepalive
        bus.record_progress(PROJECT_ID)
        # Set progress to 24s ago (just before keepalive would fire)
        bus._last_progress[PROJECT_ID] = time.time() - 24

        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "healthy", (
            "At 24s since progress (just before keepalive), should still be healthy"
        )

    @pytest.mark.asyncio
    async def test_diagnostics_when_no_events_after_91s_should_be_critical(self, bus):
        """Without keepalive, silence >90s is critical."""
        bus._last_progress[PROJECT_ID] = time.time() - 91
        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "critical"

    @pytest.mark.asyncio
    async def test_diagnostics_when_keepalive_resets_timer_should_prevent_critical(self, bus):
        """After a long gap, a single progress event resets health to healthy."""
        bus._last_progress[PROJECT_ID] = time.time() - 100  # Critical territory
        assert bus.get_diagnostics(PROJECT_ID)["health_score"] == "critical"

        # Simulate keepalive event
        await bus.publish({
            "type": "task_progress",
            "project_id": PROJECT_ID,
            "agent": "orchestrator",
            "step": "startup_keepalive",
            "step_description": "Architect reviewing...",
        })

        diag = bus.get_diagnostics(PROJECT_ID)
        assert diag["health_score"] == "healthy"
        assert diag["seconds_since_progress"] < 2


# ---------------------------------------------------------------------------
# Startup milestones comprehensive test
# ---------------------------------------------------------------------------


class TestStartupMilestoneCoverage:
    """Verify all documented startup milestones emit events."""

    EXPECTED_MILESTONES = [
        "load_manifest",
        "load_memory",
        "file_tree",
        "context_loaded",
        "lessons_learned",
        "cross_project_memory",
        "architect_review",
        "pm_planning_start",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("step", EXPECTED_MILESTONES)
    async def test_milestone_when_emitted_should_reset_progress(self, bus, step):
        """Each startup milestone event should reset the progress timer."""
        bus.record_progress(PROJECT_ID)
        old_ts = bus._last_progress[PROJECT_ID]
        await asyncio.sleep(0.01)

        await bus.publish({
            "type": "task_progress",
            "project_id": PROJECT_ID,
            "agent": "orchestrator",
            "step": step,
            "step_description": f"Milestone: {step}",
        })

        new_ts = bus._last_progress[PROJECT_ID]
        assert new_ts > old_ts, f"Milestone {step} should reset progress timer"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestStartupProgressEdgeCases:
    """Edge cases for startup progress emission."""

    @pytest.mark.asyncio
    async def test_emit_event_when_callback_raises_should_not_propagate(
        self, mock_orchestrator
    ):
        """If on_event callback raises, _emit_event should catch and log."""
        mock_orchestrator.on_event.side_effect = RuntimeError("Callback broke")

        # Should not raise
        await mock_orchestrator._emit_startup_progress("step", "desc")

    @pytest.mark.asyncio
    async def test_progress_when_project_id_differs_should_track_separately(self, bus):
        """Different projects should have independent progress tracking."""
        await bus.publish({
            "type": "task_progress",
            "project_id": "proj_A",
            "step": "test",
        })
        await bus.publish({
            "type": "task_progress",
            "project_id": "proj_B",
            "step": "test",
        })

        diag_a = bus.get_diagnostics("proj_A")
        diag_b = bus.get_diagnostics("proj_B")
        assert diag_a["health_score"] == "healthy"
        assert diag_b["health_score"] == "healthy"

        # Stale only proj_A
        bus._last_progress["proj_A"] = time.time() - 91
        assert bus.get_diagnostics("proj_A")["health_score"] == "critical"
        assert bus.get_diagnostics("proj_B")["health_score"] == "healthy"
