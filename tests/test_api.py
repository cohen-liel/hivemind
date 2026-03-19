"""
tests/test_api.py — FastAPI endpoint smoke tests for dashboard/api.py.

Uses httpx.AsyncClient + ASGITransport (pytest-asyncio STRICT mode).
All external dependencies (DB, SDK, OrchestratorManager) are mocked.

Endpoints covered
-----------------
GET  /health          — liveness probe (always 200, no DB dependency)
GET  /api/ready       — readiness probe (200 when DB healthy, 503 otherwise)
GET  /api/health      — enhanced health check (DB + CLI + disk + sessions)
GET  /api/projects    — list projects (empty state)
GET  /api/stats       — aggregate stats
GET  /api/projects/{project_id}  — project detail: 404 for unknown projects

Naming convention: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_session_mgr(healthy: bool = True):
    """Minimal mock SessionManager sufficient for smoke tests."""
    smgr = AsyncMock()
    smgr.is_healthy = AsyncMock(return_value=healthy)
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


def _make_app(session_mgr=None):
    """Create the FastAPI app with a mocked state (session + SDK)."""
    import state

    state.session_mgr = session_mgr if session_mgr is not None else _make_session_mgr()
    state.sdk_client = MagicMock()
    from dashboard.api import create_app

    return create_app()


def _make_app_no_db():
    """Create the FastAPI app with session_mgr=None (DB not initialised)."""
    import state

    state.session_mgr = None
    state.sdk_client = MagicMock()
    from dashboard.api import create_app

    return create_app()


# ===========================================================================
# GET /health — liveness probe
# ===========================================================================


class TestLivenessProbe:
    """GET /health must ALWAYS return 200, regardless of DB state."""

    @pytest.mark.asyncio
    async def test_health_when_db_healthy_should_return_200(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_when_called_should_contain_status_ok(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        assert resp.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_health_when_db_is_none_should_still_return_200(self):
        """Liveness is unconditional — never depends on external services."""
        app = _make_app_no_db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_when_db_is_none_should_return_status_ok(self):
        """Even with no DB, liveness must return {"status": "ok"}."""
        app = _make_app_no_db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_response_is_json(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        assert "application/json" in resp.headers.get("content-type", "")


# ===========================================================================
# GET /api/ready — readiness probe
# ===========================================================================


class TestReadinessProbe:
    """GET /api/ready returns 200 when DB is healthy, 503 otherwise."""

    @pytest.mark.asyncio
    async def test_ready_when_db_healthy_should_return_200(self):
        app = _make_app(_make_session_mgr(healthy=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_ready_when_db_healthy_should_return_status_ok(self):
        app = _make_app(_make_session_mgr(healthy=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_ready_when_db_unhealthy_should_return_503(self):
        app = _make_app(_make_session_mgr(healthy=False))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_ready_when_db_unhealthy_should_return_not_ready_status(self):
        app = _make_app(_make_session_mgr(healthy=False))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
        data = resp.json()
        assert data["status"] == "not_ready"

    @pytest.mark.asyncio
    async def test_ready_when_session_mgr_none_should_return_503(self):
        """DB not initialised → readiness must return 503 'starting'."""
        app = _make_app_no_db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_ready_when_session_mgr_none_should_return_starting_status(self):
        app = _make_app_no_db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
        assert resp.json()["status"] == "starting"

    @pytest.mark.asyncio
    async def test_ready_503_body_contains_reason_field(self):
        app = _make_app_no_db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/ready")
        assert "reason" in resp.json()


# ===========================================================================
# GET /api/projects — list projects
# ===========================================================================


class TestListProjectsSmoke:
    """GET /api/projects smoke tests."""

    @pytest.mark.asyncio
    async def test_list_projects_when_no_active_projects_should_return_200(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_projects_when_no_projects_should_return_empty_list(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects")
        data = resp.json()
        assert "projects" in data
        assert isinstance(data["projects"], list)
        assert len(data["projects"]) == 0

    @pytest.mark.asyncio
    async def test_list_projects_response_is_json(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects")
        assert "application/json" in resp.headers.get("content-type", "")


# ===========================================================================
# GET /api/stats — aggregate statistics
# ===========================================================================


class TestStatsEndpoint:
    """GET /api/stats smoke tests."""

    @pytest.mark.asyncio
    async def test_stats_when_called_should_return_200(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/stats")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_stats_when_no_projects_should_contain_expected_keys(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/stats")
        data = resp.json()
        assert "total_tokens" in data
        assert "total_projects" in data
        assert "active_projects" in data

    @pytest.mark.asyncio
    async def test_stats_when_no_projects_should_return_zero_counts(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/stats")
        data = resp.json()
        assert data["total_tokens"] == 0
        assert data["total_projects"] == 0
        assert data["active_projects"] == 0

    @pytest.mark.asyncio
    async def test_stats_response_is_json(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/stats")
        assert "application/json" in resp.headers.get("content-type", "")


# ===========================================================================
# GET /api/projects/{project_id} — unknown / invalid project IDs
# ===========================================================================


class TestProjectDetailSmoke:
    """GET /api/projects/{project_id} error cases."""

    @pytest.mark.asyncio
    async def test_get_project_when_project_not_in_db_should_return_404(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/nonexistent-proj")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_project_404_response_is_json(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/nonexistent-proj")
        assert "application/json" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_get_project_when_id_has_uppercase_should_return_404(self):
        """Project IDs must be lowercase — uppercase should be treated as invalid."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/UPPERCASE-ID")
        # Invalid format → _valid_project_id returns False → 404
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_project_when_id_has_spaces_should_return_404(self):
        """Project IDs with spaces are invalid format."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/bad%20id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_project_when_id_starts_with_hyphen_should_return_404(self):
        """Project IDs must start with alphanumeric, not hyphen."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/projects/-bad-id")
        assert resp.status_code == 404


# ===========================================================================
# GET /api/health — enhanced health check
# ===========================================================================


class TestEnhancedHealthCheck:
    """GET /api/health returns a detailed health payload."""

    @pytest.mark.asyncio
    async def test_api_health_when_db_healthy_should_return_200(self):
        app = _make_app(_make_session_mgr(healthy=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_api_health_when_db_healthy_should_contain_status_ok(self):
        app = _make_app(_make_session_mgr(healthy=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert data["db"] == "ok"

    @pytest.mark.asyncio
    async def test_api_health_when_db_unhealthy_should_return_degraded(self):
        app = _make_app(_make_session_mgr(healthy=False))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        data = resp.json()
        assert data["db"] == "error"
        assert data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_api_health_response_contains_required_keys(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        data = resp.json()
        assert "status" in data
        assert "db" in data
        assert "active_sessions" in data

    @pytest.mark.asyncio
    async def test_api_health_active_sessions_is_integer(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        data = resp.json()
        assert isinstance(data["active_sessions"], int)
        assert data["active_sessions"] >= 0

    @pytest.mark.asyncio
    async def test_api_health_when_called_should_contain_disk_info(self):
        """Enhanced health endpoint returns disk space information."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        data = resp.json()
        assert "disk_free_gb" in data

    @pytest.mark.asyncio
    async def test_api_health_when_called_should_contain_python_version(self):
        """Enhanced health endpoint reports Python version for debugging."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        data = resp.json()
        assert "python_version" in data
        # Python version should look like "3.x.y"
        assert isinstance(data["python_version"], str)
        assert data["python_version"].count(".") >= 1

    @pytest.mark.asyncio
    async def test_api_health_when_called_should_contain_platform(self):
        """Enhanced health endpoint reports OS platform."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        data = resp.json()
        assert "platform" in data
        assert isinstance(data["platform"], str)
        assert data["platform"] in ("Linux", "Darwin", "Windows", "Java")

    @pytest.mark.asyncio
    async def test_api_health_status_is_string(self):
        """Health status must be a string: 'ok' or 'degraded'."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        data = resp.json()
        assert isinstance(data["status"], str)
        assert data["status"] in ("ok", "degraded")

    @pytest.mark.asyncio
    async def test_api_health_always_returns_200(self):
        """Health endpoint must ALWAYS return 200 regardless of subsystem state.

        Kubernetes and load balancers use the HTTP status code to decide whether
        to kill a pod. A 500 from /api/health would cause cascading restarts.
        """
        # Even with an unhealthy DB, the endpoint itself should return 200
        app = _make_app(_make_session_mgr(healthy=False))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/health")
        assert resp.status_code == 200
