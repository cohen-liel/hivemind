"""Pydantic models for watchdog test health results."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TestFailure(BaseModel):
    """A single test failure with identifying information."""

    test_name: str = Field(..., description="Fully qualified test name")
    message: str = Field("", description="Failure message or assertion text")

    model_config = {"json_schema_extra": {"examples": [{"test_name": "tests/test_api.py::test_login_401", "message": "AssertionError: expected 401"}]}}


class SuiteResult(BaseModel):
    """Result of running one test suite (backend or frontend)."""

    suite: str = Field(..., description="Suite identifier: 'backend' or 'frontend'")
    status: str = Field(..., description="'passed', 'failed', or 'error'")
    total: int = Field(0, ge=0, description="Total tests discovered")
    passed: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    skipped: int = Field(0, ge=0)
    errors: int = Field(0, ge=0)
    failures: list[TestFailure] = Field(default_factory=list, description="Details of each failure")
    coverage_pct: float | None = Field(None, ge=0, le=100, description="Line coverage percentage")
    duration_seconds: float = Field(0.0, ge=0, description="Wall-clock seconds for the suite run")
    error_message: str | None = Field(None, description="Top-level error if suite could not run")

    model_config = {"json_schema_extra": {"examples": [{"suite": "backend", "status": "passed", "total": 42, "passed": 40, "failed": 1, "skipped": 1, "errors": 0, "failures": [{"test_name": "test_foo", "message": "assert 1 == 2"}], "coverage_pct": 87.3, "duration_seconds": 12.5}]}}


class TestRunResult(BaseModel):
    """Aggregated result of a full watchdog test run."""

    run_id: str = Field(..., description="Unique run identifier")
    status: str = Field(..., description="'passed', 'failed', or 'error'")
    trigger: str = Field("manual", description="'manual', 'scheduled', or 'startup'")
    started_at: str = Field(..., description="ISO 8601 timestamp")
    finished_at: str | None = Field(None, description="ISO 8601 timestamp")
    duration_seconds: float = Field(0.0, ge=0)
    suites: list[SuiteResult] = Field(default_factory=list)
    summary: str = Field("", description="Human-readable one-line summary")


class TestHealthResponse(BaseModel):
    """Response model for GET /api/health/tests."""

    status: str = Field(..., description="Overall test health: 'healthy', 'failing', 'unknown'")
    last_run: TestRunResult | None = Field(None, description="Most recent test run")
    recent_runs: list[TestRunResult] = Field(default_factory=list, description="Last N runs")
    scheduler_active: bool = Field(False, description="Whether periodic scheduling is running")
    scheduler_interval_seconds: int = Field(1800, description="Interval between scheduled runs")
    is_running: bool = Field(False, description="Whether a test run is currently in progress")


class TestRunTriggerResponse(BaseModel):
    """Response for POST /api/health/tests/run."""

    run_id: str
    message: str = "Test run started"
