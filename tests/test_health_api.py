"""Tests for watchdog health API endpoints — GET /api/health/tests, POST /api/health/tests/run.

task_id: task_007
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.health import (
    SuiteResult,
    TestFailure,
    TestHealthResponse,
    TestRunResult,
    TestRunTriggerResponse,
)


# ---------------------------------------------------------------------------
# Helpers: build a minimal FastAPI app with just the system router
# ---------------------------------------------------------------------------


def _make_app():
    """Create a FastAPI app with only the system router for isolated testing."""
    from fastapi import FastAPI
    from dashboard.routers.system import router
    app = FastAPI()
    app.include_router(router)
    return app


def _make_runner_mock(
    is_running: bool = False,
    scheduler_active: bool = False,
    interval: int = 1800,
    last_run: TestRunResult | None = None,
    recent_runs: list[TestRunResult] | None = None,
):
    """Build a mock WatchdogTestRunner with the given property values."""
    runner = MagicMock()
    type(runner).is_running = PropertyMock(return_value=is_running)
    type(runner).scheduler_active = PropertyMock(return_value=scheduler_active)
    type(runner).interval = PropertyMock(return_value=interval)
    type(runner).last_run = PropertyMock(return_value=last_run)
    type(runner).recent_runs = PropertyMock(return_value=recent_runs or [])
    runner.run_all = AsyncMock()
    return runner


_SAMPLE_RUN = TestRunResult(
    run_id="abc123",
    status="passed",
    trigger="manual",
    started_at="2026-01-01T00:00:00Z",
    finished_at="2026-01-01T00:01:00Z",
    duration_seconds=60.0,
    summary="5/5 passed, 0 failed (60.0s)",
    suites=[SuiteResult(suite="backend", status="passed", total=5, passed=5)],
)


# ===========================================================================
# Pydantic model validation tests
# ===========================================================================


class TestPydanticModels:
    def test_test_failure_requires_test_name(self):
        f = TestFailure(test_name="test_x")
        assert f.test_name == "test_x"
        assert f.message == ""

    def test_suite_result_defaults(self):
        s = SuiteResult(suite="backend", status="passed")
        assert s.total == 0
        assert s.passed == 0
        assert s.failed == 0
        assert s.failures == []
        assert s.coverage_pct is None

    def test_test_run_result_serialization(self):
        data = _SAMPLE_RUN.model_dump()
        assert data["run_id"] == "abc123"
        assert data["status"] == "passed"
        assert len(data["suites"]) == 1

    def test_test_health_response_schema(self):
        resp = TestHealthResponse(
            status="healthy",
            last_run=_SAMPLE_RUN,
            recent_runs=[_SAMPLE_RUN],
            scheduler_active=True,
            scheduler_interval_seconds=1800,
            is_running=False,
        )
        data = resp.model_dump()
        assert data["status"] == "healthy"
        assert data["scheduler_active"] is True
        assert data["last_run"]["run_id"] == "abc123"

    def test_test_run_trigger_response(self):
        r = TestRunTriggerResponse(run_id="xyz")
        assert r.message == "Test run started"


# ===========================================================================
# GET /api/health/tests
# ===========================================================================


class TestGetHealthTests:
    @pytest.mark.asyncio
    async def test_get_health_tests_when_no_runs_should_return_unknown(self):
        runner = _make_runner_mock()
        app = _make_app()
        with patch("src.watchdog.test_runner.watchdog_runner", runner):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/health/tests")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unknown"
        assert data["last_run"] is None
        assert data["recent_runs"] == []
        assert data["is_running"] is False

    @pytest.mark.asyncio
    async def test_get_health_tests_when_last_run_passed_should_return_healthy(self):
        runner = _make_runner_mock(last_run=_SAMPLE_RUN, recent_runs=[_SAMPLE_RUN])
        app = _make_app()
        with patch("src.watchdog.test_runner.watchdog_runner", runner):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/health/tests")

        data = resp.json()
        assert data["status"] == "healthy"
        assert data["last_run"]["run_id"] == "abc123"
        assert len(data["recent_runs"]) == 1

    @pytest.mark.asyncio
    async def test_get_health_tests_when_last_run_failed_should_return_failing(self):
        failed_run = TestRunResult(
            run_id="fail1", status="failed", trigger="scheduled",
            started_at="2026-01-01T00:00:00Z",
        )
        runner = _make_runner_mock(last_run=failed_run, recent_runs=[failed_run])
        app = _make_app()
        with patch("src.watchdog.test_runner.watchdog_runner", runner):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/health/tests")

        assert resp.json()["status"] == "failing"

    @pytest.mark.asyncio
    async def test_get_health_tests_should_include_scheduler_info(self):
        runner = _make_runner_mock(scheduler_active=True, interval=600)
        app = _make_app()
        with patch("src.watchdog.test_runner.watchdog_runner", runner):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/health/tests")

        data = resp.json()
        assert data["scheduler_active"] is True
        assert data["scheduler_interval_seconds"] == 600

    @pytest.mark.asyncio
    async def test_get_health_tests_response_matches_schema(self):
        runner = _make_runner_mock(last_run=_SAMPLE_RUN, recent_runs=[_SAMPLE_RUN])
        app = _make_app()
        with patch("src.watchdog.test_runner.watchdog_runner", runner):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/health/tests")

        data = resp.json()
        # Validate all required fields exist
        required_keys = {"status", "last_run", "recent_runs", "scheduler_active",
                         "scheduler_interval_seconds", "is_running"}
        assert required_keys.issubset(data.keys())


# ===========================================================================
# POST /api/health/tests/run
# ===========================================================================


class TestPostHealthTestsRun:
    @pytest.mark.asyncio
    async def test_trigger_run_when_idle_should_return_202(self):
        runner = _make_runner_mock(is_running=False)
        app = _make_app()
        with patch("src.watchdog.test_runner.watchdog_runner", runner):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/health/tests/run")

        assert resp.status_code == 202
        data = resp.json()
        assert "run_id" in data
        assert data["message"] == "Test run started"

    @pytest.mark.asyncio
    async def test_trigger_run_when_already_running_should_return_409(self):
        runner = _make_runner_mock(is_running=True)
        app = _make_app()
        with patch("src.watchdog.test_runner.watchdog_runner", runner):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/health/tests/run")

        assert resp.status_code == 409
        assert "already in progress" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_trigger_run_response_matches_schema(self):
        runner = _make_runner_mock(is_running=False)
        app = _make_app()
        with patch("src.watchdog.test_runner.watchdog_runner", runner):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/health/tests/run")

        data = resp.json()
        assert isinstance(data["run_id"], str)
        assert len(data["run_id"]) > 0
        assert isinstance(data["message"], str)
