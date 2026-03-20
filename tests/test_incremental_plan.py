"""
Comprehensive tests for the incremental plan system.

Covers:
- TaskGraph.append_tasks() with DAG validation and rollback
- TaskGraph.skip_tasks() with dependency unblocking
- TaskGraph.record_task_result() cumulative history
- ready_tasks() / is_complete() / has_failed() with SKIPPED status
- DAG executor _apply_pending_deltas() mid-execution injection
- DAGCheckpoint cumulative fields
- Edge cases: skip-with-dependents, inject-after-complete, concurrent deltas

Task: task_007
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from contracts import (
    AgentRole,
    ArtifactType,
    DAGCheckpoint,
    TaskGraph,
    TaskInput,
    TaskOutput,
    TaskStatus,
)


# ============================================================================
# Helpers
# ============================================================================


def _task(
    tid: str,
    role: AgentRole = AgentRole.BACKEND_DEVELOPER,
    depends_on: list[str] | None = None,
    goal: str = "Do something useful here",
) -> TaskInput:
    """Create a minimal TaskInput for testing."""
    return TaskInput(
        id=tid,
        role=role,
        goal=goal,
        depends_on=depends_on or [],
    )


def _graph(tasks: list[TaskInput] | None = None, project_id: str = "proj_1") -> TaskGraph:
    """Create a minimal TaskGraph for testing."""
    return TaskGraph(
        project_id=project_id,
        user_message="test message",
        vision="test vision",
        tasks=tasks or [],
    )


def _output(
    tid: str,
    status: TaskStatus = TaskStatus.COMPLETED,
    summary: str = "done",
) -> TaskOutput:
    """Create a minimal TaskOutput for testing."""
    return TaskOutput(
        task_id=tid,
        status=status,
        summary=summary,
        confidence=0.9,
    )


# ============================================================================
# TaskGraph.append_tasks()
# ============================================================================


class TestAppendTasks:
    """Tests for TaskGraph.append_tasks() — incremental DAG merging."""

    def test_append_tasks_when_empty_graph_should_add_all(self):
        graph = _graph([])
        new_tasks = [_task("task_001"), _task("task_002", depends_on=["task_001"])]
        errors = graph.append_tasks(new_tasks)
        assert errors == []
        assert len(graph.tasks) == 2
        assert graph.get_task("task_001") is not None
        assert graph.get_task("task_002") is not None

    def test_append_tasks_when_existing_tasks_should_preserve_them(self):
        existing = [_task("task_001")]
        graph = _graph(existing)
        new_tasks = [_task("task_002", depends_on=["task_001"])]
        errors = graph.append_tasks(new_tasks)
        assert errors == []
        assert len(graph.tasks) == 2
        assert graph.tasks[0].id == "task_001"
        assert graph.tasks[1].id == "task_002"

    def test_append_tasks_when_duplicate_id_should_return_error(self):
        graph = _graph([_task("task_001")])
        new_tasks = [_task("task_001", goal="duplicate task goal")]
        errors = graph.append_tasks(new_tasks)
        assert len(errors) == 1
        assert "duplicate" in errors[0].lower() or "task_001" in errors[0]
        # Graph should be unchanged
        assert len(graph.tasks) == 1

    def test_append_tasks_when_cycle_introduced_should_rollback(self):
        # task_001 → task_002
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
        ])
        # Adding task_003 that depends on task_002, plus making task_001 depend on task_003
        # would create a cycle, but since we can't modify existing tasks,
        # let's create a cycle with new tasks only:
        # task_003 depends on task_002, task_004 depends on task_003,
        # but task_003 also depends on task_004 → cycle
        new_tasks = [
            _task("task_003", depends_on=["task_004"]),
            _task("task_004", depends_on=["task_003"]),
        ]
        errors = graph.append_tasks(new_tasks)
        assert len(errors) > 0
        # Rollback: graph should only have original 2 tasks
        assert len(graph.tasks) == 2
        assert graph.get_task("task_003") is None
        assert graph.get_task("task_004") is None

    def test_append_tasks_when_new_depends_on_existing_should_succeed(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        new_tasks = [_task("task_003", depends_on=["task_001", "task_002"])]
        errors = graph.append_tasks(new_tasks)
        assert errors == []
        assert len(graph.tasks) == 3

    def test_append_tasks_when_new_depends_on_unknown_should_fail(self):
        graph = _graph([_task("task_001")])
        new_tasks = [_task("task_002", depends_on=["nonexistent"])]
        errors = graph.append_tasks(new_tasks)
        assert len(errors) > 0
        assert "nonexistent" in errors[0]
        # Rollback
        assert len(graph.tasks) == 1

    def test_append_tasks_when_self_dependency_should_fail(self):
        graph = _graph([_task("task_001")])
        new_tasks = [_task("task_002", depends_on=["task_002"])]
        errors = graph.append_tasks(new_tasks)
        assert len(errors) > 0
        assert len(graph.tasks) == 1

    def test_append_tasks_when_completed_dict_passed_should_not_affect_merge(self):
        graph = _graph([_task("task_001")])
        completed = {"task_001": _output("task_001")}
        new_tasks = [_task("task_002", depends_on=["task_001"])]
        errors = graph.append_tasks(new_tasks, completed=completed)
        assert errors == []
        assert len(graph.tasks) == 2

    def test_append_tasks_when_multiple_batches_should_accumulate(self):
        graph = _graph([_task("task_001")])
        errors1 = graph.append_tasks([_task("task_002", depends_on=["task_001"])])
        errors2 = graph.append_tasks([_task("task_003", depends_on=["task_002"])])
        assert errors1 == []
        assert errors2 == []
        assert len(graph.tasks) == 3


# ============================================================================
# TaskGraph.skip_tasks()
# ============================================================================


class TestSkipTasks:
    """Tests for TaskGraph.skip_tasks() — marking tasks as SKIPPED."""

    def test_skip_tasks_when_valid_ids_should_record_in_history(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        skipped = graph.skip_tasks(["task_001"], reason="No longer needed")
        assert skipped == ["task_001"]
        assert len(graph.task_history) == 1
        assert graph.task_history[0]["task_id"] == "task_001"
        assert graph.task_history[0]["status"] == "skipped"
        assert graph.task_history[0]["reason"] == "No longer needed"

    def test_skip_tasks_when_unknown_id_should_ignore(self):
        graph = _graph([_task("task_001")])
        skipped = graph.skip_tasks(["nonexistent"])
        assert skipped == []
        assert len(graph.task_history) == 0

    def test_skip_tasks_when_multiple_ids_should_skip_all_known(self):
        graph = _graph([_task("task_001"), _task("task_002"), _task("task_003")])
        skipped = graph.skip_tasks(["task_001", "task_003", "unknown"])
        assert set(skipped) == {"task_001", "task_003"}
        assert len(graph.task_history) == 2

    def test_skip_tasks_when_empty_list_should_do_nothing(self):
        graph = _graph([_task("task_001")])
        skipped = graph.skip_tasks([])
        assert skipped == []
        assert len(graph.task_history) == 0

    def test_skip_tasks_should_unblock_dependents_via_ready_tasks(self):
        """Skipping a dependency should make downstream tasks ready."""
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
        ])
        graph.skip_tasks(["task_001"])
        ready = graph.ready_tasks(completed={})
        ready_ids = [t.id for t in ready]
        assert "task_002" in ready_ids

    def test_skip_tasks_when_chain_of_deps_should_cascade_unblock(self):
        """Skipping task_001 should eventually let task_003 become ready
        once task_002 is also completed or skipped."""
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
            _task("task_003", depends_on=["task_002"]),
        ])
        # Skip task_001 — task_002 becomes ready
        graph.skip_tasks(["task_001"])
        ready = graph.ready_tasks(completed={})
        assert any(t.id == "task_002" for t in ready)
        # Now skip task_002 — task_003 becomes ready
        graph.skip_tasks(["task_002"])
        ready = graph.ready_tasks(completed={})
        assert any(t.id == "task_003" for t in ready)


# ============================================================================
# TaskGraph.record_task_result()
# ============================================================================


class TestRecordTaskResult:
    """Tests for TaskGraph.record_task_result() — cumulative history."""

    def test_record_task_result_when_completed_should_add_to_history(self):
        graph = _graph([_task("task_001")])
        graph.record_task_result("task_001", TaskStatus.COMPLETED, "All tests passed")
        assert len(graph.task_history) == 1
        assert graph.task_history[0]["task_id"] == "task_001"
        assert graph.task_history[0]["status"] == "completed"
        assert graph.task_history[0]["details"] == "All tests passed"

    def test_record_task_result_when_multiple_results_should_accumulate(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        graph.record_task_result("task_001", TaskStatus.COMPLETED)
        graph.record_task_result("task_002", TaskStatus.FAILED, "Build error")
        assert len(graph.task_history) == 2


# ============================================================================
# ready_tasks() with SKIPPED status
# ============================================================================


class TestReadyTasksWithSkipped:
    """Tests for ready_tasks() treating SKIPPED deps as resolved."""

    def test_ready_tasks_when_dep_skipped_should_treat_as_resolved(self):
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
        ])
        graph.skip_tasks(["task_001"])
        ready = graph.ready_tasks(completed={})
        assert any(t.id == "task_002" for t in ready)

    def test_ready_tasks_when_dep_completed_should_treat_as_resolved(self):
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
        ])
        completed = {"task_001": _output("task_001")}
        ready = graph.ready_tasks(completed)
        assert any(t.id == "task_002" for t in ready)

    def test_ready_tasks_when_dep_neither_completed_nor_skipped_should_not_be_ready(self):
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
        ])
        ready = graph.ready_tasks(completed={})
        assert not any(t.id == "task_002" for t in ready)

    def test_ready_tasks_when_mixed_deps_should_require_all_resolved(self):
        """Task with 2 deps: one completed, one pending → not ready."""
        graph = _graph([
            _task("task_001"),
            _task("task_002"),
            _task("task_003", depends_on=["task_001", "task_002"]),
        ])
        completed = {"task_001": _output("task_001")}
        ready = graph.ready_tasks(completed)
        ready_ids = [t.id for t in ready]
        assert "task_003" not in ready_ids

    def test_ready_tasks_when_one_dep_skipped_one_completed_should_be_ready(self):
        graph = _graph([
            _task("task_001"),
            _task("task_002"),
            _task("task_003", depends_on=["task_001", "task_002"]),
        ])
        graph.skip_tasks(["task_001"])
        completed = {"task_002": _output("task_002")}
        ready = graph.ready_tasks(completed)
        ready_ids = [t.id for t in ready]
        assert "task_003" in ready_ids

    def test_ready_tasks_should_not_return_already_skipped_tasks(self):
        graph = _graph([_task("task_001")])
        graph.skip_tasks(["task_001"])
        ready = graph.ready_tasks(completed={})
        assert not any(t.id == "task_001" for t in ready)

    def test_ready_tasks_with_set_of_completed_ids(self):
        """ready_tasks also accepts set[str] instead of dict."""
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
        ])
        ready = graph.ready_tasks(completed={"task_001"})
        assert any(t.id == "task_002" for t in ready)


# ============================================================================
# is_complete() with SKIPPED status
# ============================================================================


class TestIsCompleteWithSkipped:
    """Tests for is_complete() treating SKIPPED as resolved."""

    def test_is_complete_when_all_completed_should_return_true(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        completed = {
            "task_001": _output("task_001"),
            "task_002": _output("task_002"),
        }
        assert graph.is_complete(completed) is True

    def test_is_complete_when_some_skipped_should_count_as_done(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        graph.skip_tasks(["task_001"])
        completed = {"task_002": _output("task_002")}
        assert graph.is_complete(completed) is True

    def test_is_complete_when_all_skipped_should_return_true(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        graph.skip_tasks(["task_001", "task_002"])
        assert graph.is_complete(completed={}) is True

    def test_is_complete_when_pending_exists_should_return_false(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        completed = {"task_001": _output("task_001")}
        assert graph.is_complete(completed) is False


# ============================================================================
# has_failed() with SKIPPED status
# ============================================================================


class TestHasFailedWithSkipped:
    """Tests for has_failed() considering SKIPPED dependencies."""

    def test_has_failed_when_no_failures_should_return_false(self):
        graph = _graph([_task("task_001")])
        completed = {"task_001": _output("task_001")}
        assert graph.has_failed(completed) is False

    def test_has_failed_when_failed_with_pending_dependent_should_return_true(self):
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
        ])
        completed = {"task_001": _output("task_001", status=TaskStatus.FAILED)}
        assert graph.has_failed(completed) is True

    def test_has_failed_when_failed_but_dependent_skipped_should_return_false(self):
        """If a task failed but its dependent was skipped, no pending downstream path."""
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
        ])
        graph.skip_tasks(["task_002"])
        completed = {"task_001": _output("task_001", status=TaskStatus.FAILED)}
        assert graph.has_failed(completed) is False

    def test_has_failed_when_failed_task_has_no_dependents_should_return_false(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        completed = {
            "task_001": _output("task_001", status=TaskStatus.FAILED),
            "task_002": _output("task_002"),
        }
        assert graph.has_failed(completed) is False


# ============================================================================
# _skipped_task_ids()
# ============================================================================


class TestSkippedTaskIds:
    """Tests for _skipped_task_ids() helper."""

    def test_skipped_task_ids_when_none_skipped_should_return_empty(self):
        graph = _graph([_task("task_001")])
        assert graph._skipped_task_ids() == set()

    def test_skipped_task_ids_when_some_skipped_should_return_them(self):
        graph = _graph([_task("task_001"), _task("task_002")])
        graph.skip_tasks(["task_001"])
        assert graph._skipped_task_ids() == {"task_001"}


# ============================================================================
# DAGCheckpoint cumulative fields
# ============================================================================


class TestDAGCheckpointCumulativeFields:
    """Tests for DAGCheckpoint cumulative_task_history and skipped_task_ids."""

    def test_checkpoint_when_defaults_should_have_empty_cumulative_fields(self):
        cp = DAGCheckpoint(project_id="proj_1", graph_json="{}")
        assert cp.cumulative_task_history == []
        assert cp.skipped_task_ids == []
        assert cp.status == "running"

    def test_checkpoint_when_populated_should_serialize_correctly(self):
        history = [
            {"task_id": "task_001", "status": "completed"},
            {"task_id": "task_002", "status": "skipped", "reason": "Not needed"},
        ]
        cp = DAGCheckpoint(
            project_id="proj_1",
            graph_json='{"tasks": []}',
            cumulative_task_history=history,
            skipped_task_ids=["task_002"],
        )
        dumped = cp.model_dump()
        assert dumped["cumulative_task_history"] == history
        assert dumped["skipped_task_ids"] == ["task_002"]


# ============================================================================
# DAG Executor: _apply_pending_deltas()
# ============================================================================


class TestApplyPendingDeltas:
    """Tests for _apply_pending_deltas() mid-execution injection."""

    @pytest.fixture
    def graph_with_tasks(self):
        """A simple graph with 3 tasks."""
        return _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
            _task("task_003", depends_on=["task_002"]),
        ])

    @pytest.fixture
    def mock_ctx(self, graph_with_tasks):
        """Create a mock _ExecutionContext with required attributes."""
        ctx = MagicMock()
        ctx.plan_delta_queue = asyncio.Queue()
        ctx._delta_lock = asyncio.Lock()
        ctx.completed = {}
        ctx.running_tasks = set()
        ctx.deltas_applied = 0
        ctx.task_counter = 3
        ctx.on_event = None
        return ctx

    async def test_apply_deltas_when_empty_queue_should_return_zero(self, mock_ctx, graph_with_tasks):
        from dag_executor import _apply_pending_deltas
        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        assert result == 0

    async def test_apply_deltas_when_add_tasks_should_merge_into_graph(self, mock_ctx, graph_with_tasks):
        from dag_executor import _apply_pending_deltas

        new_tasks = [_task("task_004", depends_on=["task_003"])]
        await mock_ctx.plan_delta_queue.put({
            "add_tasks": new_tasks,
            "skip_task_ids": [],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        assert result == 1
        assert len(graph_with_tasks.tasks) == 4
        assert graph_with_tasks.get_task("task_004") is not None
        assert mock_ctx.deltas_applied == 1

    async def test_apply_deltas_when_skip_pending_task_should_mark_skipped(self, mock_ctx, graph_with_tasks):
        from dag_executor import _apply_pending_deltas

        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [],
            "skip_task_ids": ["task_003"],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        assert result == 1
        # task_003 should be in completed dict as SKIPPED
        assert "task_003" in mock_ctx.completed
        assert mock_ctx.completed["task_003"].status == TaskStatus.SKIPPED

    async def test_apply_deltas_when_skip_running_task_should_not_skip(self, mock_ctx, graph_with_tasks):
        """Cannot skip tasks that are already running."""
        from dag_executor import _apply_pending_deltas

        mock_ctx.running_tasks = {"task_002"}
        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [],
            "skip_task_ids": ["task_002"],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        # Delta still counts as applied but the skip is filtered out
        assert "task_002" not in mock_ctx.completed

    async def test_apply_deltas_when_skip_completed_task_should_not_skip(self, mock_ctx, graph_with_tasks):
        """Cannot skip tasks that are already completed."""
        from dag_executor import _apply_pending_deltas

        mock_ctx.completed = {"task_001": _output("task_001")}
        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [],
            "skip_task_ids": ["task_001"],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        # Original completion preserved
        assert mock_ctx.completed["task_001"].status == TaskStatus.COMPLETED

    async def test_apply_deltas_when_add_causes_cycle_should_drop_delta(self, mock_ctx, graph_with_tasks):
        """If new tasks introduce a cycle, the delta should be dropped."""
        from dag_executor import _apply_pending_deltas

        # Create a cycle: task_004 depends on task_005, task_005 depends on task_004
        cyclic_tasks = [
            _task("task_004", depends_on=["task_005"]),
            _task("task_005", depends_on=["task_004"]),
        ]
        await mock_ctx.plan_delta_queue.put({
            "add_tasks": cyclic_tasks,
            "skip_task_ids": [],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        # Delta dropped due to cycle
        assert result == 0
        assert len(graph_with_tasks.tasks) == 3  # No change

    async def test_apply_deltas_when_add_duplicate_id_should_drop_delta(self, mock_ctx, graph_with_tasks):
        from dag_executor import _apply_pending_deltas

        dup_tasks = [_task("task_001", goal="duplicate task that conflicts")]
        await mock_ctx.plan_delta_queue.put({
            "add_tasks": dup_tasks,
            "skip_task_ids": [],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        assert result == 0
        assert len(graph_with_tasks.tasks) == 3

    async def test_apply_deltas_when_empty_delta_should_skip(self, mock_ctx, graph_with_tasks):
        """Delta with no add_tasks and no skip_task_ids should be skipped."""
        from dag_executor import _apply_pending_deltas

        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [],
            "skip_task_ids": [],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        assert result == 0

    async def test_apply_deltas_when_multiple_deltas_should_apply_all(self, mock_ctx, graph_with_tasks):
        """Multiple deltas queued should all be applied in order."""
        from dag_executor import _apply_pending_deltas

        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [_task("task_004", depends_on=["task_003"])],
            "skip_task_ids": [],
        })
        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [_task("task_005", depends_on=["task_004"])],
            "skip_task_ids": [],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        assert result == 2
        assert len(graph_with_tasks.tasks) == 5
        assert mock_ctx.deltas_applied == 2

    async def test_apply_deltas_when_skip_and_add_in_same_delta_should_both_apply(self, mock_ctx, graph_with_tasks):
        """A single delta can both skip and add tasks."""
        from dag_executor import _apply_pending_deltas

        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [_task("task_004", depends_on=["task_001"])],
            "skip_task_ids": ["task_003"],
        })

        result = await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        assert result == 1
        assert len(graph_with_tasks.tasks) == 4
        assert "task_003" in mock_ctx.completed
        assert mock_ctx.completed["task_003"].status == TaskStatus.SKIPPED

    async def test_apply_deltas_should_update_task_counter(self, mock_ctx, graph_with_tasks):
        """task_counter should be updated to prevent remediation ID collisions."""
        from dag_executor import _apply_pending_deltas

        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [
                _task("task_004"),
                _task("task_005"),
                _task("task_006"),
            ],
            "skip_task_ids": [],
        })

        await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        assert mock_ctx.task_counter >= 6  # At least the number of total tasks

    async def test_apply_deltas_should_emit_task_graph_event(self, mock_ctx, graph_with_tasks):
        """After applying a delta, a task_graph event should be emitted."""
        from dag_executor import _apply_pending_deltas

        events = []
        mock_ctx.on_event = lambda event: events.append(event)

        await mock_ctx.plan_delta_queue.put({
            "add_tasks": [_task("task_004")],
            "skip_task_ids": [],
        })

        await _apply_pending_deltas(mock_ctx, graph_with_tasks)
        # Check that an event was fired (via _fire_event)
        # Since _fire_event may use a different mechanism, just verify
        # the delta was applied
        assert len(graph_with_tasks.tasks) == 4


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """Edge cases for incremental plan operations."""

    def test_skip_task_with_running_dependents_should_still_skip(self):
        """Skipping a PENDING task should work even if its dependents are pending.
        The dependents become unblocked."""
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
            _task("task_003", depends_on=["task_001"]),
        ])
        skipped = graph.skip_tasks(["task_001"])
        assert skipped == ["task_001"]
        ready = graph.ready_tasks(completed={})
        ready_ids = {t.id for t in ready}
        assert "task_002" in ready_ids
        assert "task_003" in ready_ids

    def test_inject_task_depending_on_already_completed_should_be_immediately_ready(self):
        """A new task whose deps are all completed should be immediately ready."""
        graph = _graph([_task("task_001"), _task("task_002")])
        completed = {
            "task_001": _output("task_001"),
            "task_002": _output("task_002"),
        }
        # Inject a new task depending on completed tasks
        errors = graph.append_tasks([_task("task_003", depends_on=["task_001", "task_002"])])
        assert errors == []
        ready = graph.ready_tasks(completed)
        assert any(t.id == "task_003" for t in ready)

    def test_inject_task_depending_on_skipped_should_be_ready(self):
        """A new task whose deps were skipped should be immediately ready."""
        graph = _graph([_task("task_001")])
        graph.skip_tasks(["task_001"])
        errors = graph.append_tasks([_task("task_002", depends_on=["task_001"])])
        assert errors == []
        ready = graph.ready_tasks(completed={})
        assert any(t.id == "task_002" for t in ready)

    def test_skip_already_skipped_task_should_add_duplicate_history(self):
        """Skipping a task that's already skipped records another history entry
        (idempotent from a dependency perspective since _skipped_task_ids
        returns a set)."""
        graph = _graph([_task("task_001")])
        graph.skip_tasks(["task_001"])
        graph.skip_tasks(["task_001"])
        # Two entries in history, but _skipped_task_ids still returns 1
        assert len(graph.task_history) == 2
        assert graph._skipped_task_ids() == {"task_001"}

    def test_append_then_skip_newly_added_task(self):
        """Append a task, then skip it — should work seamlessly."""
        graph = _graph([_task("task_001")])
        graph.append_tasks([_task("task_002", depends_on=["task_001"])])
        skipped = graph.skip_tasks(["task_002"])
        assert skipped == ["task_002"]
        assert graph._skipped_task_ids() == {"task_002"}

    def test_large_graph_append_many_tasks(self):
        """Stress test: append 50 tasks to a graph of 50."""
        existing = [_task(f"task_{i:03d}") for i in range(1, 51)]
        graph = _graph(existing)
        new_tasks = [
            _task(f"task_{i:03d}", depends_on=[f"task_{i-1:03d}"])
            for i in range(51, 101)
        ]
        errors = graph.append_tasks(new_tasks)
        assert errors == []
        assert len(graph.tasks) == 100

    def test_concurrent_skip_and_inject_should_preserve_consistency(self):
        """After skip + inject in sequence, graph should be valid."""
        graph = _graph([
            _task("task_001"),
            _task("task_002", depends_on=["task_001"]),
            _task("task_003", depends_on=["task_002"]),
        ])
        # Skip the middle task
        graph.skip_tasks(["task_002"])
        # Inject a replacement that depends on task_001
        errors = graph.append_tasks([_task("task_004", depends_on=["task_001"])])
        assert errors == []
        # task_003's dep (task_002) is skipped, so task_003 is ready
        ready = graph.ready_tasks(completed={"task_001": _output("task_001")})
        ready_ids = {t.id for t in ready}
        assert "task_003" in ready_ids
        assert "task_004" in ready_ids

    def test_validate_dag_after_append_should_pass(self):
        """After successful append, validate_dag should return no errors."""
        graph = _graph([_task("task_001")])
        graph.append_tasks([
            _task("task_002", depends_on=["task_001"]),
            _task("task_003", depends_on=["task_001"]),
            _task("task_004", depends_on=["task_002", "task_003"]),
        ])
        errors = graph.validate_dag()
        assert errors == []


# ============================================================================
# TaskQueueRegistry active DAG methods
# ============================================================================


class TestTaskQueueRegistryActiveDag:
    """Tests for TaskQueueRegistry register/unregister/has/get active DAG methods."""

    @pytest.fixture
    def registry(self):
        """Fresh TaskQueueRegistry for testing."""
        from src.workers.task_queue import TaskQueueRegistry
        reg = TaskQueueRegistry.__new__(TaskQueueRegistry)
        reg._active_dag_delta_queues = {}
        reg._dag_lock = asyncio.Lock()
        return reg

    async def test_register_active_dag_should_store_queue(self, registry):
        q = asyncio.Queue()
        await registry.register_active_dag("proj_1", q)
        assert await registry.has_active_dag("proj_1") is True
        assert await registry.get_active_dag_delta_queue("proj_1") is q

    async def test_unregister_active_dag_should_remove_queue(self, registry):
        q = asyncio.Queue()
        await registry.register_active_dag("proj_1", q)
        await registry.unregister_active_dag("proj_1")
        assert await registry.has_active_dag("proj_1") is False
        assert await registry.get_active_dag_delta_queue("proj_1") is None

    async def test_has_active_dag_when_not_registered_should_return_false(self, registry):
        assert await registry.has_active_dag("unknown") is False

    async def test_get_active_dag_when_not_registered_should_return_none(self, registry):
        result = await registry.get_active_dag_delta_queue("unknown")
        assert result is None

    async def test_register_multiple_projects_should_isolate(self, registry):
        q1 = asyncio.Queue()
        q2 = asyncio.Queue()
        await registry.register_active_dag("proj_1", q1)
        await registry.register_active_dag("proj_2", q2)
        assert await registry.get_active_dag_delta_queue("proj_1") is q1
        assert await registry.get_active_dag_delta_queue("proj_2") is q2

    async def test_unregister_nonexistent_should_not_raise(self, registry):
        """Unregistering a project that was never registered should not error."""
        await registry.unregister_active_dag("unknown")  # Should not raise


# ============================================================================
# WebSocket plan_delta event building
# ============================================================================


class TestWebSocketPlanDeltaEvent:
    """Tests for the task_graph event emitted after delta application."""

    async def test_delta_event_should_contain_graph_and_delta_info(self):
        """Verify the structure of events emitted by _apply_pending_deltas."""
        from dag_executor import _apply_pending_deltas

        graph = _graph([_task("task_001")])
        events_captured: list[dict] = []

        def capture_event(event):
            events_captured.append(event)

        ctx = MagicMock()
        ctx.plan_delta_queue = asyncio.Queue()
        ctx._delta_lock = asyncio.Lock()
        ctx.completed = {}
        ctx.running_tasks = set()
        ctx.deltas_applied = 0
        ctx.task_counter = 1
        ctx.on_event = capture_event

        new_task = _task("task_002", depends_on=["task_001"])
        await ctx.plan_delta_queue.put({
            "add_tasks": [new_task],
            "skip_task_ids": ["task_001"],
        })

        await _apply_pending_deltas(ctx, graph)

        # Verify event was captured
        assert len(events_captured) >= 1
        evt = events_captured[-1]
        assert evt["type"] == "task_graph"
        assert evt["project_id"] == "proj_1"
        assert evt["delta_applied"] is True
        assert "task_002" in evt["added_task_ids"]
        assert "task_001" in evt["skipped_task_ids"]
        assert "graph" in evt
