"""tests/test_health_metrics.py — Tests for GET /api/health/detailed and GET /api/metrics.

Covers:
  - Correct JSON shape and all required fields for both endpoints
  - HTTP 200 status on happy path
  - HTTP 200 (with partial/empty data) on internal failure (not 500)
  - Correct aggregation of agent state data for metrics

Naming convention: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers — minimal app factory using only the system router
# ---------------------------------------------------------------------------

HEALTH_DETAILED_URL = "/api/health/detailed"
METRICS_URL = "/api/metrics"

# Required fields per the acceptance criteria
HEALTH_DETAILED_REQUIRED_FIELDS = {
    "uptime_seconds",
    "active_sessions_count",
    "total_tasks_completed",
    "memory_usage_mb",
    "last_error_timestamp",
}

METRICS_AGENT_REQUIRED_FIELDS = {
    "total_tasks_run",
    "avg_duration_ms",
    "success_rate",
    "total_cost_usd",
}


def _make_system_app() -> FastAPI:
    """Create a minimal FastAPI app with only the system router mounted.

    Avoids heavy imports from dashboard.api.create_app() (which requires
    database initialisation, platform DB, task queues, etc.).
    """
    from dashboard.routers.system import router as system_router

    app = FastAPI()
    app.include_router(system_router)
    return app


def _make_mock_manager(agent_states: dict) -> MagicMock:
    """Create a mock OrchestratorManager with the given agent_states dict."""
    mgr = MagicMock()
    mgr.agent_states = agent_states
    return mgr


# ---------------------------------------------------------------------------
# GET /api/health/detailed — happy path
# ---------------------------------------------------------------------------


class TestHealthDetailedHappyPath:
    """Verify the detailed health endpoint returns correct shape and 200 on success."""

    @pytest.mark.asyncio
    async def test_health_detailed_when_no_managers_should_return_200(self):
        """Basic liveness: endpoint must return HTTP 200 even with no active managers."""
        import state

        state.server_start_time = __import__("time").monotonic()
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_detailed_when_no_managers_should_have_all_required_fields(self):
        """All five required fields must be present in the response body."""
        import state

        state.server_start_time = __import__("time").monotonic()
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        for field in HEALTH_DETAILED_REQUIRED_FIELDS:
            assert field in data, f"Required field '{field}' missing from /api/health/detailed"

    @pytest.mark.asyncio
    async def test_health_detailed_when_server_started_should_return_non_negative_uptime(self):
        """uptime_seconds must be >= 0 when server_start_time is set."""
        import time

        import state

        state.server_start_time = time.monotonic()
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        assert data["uptime_seconds"] is not None
        assert data["uptime_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_health_detailed_when_server_start_time_none_should_return_null_uptime(self):
        """uptime_seconds must be None when server_start_time is not set."""
        import state

        state.server_start_time = None  # type: ignore[assignment]
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        assert data["uptime_seconds"] is None

    @pytest.mark.asyncio
    async def test_health_detailed_when_no_managers_should_return_zero_active_sessions(self):
        """active_sessions_count must be 0 with an empty active_sessions dict."""
        import state

        state.active_sessions.clear()
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        assert data["active_sessions_count"] == 0

    @pytest.mark.asyncio
    async def test_health_detailed_when_sessions_exist_should_count_active_sessions(self):
        """active_sessions_count must sum all sessions across all users."""
        import state

        # Two users: user 1 has 2 sessions, user 2 has 1 session → total 3
        state.active_sessions.clear()
        state.active_sessions[1] = {"proj_a": MagicMock(), "proj_b": MagicMock()}
        state.active_sessions[2] = {"proj_c": MagicMock()}
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        assert data["active_sessions_count"] == 3

    @pytest.mark.asyncio
    async def test_health_detailed_when_done_agents_exist_should_count_total_tasks_completed(self):
        """total_tasks_completed must count agents with state 'done' or 'error'."""
        mgr = _make_mock_manager(
            {
                "pm": {"state": "done"},
                "dev_frontend": {"state": "done"},
                "dev_backend": {"state": "error"},
                "reviewer": {"state": "running"},  # NOT counted
            }
        )
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj_x", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        # pm=done, dev_frontend=done, dev_backend=error → 3 completed (reviewer is running)
        assert data["total_tasks_completed"] == 3

    @pytest.mark.asyncio
    async def test_health_detailed_when_no_tasks_done_should_return_zero_total_tasks(self):
        """total_tasks_completed must be 0 when all agents are still running."""
        mgr = _make_mock_manager({"pm": {"state": "running"}, "dev": {"state": "pending"}})
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj_y", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        assert data["total_tasks_completed"] == 0

    @pytest.mark.asyncio
    async def test_health_detailed_when_no_prior_errors_should_return_null_last_error_timestamp(
        self,
    ):
        """last_error_timestamp must be None when no errors have occurred."""
        # Reset module-level _last_error_timestamp
        import dashboard.routers.system as system_mod

        system_mod._last_error_timestamp = None

        app = _make_system_app()
        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        assert data["last_error_timestamp"] is None

    @pytest.mark.asyncio
    async def test_health_detailed_when_memory_usage_present_should_be_positive_or_null(self):
        """memory_usage_mb must be a positive number or None (if measurement fails)."""
        app = _make_system_app()
        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        mem = data["memory_usage_mb"]
        # Either None (probe failed) or a positive float
        assert mem is None or mem > 0


# ---------------------------------------------------------------------------
# GET /api/health/detailed — error path
# ---------------------------------------------------------------------------


class TestHealthDetailedErrorPath:
    """Verify /api/health/detailed returns 200 (never 500) on internal failure."""

    @pytest.mark.asyncio
    async def test_health_detailed_when_get_all_managers_raises_should_return_200(self):
        """Internal exception must NOT bubble to HTTP 500; endpoint returns 200 with partial data."""
        app = _make_system_app()

        with patch("state.get_all_managers", side_effect=RuntimeError("state exploded")):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_detailed_when_get_all_managers_raises_should_still_have_required_fields(
        self,
    ):
        """Even on failure, all five required fields must be present (possibly None/0)."""
        app = _make_system_app()

        with patch("state.get_all_managers", side_effect=RuntimeError("state exploded")):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(HEALTH_DETAILED_URL)

        data = resp.json()
        for field in HEALTH_DETAILED_REQUIRED_FIELDS:
            assert field in data, f"Required field '{field}' missing from error response"

    @pytest.mark.asyncio
    async def test_health_detailed_when_active_sessions_raises_should_still_return_200(self):
        """active_sessions access failure must not propagate as 500."""
        app = _make_system_app()

        # Patch active_sessions to raise when accessed
        with patch("state.active_sessions", new_callable=MagicMock) as mock_sessions:
            mock_sessions.values.side_effect = AttributeError("sessions broken")
            with patch("state.get_all_managers", return_value=[]):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.get(HEALTH_DETAILED_URL)

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/metrics — happy path
# ---------------------------------------------------------------------------


class TestMetricsHappyPath:
    """Verify the metrics endpoint returns correct shape and 200 on success."""

    @pytest.mark.asyncio
    async def test_metrics_when_no_managers_should_return_200(self):
        """Metrics must return HTTP 200 even when there are no active managers."""
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_when_no_managers_should_return_empty_dict(self):
        """Metrics must return an empty JSON object when no managers exist."""
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        assert resp.json() == {}

    @pytest.mark.asyncio
    async def test_metrics_when_done_agents_exist_should_have_all_required_fields_per_role(self):
        """Each role entry in metrics must have all four required fields."""
        mgr = _make_mock_manager(
            {
                "pm": {"state": "done", "duration": 10.0, "cost": 0.005},
            }
        )
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        assert "pm" in data
        for field in METRICS_AGENT_REQUIRED_FIELDS:
            assert field in data["pm"], f"Required field '{field}' missing from metrics role entry"

    @pytest.mark.asyncio
    async def test_metrics_when_one_done_task_should_return_total_tasks_run_one(self):
        """total_tasks_run must be 1 for a role with a single completed task."""
        mgr = _make_mock_manager({"dev_backend": {"state": "done", "duration": 30.0, "cost": 0.01}})
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        assert data["dev_backend"]["total_tasks_run"] == 1

    @pytest.mark.asyncio
    async def test_metrics_when_all_tasks_succeed_should_return_success_rate_one(self):
        """success_rate must be 1.0 when all tasks for a role finished with state='done'."""
        mgr = _make_mock_manager(
            {
                "reviewer": {"state": "done", "duration": 5.0, "cost": 0.002},
            }
        )
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        assert data["reviewer"]["success_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_metrics_when_all_tasks_fail_should_return_success_rate_zero(self):
        """success_rate must be 0.0 when all tasks for a role finished with state='error'."""
        mgr = _make_mock_manager({"qa": {"state": "error", "duration": 2.0, "cost": 0.001}})
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        assert data["qa"]["success_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_metrics_when_mixed_results_should_compute_correct_success_rate(self):
        """success_rate must be 0.5 for one success and one failure (two managers, same role)."""
        mgr_a = _make_mock_manager({"dev": {"state": "done", "duration": 20.0, "cost": 0.01}})
        mgr_b = _make_mock_manager({"dev": {"state": "error", "duration": 10.0, "cost": 0.005}})
        app = _make_system_app()

        with patch(
            "state.get_all_managers",
            return_value=[(1, "proj_a", mgr_a), (1, "proj_b", mgr_b)],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        assert data["dev"]["total_tasks_run"] == 2
        assert data["dev"]["success_rate"] == 0.5

    @pytest.mark.asyncio
    async def test_metrics_when_duration_provided_should_compute_avg_duration_ms(self):
        """avg_duration_ms must convert seconds to milliseconds correctly."""
        # 10 seconds → 10 000 ms
        mgr = _make_mock_manager({"architect": {"state": "done", "duration": 10.0, "cost": 0.0}})
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        assert data["architect"]["avg_duration_ms"] == 10_000.0

    @pytest.mark.asyncio
    async def test_metrics_when_cost_provided_should_sum_total_cost_usd(self):
        """total_cost_usd must equal the sum of cost values across all completed tasks."""
        mgr_a = _make_mock_manager({"pm": {"state": "done", "duration": 5.0, "cost": 0.005}})
        mgr_b = _make_mock_manager({"pm": {"state": "done", "duration": 8.0, "cost": 0.003}})
        app = _make_system_app()

        with patch(
            "state.get_all_managers",
            return_value=[(1, "proj_a", mgr_a), (2, "proj_b", mgr_b)],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        # 0.005 + 0.003 = 0.008
        assert abs(data["pm"]["total_cost_usd"] - 0.008) < 1e-9

    @pytest.mark.asyncio
    async def test_metrics_when_running_agents_exist_should_not_count_them(self):
        """Agents still running (state != 'done'/'error') must not appear in metrics."""
        mgr = _make_mock_manager(
            {
                "dev": {"state": "running", "duration": 0.0, "cost": 0.0},
                "pm": {"state": "pending", "duration": 0.0, "cost": 0.0},
            }
        )
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        assert "dev" not in data
        assert "pm" not in data

    @pytest.mark.asyncio
    async def test_metrics_when_multiple_roles_done_should_return_entry_for_each_role(self):
        """Metrics response must contain one entry per completed role."""
        mgr = _make_mock_manager(
            {
                "pm": {"state": "done", "duration": 10.0, "cost": 0.005},
                "dev_frontend": {"state": "done", "duration": 20.0, "cost": 0.01},
                "reviewer": {"state": "error", "duration": 5.0, "cost": 0.002},
            }
        )
        app = _make_system_app()

        with patch("state.get_all_managers", return_value=[(1, "proj", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        data = resp.json()
        assert set(data.keys()) == {"pm", "dev_frontend", "reviewer"}


# ---------------------------------------------------------------------------
# GET /api/metrics — error path
# ---------------------------------------------------------------------------


class TestMetricsErrorPath:
    """Verify /api/metrics returns 200 (never 500) on internal failure."""

    @pytest.mark.asyncio
    async def test_metrics_when_get_all_managers_raises_should_return_200(self):
        """Internal exception during manager iteration must not propagate as HTTP 500."""
        app = _make_system_app()

        with patch("state.get_all_managers", side_effect=RuntimeError("managers broken")):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_when_get_all_managers_raises_should_return_empty_dict(self):
        """On internal failure, metrics must gracefully return an empty dict."""
        app = _make_system_app()

        with patch("state.get_all_managers", side_effect=RuntimeError("managers broken")):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        assert resp.json() == {}

    @pytest.mark.asyncio
    async def test_metrics_when_agent_state_malformed_should_still_return_200(self):
        """Malformed agent state (non-dict) must not crash the endpoint."""
        mgr = _make_mock_manager({"pm": "not-a-dict"})  # intentionally wrong type
        app = _make_system_app()

        # The endpoint does agent_state.get("state", "") — this will raise AttributeError
        # on a str; the endpoint must catch it gracefully
        with patch("state.get_all_managers", return_value=[(1, "proj", mgr)]):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(METRICS_URL)

        assert resp.status_code == 200
