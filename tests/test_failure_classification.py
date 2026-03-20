"""Tests for failure classification, build_error_context, and remediation.

Covers: contracts.py (task_005) — classify_failure, build_error_context,
        create_remediation_task, get_retry_strategy, StructuredErrorContext
"""

from __future__ import annotations

import pytest

from contracts import (
    AgentRole,
    ArtifactType,
    FailureCategory,
    StructuredErrorContext,
    TaskInput,
    TaskOutput,
    TaskStatus,
    build_error_context,
    classify_failure,
    create_remediation_task,
    get_parent_category,
    get_retry_strategy,
    is_subcategory,
    validate_artifact_contracts,
    TaskGraph,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_failed_output(
    task_id: str = "task_001",
    summary: str = "",
    failure_details: str = "",
    issues: list[str] | None = None,
    blockers: list[str] | None = None,
    failure_category: FailureCategory | None = None,
    confidence: float = 0.5,
    turns_used: int = 5,
) -> TaskOutput:
    return TaskOutput(
        task_id=task_id,
        status=TaskStatus.FAILED,
        summary=summary or "Task failed during execution",
        failure_details=failure_details,
        issues=issues or [],
        blockers=blockers or [],
        failure_category=failure_category,
        confidence=confidence,
        turns_used=turns_used,
    )


def _make_task_input(task_id: str = "task_001", **kwargs) -> TaskInput:
    defaults = {
        "id": task_id,
        "role": "backend_developer",
        "goal": "Implement a feature for testing purposes",
    }
    defaults.update(kwargs)
    return TaskInput(**defaults)


# ── Tests: classify_failure — keyword matching ──────────────────────────────


class TestClassifyFailureKeywords:
    def test_classify_when_type_error_should_return_build_type_error(self):
        output = _make_failed_output(failure_details="TypeError: 'NoneType' object is not subscriptable")
        cat = classify_failure(output)
        assert cat == FailureCategory.BUILD_TYPE_ERROR

    def test_classify_when_import_error_should_return_build_import_error(self):
        output = _make_failed_output(failure_details="ImportError: No module named 'missing_pkg'")
        cat = classify_failure(output)
        assert cat == FailureCategory.BUILD_IMPORT_ERROR

    def test_classify_when_syntax_error_should_return_build_syntax_error(self):
        output = _make_failed_output(failure_details="SyntaxError: invalid syntax at line 42")
        cat = classify_failure(output)
        assert cat == FailureCategory.BUILD_SYNTAX_ERROR

    def test_classify_when_assertion_error_should_return_test_category(self):
        output = _make_failed_output(failure_details="AssertionError: expected 42 but got 0")
        cat = classify_failure(output)
        # May be TEST_ASSERTION or TEST_FAILURE depending on keyword scoring
        assert cat in (FailureCategory.TEST_ASSERTION, FailureCategory.TEST_FAILURE)

    def test_classify_when_permission_denied_should_return_permission(self):
        output = _make_failed_output(failure_details="PermissionError: permission denied for /etc/passwd")
        cat = classify_failure(output)
        assert cat == FailureCategory.PERMISSION

    def test_classify_when_timeout_should_return_timeout(self):
        output = _make_failed_output(failure_details="Agent timed out after 300s")
        cat = classify_failure(output)
        assert cat == FailureCategory.TIMEOUT

    def test_classify_when_rate_limit_should_return_external_rate_limit(self):
        output = _make_failed_output(failure_details="429 Too many requests — rate limit exceeded")
        cat = classify_failure(output)
        assert cat == FailureCategory.EXTERNAL_RATE_LIMIT

    def test_classify_when_missing_dep_should_return_build_missing_dep(self):
        output = _make_failed_output(failure_details="Package not found: pip install flask")
        cat = classify_failure(output)
        assert cat == FailureCategory.BUILD_MISSING_DEP

    def test_classify_when_empty_text_should_return_unknown(self):
        output = _make_failed_output(summary="", failure_details="")
        cat = classify_failure(output)
        assert cat == FailureCategory.UNKNOWN

    def test_classify_when_artifact_invalid_should_return_artifact_invalid(self):
        output = _make_failed_output(failure_details="artifact invalid: malformed artifact data")
        cat = classify_failure(output)
        assert cat == FailureCategory.ARTIFACT_INVALID

    def test_classify_when_context_insufficient_should_return_context_insufficient(self):
        output = _make_failed_output(failure_details="insufficient context to complete task, need more context")
        cat = classify_failure(output)
        assert cat == FailureCategory.CONTEXT_INSUFFICIENT

    def test_classify_when_agent_already_classified_should_use_existing(self):
        output = _make_failed_output(
            failure_details="some random text",
            failure_category=FailureCategory.PERMISSION,
        )
        cat = classify_failure(output)
        assert cat == FailureCategory.PERMISSION


# ── Tests: classify_failure — structured checks ────────────────────────────


class TestClassifyFailureStructuredChecks:
    def test_classify_when_idle_timeout_pattern_should_return_agent_timeout_idle(self):
        output = _make_failed_output(
            failure_details="Agent idle timeout detected, no progress for 60s",
            turns_used=0,
        )
        cat = classify_failure(output)
        assert cat == FailureCategory.AGENT_TIMEOUT_IDLE

    def test_classify_when_low_confidence_with_context_words_should_classify(self):
        output = _make_failed_output(
            summary="I could not understand the task context",
            confidence=0.1,
            issues=["Not enough context to complete the task"],
        )
        cat = classify_failure(output)
        assert cat == FailureCategory.CONTEXT_INSUFFICIENT

    def test_classify_when_blocker_mentions_upstream_missing_should_classify(self):
        output = _make_failed_output(
            blockers=["Upstream task dependency missing — not completed yet"],
        )
        cat = classify_failure(output)
        assert cat == FailureCategory.DEPENDENCY_MISSING

    def test_classify_when_blocker_mentions_artifact_invalid_should_classify(self):
        output = _make_failed_output(
            blockers=["The artifact from task_001 is invalid and could not be parsed"],
        )
        cat = classify_failure(output)
        assert cat == FailureCategory.ARTIFACT_INVALID

    def test_classify_when_stalled_should_return_agent_timeout_idle(self):
        output = _make_failed_output(failure_details="Agent stalled and produced no output")
        cat = classify_failure(output)
        assert cat == FailureCategory.AGENT_TIMEOUT_IDLE


# ── Tests: build_error_context ──────────────────────────────────────────────


class TestBuildErrorContext:
    def test_build_context_should_return_structured_error_context(self):
        output = _make_failed_output(
            failure_details="TypeError: expected int but got str at File 'app.py', line 42"
        )
        ctx = build_error_context(output)
        assert isinstance(ctx, StructuredErrorContext)
        assert ctx.category == FailureCategory.BUILD_TYPE_ERROR
        assert ctx.failed_file == "app.py"
        assert ctx.failed_line == 42

    def test_build_context_when_ts_file_pattern_should_extract_file_line(self):
        output = _make_failed_output(
            failure_details="Error in component.tsx(15,8): Property 'foo' does not exist"
        )
        ctx = build_error_context(output)
        assert ctx.failed_file == "component.tsx"
        assert ctx.failed_line == 15

    def test_build_context_should_include_suggestion(self):
        output = _make_failed_output(failure_details="SyntaxError: invalid syntax")
        ctx = build_error_context(output)
        assert ctx.suggestion != ""
        assert len(ctx.suggestion) > 10

    def test_build_context_should_truncate_traceback_to_1000(self):
        long_details = "x" * 2000
        output = _make_failed_output(failure_details=long_details)
        ctx = build_error_context(output)
        assert len(ctx.traceback) <= 1000

    def test_build_context_when_no_details_should_use_issues(self):
        output = _make_failed_output(
            failure_details="",
            issues=["Module not found error", "Import failed"],
        )
        ctx = build_error_context(output)
        assert "Module not found" in ctx.traceback

    def test_build_context_when_no_file_match_should_leave_empty(self):
        output = _make_failed_output(failure_details="Something went wrong badly")
        ctx = build_error_context(output)
        assert ctx.failed_file == ""
        assert ctx.failed_line == 0


# ── Tests: get_retry_strategy ───────────────────────────────────────────────


class TestGetRetryStrategy:
    def test_retry_strategy_when_subcategory_should_use_specific(self):
        strategy = get_retry_strategy(FailureCategory.BUILD_SYNTAX_ERROR)
        assert strategy["max_retries"] == 2
        assert strategy["remediation_allowed"] is True

    def test_retry_strategy_when_rate_limit_should_have_high_backoff(self):
        strategy = get_retry_strategy(FailureCategory.EXTERNAL_RATE_LIMIT)
        assert strategy["backoff_seconds"] == 10
        assert strategy["max_retries"] == 3
        assert strategy["remediation_allowed"] is False

    def test_retry_strategy_when_permission_should_not_allow_remediation(self):
        strategy = get_retry_strategy(FailureCategory.PERMISSION)
        assert strategy["remediation_allowed"] is False

    def test_retry_strategy_when_agent_timeout_idle_should_have_strategy(self):
        strategy = get_retry_strategy(FailureCategory.AGENT_TIMEOUT_IDLE)
        assert strategy["max_retries"] == 1
        assert strategy["remediation_allowed"] is True

    def test_retry_strategy_when_context_insufficient_should_not_retry(self):
        strategy = get_retry_strategy(FailureCategory.CONTEXT_INSUFFICIENT)
        assert strategy["max_retries"] == 0
        assert strategy["remediation_allowed"] is True


# ── Tests: Parent / Subcategory helpers ─────────────────────────────────────


class TestCategoryHelpers:
    def test_parent_when_subcategory_should_return_parent(self):
        assert get_parent_category(FailureCategory.BUILD_TYPE_ERROR) == FailureCategory.BUILD_ERROR
        assert get_parent_category(FailureCategory.TEST_ASSERTION) == FailureCategory.TEST_FAILURE

    def test_parent_when_top_level_should_return_self(self):
        assert get_parent_category(FailureCategory.TIMEOUT) == FailureCategory.TIMEOUT

    def test_is_subcategory_should_detect_subcategories(self):
        assert is_subcategory(FailureCategory.BUILD_SYNTAX_ERROR) is True
        assert is_subcategory(FailureCategory.BUILD_ERROR) is False
        assert is_subcategory(FailureCategory.AGENT_TIMEOUT_IDLE) is True

    def test_agent_timeout_idle_parent_should_be_timeout(self):
        assert get_parent_category(FailureCategory.AGENT_TIMEOUT_IDLE) == FailureCategory.TIMEOUT


# ── Tests: create_remediation_task ──────────────────────────────────────────


class TestCreateRemediationTask:
    def test_create_remediation_when_valid_should_return_task(self):
        failed_task = _make_task_input(task_id="task_001", role="frontend_developer")
        failed_output = _make_failed_output(
            task_id="task_001",
            failure_details="SyntaxError in component.tsx line 10",
        )
        result = create_remediation_task(failed_task, failed_output, 10)
        assert result is not None
        assert result.is_remediation is True
        assert result.original_task_id == "task_001"
        # failure_context should contain the failure details
        assert "SyntaxError" in result.failure_context

    def test_create_remediation_when_unclear_goal_should_return_none(self):
        failed_task = _make_task_input(task_id="task_001")
        failed_output = _make_failed_output(
            task_id="task_001",
            failure_details="The goal is unclear and ambiguous, need clarification",
            failure_category=FailureCategory.UNCLEAR_GOAL,
        )
        result = create_remediation_task(failed_task, failed_output, 10)
        assert result is None


# ── Tests: validate_artifact_contracts ──────────────────────────────────────


class TestValidateArtifactContracts:
    def test_validate_when_satisfied_should_return_empty(self):
        graph = TaskGraph(
            project_id="proj1",
            user_message="build a feature",
            vision="Create an app",
            tasks=[
                _make_task_input(
                    task_id="task_001",
                    required_artifacts=[ArtifactType.API_CONTRACT],
                ),
                _make_task_input(
                    task_id="task_002",
                    context_from=["task_001"],
                    expected_input_artifact_types=[ArtifactType.API_CONTRACT],
                    depends_on=["task_001"],
                ),
            ],
        )
        errors = validate_artifact_contracts(graph)
        assert errors == []

    def test_validate_when_missing_type_should_return_error(self):
        graph = TaskGraph(
            project_id="proj1",
            user_message="build a feature",
            vision="Create an app",
            tasks=[
                _make_task_input(
                    task_id="task_001",
                    required_artifacts=[],  # Doesn't produce api_contract
                ),
                _make_task_input(
                    task_id="task_002",
                    context_from=["task_001"],
                    expected_input_artifact_types=[ArtifactType.API_CONTRACT],
                    depends_on=["task_001"],
                ),
            ],
        )
        errors = validate_artifact_contracts(graph)
        assert len(errors) > 0


# ── Tests: StructuredErrorContext model ─────────────────────────────────────


class TestStructuredErrorContextModel:
    def test_model_should_have_defaults(self):
        ctx = StructuredErrorContext()
        assert ctx.category == FailureCategory.UNKNOWN
        assert ctx.traceback == ""
        assert ctx.suggestion == ""
        assert ctx.failed_file == ""
        assert ctx.failed_line == 0

    def test_model_should_reject_negative_line(self):
        with pytest.raises(Exception):
            StructuredErrorContext(failed_line=-1)
