"""
tests/test_pm_agent.py — Tests for the PM Agent module.

Tests cover:
- fallback_single_task_graph: generates valid TaskGraphs for various request types
- _classify_request: correctly classifies different user request patterns
- TaskGraph validation after creation
- Dependency wiring is correct in fallback graphs
- Vision and epic_breakdown are set correctly
- validate_graph_quality: Critic pattern checks for common quality issues

Naming convention: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

from contracts import (
    AgentRole,
    TaskGraph,
    TaskInput,
)
from pm_agent import fallback_single_task_graph, validate_graph_quality

# ===========================================================================
# fallback_single_task_graph
# ===========================================================================


class TestFallbackSingleTaskGraph:
    """fallback_single_task_graph creates valid TaskGraphs for any user request."""

    def test_fallback_when_simple_request_should_return_task_graph(self):
        graph = fallback_single_task_graph("Build a login form", "test-project")
        assert isinstance(graph, TaskGraph)

    def test_fallback_when_simple_request_should_have_at_least_one_task(self):
        graph = fallback_single_task_graph("Build a login form", "test-project")
        assert len(graph.tasks) >= 1

    def test_fallback_should_set_project_id(self):
        graph = fallback_single_task_graph("Build a login form", "my-project-123")
        assert graph.project_id == "my-project-123"

    def test_fallback_should_set_vision(self):
        graph = fallback_single_task_graph("Build a login form", "test-project")
        assert graph.vision is not None
        assert len(graph.vision) > 0

    def test_fallback_should_set_user_message(self):
        graph = fallback_single_task_graph("Build a login form", "test-project")
        assert graph.user_message == "Build a login form"

    def test_fallback_when_called_should_have_valid_task_ids(self):
        """All task IDs must match the valid pattern (letters, digits, _, -)."""
        import re

        graph = fallback_single_task_graph("Build a REST API for user management", "test-project")
        for task in graph.tasks:
            assert re.match(r"^[a-zA-Z0-9_-]+$", task.id), f"Invalid task ID: {task.id}"

    def test_fallback_when_called_should_have_valid_task_roles(self):
        """All task roles must be valid AgentRole values."""
        graph = fallback_single_task_graph("Build a web app", "test-project")
        valid_roles = {r.value for r in AgentRole}
        for task in graph.tasks:
            assert task.role in valid_roles

    def test_fallback_when_called_should_have_non_empty_goals(self):
        """All task goals must be non-empty."""
        graph = fallback_single_task_graph("Create a user dashboard", "test-project")
        for task in graph.tasks:
            assert len(task.goal.strip()) >= 10, (
                f"Task {task.id} has goal that's too short: '{task.goal}'"
            )

    def test_fallback_when_called_should_produce_acyclic_dag(self):
        """The generated task graph must be a valid DAG with no cycles."""
        graph = fallback_single_task_graph(
            "Build a full-stack e-commerce platform with auth, products, and payments",
            "test-project",
        )
        errors = graph.validate_dag()
        assert errors == [], f"DAG validation failed: {errors}"

    def test_fallback_when_called_dependencies_should_reference_existing_tasks(self):
        """All depends_on references must point to real tasks in the graph."""
        graph = fallback_single_task_graph(
            "Build an API and frontend for todo management", "test-project"
        )
        task_ids = {t.id for t in graph.tasks}
        for task in graph.tasks:
            for dep in task.depends_on:
                assert dep in task_ids, f"Task {task.id} depends on non-existent task {dep}"

    def test_fallback_when_called_context_from_should_reference_existing_tasks(self):
        """All context_from references must point to real tasks in the graph."""
        graph = fallback_single_task_graph(
            "Build a React frontend that connects to a FastAPI backend", "test-project"
        )
        task_ids = {t.id for t in graph.tasks}
        for task in graph.tasks:
            for ctx in task.context_from:
                assert ctx in task_ids, f"Task {task.id} has context_from non-existent task {ctx}"

    def test_fallback_for_frontend_request_should_create_valid_graph(self):
        """Fallback always creates a backend_developer + reviewer graph."""
        graph = fallback_single_task_graph(
            "Build a React dashboard with charts and dark mode", "test-project"
        )
        roles = {t.role for t in graph.tasks}
        assert "backend_developer" in roles, f"Expected backend_developer in {roles}"
        assert "reviewer" in roles, f"Expected reviewer in {roles}"

    def test_fallback_for_api_request_should_include_backend_role(self):
        """Requests mentioning API/FastAPI should route to backend role."""
        graph = fallback_single_task_graph(
            "Build a FastAPI REST API with JWT authentication", "test-project"
        )
        roles = {t.role for t in graph.tasks}
        backend_roles = {"backend_developer", "python_backend", "developer"}
        assert roles & backend_roles, f"Expected a backend role in {roles} for an API request"

    def test_fallback_for_database_request_should_include_database_role(self):
        """Requests mentioning schema/database should include database role."""
        graph = fallback_single_task_graph(
            "Design a PostgreSQL database schema for a multi-tenant SaaS application",
            "test-project",
        )
        roles = {t.role for t in graph.tasks}
        db_roles = {"database_expert", "backend_developer", "developer"}
        assert roles & db_roles, f"Expected a database role in {roles} for a database request"

    def test_fallback_tasks_should_have_required_artifacts(self):
        """Each task should have at least one required artifact type."""
        graph = fallback_single_task_graph("Build a web service", "test-project")
        for task in graph.tasks:
            assert len(task.required_artifacts) >= 1, f"Task {task.id} has no required artifacts"

    def test_fallback_should_be_ready_to_execute_first_task(self):
        """At least one task in the graph should have no deps (ready to execute)."""
        graph = fallback_single_task_graph(
            "Add user authentication to a web application", "test-project"
        )
        no_dep_tasks = [t for t in graph.tasks if not t.depends_on]
        assert len(no_dep_tasks) >= 1, (
            "No tasks with zero dependencies found — DAG cannot start executing"
        )

    def test_fallback_for_test_request_should_create_valid_graph(self):
        """Fallback creates backend_developer + reviewer regardless of request type."""
        graph = fallback_single_task_graph(
            "Write comprehensive pytest tests for the authentication module", "test-project"
        )
        roles = {t.role for t in graph.tasks}
        assert "backend_developer" in roles, f"Expected backend_developer in {roles}"
        assert len(graph.tasks) >= 2, "Fallback should create at least 2 tasks"

    def test_fallback_epic_breakdown_should_be_non_empty_list(self):
        """epic_breakdown should be a list with at least one item."""
        graph = fallback_single_task_graph("Build something useful", "test-project")
        assert isinstance(graph.epic_breakdown, list)
        assert len(graph.epic_breakdown) >= 1

    def test_fallback_when_very_long_request_should_still_create_graph(self):
        """Very long user messages should be handled without truncation errors."""
        long_message = (
            "Build a complete enterprise SaaS platform with: "
            "1. Multi-tenant authentication with SSO and 2FA, "
            "2. Real-time dashboard with WebSocket updates, "
            "3. REST API with OpenAPI spec, "
            "4. Admin panel with RBAC, "
            "5. Billing integration with Stripe, "
            "6. Email notifications with templates, "
            "7. Analytics and reporting module, "
            "8. Mobile-responsive React frontend. "
        ) * 3  # Make it very long
        graph = fallback_single_task_graph(long_message, "test-project")
        assert isinstance(graph, TaskGraph)
        assert len(graph.tasks) >= 1
        # Vision should be truncated to a reasonable length
        assert len(graph.vision) <= 1000

    def test_fallback_when_empty_request_should_still_create_graph(self):
        """Empty or minimal requests should get a default developer task."""
        graph = fallback_single_task_graph("help", "test-project")
        assert isinstance(graph, TaskGraph)
        assert len(graph.tasks) >= 1

    def test_fallback_should_produce_deterministic_task_structure(self):
        """Same request should produce consistent task structure (same number of tasks)."""
        graph1 = fallback_single_task_graph("Build a React login page", "project-a")
        graph2 = fallback_single_task_graph("Build a React login page", "project-b")
        # Same structure — tasks might differ in project_id but structure should be consistent
        assert len(graph1.tasks) == len(graph2.tasks)


# ===========================================================================
# TaskGraph validation for fallback graphs
# ===========================================================================


class TestFallbackGraphValidation:
    """The task graphs produced by fallback_single_task_graph pass all validation."""

    def test_fallback_graph_passes_validate_dag_for_fullstack_request(self):
        graph = fallback_single_task_graph(
            "Build a full-stack todo app with React and FastAPI", "fullstack-project"
        )
        errors = graph.validate_dag()
        assert errors == [], f"Full-stack graph has DAG errors: {errors}"

    def test_fallback_graph_passes_validate_dag_for_api_only_request(self):
        graph = fallback_single_task_graph(
            "Create a REST API for managing blog posts with CRUD operations", "api-project"
        )
        errors = graph.validate_dag()
        assert errors == [], f"API graph has DAG errors: {errors}"

    def test_fallback_graph_passes_validate_dag_for_frontend_only_request(self):
        graph = fallback_single_task_graph(
            "Build a React component library with dark mode support", "frontend-project"
        )
        errors = graph.validate_dag()
        assert errors == [], f"Frontend graph has DAG errors: {errors}"

    def test_fallback_graph_is_executable_from_start(self):
        """The graph can be executed: ready_tasks returns tasks without errors."""
        graph = fallback_single_task_graph(
            "Implement a search feature with filtering and pagination", "search-project"
        )
        # Should be able to get ready tasks with empty completed set
        ready = graph.ready_tasks(completed=set())
        assert isinstance(ready, list)
        # At least one task should be executable immediately
        assert len(ready) >= 1


# ===========================================================================
# Edge cases
# ===========================================================================


class TestFallbackEdgeCases:
    """Edge cases for fallback_single_task_graph."""

    def test_fallback_when_request_has_special_characters_should_create_graph(self):
        graph = fallback_single_task_graph(
            "Build an API that handles UTF-8: 你好, émojis 🚀, and special chars <>&",
            "special-project",
        )
        assert isinstance(graph, TaskGraph)

    def test_fallback_task_roles_are_never_pm_or_orchestrator(self):
        """PM and Orchestrator should never be used as task roles in the fallback."""
        graph = fallback_single_task_graph(
            "Build a complete web application with all features", "test-project"
        )
        forbidden_roles = {AgentRole.PM, AgentRole.ORCHESTRATOR}
        for task in graph.tasks:
            assert task.role not in forbidden_roles, (
                f"Task {task.id} has forbidden role {task.role}"
            )

    def test_fallback_security_request_may_include_security_auditor(self):
        """Security-focused requests may route to security_auditor."""
        graph = fallback_single_task_graph(
            "Audit the authentication code for security vulnerabilities", "security-project"
        )
        # Should create a graph — role doesn't matter as much as success
        assert isinstance(graph, TaskGraph)
        assert len(graph.tasks) >= 1


# ===========================================================================
# validate_graph_quality — Critic Pattern
# ===========================================================================


def _make_minimal_task(
    task_id: str = "task_001",
    role: AgentRole = AgentRole.BACKEND_DEVELOPER,
    goal: str = "Build a REST API for managing users with CRUD endpoints",
    acceptance_criteria: list[str] | None = None,
    constraints: list[str] | None = None,
    depends_on: list[str] | None = None,
    context_from: list[str] | None = None,
    files_scope: list[str] | None = None,
) -> TaskInput:
    """Helper to create a TaskInput with sensible defaults."""
    return TaskInput(
        id=task_id,
        role=role,
        goal=goal,
        acceptance_criteria=acceptance_criteria or ["API returns 200 for valid requests"],
        constraints=constraints or ["Follow RESTful conventions"],
        depends_on=depends_on or [],
        context_from=context_from or [],
        files_scope=files_scope or ["api/routes.py"],
        required_artifacts=["api_contract"],
    )


def _make_minimal_graph(tasks: list[TaskInput] | None = None) -> TaskGraph:
    """Helper to create a minimal valid TaskGraph."""
    return TaskGraph(
        project_id="test-project",
        user_message="Build a REST API for managing users with CRUD endpoints",
        vision="A production-ready REST API for user management",
        epic_breakdown=["Backend API", "Authentication"],
        tasks=tasks or [_make_minimal_task()],
    )


class TestValidateGraphQualityPassingGraphs:
    """validate_graph_quality returns no issues for high-quality graphs."""

    def test_high_quality_graph_should_return_no_issues(self):
        graph = _make_minimal_graph()
        issues = validate_graph_quality(graph)
        assert issues == [], f"Expected no issues but got: {issues}"

    def test_graph_from_fallback_should_have_few_critical_issues(self):
        """Fallback graphs from pm_agent should not have ERROR-level issues."""
        graph = fallback_single_task_graph(
            "Build a FastAPI backend with user authentication and JWT tokens", "test-project"
        )
        issues = validate_graph_quality(graph)
        critical_issues = [i for i in issues if i.startswith("ERROR")]
        assert critical_issues == [], f"Fallback graph had critical issues: {critical_issues}"

    def test_graph_with_all_fields_populated_should_pass(self):
        task = _make_minimal_task(
            acceptance_criteria=["All endpoints tested", "Coverage >= 90%"],
            constraints=["Use async/await", "No global state"],
            files_scope=["backend/api.py", "backend/models.py"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        assert issues == []

    def test_returns_list_type(self):
        graph = _make_minimal_graph()
        result = validate_graph_quality(graph)
        assert isinstance(result, list)

    def test_all_issue_strings_are_str(self):
        graph = fallback_single_task_graph("Build a web app", "test-proj")
        issues = validate_graph_quality(graph)
        for issue in issues:
            assert isinstance(issue, str), f"Issue is not a str: {type(issue)}"


class TestValidateGraphQualityEmptyGraph:
    """validate_graph_quality correctly identifies empty graph."""

    def test_empty_task_list_should_return_critical_issue(self):
        graph = TaskGraph(
            project_id="empty-project",
            user_message="Build something",
            vision="Something useful",
            epic_breakdown=[],
            tasks=[],
        )
        issues = validate_graph_quality(graph)
        assert any("no tasks" in i.lower() or "CRITICAL" in i for i in issues), (
            f"Expected CRITICAL issue for empty task list, got: {issues}"
        )

    def test_empty_task_list_should_return_immediately(self):
        """For empty graphs, should not crash and should return quickly."""
        graph = TaskGraph(
            project_id="empty",
            user_message="Build something",
            vision="Something",
            epic_breakdown=[],
            tasks=[],
        )
        issues = validate_graph_quality(graph)
        # Should have at least 1 issue (the CRITICAL one)
        assert len(issues) >= 1


class TestValidateGraphQualityMissingFields:
    """validate_graph_quality flags missing required quality fields."""

    def test_missing_vision_should_flag_warning(self):
        task = _make_minimal_task()
        graph = TaskGraph(
            project_id="test",
            user_message="Build a REST API",
            vision="",  # Empty vision
            epic_breakdown=["Backend"],
            tasks=[task],
        )
        issues = validate_graph_quality(graph)
        assert any("vision" in i.lower() for i in issues), f"Expected vision warning, got: {issues}"

    def test_missing_acceptance_criteria_should_flag_warning(self):
        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build a comprehensive REST API for user management with CRUD",
            acceptance_criteria=[],  # No acceptance criteria
            constraints=["Follow RESTful conventions"],
            required_artifacts=["api_contract"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        assert any(
            "acceptance_criteria" in i or "acceptance criteria" in i.lower() for i in issues
        ), f"Expected acceptance_criteria warning, got: {issues}"

    def test_missing_constraints_should_flag_info(self):
        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build a comprehensive REST API for user management with CRUD",
            acceptance_criteria=["API returns correct status codes"],
            constraints=[],  # No constraints
            required_artifacts=["api_contract"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        assert any("constraints" in i.lower() or "constraint" in i.lower() for i in issues), (
            f"Expected constraints info, got: {issues}"
        )

    def test_short_goal_should_flag_warning(self):
        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build API endpoints",  # Very short (< 40 chars)
            acceptance_criteria=["Done"],
            constraints=["Good code"],
            required_artifacts=["api_contract"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        assert any("short goal" in i.lower() or "very short" in i.lower() for i in issues), (
            f"Expected short goal warning, got: {issues}"
        )

    def test_writer_without_files_scope_should_flag_info(self):
        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build a comprehensive REST API for user management with CRUD and auth",
            acceptance_criteria=["All tests pass"],
            constraints=["Use async"],
            files_scope=[],  # No files scope for a writer
            required_artifacts=["api_contract"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        assert any("files_scope" in i.lower() for i in issues), (
            f"Expected files_scope info for writer without scope, got: {issues}"
        )


class TestValidateGraphQualityDAGErrors:
    """validate_graph_quality detects invalid DAG references."""

    def test_context_from_nonexistent_task_should_flag_error(self):
        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build a REST API for user management with authentication and CRUD",
            acceptance_criteria=["API working"],
            constraints=["Use FastAPI"],
            context_from=["nonexistent_task_999"],  # Bad reference
            required_artifacts=["api_contract"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        errors = [i for i in issues if "ERROR" in i]
        assert any("context_from" in i.lower() or "nonexistent" in i.lower() for i in errors), (
            f"Expected ERROR for bad context_from reference, errors: {errors}"
        )

    def test_depends_on_nonexistent_task_should_flag_error(self):
        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build a REST API for user management with authentication and CRUD",
            acceptance_criteria=["API working"],
            constraints=["Use FastAPI"],
            depends_on=["ghost_task_xyz"],  # Bad reference
            required_artifacts=["api_contract"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        errors = [i for i in issues if "ERROR" in i]
        assert any("depends_on" in i.lower() or "ghost_task" in i.lower() for i in errors), (
            f"Expected ERROR for bad depends_on reference, errors: {errors}"
        )

    def test_valid_cross_task_references_should_not_flag_errors(self):
        task_a = _make_minimal_task(task_id="task_001", role=AgentRole.BACKEND_DEVELOPER)
        task_b = _make_minimal_task(
            task_id="task_002",
            role=AgentRole.TEST_ENGINEER,
            goal="Write comprehensive pytest tests for the backend REST API endpoints",
            depends_on=["task_001"],
            context_from=["task_001"],
        )
        graph = _make_minimal_graph([task_a, task_b])
        issues = validate_graph_quality(graph)
        errors = [i for i in issues if "ERROR" in i]
        assert errors == [], f"Expected no errors for valid cross-task references, got: {errors}"


class TestValidateGraphQualityStructuralChecks:
    """validate_graph_quality checks structural completeness of the graph."""

    def test_large_graph_without_reviewer_should_flag_info(self):
        tasks = [
            _make_minimal_task(
                "task_001",
                AgentRole.BACKEND_DEVELOPER,
                "Build a comprehensive REST API with authentication and CRUD endpoints",
            ),
            _make_minimal_task(
                "task_002",
                AgentRole.FRONTEND_DEVELOPER,
                "Build React UI components for the user dashboard with charts",
            ),
            _make_minimal_task(
                "task_003",
                AgentRole.DATABASE_EXPERT,
                "Design PostgreSQL schema for multi-tenant user management",
            ),
            _make_minimal_task(
                "task_004",
                AgentRole.TEST_ENGINEER,
                "Write pytest tests for all API endpoints with 90% coverage",
            ),
            _make_minimal_task(
                "task_005",
                AgentRole.DEVOPS,
                "Set up Docker deployment with CI/CD pipeline and health checks",
            ),
        ]
        # 5 tasks, no reviewer
        graph = _make_minimal_graph(tasks)
        issues = validate_graph_quality(graph)
        assert any("reviewer" in i.lower() for i in issues), (
            f"Expected reviewer suggestion for 5-task graph, got: {issues}"
        )

    def test_large_graph_with_reviewer_should_not_flag_reviewer_issue(self):
        tasks = [
            _make_minimal_task(
                "task_001",
                AgentRole.BACKEND_DEVELOPER,
                "Build a comprehensive REST API with authentication and CRUD endpoints",
            ),
            _make_minimal_task(
                "task_002",
                AgentRole.FRONTEND_DEVELOPER,
                "Build React UI components for the user dashboard with charts",
            ),
            _make_minimal_task(
                "task_003",
                AgentRole.DATABASE_EXPERT,
                "Design PostgreSQL schema for multi-tenant user management",
            ),
            _make_minimal_task(
                "task_004",
                AgentRole.TEST_ENGINEER,
                "Write pytest tests for all API endpoints with 90% coverage",
            ),
            _make_minimal_task(
                "task_005",
                AgentRole.REVIEWER,
                "Review all code for quality, security, and adherence to standards",
            ),
        ]
        graph = _make_minimal_graph(tasks)
        issues = validate_graph_quality(graph)
        reviewer_issues = [i for i in issues if "no reviewer" in i.lower()]
        assert reviewer_issues == [], (
            f"Should not flag reviewer issue when reviewer is present, got: {reviewer_issues}"
        )

    def test_small_graph_without_reviewer_should_not_flag(self):
        """Small graphs (< 5 tasks) don't need a reviewer."""
        task_a = _make_minimal_task(
            "task_001",
            AgentRole.BACKEND_DEVELOPER,
            "Build a REST API for user authentication with JWT tokens",
        )
        task_b = _make_minimal_task(
            "task_002",
            AgentRole.TEST_ENGINEER,
            "Write comprehensive pytest tests for the API endpoints",
        )
        graph = _make_minimal_graph([task_a, task_b])
        issues = validate_graph_quality(graph)
        reviewer_issues = [i for i in issues if "reviewer" in i.lower()]
        assert reviewer_issues == [], (
            f"Should not require reviewer for 2-task graph, got: {reviewer_issues}"
        )

    def test_tests_without_code_writers_should_flag_warning(self):
        """A test engineer without any code-writing agents is suspicious."""
        task = _make_minimal_task(
            "task_001",
            AgentRole.TEST_ENGINEER,
            "Write comprehensive pytest tests for all application endpoints",
        )
        task2 = _make_minimal_task(
            "task_002",
            AgentRole.REVIEWER,
            "Review code quality, architecture, and compliance with standards",
        )
        task3 = _make_minimal_task(
            "task_003",
            AgentRole.SECURITY_AUDITOR,
            "Audit the codebase for OWASP Top 10 security vulnerabilities",
        )
        graph = _make_minimal_graph([task, task2, task3])
        issues = validate_graph_quality(graph)
        assert any("test" in i.lower() and "code" in i.lower() for i in issues), (
            f"Expected warning about tests without code writers, got: {issues}"
        )


class TestValidateGraphQualityIssueSeverity:
    """validate_graph_quality uses correct severity prefixes."""

    def test_error_issues_use_error_prefix(self):
        task = _make_minimal_task(context_from=["nonexistent_999"])
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        assert any(i.startswith("ERROR") for i in issues), (
            "Bad reference should produce ERROR-prefixed issue"
        )

    def test_warning_issues_use_warning_prefix(self):
        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build REST API",  # Just > 10 chars but < 40, triggers short-goal WARNING
            acceptance_criteria=[],  # Missing acceptance_criteria triggers WARNING
            constraints=[],
            required_artifacts=["api_contract"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        assert any(i.startswith("WARNING") for i in issues), (
            "Short goal or missing criteria should produce WARNING-prefixed issue"
        )

    def test_info_issues_use_info_prefix(self):
        task = TaskInput(
            id="task_001",
            role=AgentRole.BACKEND_DEVELOPER,
            goal="Build a comprehensive REST API for user management and authentication",
            acceptance_criteria=["API working"],
            constraints=[],  # Missing constraints produces INFO
            files_scope=[],  # Missing files_scope for writer produces INFO
            required_artifacts=["api_contract"],
        )
        graph = _make_minimal_graph([task])
        issues = validate_graph_quality(graph)
        assert any(i.startswith("INFO") for i in issues), (
            "Missing optional fields should produce INFO-prefixed issue"
        )

    def test_no_issues_returns_empty_list(self):
        graph = _make_minimal_graph()
        issues = validate_graph_quality(graph)
        # A well-formed graph with all required fields should return empty list
        assert isinstance(issues, list)
        # Note: fallback graph may have INFO-level issues (no files_scope etc.)
        # but a hand-crafted minimal graph with all fields should be clean
        assert not any(i.startswith("ERROR") for i in issues), (
            "Well-formed graph should have no ERROR-level issues"
        )
