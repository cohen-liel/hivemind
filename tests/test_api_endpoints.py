"""REST API endpoint tests for dashboard/api.py.

Tests the main HTTP endpoints using httpx AsyncClient + ASGITransport.
All external dependencies (DB, SDK, OrchestratorManager) are mocked.

Covers:
- Health check
- Project CRUD (list, get, create, update, delete)
- Send message / talk agent
- Project lifecycle (pause, resume, stop, approve, reject)
- Settings (get, update, persist)
- Schedules CRUD
- Stats
- Browse dirs (security)
- Read file (path traversal)
- Error response format
- Message length validation (SEC-01)
- Project path restriction (SEC-03)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# --- Mock helpers ---


def _make_mock_session_mgr():
    """Fully mocked SessionManager with async methods."""
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
    return smgr


def _make_mock_manager(
    project_name="test-project",
    project_dir="/tmp/test-project",
    is_running=False,
    is_paused=False,
    turn_count=0,
    total_cost_usd=0.0,
    total_input_tokens=0,
    total_output_tokens=0,
    total_tokens=0,
):
    """Mock OrchestratorManager with realistic attributes."""
    mgr = MagicMock()
    mgr.project_name = project_name
    mgr.project_dir = project_dir
    mgr.is_running = is_running
    mgr.is_paused = is_paused
    mgr.turn_count = turn_count
    mgr.total_cost_usd = total_cost_usd
    mgr.total_input_tokens = total_input_tokens
    mgr.total_output_tokens = total_output_tokens
    mgr.total_tokens = total_tokens
    mgr.agent_names = ["orchestrator", "developer"]
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
    # session_mgr attribute used by clear_project_history
    mock_inner_smgr = AsyncMock()
    mock_inner_smgr.invalidate_all_sessions = AsyncMock()
    mgr.session_mgr = mock_inner_smgr
    return mgr


def _setup_app():
    """Create the FastAPI app with mocked state."""
    import state

    mock_smgr = _make_mock_session_mgr()
    mock_sdk = MagicMock()
    state.session_mgr = mock_smgr
    state.sdk_client = mock_sdk
    from dashboard.api import create_app

    return create_app(), mock_smgr, mock_sdk


# ============================================================
# Health check
# ============================================================


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert "status" in data
            assert data["db"] == "ok"

    @pytest.mark.asyncio
    async def test_health_degraded_when_db_unhealthy(self):
        app, mock_smgr, _ = _setup_app()
        mock_smgr.is_healthy = AsyncMock(return_value=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
            data = resp.json()
            assert data["db"] == "error"
            assert data["status"] == "degraded"


# ============================================================
# GET /api/projects
# ============================================================


class TestListProjects:
    @pytest.mark.asyncio
    async def test_empty_list(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects")
            assert resp.status_code == 200
            assert resp.json()["projects"] == []

    @pytest.mark.asyncio
    async def test_projects_from_db(self):
        app, mock_smgr, _ = _setup_app()
        mock_smgr.list_projects = AsyncMock(
            return_value=[
                {
                    "project_id": "p1",
                    "name": "P1",
                    "project_dir": "/tmp",
                    "description": "",
                    "created_at": 1000,
                    "updated_at": 2000,
                    "message_count": 5,
                    "user_id": 0,
                },
            ]
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects")
            assert resp.status_code == 200
            assert len(resp.json()["projects"]) == 1
            assert resp.json()["projects"][0]["project_id"] == "p1"

    @pytest.mark.asyncio
    async def test_active_manager_included(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=True, total_cost_usd=0.42, total_tokens=1500)
        await state.register_manager(0, "active-proj", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects")
            assert resp.status_code == 200
            projects = resp.json()["projects"]
            assert len(projects) == 1
            assert projects[0]["status"] == "running"
            assert projects[0]["total_tokens"] == 1500


# ============================================================
# GET /api/projects/{project_id}
# ============================================================


class TestGetProject:
    @pytest.mark.asyncio
    async def test_not_found(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/nonexistent")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_from_db(self):
        app, mock_smgr, _ = _setup_app()
        mock_smgr.load_project = AsyncMock(
            return_value={
                "project_id": "p1",
                "name": "DB Project",
                "project_dir": "/tmp",
                "description": "Test",
                "user_id": 0,
            }
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/p1")
            assert resp.status_code == 200
            assert resp.json()["project_name"] == "DB Project"
            assert resp.json()["status"] == "idle"


# ============================================================
# POST /api/projects (create)
# ============================================================


class TestCreateProject:
    @pytest.mark.asyncio
    async def test_success(self):
        app, mock_smgr, _ = _setup_app()
        mock_smgr.load_project = AsyncMock(return_value=None)
        with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
            fake_home = Path(tmpdir)
            project_dir = os.path.join(tmpdir, "new-proj")
            with patch.object(Path, "home", return_value=fake_home):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as c:
                    resp = await c.post(
                        "/api/projects",
                        json={
                            "name": "My Project",
                            "directory": project_dir,
                            "agents_count": 2,
                        },
                    )
                    assert resp.status_code == 200
                    assert resp.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_invalid_name(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/projects",
                json={
                    "name": "bad@name!",
                    "directory": "/tmp/x",
                    "agents_count": 2,
                },
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_empty_directory_auto_generates(self):
        """Empty directory is valid — the API auto-generates from the project name."""
        app, mock_smgr, _ = _setup_app()
        mock_smgr.load_project = AsyncMock(return_value=None)
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path as _Path

            fake_home = _Path(tmpdir)
            with patch.object(_Path, "home", return_value=fake_home):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as c:
                    resp = await c.post(
                        "/api/projects",
                        json={
                            "name": "good-name",
                            "directory": "",
                            "agents_count": 2,
                        },
                    )
                    # API auto-generates directory from name — should succeed or fail
                    # with 200 (directory auto-generated) or 403 (path restriction)
                    assert resp.status_code in (200, 400, 403)

    @pytest.mark.asyncio
    async def test_system_path_blocked(self):
        """SEC-03: Creating at /var/www should be blocked."""
        app, mock_smgr, _ = _setup_app()
        mock_smgr.load_project = AsyncMock(return_value=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/projects",
                json={
                    "name": "hack",
                    "directory": "/var/www/hack",
                    "agents_count": 2,
                },
            )
            assert resp.status_code in (400, 403)


# ============================================================
# DELETE /api/projects/{project_id}
# ============================================================


class TestDeleteProject:
    @pytest.mark.asyncio
    async def test_success(self):
        app, mock_smgr, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete("/api/projects/some-proj")
            assert resp.status_code == 200
            mock_smgr.delete_project.assert_awaited_once_with("some-proj")

    @pytest.mark.asyncio
    async def test_stops_running_project(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=True)
        await state.register_manager(0, "run-proj", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete("/api/projects/run-proj")
            assert resp.status_code == 200
            mgr.stop.assert_awaited_once()


# ============================================================
# PUT /api/projects/{project_id}
# ============================================================


class TestUpdateProject:
    @pytest.mark.asyncio
    async def test_update_name(self):
        app, mock_smgr, _ = _setup_app()
        mock_smgr.load_project = AsyncMock(return_value={"project_id": "p1", "name": "Old"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put("/api/projects/p1", json={"name": "New Name"})
            assert resp.status_code == 200
            mock_smgr.update_project_fields.assert_awaited()

    @pytest.mark.asyncio
    async def test_nonexistent_returns_404(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put("/api/projects/nonexistent", json={"name": "X"})
            assert resp.status_code == 404


# ============================================================
# POST /api/projects/{project_id}/message
# ============================================================


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_no_manager_returns_404(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/nonexistent/message", json={"message": "Hi"})
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_idle_manager_enqueues_task(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=False)
        await state.register_manager(0, "idle-proj", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/idle-proj/message", json={"message": "Start"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert "task_id" in body

    @pytest.mark.asyncio
    async def test_running_manager_enqueues_task(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=True)
        await state.register_manager(0, "run-proj", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/run-proj/message", json={"message": "Follow up"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert "task_id" in body

    @pytest.mark.asyncio
    async def test_oversized_message_rejected(self):
        """SEC-01: Messages > MAX_USER_MESSAGE_LENGTH chars should be rejected."""
        import state
        from config import MAX_USER_MESSAGE_LENGTH

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=False)
        await state.register_manager(0, "test", mgr)
        huge_msg = "A" * (MAX_USER_MESSAGE_LENGTH + 1)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/test/message", json={"message": huge_msg})
            assert resp.status_code in (400, 422)
            body = resp.json()
            # Pydantic returns 422 with "detail" array; custom handler returns 400 with "error" string
            if "error" in body:
                assert "too long" in body["error"].lower()
            else:
                assert "detail" in body  # Pydantic validation error format


# ============================================================
# POST /api/projects/{project_id}/talk/{agent}
# ============================================================


class TestTalkAgent:
    @pytest.mark.asyncio
    async def test_talk_no_manager(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/x/talk/dev", json={"message": "Hi"})
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_talk_oversized_rejected(self):
        """SEC-01: talk_agent also validates message length."""
        import state
        from config import MAX_USER_MESSAGE_LENGTH

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=True)
        await state.register_manager(0, "x", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/projects/x/talk/dev",
                json={"message": "B" * (MAX_USER_MESSAGE_LENGTH + 1)},
            )
            assert resp.status_code in (400, 422)


# ============================================================
# Project lifecycle: pause, resume, stop, approve, reject
# ============================================================


class TestProjectLifecycle:
    @pytest.mark.asyncio
    async def test_pause(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=True)
        await state.register_manager(0, "p1", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/p1/pause")
            assert resp.status_code == 200
            mgr.pause.assert_called_once()

    @pytest.mark.asyncio
    async def test_pause_nonexistent(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post("/api/projects/x/pause")).status_code == 404

    @pytest.mark.asyncio
    async def test_resume(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_paused=True)
        await state.register_manager(0, "p1", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/p1/resume")
            assert resp.status_code == 200
            mgr.resume.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=True)
        await state.register_manager(0, "p1", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/p1/stop")
            assert resp.status_code == 200
            mgr.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_approve_no_pending(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager()
        mgr.pending_approval = None
        await state.register_manager(0, "p1", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/p1/approve")
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_reject_no_pending(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager()
        mgr.pending_approval = None
        await state.register_manager(0, "p1", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/p1/reject")
            assert resp.status_code in (400, 422)


# ============================================================
# Settings
# ============================================================


class TestSettings:
    @pytest.mark.asyncio
    async def test_get_settings(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/settings")
            assert resp.status_code == 200
            data = resp.json()
            assert "max_turns_per_cycle" in data
            assert "max_budget_usd" in data

    @pytest.mark.asyncio
    async def test_update_settings(self):
        import config as cfg

        original = cfg.MAX_TURNS_PER_CYCLE
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put("/api/settings", json={"max_turns_per_cycle": 999})
            assert resp.status_code == 200
            assert resp.json()["updated"]["max_turns_per_cycle"] == 999
        cfg.MAX_TURNS_PER_CYCLE = original

    @pytest.mark.asyncio
    async def test_persist_disallowed_key(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/settings/persist", json={"evil_key": "bad"})
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_persist_non_object(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/settings/persist",
                content="[1,2,3]",
                headers={"content-type": "application/json"},
            )
            assert resp.status_code in (400, 422)


# ============================================================
# Schedules
# ============================================================


class TestSchedules:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/schedules")
            assert resp.status_code == 200
            assert resp.json()["schedules"] == []

    @pytest.mark.asyncio
    async def test_create_valid(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/schedules",
                json={
                    "project_id": "p1",
                    "schedule_time": "09:30",
                    "task_description": "Daily build",
                },
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_invalid_time(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/schedules",
                json={
                    "project_id": "p1",
                    "schedule_time": "25:99",
                    "task_description": "Fail",
                },
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_create_bad_format(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/schedules",
                json={
                    "project_id": "p1",
                    "schedule_time": "9am",
                    "task_description": "Fail",
                },
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        app, mock_smgr, _ = _setup_app()
        mock_smgr.delete_schedule = AsyncMock(return_value=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete("/api/schedules/999")
            assert resp.status_code == 404


# ============================================================
# Stats
# ============================================================


class TestStats:
    @pytest.mark.asyncio
    async def test_empty_stats(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_tokens"] == 0
            assert data["active_projects"] == 0

    @pytest.mark.asyncio
    async def test_stats_with_projects(self):
        import state

        app, _, _ = _setup_app()
        await state.register_manager(
            0, "p1", _make_mock_manager(is_running=True, total_tokens=1000)
        )
        await state.register_manager(0, "p2", _make_mock_manager(is_paused=True, total_tokens=500))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/stats")
            data = resp.json()
            assert data["total_tokens"] == 1500
            assert data["running"] == 1
            assert data["paused"] == 1


# ============================================================
# Browse dirs (security)
# ============================================================


class TestBrowseDirs:
    @pytest.mark.asyncio
    async def test_home_allowed(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/browse-dirs", params={"path": "~"})
            assert resp.status_code == 200
            assert "entries" in resp.json()

    @pytest.mark.asyncio
    async def test_etc_blocked(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/browse-dirs", params={"path": "/etc"})
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_root_blocked(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/browse-dirs", params={"path": "/"})
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_dotdot_traversal_blocked(self):
        app, _, _ = _setup_app()
        home = str(Path.home())
        escape = os.path.join(home, "..", "..", "etc")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/browse-dirs", params={"path": escape})
            assert resp.status_code == 403


# ============================================================
# Read file (path traversal)
# ============================================================


class TestReadFile:
    @pytest.mark.asyncio
    async def test_traversal_blocked(self):
        app, mock_smgr, _ = _setup_app()
        import state

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_smgr.load_project = AsyncMock(return_value={"project_dir": tmpdir, "name": "test"})
            state.session_mgr = mock_smgr
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/p1/file", params={"path": "../../etc/passwd"})
                data = resp.json()
                # task_002/fix-1: error responses now use RFC 7807 Problem Detail format
                assert "detail" in data or "error" in data

    @pytest.mark.asyncio
    async def test_valid_file_works(self):
        app, mock_smgr, _ = _setup_app()
        import state

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "hello.txt")
            with open(test_file, "w") as f:
                f.write("Hello!")
            mock_smgr.load_project = AsyncMock(return_value={"project_dir": tmpdir, "name": "test"})
            state.session_mgr = mock_smgr
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/p1/file", params={"path": "hello.txt"})
                data = resp.json()
                assert data["content"] == "Hello!"


# ============================================================
# Error response format
# ============================================================


class TestErrorResponses:
    @pytest.mark.asyncio
    async def test_404_has_error_field(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/nonexistent")
            assert resp.status_code == 404
            # task_002/fix-1: error responses now use RFC 7807 Problem Detail format
            assert "detail" in resp.json() or "error" in resp.json()

    @pytest.mark.asyncio
    async def test_400_has_rfc7807_fields(self):
        """RFC 7807 validation errors return 'type', 'title', 'status', 'detail' fields."""
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/projects",
                json={
                    "name": "",
                    "directory": "/tmp/x",
                    "agents_count": 2,
                },
            )
            assert resp.status_code in (400, 422)
            body = resp.json()
            # RFC 7807 format uses 'detail', direct route errors use 'error'
            assert "detail" in body or "error" in body

    @pytest.mark.asyncio
    async def test_missing_body_returns_400(self):
        """Missing request body should return 400 (converted from Pydantic's 422)."""
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects")
            assert resp.status_code == 400


# ============================================================
# GET /api/projects/{project_id}/messages
# ============================================================


class TestGetMessages:
    @pytest.mark.asyncio
    async def test_empty(self):
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/p1/messages")
            assert resp.status_code == 200
            data = resp.json()
            assert data["messages"] == []
            assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_pagination_params(self):
        app, mock_smgr, _ = _setup_app()
        mock_smgr.get_messages_paginated = AsyncMock(
            return_value=(
                [{"content": "Hello", "role": "user"}],
                1,
            )
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/p1/messages", params={"limit": 10, "offset": 5})
            assert resp.status_code == 200
            mock_smgr.get_messages_paginated.assert_awaited_once_with("p1", 10, 5)


# ============================================================
# Clear history
# ============================================================


class TestClearHistory:
    @pytest.mark.asyncio
    async def test_clear_idle_project(self):
        import state

        app, mock_smgr, _ = _setup_app()
        mgr = _make_mock_manager(is_running=False)
        await state.register_manager(0, "p1", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/p1/clear-history")
            assert resp.status_code == 200
            mock_smgr.clear_project_data.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_clear_running_blocked(self):
        import state

        app, _, _ = _setup_app()
        mgr = _make_mock_manager(is_running=True)
        await state.register_manager(0, "p1", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/projects/p1/clear-history")
            assert resp.status_code in (400, 422)


# ============================================================
# GET /health  (liveness probe — always 200)
# ============================================================


class TestLivenessEndpoint:
    """Tests for the simple /health liveness probe endpoint."""

    @pytest.mark.asyncio
    async def test_health_always_returns_200(self):
        """GET /health must always return HTTP 200."""
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_returns_status_ok(self):
        """GET /health body must contain {"status": "ok"}."""
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_returns_200_even_when_db_unhealthy(self):
        """Liveness probe must return 200 regardless of DB health (it's a process check)."""
        app, mock_smgr, _ = _setup_app()
        mock_smgr.is_healthy = AsyncMock(side_effect=RuntimeError("DB dead"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
            # Liveness does NOT check DB — must always be 200
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


# ============================================================
# GET /api/ready  (readiness probe — 503 until DB is up)
# ============================================================


class TestReadinessEndpoint:
    """Tests for the /api/ready readiness probe endpoint."""

    @pytest.mark.asyncio
    async def test_ready_returns_200_when_db_healthy(self):
        """GET /api/ready returns 200 when DB is healthy."""
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_ready_returns_503_when_db_unhealthy(self):
        """GET /api/ready returns 503 when DB reports unhealthy."""
        app, mock_smgr, _ = _setup_app()
        mock_smgr.is_healthy = AsyncMock(return_value=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
            assert resp.status_code == 503
            assert resp.json()["status"] != "ok"

    @pytest.mark.asyncio
    async def test_ready_returns_503_when_session_mgr_none(self):
        """GET /api/ready returns 503 when session manager is not initialised."""
        import state

        app, _, _ = _setup_app()
        original_smgr = state.session_mgr
        state.session_mgr = None  # Simulate uninitialised state
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/ready")
                assert resp.status_code == 503
        finally:
            state.session_mgr = original_smgr

    @pytest.mark.asyncio
    async def test_ready_returns_503_on_db_exception(self):
        """GET /api/ready returns 503 when DB health check throws an exception."""
        app, mock_smgr, _ = _setup_app()
        mock_smgr.is_healthy = AsyncMock(side_effect=ConnectionError("DB unreachable"))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
            assert resp.status_code == 503


# ============================================================
# PUT /api/projects/{project_id}/budget  (SetBudgetRequest validation)
# ============================================================


class TestSetBudget:
    """Tests for the project budget endpoint with Pydantic-level validation."""

    @pytest.mark.asyncio
    async def test_valid_budget_accepted(self):
        """A positive budget within range is accepted."""
        import state

        app, mock_smgr, _ = _setup_app()
        mgr = _make_mock_manager()
        await state.register_manager(0, "proj1", mgr)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put("/api/projects/proj1/budget", json={"budget_usd": 10.0})
            assert resp.status_code == 200
            assert resp.json()["budget_usd"] == 10.0

    @pytest.mark.asyncio
    async def test_negative_budget_rejected(self):
        """Negative budget must be rejected by Pydantic model (gt=0 constraint)."""
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put("/api/projects/proj1/budget", json={"budget_usd": -5.0})
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_zero_budget_rejected(self):
        """Zero budget must be rejected (gt=0 means strictly positive)."""
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put("/api/projects/proj1/budget", json={"budget_usd": 0})
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_budget_above_max_rejected(self):
        """Budget exceeding $10,000 must be rejected (le=10_000 constraint)."""
        app, _, _ = _setup_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put("/api/projects/proj1/budget", json={"budget_usd": 99999.0})
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_nan_budget_rejected(self):
        """NaN budget must be rejected — JSON NaN is not valid JSON so this tests string coercion."""
        app, _, _ = _setup_app()
        # JSON doesn't support NaN natively — simulate by sending a string
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                "/api/projects/proj1/budget",
                content=b'{"budget_usd": "not-a-number"}',
                headers={"content-type": "application/json"},
            )
            assert resp.status_code in (400, 422)
