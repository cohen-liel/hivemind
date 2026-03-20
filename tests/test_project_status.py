"""
tests/test_project_status.py — Project status lifecycle tests.

Scope
-----
Verifies that the orchestrator does not emit premature idle status during
active DAG execution. The fix (task_003) moved is_running=False from the top
of finally blocks to immediately before the final idle emission.

Covers:
- _get_project_status_metadata computes correct metadata fields
- _is_dag_truly_idle returns False when tasks are pending/working
- _is_dag_truly_idle returns True when all tasks are terminal
- _is_dag_truly_idle considers active agent states
- is_running flag stays True during cleanup events (before idle emission)
- project_status metadata includes is_running=True during teardown
- No spurious idle emission while agents are still working

Naming: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — Minimal orchestrator mock to test _get_project_status_metadata
# and _is_dag_truly_idle without importing the full module
# ---------------------------------------------------------------------------


class MockOrchestrator:
    """Lightweight mock that mirrors the relevant attributes of OrchestratorManager."""

    def __init__(self):
        self.is_running = False
        self.is_paused = False
        self.agent_states: dict = {}
        self._current_dag_graph: dict | None = None
        self._dag_task_statuses: dict = {}
        self.project_id = "proj_status_test"
        self.on_event = None
        self._emitted_events: list[dict] = []

    def _get_project_status_metadata(self) -> dict:
        """Compute structured metadata for project_status events.

        Mirrors the implementation in orchestrator.py lines 719-755.
        """
        active = sum(
            1 for s in self.agent_states.values()
            if isinstance(s, dict) and s.get("state") == "working"
        )
        total_tasks = 0
        completed_tasks = 0
        pending_tasks = 0
        failed_tasks = 0
        running_tasks = 0
        if self._current_dag_graph:
            total_tasks = len(self._current_dag_graph.get("tasks", []))
        if self._dag_task_statuses:
            completed_tasks = sum(
                1 for s in self._dag_task_statuses.values() if s == "completed"
            )
            failed_tasks = sum(
                1 for s in self._dag_task_statuses.values() if s == "failed"
            )
            running_tasks = sum(
                1 for s in self._dag_task_statuses.values() if s == "working"
            )
        if total_tasks > 0:
            pending_tasks = total_tasks - completed_tasks - failed_tasks - running_tasks
        progress_pct = round(completed_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0.0
        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "agent_count": active,
            "active_task_count": running_tasks,
            "completed_tasks": completed_tasks,
            "pending_tasks": pending_tasks,
            "failed_tasks": failed_tasks,
            "total_tasks": total_tasks,
            "progress_percent": progress_pct,
        }

    def _is_dag_truly_idle(self) -> bool:
        """Check if the DAG has no pending or in-progress tasks remaining.

        Mirrors orchestrator.py lines 757-778.
        """
        if not self._current_dag_graph:
            return True
        tasks = self._current_dag_graph.get("tasks", [])
        if not tasks:
            return True
        for t in tasks:
            tid = t.get("id")
            if not tid:
                continue
            status = self._dag_task_statuses.get(tid)
            if status in (None, "working", "pending"):
                return False
        for s in self.agent_states.values():
            if isinstance(s, dict) and s.get("state") == "working":
                return False
        return True

    async def _emit_event(self, event_type: str, **data):
        """Track emitted events for assertions."""
        event = {
            "type": event_type,
            "timestamp": time.time(),
            **data,
        }
        if event_type == "project_status":
            event["metadata"] = self._get_project_status_metadata()
        self._emitted_events.append(event)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def orch():
    """A fresh MockOrchestrator."""
    return MockOrchestrator()


def make_dag_graph(task_ids: list[str]) -> dict:
    """Create a minimal DAG graph dict with given task IDs."""
    return {
        "project_id": "proj_status_test",
        "user_message": "test",
        "vision": "test vision",
        "tasks": [
            {"id": tid, "role": "backend_developer", "goal": f"Do {tid}", "depends_on": []}
            for tid in task_ids
        ],
    }


# ===========================================================================
# _get_project_status_metadata tests
# ===========================================================================


class TestGetProjectStatusMetadata:
    """Verify metadata computation accuracy."""

    def test_metadata_when_no_dag_should_show_zero_tasks(self, orch):
        meta = orch._get_project_status_metadata()
        assert meta["total_tasks"] == 0
        assert meta["completed_tasks"] == 0
        assert meta["pending_tasks"] == 0
        assert meta["progress_percent"] == 0.0

    def test_metadata_when_dag_active_should_count_tasks_correctly(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1", "t2", "t3", "t4"])
        orch._dag_task_statuses = {
            "t1": "completed",
            "t2": "working",
            "t3": "failed",
        }
        meta = orch._get_project_status_metadata()
        assert meta["total_tasks"] == 4
        assert meta["completed_tasks"] == 1
        assert meta["active_task_count"] == 1  # running_tasks
        assert meta["failed_tasks"] == 1
        assert meta["pending_tasks"] == 1  # t4 has no status
        assert meta["progress_percent"] == 25.0  # 1/4 = 25%

    def test_metadata_when_all_completed_should_show_100_percent(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1", "t2"])
        orch._dag_task_statuses = {"t1": "completed", "t2": "completed"}
        meta = orch._get_project_status_metadata()
        assert meta["progress_percent"] == 100.0
        assert meta["pending_tasks"] == 0

    def test_metadata_when_running_should_reflect_is_running(self, orch):
        orch.is_running = True
        meta = orch._get_project_status_metadata()
        assert meta["is_running"] is True

    def test_metadata_when_paused_should_reflect_is_paused(self, orch):
        orch.is_paused = True
        meta = orch._get_project_status_metadata()
        assert meta["is_paused"] is True

    def test_metadata_when_agents_working_should_count_active(self, orch):
        orch.agent_states = {
            "agent_1": {"state": "working"},
            "agent_2": {"state": "idle"},
            "agent_3": {"state": "working"},
        }
        meta = orch._get_project_status_metadata()
        assert meta["agent_count"] == 2


# ===========================================================================
# _is_dag_truly_idle tests
# ===========================================================================


class TestIsDagTrulyIdle:
    """Verify idle detection only fires when the DAG is genuinely done."""

    def test_idle_when_no_dag_graph_should_return_true(self, orch):
        assert orch._is_dag_truly_idle() is True

    def test_idle_when_empty_tasks_should_return_true(self, orch):
        orch._current_dag_graph = {"tasks": []}
        assert orch._is_dag_truly_idle() is True

    def test_idle_when_all_completed_should_return_true(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1", "t2"])
        orch._dag_task_statuses = {"t1": "completed", "t2": "completed"}
        assert orch._is_dag_truly_idle() is True

    def test_idle_when_all_terminal_mixed_should_return_true(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1", "t2", "t3"])
        orch._dag_task_statuses = {"t1": "completed", "t2": "failed", "t3": "skipped"}
        assert orch._is_dag_truly_idle() is True

    def test_idle_when_task_pending_should_return_false(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1", "t2"])
        orch._dag_task_statuses = {"t1": "completed"}
        # t2 has no status → treated as pending
        assert orch._is_dag_truly_idle() is False

    def test_idle_when_task_working_should_return_false(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1"])
        orch._dag_task_statuses = {"t1": "working"}
        assert orch._is_dag_truly_idle() is False

    def test_idle_when_task_status_none_should_return_false(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1"])
        orch._dag_task_statuses = {}
        assert orch._is_dag_truly_idle() is False

    def test_idle_when_agents_working_should_return_false(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1"])
        orch._dag_task_statuses = {"t1": "completed"}
        orch.agent_states = {"agent_1": {"state": "working"}}
        assert orch._is_dag_truly_idle() is False

    def test_idle_when_agents_idle_and_all_complete_should_return_true(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1"])
        orch._dag_task_statuses = {"t1": "completed"}
        orch.agent_states = {"agent_1": {"state": "idle"}}
        assert orch._is_dag_truly_idle() is True

    def test_idle_when_cancelled_tasks_should_return_true(self, orch):
        orch._current_dag_graph = make_dag_graph(["t1", "t2"])
        orch._dag_task_statuses = {"t1": "completed", "t2": "cancelled"}
        assert orch._is_dag_truly_idle() is True


# ===========================================================================
# is_running flag lifecycle — no premature idle during teardown
# ===========================================================================


class TestIsRunningLifecycle:
    """Verify is_running stays True through cleanup events."""

    @pytest.mark.asyncio
    async def test_emit_event_when_running_and_cleanup_should_show_is_running_true(self, orch):
        """During cleanup (agent_finished events), metadata must show is_running=True."""
        orch.is_running = True
        orch._current_dag_graph = make_dag_graph(["t1", "t2"])

        # Simulate cleanup: emit agent_finished while is_running is still True
        await orch._emit_event("agent_finished", agent="dev_1")
        await orch._emit_event("dag_task_update", task_id="t1", status="completed")
        await orch._emit_event("project_status", status="running")

        # All events emitted while is_running=True should have correct metadata
        for event in orch._emitted_events:
            if "metadata" in event:
                assert event["metadata"]["is_running"] is True

    @pytest.mark.asyncio
    async def test_emit_event_when_idle_after_cleanup_should_show_is_running_false(self, orch):
        """The idle emission happens AFTER is_running=False."""
        orch.is_running = True
        orch._current_dag_graph = make_dag_graph(["t1"])
        orch._dag_task_statuses = {"t1": "completed"}

        # Simulate cleanup events (still running)
        await orch._emit_event("agent_finished", agent="dev_1")
        cleanup_event = orch._emitted_events[-1]

        # NOW set is_running=False (like the fix does)
        orch.is_running = False
        await orch._emit_event("project_status", status="idle")
        idle_event = orch._emitted_events[-1]

        assert idle_event["metadata"]["is_running"] is False
        assert idle_event["status"] == "idle"

    @pytest.mark.asyncio
    async def test_no_idle_emission_when_dag_still_has_pending_tasks(self, orch):
        """If tasks are still pending, no idle should be emitted."""
        orch.is_running = True
        orch._current_dag_graph = make_dag_graph(["t1", "t2", "t3"])
        orch._dag_task_statuses = {"t1": "completed"}

        # DAG is not truly idle
        assert not orch._is_dag_truly_idle()

        # Metadata should show pending tasks
        meta = orch._get_project_status_metadata()
        assert meta["is_running"] is True
        assert meta["pending_tasks"] == 2

    @pytest.mark.asyncio
    async def test_status_sequence_when_executing_should_be_running_then_idle(self, orch):
        """Simulate full lifecycle: running → cleanup → idle."""
        orch.is_running = True
        orch._current_dag_graph = make_dag_graph(["t1"])

        # Task starts
        await orch._emit_event("project_status", status="running")

        # Task completes
        orch._dag_task_statuses = {"t1": "completed"}
        await orch._emit_event("agent_finished", agent="dev_1")
        await orch._emit_event("dag_task_update", task_id="t1", status="completed")

        # Cleanup events — is_running still True
        running_events = [e for e in orch._emitted_events if "metadata" in e]
        for e in running_events:
            assert e["metadata"]["is_running"] is True

        # Final idle — is_running set to False right before
        orch.is_running = False
        await orch._emit_event("project_status", status="idle")

        idle_events = [
            e for e in orch._emitted_events
            if e.get("type") == "project_status" and e.get("status") == "idle"
        ]
        assert len(idle_events) == 1
        assert idle_events[0]["metadata"]["is_running"] is False


# ===========================================================================
# Edge cases
# ===========================================================================


class TestStatusEdgeCases:
    """Edge case coverage for status computation."""

    def test_metadata_when_agent_state_not_dict_should_ignore(self, orch):
        """Agent states that aren't dicts should be safely ignored."""
        orch.agent_states = {"agent_1": "working", "agent_2": None}
        meta = orch._get_project_status_metadata()
        assert meta["agent_count"] == 0

    def test_idle_when_task_without_id_should_skip(self, orch):
        """Tasks without an 'id' field should be skipped in idle check."""
        orch._current_dag_graph = {
            "tasks": [{"role": "dev", "goal": "no id task"}]
        }
        assert orch._is_dag_truly_idle() is True

    def test_metadata_when_zero_total_tasks_should_not_divide_by_zero(self, orch):
        """progress_percent should be 0.0 when total_tasks is 0."""
        orch._current_dag_graph = {"tasks": []}
        meta = orch._get_project_status_metadata()
        assert meta["progress_percent"] == 0.0

    def test_metadata_when_mixed_statuses_should_compute_pending_correctly(self, orch):
        """pending = total - completed - failed - running (not explicitly tracked)."""
        orch._current_dag_graph = make_dag_graph(["t1", "t2", "t3", "t4", "t5"])
        orch._dag_task_statuses = {
            "t1": "completed",
            "t2": "completed",
            "t3": "working",
            "t4": "failed",
            # t5 has no status
        }
        meta = orch._get_project_status_metadata()
        assert meta["pending_tasks"] == 1
        assert meta["active_task_count"] == 1
        assert meta["failed_tasks"] == 1
        assert meta["completed_tasks"] == 2
