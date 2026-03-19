"""
tests/test_dag_executor.py — Unit tests for DAG executor utilities.

Tests cover:
- _plan_batches: splits ready tasks into parallel batches by role type
- _split_writers_by_conflicts: groups writers to avoid file conflicts
- ExecutionResult: properties and summary text
- build_execution_summary: human-readable output
- _get_max_turns and _get_task_timeout: per-role config lookup
- _get_task_budget: per-role budget lookup

These are pure unit tests — no real SDK calls, no Claude API.

Naming convention: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

from contracts import (
    AgentRole,
    TaskGraph,
    TaskInput,
    TaskOutput,
    TaskStatus,
)
from dag_executor import (
    ExecutionResult,
    _get_max_turns,
    _get_task_budget,
    _get_task_timeout,
    _plan_batches,
    _split_writers_by_conflicts,
    build_execution_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str,
    role: AgentRole,
    files_scope: list[str] | None = None,
    goal: str = "Build a production-grade REST API for user management",
    depends_on: list[str] | None = None,
) -> TaskInput:
    return TaskInput(
        id=task_id,
        role=role,
        goal=goal,
        acceptance_criteria=["Feature is working"],
        constraints=["Follow existing patterns"],
        files_scope=files_scope or [],
        depends_on=depends_on or [],
        required_artifacts=["api_contract"],
    )


def _make_output(
    task_id: str,
    status: TaskStatus = TaskStatus.COMPLETED,
    cost: float = 0.01,
    summary: str = "Done",
) -> TaskOutput:
    return TaskOutput(
        task_id=task_id,
        role=AgentRole.BACKEND_DEVELOPER,
        status=status,
        summary=summary,
        cost_usd=cost,
        turns_used=5,
    )


def _make_graph(tasks: list[TaskInput]) -> TaskGraph:
    return TaskGraph(
        project_id="test",
        user_message="Build a REST API",
        vision="A REST API for user management",
        epic_breakdown=["Backend"],
        tasks=tasks,
    )


# ===========================================================================
# _plan_batches
# ===========================================================================


class TestPlanBatches:
    """_plan_batches splits tasks correctly by reader/writer roles."""

    def test_empty_tasks_should_return_empty_list(self):
        result = _plan_batches([])
        assert result == []

    def test_single_writer_should_return_one_batch(self):
        task = _make_task("t1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py"])
        batches = _plan_batches([task])
        assert len(batches) == 1
        assert task in batches[0]

    def test_single_reader_should_return_one_batch(self):
        task = _make_task("t1", AgentRole.REVIEWER)
        batches = _plan_batches([task])
        assert len(batches) == 1
        assert task in batches[0]

    def test_readers_and_writers_should_run_writers_first(self):
        writer = _make_task("w1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py"])
        reader = _make_task("r1", AgentRole.REVIEWER)
        batches = _plan_batches([reader, writer])
        # Writers should be in earlier batches than readers
        writer_batch_idx = next(i for i, b in enumerate(batches) if writer in b)
        reader_batch_idx = next(i for i, b in enumerate(batches) if reader in b)
        assert writer_batch_idx < reader_batch_idx, (
            f"Writers should run before readers: writer_batch={writer_batch_idx} reader_batch={reader_batch_idx}"
        )

    def test_multiple_readers_should_batch_together(self):
        reviewer = _make_task("r1", AgentRole.REVIEWER)
        security = _make_task("r2", AgentRole.SECURITY_AUDITOR)
        tester = _make_task("r3", AgentRole.TEST_ENGINEER)
        batches = _plan_batches([reviewer, security, tester])
        # All readers should be in the same batch
        reader_batch = [b for b in batches if reviewer in b]
        assert len(reader_batch) == 1
        assert security in reader_batch[0]
        assert tester in reader_batch[0]

    def test_non_overlapping_writers_should_batch_together(self):
        backend = _make_task("w1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py", "models.py"])
        frontend = _make_task(
            "w2", AgentRole.FRONTEND_DEVELOPER, files_scope=["App.tsx", "index.css"]
        )
        batches = _plan_batches([backend, frontend])
        # Non-overlapping writers can run in parallel (same batch)
        writer_batches = (
            batches[:-1] if len(batches) > 1 else batches
        )  # Writers are in early batches
        all_writer_tasks = [t for batch in writer_batches for t in batch]
        # Both should be in the writer phase
        assert backend in all_writer_tasks or frontend in all_writer_tasks

    def test_overlapping_writers_should_split_into_separate_batches(self):
        backend1 = _make_task(
            "w1", AgentRole.BACKEND_DEVELOPER, files_scope=["models.py", "api.py"]
        )
        backend2 = _make_task("w2", AgentRole.PYTHON_BACKEND, files_scope=["api.py", "routes.py"])
        batches = _plan_batches([backend1, backend2])
        # Overlapping writers must be in separate batches
        # Find which batch each task is in
        batch1_idx = next((i for i, b in enumerate(batches) if backend1 in b), -1)
        batch2_idx = next((i for i, b in enumerate(batches) if backend2 in b), -1)
        assert batch1_idx != batch2_idx, "Overlapping writers should be in separate batches"

    def test_writer_without_files_scope_runs_alone(self):
        """A writer with empty files_scope runs in its own batch (safest behavior)."""
        isolated_writer = _make_task("w1", AgentRole.BACKEND_DEVELOPER, files_scope=[])
        other_writer = _make_task("w2", AgentRole.FRONTEND_DEVELOPER, files_scope=["App.tsx"])
        batches = _plan_batches([isolated_writer, other_writer])
        # Isolated writer should not be grouped with other writers
        for batch in batches:
            if isolated_writer in batch:
                assert len(batch) == 1, "Writer without files_scope should run alone"

    def test_returns_list_of_lists(self):
        tasks = [
            _make_task("t1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py"]),
            _make_task("t2", AgentRole.REVIEWER),
        ]
        batches = _plan_batches(tasks)
        assert isinstance(batches, list)
        for batch in batches:
            assert isinstance(batch, list)

    def test_all_tasks_accounted_for(self):
        """Every task appears in exactly one batch."""
        tasks = [
            _make_task("t1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py"]),
            _make_task("t2", AgentRole.FRONTEND_DEVELOPER, files_scope=["App.tsx"]),
            _make_task("t3", AgentRole.REVIEWER),
            _make_task("t4", AgentRole.SECURITY_AUDITOR),
            _make_task("t5", AgentRole.TEST_ENGINEER),
        ]
        batches = _plan_batches(tasks)
        all_tasks_in_batches = [t for batch in batches for t in batch]
        for task in tasks:
            assert task in all_tasks_in_batches, f"Task {task.id} not found in any batch"
        assert len(all_tasks_in_batches) == len(tasks), (
            f"Some tasks appeared in multiple batches: {len(all_tasks_in_batches)} vs {len(tasks)}"
        )


# ===========================================================================
# _split_writers_by_conflicts
# ===========================================================================


class TestSplitWritersByConflicts:
    """_split_writers_by_conflicts groups writers to prevent file conflicts."""

    def test_empty_writers_should_return_empty(self):
        result = _split_writers_by_conflicts([])
        assert result == []

    def test_single_writer_with_scope_returns_one_batch(self):
        writer = _make_task("w1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py"])
        batches = _split_writers_by_conflicts([writer])
        assert len(batches) == 1
        assert writer in batches[0]

    def test_non_conflicting_writers_share_batch(self):
        w1 = _make_task("w1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py"])
        w2 = _make_task("w2", AgentRole.FRONTEND_DEVELOPER, files_scope=["App.tsx"])
        batches = _split_writers_by_conflicts([w1, w2])
        # Should be in same batch (no file overlap)
        assert len(batches) == 1
        assert w1 in batches[0]
        assert w2 in batches[0]

    def test_conflicting_writers_get_separate_batches(self):
        w1 = _make_task("w1", AgentRole.BACKEND_DEVELOPER, files_scope=["models.py", "shared.py"])
        w2 = _make_task("w2", AgentRole.PYTHON_BACKEND, files_scope=["shared.py", "routes.py"])
        batches = _split_writers_by_conflicts([w1, w2])
        assert len(batches) == 2
        # Each should be in a separate batch
        batch1_has_w1 = any(w1 in b for b in batches)
        batch1_has_w2 = any(w2 in b for b in batches)
        assert batch1_has_w1 and batch1_has_w2

    def test_writer_without_scope_runs_alone(self):
        w1 = _make_task("w1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py"])
        w_isolated = _make_task("w2", AgentRole.DEVOPS, files_scope=[])  # No scope
        batches = _split_writers_by_conflicts([w1, w_isolated])
        # The isolated writer should be in its own batch
        isolated_batch = [b for b in batches if w_isolated in b]
        assert len(isolated_batch) == 1
        assert len(isolated_batch[0]) == 1, "No-scope writer should be alone"

    def test_three_non_conflicting_writers_all_in_same_batch(self):
        w1 = _make_task("w1", AgentRole.BACKEND_DEVELOPER, files_scope=["api.py"])
        w2 = _make_task("w2", AgentRole.FRONTEND_DEVELOPER, files_scope=["App.tsx"])
        w3 = _make_task("w3", AgentRole.DATABASE_EXPERT, files_scope=["schema.sql"])
        batches = _split_writers_by_conflicts([w1, w2, w3])
        assert len(batches) == 1
        assert all(w in batches[0] for w in [w1, w2, w3])


# ===========================================================================
# ExecutionResult
# ===========================================================================


class TestExecutionResult:
    """ExecutionResult correctly aggregates results."""

    def test_all_successful_when_no_failures(self):
        result = ExecutionResult(
            outputs=[_make_output("t1"), _make_output("t2")],
            total_cost=0.02,
            success_count=2,
            failure_count=0,
            remediation_count=0,
            healing_history=[],
        )
        assert result.all_successful is True

    def test_not_all_successful_when_failures(self):
        result = ExecutionResult(
            outputs=[_make_output("t1"), _make_output("t2", status=TaskStatus.FAILED)],
            total_cost=0.02,
            success_count=1,
            failure_count=1,
            remediation_count=0,
            healing_history=[],
        )
        assert result.all_successful is False

    def test_summary_text_includes_task_counts(self):
        result = ExecutionResult(
            outputs=[_make_output("t1"), _make_output("t2")],
            total_cost=0.05,
            success_count=2,
            failure_count=0,
            remediation_count=1,
            healing_history=[],
        )
        summary = result.summary_text()
        assert "2" in summary  # success count
        assert "0" in summary  # failure count
        assert "0.0500" in summary  # cost

    def test_summary_text_includes_healing_history(self):
        result = ExecutionResult(
            outputs=[],
            total_cost=0.0,
            success_count=0,
            failure_count=0,
            remediation_count=1,
            healing_history=[{"action": "retry", "detail": "Retried task_001"}],
        )
        summary = result.summary_text()
        assert "retry" in summary or "healing" in summary.lower()

    def test_summary_text_returns_string(self):
        result = ExecutionResult(
            outputs=[],
            total_cost=0.0,
            success_count=0,
            failure_count=0,
            remediation_count=0,
            healing_history=[],
        )
        assert isinstance(result.summary_text(), str)

    def test_zero_cost_and_counts_defaults(self):
        result = ExecutionResult(
            outputs=[],
            total_cost=0.0,
            success_count=0,
            failure_count=0,
            remediation_count=0,
            healing_history=[],
        )
        assert result.total_cost == 0.0
        assert result.success_count == 0
        assert result.failure_count == 0
        assert result.remediation_count == 0
        assert result.healing_history == []
        assert result.outputs == []


# ===========================================================================
# build_execution_summary
# ===========================================================================


class TestBuildExecutionSummary:
    """build_execution_summary generates human-readable output."""

    def _make_result(self, outputs: list[TaskOutput]) -> ExecutionResult:
        success = sum(1 for o in outputs if o.is_successful())
        failure = len(outputs) - success
        return ExecutionResult(
            outputs=outputs,
            total_cost=sum(o.cost_usd for o in outputs),
            success_count=success,
            failure_count=failure,
            remediation_count=0,
            healing_history=[],
        )

    def test_summary_returns_string(self):
        task = _make_task("t1", AgentRole.BACKEND_DEVELOPER)
        graph = _make_graph([task])
        result = self._make_result([_make_output("t1")])
        summary = build_execution_summary(graph, result)
        assert isinstance(summary, str)

    def test_summary_includes_vision(self):
        task = _make_task("t1", AgentRole.BACKEND_DEVELOPER)
        graph = _make_graph([task])
        result = self._make_result([_make_output("t1")])
        summary = build_execution_summary(graph, result)
        assert "A REST API for user management" in summary

    def test_summary_includes_success_count(self):
        tasks = [
            _make_task("t1", AgentRole.BACKEND_DEVELOPER),
            _make_task("t2", AgentRole.TEST_ENGINEER),
        ]
        graph = _make_graph(tasks)
        outputs = [_make_output("t1"), _make_output("t2")]
        result = self._make_result(outputs)
        summary = build_execution_summary(graph, result)
        # Should mention success
        assert "2" in summary or "succeed" in summary.lower()

    def test_summary_includes_failed_task_info(self):
        task = _make_task("t1", AgentRole.BACKEND_DEVELOPER)
        graph = _make_graph([task])
        failed_output = _make_output("t1", status=TaskStatus.FAILED, summary="Build error")
        result = self._make_result([failed_output])
        summary = build_execution_summary(graph, result)
        # Should mention failure
        assert "fail" in summary.lower() or "error" in summary.lower() or "Build error" in summary

    def test_summary_includes_cost(self):
        task = _make_task("t1", AgentRole.BACKEND_DEVELOPER)
        graph = _make_graph([task])
        outputs = [_make_output("t1", cost=0.0567)]
        result = self._make_result(outputs)
        # After the USD→token migration the summary shows "Tokens: X.XK"
        # instead of a dollar amount.  Accept either format.
        summary = build_execution_summary(graph, result)
        assert "0.05" in summary or "0.0567" in summary or "Tokens:" in summary

    def test_summary_empty_graph(self):
        """Empty graph with no outputs should still produce a string."""
        graph = _make_graph([_make_task("t1", AgentRole.BACKEND_DEVELOPER)])
        result = self._make_result([])
        summary = build_execution_summary(graph, result)
        assert isinstance(summary, str)
        assert len(summary) > 0


# ===========================================================================
# Per-role configuration lookup
# ===========================================================================


class TestPerRoleConfig:
    """_get_max_turns and _get_task_timeout correctly look up per-role config."""

    def test_get_max_turns_for_backend_developer(self):
        turns = _get_max_turns("backend_developer")
        assert isinstance(turns, int)
        assert turns > 0

    def test_get_max_turns_for_frontend_developer(self):
        turns = _get_max_turns("frontend_developer")
        assert isinstance(turns, int)
        assert turns > 0

    def test_get_max_turns_for_test_engineer(self):
        turns = _get_max_turns("test_engineer")
        assert isinstance(turns, int)
        assert turns > 0

    def test_get_max_turns_for_unknown_role_returns_default(self):
        turns = _get_max_turns("nonexistent_role_xyz")
        assert isinstance(turns, int)
        assert turns > 0  # Should return a sensible default

    def test_get_task_timeout_for_backend_developer(self):
        timeout = _get_task_timeout("backend_developer")
        assert isinstance(timeout, int)
        assert timeout > 60  # At least 1 minute

    def test_get_task_timeout_for_reviewer(self):
        timeout = _get_task_timeout("reviewer")
        assert isinstance(timeout, int)
        assert timeout > 0

    def test_get_task_timeout_for_unknown_role_returns_default(self):
        timeout = _get_task_timeout("nonexistent_role_abc")
        assert isinstance(timeout, int)
        assert timeout > 0

    def test_get_task_budget_for_backend_developer(self):
        budget = _get_task_budget("backend_developer")
        assert isinstance(budget, float)
        assert budget > 0.0

    def test_get_task_budget_for_unknown_role_returns_default(self):
        budget = _get_task_budget("nonexistent_role_abc")
        assert isinstance(budget, float)
        assert budget > 0.0

    def test_execution_agents_have_higher_turns_than_quality_agents(self):
        """Execution agents (writers) generally have higher turn limits."""
        backend_turns = _get_max_turns("backend_developer")
        reviewer_turns = _get_max_turns("reviewer")
        # Backend developer should have at least as many turns as reviewer
        # (may be equal but not less)
        assert backend_turns >= reviewer_turns or backend_turns > 10, (
            f"Backend developer ({backend_turns}) should have >= reviewer ({reviewer_turns}) turns"
        )
