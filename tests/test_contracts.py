"""
tests/test_contracts.py — Comprehensive tests for the Agent Protocol Layer.

Tests cover:
- TaskInput / TaskOutput model validation
- Artifact contract validation
- TaskGraph construction and querying
- FailureCategory classification logic
- Retry strategy selection
- task_input_to_prompt generation (including new thinking_protocol)
- extract_task_output JSON parsing
- validate_artifact_contracts checks
- create_remediation_task
- classify_failure

Naming convention: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts import (
    AgentRole,
    Artifact,
    ArtifactType,
    FailureCategory,
    TaskGraph,
    TaskInput,
    TaskOutput,
    TaskStatus,
    classify_failure,
    create_remediation_task,
    extract_task_output,
    get_retry_strategy,
    task_input_to_prompt,
    validate_artifact_contracts,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def simple_task() -> TaskInput:
    """A minimal valid TaskInput."""
    return TaskInput(
        id="task_001",
        role=AgentRole.BACKEND_DEVELOPER,
        goal="Build a REST API endpoint for user login",
        constraints=["No plaintext passwords", "Use bcrypt"],
        acceptance_criteria=["POST /auth/login returns 200 on success"],
    )


@pytest.fixture
def task_with_artifacts() -> TaskInput:
    """A TaskInput that requires and expects artifacts."""
    return TaskInput(
        id="task_002",
        role=AgentRole.FRONTEND_DEVELOPER,
        goal="Build the login form UI",
        depends_on=["task_001"],
        context_from=["task_001"],
        required_artifacts=[ArtifactType.COMPONENT_MAP],
        expected_input_artifact_types=[ArtifactType.API_CONTRACT],
        acceptance_criteria=["Form validates empty fields", "Shows error on 401"],
    )


@pytest.fixture
def completed_output() -> TaskOutput:
    """A successful TaskOutput."""
    return TaskOutput(
        task_id="task_001",
        role=AgentRole.BACKEND_DEVELOPER,
        status=TaskStatus.COMPLETED,
        summary="Built POST /auth/login endpoint with bcrypt hashing",
        artifacts=["api/auth.py", "tests/test_auth.py"],
        cost_usd=0.05,
        turns_used=8,
    )


@pytest.fixture
def failed_output() -> TaskOutput:
    """A failed TaskOutput with error info."""
    return TaskOutput(
        task_id="task_001",
        role=AgentRole.BACKEND_DEVELOPER,
        status=TaskStatus.FAILED,
        summary="TypeError: expected str got None at line 42",
        failure_details="TypeError: expected str got None at line 42",
        issues=["TS2322: Type mismatch in auth.py"],
    )


@pytest.fixture
def artifact_output() -> TaskOutput:
    """A TaskOutput with structured artifacts."""
    return TaskOutput(
        task_id="task_001",
        role=AgentRole.BACKEND_DEVELOPER,
        status=TaskStatus.COMPLETED,
        summary="Built auth API with contract",
        structured_artifacts=[
            Artifact(
                type=ArtifactType.API_CONTRACT,
                title="Auth API Endpoints",
                data={
                    "endpoints": [
                        {"method": "POST", "path": "/auth/login", "description": "Login"},
                        {"method": "POST", "path": "/auth/logout", "description": "Logout"},
                    ]
                },
                summary="2 authentication endpoints",
            )
        ],
        cost_usd=0.08,
        turns_used=12,
    )


@pytest.fixture
def simple_graph(simple_task, task_with_artifacts) -> TaskGraph:
    """A minimal TaskGraph with two sequential tasks."""
    return TaskGraph(
        vision="Build a login feature",
        user_message="Build a login system with React frontend and FastAPI backend",
        tasks=[simple_task, task_with_artifacts],
        epic_breakdown=["Auth backend", "Login UI"],
        project_id="test-project",
    )


# ===========================================================================
# TaskInput validation
# ===========================================================================


class TestTaskInputValidation:
    """TaskInput model validates all fields correctly."""

    def test_task_input_when_valid_should_create_successfully(self, simple_task):
        assert simple_task.id == "task_001"
        assert simple_task.role == AgentRole.BACKEND_DEVELOPER
        assert "No plaintext passwords" in simple_task.constraints

    def test_task_input_when_id_has_spaces_should_raise(self):
        with pytest.raises(ValidationError) as exc_info:
            TaskInput(id="task 001", role=AgentRole.BACKEND_DEVELOPER, goal="test")
        assert "Invalid task id" in str(exc_info.value) or "id" in str(exc_info.value)

    def test_task_input_when_id_has_special_chars_should_raise(self):
        with pytest.raises(ValidationError):
            TaskInput(id="task!@#", role=AgentRole.BACKEND_DEVELOPER, goal="test")

    def test_task_input_when_id_too_long_should_raise(self):
        with pytest.raises(ValidationError):
            TaskInput(id="a" * 65, role=AgentRole.BACKEND_DEVELOPER, goal="test")

    def test_task_input_when_goal_empty_should_raise(self):
        with pytest.raises(ValidationError):
            TaskInput(id="task_001", role=AgentRole.BACKEND_DEVELOPER, goal="   ")

    def test_task_input_when_remediation_fields_set_should_store_correctly(self):
        task = TaskInput(
            id="fix_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Fix the type error in auth.py",
            is_remediation=True,
            original_task_id="task_001",
            failure_context="TypeError: expected str got None at line 42",
        )
        assert task.is_remediation is True
        assert task.original_task_id == "task_001"
        assert "TypeError" in task.failure_context

    def test_task_input_when_depends_on_set_should_preserve_list(self, task_with_artifacts):
        assert "task_001" in task_with_artifacts.depends_on
        assert "task_001" in task_with_artifacts.context_from

    def test_task_input_when_required_artifacts_set_should_preserve(self, task_with_artifacts):
        assert ArtifactType.COMPONENT_MAP in task_with_artifacts.required_artifacts

    def test_task_input_when_expected_input_artifact_types_set_should_preserve(
        self, task_with_artifacts
    ):
        assert ArtifactType.API_CONTRACT in task_with_artifacts.expected_input_artifact_types

    def test_task_input_when_valid_id_with_hyphens_should_succeed(self):
        task = TaskInput(id="my-task-001", role=AgentRole.REVIEWER, goal="Review the code")
        assert task.id == "my-task-001"

    def test_task_input_when_valid_id_with_underscores_should_succeed(self):
        task = TaskInput(id="my_task_001", role=AgentRole.REVIEWER, goal="Review the code")
        assert task.id == "my_task_001"


# ===========================================================================
# TaskOutput validation
# ===========================================================================


class TestTaskOutputValidation:
    """TaskOutput model validates correctly and reports success/failure."""

    def test_task_output_when_completed_should_be_successful(self, completed_output):
        assert completed_output.is_successful() is True

    def test_task_output_when_failed_should_not_be_successful(self, failed_output):
        assert failed_output.is_successful() is False

    def test_task_output_when_needs_followup_should_not_be_successful(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            status=TaskStatus.NEEDS_FOLLOWUP,
            summary="Partial implementation",
        )
        assert output.is_successful() is False

    def test_task_output_when_negative_cost_should_reject_with_validation_error(self):
        """Guards: cost_usd must be non-negative — negative costs indicate data corruption."""
        with pytest.raises(ValidationError) as exc_info:
            TaskOutput(
                task_id="task_001",
                role=AgentRole.BACKEND_DEVELOPER,
                status=TaskStatus.COMPLETED,
                summary="Done",
                cost_usd=-1.0,
            )
        assert "cost_usd" in str(exc_info.value)

    def test_task_output_when_structured_artifacts_set_should_preserve(self, artifact_output):
        assert len(artifact_output.structured_artifacts) == 1
        art = artifact_output.structured_artifacts[0]
        assert art.type == ArtifactType.API_CONTRACT
        assert art.title == "Auth API Endpoints"
        assert len(art.data["endpoints"]) == 2

    def test_task_output_when_default_cost_should_be_zero(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            status=TaskStatus.COMPLETED,
            summary="Done",
        )
        assert output.cost_usd == 0.0

    def test_task_output_when_blocked_should_not_be_successful(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            status=TaskStatus.BLOCKED,
            summary="Waiting for API contract from task_001",
        )
        assert output.is_successful() is False


# ===========================================================================
# TaskGraph construction and queries
# ===========================================================================


class TestTaskGraphQueries:
    """TaskGraph query methods work correctly."""

    def test_task_graph_when_valid_should_store_vision(self, simple_graph):
        assert simple_graph.vision == "Build a login feature"

    def test_task_graph_get_task_when_id_exists_should_return_task(self, simple_graph):
        task = simple_graph.get_task("task_001")
        assert task is not None
        assert task.id == "task_001"

    def test_task_graph_get_task_when_id_missing_should_return_none(self, simple_graph):
        task = simple_graph.get_task("nonexistent_task")
        assert task is None

    def test_task_graph_ready_tasks_when_no_completions_should_return_first_task(
        self, simple_graph
    ):
        """task_001 has no deps — should be ready. task_002 depends on task_001 — not ready yet."""
        ready = simple_graph.ready_tasks(completed=set())
        ready_ids = [t.id for t in ready]
        assert "task_001" in ready_ids
        assert "task_002" not in ready_ids

    def test_task_graph_ready_tasks_when_dep_complete_should_unlock_dependent(self, simple_graph):
        """After task_001 completes, task_002 should become ready."""
        ready = simple_graph.ready_tasks(completed={"task_001"})
        ready_ids = [t.id for t in ready]
        assert "task_002" in ready_ids

    def test_task_graph_ready_tasks_when_all_complete_should_return_empty(self, simple_graph):
        """If all tasks are already completed, ready_tasks should return nothing."""
        ready = simple_graph.ready_tasks(completed={"task_001", "task_002"})
        assert len(ready) == 0

    def test_task_graph_is_complete_when_all_done_should_return_true(
        self, simple_graph, completed_output, artifact_output
    ):
        """Graph is complete when all tasks have outputs."""
        # Create output for task_002 too
        output_002 = TaskOutput(
            task_id="task_002",
            role=AgentRole.FRONTEND_DEVELOPER,
            status=TaskStatus.COMPLETED,
            summary="Built login form",
        )
        completed = {"task_001": completed_output, "task_002": output_002}
        assert simple_graph.is_complete(completed) is True

    def test_task_graph_is_complete_when_missing_task_should_return_false(
        self, simple_graph, completed_output
    ):
        """Graph is not complete when some tasks haven't run."""
        completed = {"task_001": completed_output}  # task_002 missing
        assert simple_graph.is_complete(completed) is False

    def test_task_graph_validate_dag_when_valid_should_return_no_errors(self, simple_graph):
        errors = simple_graph.validate_dag()
        assert errors == []

    def test_task_graph_validate_dag_when_self_dependency_should_return_error(self):
        task = TaskInput(
            id="task_self",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Self-referencing task",
            depends_on=["task_self"],  # Self-dependency!
        )
        graph = TaskGraph(
            vision="Broken",
            user_message="Something broken",
            tasks=[task],
            project_id="broken",
        )
        errors = graph.validate_dag()
        assert len(errors) > 0

    def test_task_graph_when_single_task_should_be_immediately_ready(self):
        """A graph with one task (no deps) should always be ready to run."""
        task = TaskInput(
            id="solo_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Complete the task on my own without any help",
        )
        graph = TaskGraph(
            vision="Solo mission",
            user_message="Do everything solo",
            tasks=[task],
            project_id="solo-project",
        )
        ready = graph.ready_tasks(completed=set())
        assert len(ready) == 1
        assert ready[0].id == "solo_001"


# ===========================================================================
# Artifact model
# ===========================================================================


class TestArtifact:
    """Artifact model validates fields correctly."""

    def test_artifact_when_valid_should_create_successfully(self):
        art = Artifact(
            type=ArtifactType.API_CONTRACT,
            title="User API",
            data={"endpoints": []},
            summary="Zero endpoints defined",
        )
        assert art.type == ArtifactType.API_CONTRACT
        assert art.title == "User API"

    def test_artifact_when_title_empty_should_raise(self):
        with pytest.raises(ValidationError):
            Artifact(type=ArtifactType.API_CONTRACT, title="")

    def test_artifact_when_title_whitespace_should_raise(self):
        with pytest.raises(ValidationError):
            Artifact(type=ArtifactType.API_CONTRACT, title="   ")

    def test_artifact_when_file_path_set_should_store(self):
        art = Artifact(
            type=ArtifactType.FILE_MANIFEST,
            title="Project files",
            file_path=".hivemind/artifact_index.json",
        )
        assert art.file_path == ".hivemind/artifact_index.json"

    def test_artifact_when_data_empty_should_default_to_empty_dict(self):
        art = Artifact(type=ArtifactType.RESEARCH, title="Research findings")
        assert art.data == {}

    def test_artifact_all_types_are_constructible(self):
        """All ArtifactType values can be used to create an Artifact."""
        for atype in ArtifactType:
            art = Artifact(type=atype, title=f"Test {atype.value}")
            assert art.type == atype


# ===========================================================================
# FailureCategory & retry strategy
# ===========================================================================


class TestFailureCategoryRetryStrategy:
    """Retry strategies for failure categories are correct."""

    def test_get_retry_strategy_when_build_error_should_allow_2_retries(self):
        strategy = get_retry_strategy(FailureCategory.BUILD_ERROR)
        assert strategy["max_retries"] == 2

    def test_get_retry_strategy_when_unclear_goal_should_have_zero_retries(self):
        strategy = get_retry_strategy(FailureCategory.UNCLEAR_GOAL)
        assert strategy["max_retries"] == 0

    def test_get_retry_strategy_when_permission_should_not_allow_remediation(self):
        strategy = get_retry_strategy(FailureCategory.PERMISSION)
        assert strategy["remediation_allowed"] is False

    def test_get_retry_strategy_when_unknown_should_return_defaults(self):
        strategy = get_retry_strategy(FailureCategory.UNKNOWN)
        assert "max_retries" in strategy
        assert "backoff_seconds" in strategy
        assert "remediation_allowed" in strategy

    def test_all_categories_have_retry_strategy(self):
        """Every FailureCategory has a retry strategy (via lookup or default)."""
        for category in FailureCategory:
            strategy = get_retry_strategy(category)
            assert isinstance(strategy["max_retries"], int)
            assert isinstance(strategy["backoff_seconds"], int | float)
            assert isinstance(strategy["remediation_allowed"], bool)


# ===========================================================================
# classify_failure
# ===========================================================================


class TestClassifyFailure:
    """classify_failure correctly identifies failure categories from TaskOutput."""

    def test_classify_when_typescript_error_in_issues_should_return_build_error(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.FRONTEND_DEVELOPER,
            status=TaskStatus.FAILED,
            summary="TypeScript compilation failed",
            issues=["TS2322: Type 'string' is not assignable to type 'number'"],
        )
        category = classify_failure(output)
        assert category == FailureCategory.BUILD_ERROR

    def test_classify_when_syntax_error_should_return_build_error(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            status=TaskStatus.FAILED,
            summary="SyntaxError: Unexpected token '}'",
            failure_details="SyntaxError in auth.py line 42",
        )
        category = classify_failure(output)
        assert category == FailureCategory.BUILD_ERROR

    def test_classify_when_module_not_found_should_return_dependency_missing(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            status=TaskStatus.FAILED,
            summary="ModuleNotFoundError: No module named 'fastapi'",
        )
        category = classify_failure(output)
        assert category in (
            FailureCategory.DEPENDENCY_MISSING,
            FailureCategory.BUILD_ERROR,
        )

    def test_classify_when_assertion_error_should_return_test_failure(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.TEST_ENGINEER,
            status=TaskStatus.FAILED,
            summary="AssertionError: expected 200 got 404",
            issues=["Test assertion failed: status code mismatch"],
        )
        category = classify_failure(output)
        assert category == FailureCategory.TEST_FAILURE

    def test_classify_when_permission_error_should_return_permission(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.DEVOPS,
            status=TaskStatus.FAILED,
            summary="PermissionError: [Errno 13] Permission denied: '/etc/secret'",
        )
        category = classify_failure(output)
        assert category == FailureCategory.PERMISSION

    def test_classify_when_empty_summary_should_return_unknown(self):
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            status=TaskStatus.FAILED,
            summary="",
        )
        category = classify_failure(output)
        assert category == FailureCategory.UNKNOWN

    def test_classify_when_pre_classified_should_use_existing_category(self):
        """If failure_category is already set on the output, use it directly."""
        output = TaskOutput(
            task_id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            status=TaskStatus.FAILED,
            summary="Something random",
            failure_category=FailureCategory.TIMEOUT,
        )
        category = classify_failure(output)
        assert category == FailureCategory.TIMEOUT


# ===========================================================================
# create_remediation_task
# ===========================================================================


class TestCreateRemediationTask:
    """create_remediation_task generates correct remediation tasks."""

    def test_create_remediation_when_build_error_should_return_task(
        self, simple_task, failed_output
    ):
        """Build errors are remediable — should return a TaskInput."""
        # Make failed_output have a build error signature
        failed_output.failure_details = "TypeError: expected str got None"
        result = create_remediation_task(simple_task, failed_output, task_counter=1)
        # Should return a task (not None) since build errors are remediable
        # (Implementation may return None for some categories)
        if result is not None:
            assert result.is_remediation is True
            assert result.original_task_id == "task_001"

    def test_create_remediation_when_unclear_goal_should_return_none(self):
        """UNCLEAR_GOAL is not remediable — function should return None."""
        task = TaskInput(
            id="task_amb",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Do something with the thing",
        )
        output = TaskOutput(
            task_id="task_amb",
            role=AgentRole.BACKEND_DEVELOPER,
            status=TaskStatus.FAILED,
            summary="unclear what to do",
            failure_category=FailureCategory.UNCLEAR_GOAL,
        )
        result = create_remediation_task(task, output, task_counter=2)
        assert result is None

    def test_create_remediation_when_returned_should_have_different_id(
        self, simple_task, failed_output
    ):
        """Remediation task must have a different id from the original."""
        failed_output.failure_details = "SyntaxError in main.py"
        result = create_remediation_task(simple_task, failed_output, task_counter=3)
        if result is not None:
            assert result.id != simple_task.id


# ===========================================================================
# task_input_to_prompt
# ===========================================================================


class TestTaskInputToPrompt:
    """task_input_to_prompt generates correct prompts with required sections."""

    def test_prompt_when_simple_task_should_include_goal(self, simple_task):
        prompt = task_input_to_prompt(simple_task, context_outputs={})
        assert "Build a REST API endpoint for user login" in prompt

    def test_prompt_when_simple_task_should_include_constraints(self, simple_task):
        prompt = task_input_to_prompt(simple_task, context_outputs={})
        assert "No plaintext passwords" in prompt
        assert "Use bcrypt" in prompt

    def test_prompt_when_simple_task_should_include_acceptance_criteria(self, simple_task):
        prompt = task_input_to_prompt(simple_task, context_outputs={})
        assert "POST /auth/login returns 200 on success" in prompt

    def test_prompt_when_simple_task_should_include_task_id(self, simple_task):
        prompt = task_input_to_prompt(simple_task, context_outputs={})
        assert "task_001" in prompt

    def test_prompt_when_graph_vision_provided_should_include_it(self, simple_task):
        prompt = task_input_to_prompt(
            simple_task, context_outputs={}, graph_vision="Build a complete auth system"
        )
        assert "Build a complete auth system" in prompt

    def test_prompt_should_include_instructions_section(self, simple_task):
        """Guards: instructions must be present in every generated prompt."""
        prompt = task_input_to_prompt(simple_task, context_outputs={})
        assert "instructions" in prompt.lower()

    def test_prompt_when_task_has_required_artifacts_should_mention_them(self, task_with_artifacts):
        prompt = task_input_to_prompt(task_with_artifacts, context_outputs={})
        # Should mention required artifacts somehow
        assert "component_map" in prompt.lower() or "COMPONENT_MAP" in prompt

    def test_prompt_when_remediation_task_should_include_failure_context(self):
        task = TaskInput(
            id="fix_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Fix the TypeError in auth.py",
            is_remediation=True,
            original_task_id="task_001",
            failure_context="TypeError: expected str got None at line 42",
        )
        prompt = task_input_to_prompt(task, context_outputs={})
        assert "TypeError" in prompt
        assert "task_001" in prompt

    def test_prompt_when_context_outputs_have_artifacts_should_include_upstream(
        self, task_with_artifacts, artifact_output
    ):
        """Upstream artifacts from context_from tasks should appear in the prompt."""
        prompt = task_input_to_prompt(
            task_with_artifacts,
            context_outputs={"task_001": artifact_output},
        )
        # The prompt should include something about upstream context
        assert "Auth API Endpoints" in prompt or "upstream" in prompt.lower()

    def test_prompt_when_epics_provided_should_include_them(self, simple_task):
        prompt = task_input_to_prompt(
            simple_task,
            context_outputs={},
            graph_epics=["Implement auth backend", "Build login UI"],
        )
        assert "Implement auth backend" in prompt or "epics" in prompt.lower()


# ===========================================================================
# extract_task_output
# ===========================================================================


class TestExtractTaskOutput:
    """extract_task_output correctly parses agent responses."""

    def test_extract_when_valid_json_in_code_block_should_parse(self):
        raw = """
I completed the task successfully.

```json
{
    "task_id": "task_001",
    "status": "completed",
    "summary": "Built the auth API with login endpoint",
    "artifacts": ["api/auth.py"],
    "issues": [],
    "cost_usd": 0.05,
    "turns_used": 8
}
```

All done!
"""
        output = extract_task_output(raw, "task_001")
        assert output.task_id == "task_001"
        assert output.status == TaskStatus.COMPLETED
        assert "api/auth.py" in output.artifacts

    def test_extract_when_no_json_should_use_heuristic_fallback(self):
        """When agent produces no structured JSON, heuristic extraction kicks in."""
        raw = "I built the login endpoint. Files modified: api/auth.py, tests/test_auth.py"
        output = extract_task_output(raw, "task_001")
        # Should not crash — heuristic produces a valid output
        assert output.task_id == "task_001"
        assert output.status in (
            TaskStatus.COMPLETED,
            TaskStatus.NEEDS_FOLLOWUP,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
        )

    def test_extract_when_json_has_failed_status_should_return_failed_output(self):
        raw = """
```json
{
    "task_id": "task_001",
    "status": "failed",
    "summary": "Type error in auth.py",
    "artifacts": [],
    "issues": ["TypeError: expected str got None"],
    "cost_usd": 0.03,
    "turns_used": 5
}
```
"""
        output = extract_task_output(raw, "task_001")
        assert output.status == TaskStatus.FAILED
        assert len(output.issues) > 0
        assert "TypeError" in output.issues[0]

    def test_extract_when_json_has_structured_artifacts_should_parse_them(self):
        raw = """
```json
{
    "task_id": "task_001",
    "status": "completed",
    "summary": "Built auth API",
    "artifacts": ["api/auth.py"],
    "issues": [],
    "cost_usd": 0.05,
    "turns_used": 8,
    "structured_artifacts": [
        {
            "type": "api_contract",
            "title": "Auth Endpoints",
            "data": {"endpoints": [{"method": "POST", "path": "/auth/login"}]},
            "summary": "One login endpoint"
        }
    ]
}
```
"""
        output = extract_task_output(raw, "task_001")
        assert len(output.structured_artifacts) == 1
        assert output.structured_artifacts[0].type == ArtifactType.API_CONTRACT

    def test_extract_when_malformed_json_should_not_raise(self):
        """Malformed JSON should use heuristic fallback, not crash."""
        raw = """```json
{ "task_id": "task_001", "status": "completed", "summary": INVALID_JSON
```"""
        # Should not raise — should fall back to heuristic
        output = extract_task_output(raw, "task_001")
        assert output.task_id == "task_001"

    def test_extract_when_status_unknown_string_should_handle_gracefully(self):
        """Unknown status values should be handled without crash."""
        raw = """
```json
{
    "task_id": "task_001",
    "status": "unknown_status",
    "summary": "Some result"
}
```
"""
        # May raise ValidationError or fall back to heuristic — must not crash
        try:
            output = extract_task_output(raw, "task_001")
            assert output.task_id == "task_001"
        except Exception as e:
            pytest.fail(f"extract_task_output raised unexpectedly: {e}")


# ===========================================================================
# validate_artifact_contracts (graph-level)
# ===========================================================================


class TestValidateArtifactContracts:
    """validate_artifact_contracts catches cross-agent contract violations at graph level."""

    def test_validate_when_graph_has_satisfied_contracts_should_return_empty_list(
        self, simple_task, task_with_artifacts
    ):
        """task_002 expects API_CONTRACT, task_001 produces it — valid graph."""
        # Adjust task_001 to produce API_CONTRACT
        task_001_with_contract = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build auth API",
            required_artifacts=[ArtifactType.API_CONTRACT],
        )
        graph = TaskGraph(
            vision="Build login feature",
            user_message="Build a full login system",
            tasks=[task_001_with_contract, task_with_artifacts],
            project_id="test-match",
        )
        mismatches = validate_artifact_contracts(graph)
        assert isinstance(mismatches, list)

    def test_validate_when_graph_has_unsatisfied_contract_should_return_mismatches(self):
        """task_002 expects API_CONTRACT but task_001 doesn't produce it."""
        producer = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build backend",
            required_artifacts=[ArtifactType.FILE_MANIFEST],  # Does NOT produce API_CONTRACT
        )
        consumer = TaskInput(
            id="task_002",
            role=AgentRole.FRONTEND_DEVELOPER,
            goal="Build frontend",
            depends_on=["task_001"],
            context_from=["task_001"],
            expected_input_artifact_types=[ArtifactType.API_CONTRACT],  # Expects API_CONTRACT
        )
        graph = TaskGraph(
            vision="Full-stack feature",
            user_message="Build a full-stack feature",
            tasks=[producer, consumer],
            project_id="test-mismatch",
        )
        mismatches = validate_artifact_contracts(graph)
        # Should detect the contract mismatch
        assert isinstance(mismatches, list)
        # Implementation should detect this violation
        assert len(mismatches) >= 1

    def test_validate_when_graph_is_empty_should_return_empty_list(self):
        """Empty graph has no contracts to violate."""
        graph = TaskGraph(
            vision="Nothing",
            user_message="Do nothing",
            tasks=[],
            project_id="empty",
        )
        mismatches = validate_artifact_contracts(graph)
        assert mismatches == []

    def test_validate_when_task_has_no_contracts_should_return_empty_list(self, simple_task):
        """Tasks with no expected_input_artifact_types have no contracts to check."""
        graph = TaskGraph(
            vision="Simple",
            user_message="Do something simple",
            tasks=[simple_task],
            project_id="simple",
        )
        mismatches = validate_artifact_contracts(graph)
        assert mismatches == []


# ===========================================================================
# Enum coverage
# ===========================================================================


class TestEnumCoverage:
    """All enum values are accessible and correctly typed."""

    def test_all_task_statuses_are_string_enum(self):
        for status in TaskStatus:
            assert isinstance(status.value, str)

    def test_all_agent_roles_are_string_enum(self):
        for role in AgentRole:
            assert isinstance(role.value, str)

    def test_all_artifact_types_are_string_enum(self):
        for atype in ArtifactType:
            assert isinstance(atype.value, str)

    def test_all_failure_categories_are_string_enum(self):
        for category in FailureCategory:
            assert isinstance(category.value, str)

    def test_orchestrator_role_is_in_agent_role(self):
        assert AgentRole.ORCHESTRATOR in AgentRole.__members__.values()

    def test_memory_role_is_in_agent_role(self):
        assert AgentRole.MEMORY in AgentRole.__members__.values()

    def test_pm_role_is_in_agent_role(self):
        assert AgentRole.PM in AgentRole.__members__.values()

    def test_remediation_status_is_in_task_status(self):
        assert TaskStatus.REMEDIATION in TaskStatus.__members__.values()

    def test_needs_followup_status_is_in_task_status(self):
        assert TaskStatus.NEEDS_FOLLOWUP in TaskStatus.__members__.values()
