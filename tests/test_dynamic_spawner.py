"""Tests for dynamic_spawner module."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import AgentRole, FailureCategory, TaskInput, TaskOutput, TaskStatus
from dynamic_spawner import DEFAULT_MODEL_CASCADE, DynamicSpawner


def _make_task(task_id: str = "t1", role: AgentRole = AgentRole.BACKEND_DEVELOPER) -> TaskInput:
    return TaskInput(
        id=task_id,
        goal="Implement the authentication module for the backend API service",
        role=role,
        context_from=[],
    )


def _make_output(
    task_id: str = "t1",
    status: TaskStatus = TaskStatus.FAILED,
    summary: str = "Build error: syntax error in main.py",
    issues: list[str] | None = None,
) -> TaskOutput:
    return TaskOutput(
        task_id=task_id,
        status=status,
        summary=summary,
        issues=issues or [],
        files_modified=[],
    )


class TestShouldRespawn:
    def test_eligible_build_error(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="Build error: syntax error in main.py")
        assert spawner.should_respawn(task, output) is True

    def test_eligible_timeout(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="Agent timed out after 300s")
        assert spawner.should_respawn(task, output) is True

    def test_eligible_test_failure(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="Test failure: assertion error in test_auth.py")
        assert spawner.should_respawn(task, output) is True

    def test_not_eligible_permission(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="Permission denied: access denied to /etc/shadow")
        assert spawner.should_respawn(task, output) is False

    def test_not_eligible_dependency_missing(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="Dependency missing: upstream task not found")
        assert spawner.should_respawn(task, output) is False

    def test_not_eligible_success(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(status=TaskStatus.COMPLETED, summary="All good")
        assert spawner.should_respawn(task, output) is False

    def test_all_models_exhausted(self):
        spawner = DynamicSpawner(model_cascade=["model-a"])
        task = _make_task()
        output = _make_output(summary="build failed: cannot compile main.py")
        # First call should succeed
        assert spawner.should_respawn(task, output) is True
        # Get the model to mark it as tried
        spawner.get_respawn_model(task, output)
        # Now all models are exhausted
        assert spawner.should_respawn(task, output) is False


class TestGetRespawnModel:
    def test_returns_first_untried_model(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="build failed: syntax error in module.py")
        model = spawner.get_respawn_model(task, output, current_model=None)
        assert model == DEFAULT_MODEL_CASCADE[0]

    def test_skips_current_model(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="build failed: syntax error in module.py")
        model = spawner.get_respawn_model(task, output, current_model=DEFAULT_MODEL_CASCADE[0])
        assert model == DEFAULT_MODEL_CASCADE[1]

    def test_returns_none_when_exhausted(self):
        spawner = DynamicSpawner(model_cascade=["only-model"])
        task = _make_task()
        output = _make_output(summary="build failed: cannot compile main.py")
        spawner.get_respawn_model(task, output, current_model="only-model")
        # All models tried
        model = spawner.get_respawn_model(task, output, current_model="only-model")
        assert model is None

    def test_records_attempt_in_history(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="build failed: syntax error in module.py")
        spawner.get_respawn_model(task, output, current_model="old-model")
        assert len(spawner.history) == 1
        assert spawner.history[0].task_id == "t1"
        assert spawner.history[0].original_model == "old-model"
        assert spawner.history[0].new_model == DEFAULT_MODEL_CASCADE[0]

    def test_returns_none_for_ineligible_failure(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="Permission denied: access denied")
        model = spawner.get_respawn_model(task, output)
        assert model is None
        assert len(spawner.history) == 0


class TestRecordResult:
    def test_updates_last_attempt(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="build failed: cannot compile main.py")
        model = spawner.get_respawn_model(task, output)
        assert spawner.history[0].succeeded is None
        spawner.record_result("t1", model, True)
        assert spawner.history[0].succeeded is True

    def test_updates_correct_attempt(self):
        spawner = DynamicSpawner()
        t1 = _make_task("t1")
        t2 = _make_task("t2")
        output = _make_output(summary="build failed: cannot compile main.py", task_id="t1")
        output2 = _make_output(summary="build failed: cannot compile main.py", task_id="t2")
        spawner.get_respawn_model(t1, output)
        m2 = spawner.get_respawn_model(t2, output2)
        spawner.record_result("t2", m2, False)
        assert spawner.history[0].succeeded is None  # t1 not updated
        assert spawner.history[1].succeeded is False  # t2 updated


class TestGetSummary:
    def test_empty_summary(self):
        spawner = DynamicSpawner()
        summary = spawner.get_summary()
        assert summary["total_attempts"] == 0
        assert summary["succeeded"] == 0
        assert summary["failed"] == 0

    def test_summary_with_attempts(self):
        spawner = DynamicSpawner()
        task = _make_task()
        output = _make_output(summary="build failed: cannot compile main.py")
        model = spawner.get_respawn_model(task, output)
        spawner.record_result("t1", model, True)
        summary = spawner.get_summary()
        assert summary["total_attempts"] == 1
        assert summary["succeeded"] == 1
        assert len(summary["attempts"]) == 1


class TestClassifyFailure:
    def test_timeout_detection(self):
        spawner = DynamicSpawner()
        output = _make_output(summary="Agent timed out after max turns")
        cat = spawner._classify_failure(output)
        assert cat == FailureCategory.TIMEOUT

    def test_build_error_detection(self):
        spawner = DynamicSpawner()
        output = _make_output(summary="Syntax error in line 42")
        cat = spawner._classify_failure(output)
        assert cat == FailureCategory.BUILD_ERROR

    def test_test_failure_detection(self):
        spawner = DynamicSpawner()
        output = _make_output(summary="Test failure: assertion failed")
        cat = spawner._classify_failure(output)
        assert cat == FailureCategory.TEST_FAILURE

    def test_permission_detection(self):
        spawner = DynamicSpawner()
        output = _make_output(summary="Permission denied: access denied")
        cat = spawner._classify_failure(output)
        assert cat == FailureCategory.PERMISSION

    def test_default_to_unknown(self):
        spawner = DynamicSpawner()
        output = _make_output(summary="Something went wrong")
        cat = spawner._classify_failure(output)
        assert cat == FailureCategory.UNKNOWN
