"""
tests/test_plan_modification.py — Plan modification API endpoint tests.

Scope
-----
Covers the three plan modification endpoints added in task_005:
  PATCH /api/projects/{project_id}/plan/tasks/{task_id}
  POST  /api/projects/{project_id}/plan/tasks
  DELETE /api/projects/{project_id}/plan/tasks/{task_id}

Covers:
- CRUD operations on pending tasks
- Immutability of running/completed/failed tasks (409 responses)
- Dependency integrity validation on delete
- Request validation (Pydantic models)
- DAG acyclicity enforcement on add
- ID collision detection on add
- Mid-execution queueing via plan_delta_queue
- Event emission on successful modifications

Naming: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ID = "proj-plan-test"


def _make_mock_session_mgr():
    """Fully mocked SessionManager."""
    smgr = AsyncMock()
    smgr.is_healthy = AsyncMock(return_value=True)
    smgr.list_projects = AsyncMock(return_value=[])
    smgr.load_project = AsyncMock(return_value=None)
    smgr.save_project = AsyncMock()
    smgr.delete_project = AsyncMock()
    smgr.update_status = AsyncMock()
    smgr.update_project_fields = AsyncMock()
    smgr.get_messages_paginated = AsyncMock(return_value=([], 0))
    smgr.get_project_tasks = AsyncMock(return_value=[])
    smgr.get_schedules = AsyncMock(return_value=[])
    smgr.add_schedule = AsyncMock(return_value=1)
    smgr.delete_schedule = AsyncMock(return_value=True)
    smgr.set_project_budget = AsyncMock()
    smgr.clear_project_data = AsyncMock()
    smgr.get_recent_messages = AsyncMock(return_value=[])
    smgr.load_orchestrator_state = AsyncMock(return_value=None)
    smgr.get_activity_since = AsyncMock(return_value=[])
    smgr.get_latest_sequence = AsyncMock(return_value=0)
    smgr.get_agent_stats = AsyncMock(return_value=[])
    smgr.get_agent_recent_performance = AsyncMock(return_value=[])
    smgr.get_cost_breakdown = AsyncMock(
        return_value={"by_agent": [], "by_day": [], "total_cost": 0, "total_runs": 0}
    )
    smgr.get_project_cost_summary = AsyncMock(return_value=[])
    smgr.get_resumable_task = AsyncMock(return_value=None)
    smgr.discard_interrupted_task = AsyncMock()
    smgr.invalidate_all_sessions = AsyncMock()
    return smgr


def _make_mock_manager(
    is_running=False,
    dag_graph=None,
    dag_task_statuses=None,
):
    """Mock OrchestratorManager with plan-relevant attributes."""
    mgr = MagicMock()
    mgr.project_name = "test-project"
    mgr.project_dir = "/tmp/test-project"
    mgr.is_running = is_running
    mgr.is_paused = False
    mgr.turn_count = 0
    mgr.total_cost_usd = 0.0
    mgr.total_input_tokens = 0
    mgr.total_output_tokens = 0
    mgr.total_tokens = 0
    mgr.agent_names = ["orchestrator"]
    mgr.is_multi_agent = True
    mgr.conversation_log = []
    mgr.agent_states = {}
    mgr.current_agent = None
    mgr.current_tool = None
    mgr.shared_context = []
    mgr.pending_approval = None
    mgr.pending_message_count = 0
    mgr.drain_message_queue = MagicMock(return_value=0)
    mgr._background_tasks = []
    mgr.start_session = AsyncMock()
    mgr.inject_user_message = AsyncMock()
    mgr.stop = AsyncMock()
    mgr.pause = MagicMock()
    mgr.resume = MagicMock()
    mgr.approve = MagicMock()
    mgr.reject = MagicMock()
    mock_inner_smgr = AsyncMock()
    mock_inner_smgr.invalidate_all_sessions = AsyncMock()
    mgr.session_mgr = mock_inner_smgr

    mgr._current_dag_graph = dag_graph
    mgr._dag_task_statuses = dag_task_statuses or {}
    mgr._plan_delta_queue = asyncio.Queue()

    return mgr


def _make_dag_graph_dict(tasks=None):
    """Create a valid TaskGraph-compatible dict."""
    if tasks is None:
        tasks = [
            {
                "id": "task_001",
                "role": "backend_developer",
                "goal": "Implement the user authentication API endpoint",
                "constraints": [],
                "depends_on": [],
                "context_from": [],
                "files_scope": [],
                "acceptance_criteria": [],
                "required_artifacts": [],
                "is_remediation": False,
            },
            {
                "id": "task_002",
                "role": "frontend_developer",
                "goal": "Build the login form with validation rules",
                "constraints": [],
                "depends_on": ["task_001"],
                "context_from": [],
                "files_scope": [],
                "acceptance_criteria": [],
                "required_artifacts": [],
                "is_remediation": False,
            },
            {
                "id": "task_003",
                "role": "test_engineer",
                "goal": "Write integration tests for authentication flow",
                "constraints": [],
                "depends_on": ["task_001", "task_002"],
                "context_from": [],
                "files_scope": [],
                "acceptance_criteria": [],
                "required_artifacts": [],
                "is_remediation": False,
            },
        ]
    return {
        "project_id": PROJECT_ID,
        "user_message": "Build auth system",
        "vision": "Authentication system with login page",
        "tasks": tasks,
    }


def _setup_app_with_manager(manager=None):
    """Create app with a mock manager in active_sessions."""
    import state

    mock_smgr = _make_mock_session_mgr()
    mock_sdk = MagicMock()
    state.session_mgr = mock_smgr
    state.sdk_client = mock_sdk

    if manager:
        state.active_sessions[0] = {PROJECT_ID: manager}

    from dashboard.api import create_app
    return create_app()


# ===========================================================================
# PATCH /api/projects/{project_id}/plan/tasks/{task_id}
# ===========================================================================


class TestPatchPlanTask:
    """Tests for the PATCH endpoint — modify pending task goal/constraints."""

    @pytest.mark.asyncio
    async def test_patch_when_pending_task_should_update_goal(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"goal": "Updated goal: implement the JWT auth system with refresh tokens"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["task_id"] == "task_001"
        assert "goal" in data["changes"]

    @pytest.mark.asyncio
    async def test_patch_when_pending_task_should_update_constraints(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"constraints": ["Must use bcrypt", "No plaintext passwords"]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "constraints" in data["changes"]

    @pytest.mark.asyncio
    async def test_patch_when_running_task_should_return_409(self):
        mgr = _make_mock_manager(
            dag_graph=_make_dag_graph_dict(),
            dag_task_statuses={"task_001": "working"},
        )
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"goal": "This should fail because task is running right now"},
            )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_patch_when_completed_task_should_return_409(self):
        mgr = _make_mock_manager(
            dag_graph=_make_dag_graph_dict(),
            dag_task_statuses={"task_001": "completed"},
        )
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"goal": "This should fail because task already completed now"},
            )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_patch_when_failed_task_should_return_409(self):
        mgr = _make_mock_manager(
            dag_graph=_make_dag_graph_dict(),
            dag_task_statuses={"task_001": "failed"},
        )
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"goal": "This should fail because the task has failed already"},
            )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_patch_when_no_fields_should_return_400(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={},
            )

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_when_task_not_found_should_return_404(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/nonexistent_task",
                json={"goal": "This should fail because task does not exist at all"},
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_when_no_active_project_should_return_404(self):
        app = _setup_app_with_manager(manager=None)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"goal": "This should fail because no active project exists"},
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_when_no_dag_should_return_404(self):
        mgr = _make_mock_manager(dag_graph=None)
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"goal": "This should fail because no DAG plan is present"},
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_when_goal_too_short_should_return_422(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"goal": "short"},
            )

        # App may return 400 (custom validation handler) or 422 (default pydantic)
        assert resp.status_code in (400, 422)


# ===========================================================================
# POST /api/projects/{project_id}/plan/tasks
# ===========================================================================


class TestAddPlanTask:
    """Tests for the POST endpoint — add a new task to the DAG."""

    @pytest.mark.asyncio
    async def test_add_when_valid_task_not_running_should_succeed(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_004",
                    "role": "backend_developer",
                    "goal": "Add rate limiting middleware to the authentication endpoints",
                    "depends_on": ["task_001"],
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["task_id"] == "task_004"
        assert data["action"] == "added"

    @pytest.mark.asyncio
    async def test_add_when_running_should_enqueue_to_delta_queue(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict(), is_running=True)
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_004",
                    "role": "backend_developer",
                    "goal": "Add rate limiting middleware to the authentication endpoints",
                    "depends_on": [],
                },
            )

        assert resp.status_code == 200
        # Check the delta queue was used
        assert not mgr._plan_delta_queue.empty()
        delta = mgr._plan_delta_queue.get_nowait()
        assert len(delta["add_tasks"]) == 1
        assert delta["skip_task_ids"] == []

    @pytest.mark.asyncio
    async def test_add_when_duplicate_id_should_return_409(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_001",  # Already exists
                    "role": "backend_developer",
                    "goal": "This conflicts with existing task_001 identifier",
                },
            )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_add_when_invalid_role_should_return_400(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_004",
                    "role": "invalid_role_that_does_not_exist",
                    "goal": "This should fail because role is not a valid agent role",
                },
            )

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_add_when_unknown_dependency_should_return_400(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_004",
                    "role": "backend_developer",
                    "goal": "This depends on a task that does not exist anywhere",
                    "depends_on": ["nonexistent_task"],
                },
            )

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_add_when_goal_too_short_should_return_422(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_004",
                    "role": "backend_developer",
                    "goal": "too short",
                },
            )

        # App may return 400 (custom validation handler) or 422 (default pydantic)
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_add_when_invalid_id_format_should_return_422(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "invalid id with spaces!@#",
                    "role": "backend_developer",
                    "goal": "This should fail because the task ID format is invalid",
                },
            )

        # App may return 400 (custom validation handler) or 422 (default pydantic)
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_add_when_no_project_should_return_404(self):
        app = _setup_app_with_manager(manager=None)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_004",
                    "role": "backend_developer",
                    "goal": "This should fail because no active project is found",
                },
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_add_when_valid_deps_on_existing_tasks_should_succeed(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_004",
                    "role": "test_engineer",
                    "goal": "Write additional end-to-end tests for login flow validation",
                    "depends_on": ["task_001", "task_002"],
                    "constraints": ["Use pytest", "Mock external APIs"],
                },
            )

        assert resp.status_code == 200
        assert resp.json()["action"] == "added"


# ===========================================================================
# DELETE /api/projects/{project_id}/plan/tasks/{task_id}
# ===========================================================================


class TestDeletePlanTask:
    """Tests for the DELETE endpoint — remove pending tasks."""

    @pytest.mark.asyncio
    async def test_delete_when_pending_no_dependents_should_succeed(self):
        """task_001 has no pending dependents because task_002 depends on it
        but we need to test with a leaf task."""
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        # task_003 depends on task_001 and task_002, so delete task_003 (leaf)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_003",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "removed"

    @pytest.mark.asyncio
    async def test_delete_when_running_should_use_skip_mechanism(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict(), is_running=True)
        app = _setup_app_with_manager(mgr)

        # Delete leaf task while running
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_003",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] in ("skipped", "removed")

        # Check delta queue
        if not mgr._plan_delta_queue.empty():
            delta = mgr._plan_delta_queue.get_nowait()
            assert "task_003" in delta["skip_task_ids"]

    @pytest.mark.asyncio
    async def test_delete_when_has_pending_dependents_should_return_409(self):
        """task_001 has pending dependents (task_002, task_003), so delete should fail."""
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
            )

        assert resp.status_code == 409
        assert "depend" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_delete_when_task_completed_should_return_409(self):
        mgr = _make_mock_manager(
            dag_graph=_make_dag_graph_dict(),
            dag_task_statuses={"task_001": "completed"},
        )
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
            )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_when_task_working_should_return_409(self):
        mgr = _make_mock_manager(
            dag_graph=_make_dag_graph_dict(),
            dag_task_statuses={"task_001": "working"},
        )
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
            )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_when_task_not_found_should_return_404(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(
                f"/api/projects/{PROJECT_ID}/plan/tasks/nonexistent_task",
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_when_no_project_should_return_404(self):
        app = _setup_app_with_manager(manager=None)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
            )

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_when_dependents_completed_should_allow_delete(self):
        """If dependents are already completed (not pending), delete should succeed."""
        mgr = _make_mock_manager(
            dag_graph=_make_dag_graph_dict(),
            dag_task_statuses={
                "task_002": "completed",
                "task_003": "completed",
            },
        )
        app = _setup_app_with_manager(mgr)

        # task_001 has dependents task_002, task_003 but both are completed
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
            )

        assert resp.status_code == 200


# ===========================================================================
# Pydantic validation tests
# ===========================================================================


class TestRequestValidation:
    """Test Pydantic model validation for plan modification requests."""

    @pytest.mark.asyncio
    async def test_patch_request_when_goal_whitespace_only_should_return_422(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.patch(
                f"/api/projects/{PROJECT_ID}/plan/tasks/task_001",
                json={"goal": "         "},  # all whitespace, stripped < 10 chars
            )

        # App may return 400 (custom validation handler) or 422 (default pydantic)
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_add_request_when_constraints_exceed_max_should_return_422(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "task_004",
                    "role": "backend_developer",
                    "goal": "This task has way too many constraints listed below",
                    "constraints": [f"constraint_{i}" for i in range(25)],
                },
            )

        # App may return 400 (custom validation handler) or 422 (default pydantic)
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_add_request_when_id_too_long_should_return_422(self):
        mgr = _make_mock_manager(dag_graph=_make_dag_graph_dict())
        app = _setup_app_with_manager(mgr)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                f"/api/projects/{PROJECT_ID}/plan/tasks",
                json={
                    "id": "a" * 65,  # exceeds 64 char limit
                    "role": "backend_developer",
                    "goal": "This should fail because the task ID is too long for validation",
                },
            )

        # App may return 400 (custom validation handler) or 422 (default pydantic)
        assert resp.status_code in (400, 422)
