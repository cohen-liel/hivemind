"""
Tests for project_stats.py — per-project SQLite statistics module.

All tests use tmp_path to isolate the DB from production .hivemind/stats.db.
The module-level _HIVEMIND_DIR and _DB_PATH are patched via monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import project_stats

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_db_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirect project_stats to use a temp directory instead of .hivemind/."""
    fake_dir = tmp_path / ".hivemind"
    fake_db = fake_dir / "stats.db"
    monkeypatch.setattr(project_stats, "_HIVEMIND_DIR", fake_dir)
    monkeypatch.setattr(project_stats, "_DB_PATH", fake_db)
    return fake_db


# ---------------------------------------------------------------------------
# Test 1 — DB and directory are auto-created on first use
# ---------------------------------------------------------------------------


async def test_db_and_directory_auto_created_on_first_use(monkeypatch, tmp_path):
    """record_task_completion must create .hivemind/ and stats.db if absent."""
    fake_dir = tmp_path / ".hivemind_new"
    fake_db = fake_dir / "stats.db"
    monkeypatch.setattr(project_stats, "_HIVEMIND_DIR", fake_dir)
    monkeypatch.setattr(project_stats, "_DB_PATH", fake_db)

    assert not fake_dir.exists(), "Pre-condition: directory must not exist yet"

    await project_stats.record_task_completion(
        task_id="t-autocreate",
        project_id="proj-x",
        role="backend_developer",
        goal="Bootstrap DB",
        status="completed",
        duration_seconds=1.0,
        cost_usd=0.01,
        files_changed=0,
    )

    assert fake_dir.exists(), ".hivemind/ directory was not created"
    assert fake_db.exists(), "stats.db was not created"


# ---------------------------------------------------------------------------
# Test 2 — record_task_completion inserts a row correctly
# ---------------------------------------------------------------------------


async def test_record_task_completion_inserts_row_correctly(monkeypatch, tmp_path):
    """A successful task should appear as a single row with the right values."""
    fake_db = _patch_db_path(monkeypatch, tmp_path)

    await project_stats.record_task_completion(
        task_id="t-001",
        project_id="proj-alpha",
        role="backend_developer",
        goal="Add REST endpoint",
        status="completed",
        duration_seconds=42.5,
        cost_usd=0.05,
        files_changed=3,
        error_message=None,
        timestamp="2026-04-10T12:00:00Z",
    )

    import aiosqlite

    async with aiosqlite.connect(str(fake_db)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM task_executions WHERE task_id = ?", ("t-001",)) as cur:
            row = await cur.fetchone()

    assert row is not None, "Row was not inserted"
    assert row["task_id"] == "t-001"
    assert row["project_id"] == "proj-alpha"
    assert row["role"] == "backend_developer"
    assert row["goal"] == "Add REST endpoint"
    assert row["status"] == "completed"
    assert row["duration_seconds"] == pytest.approx(42.5)
    assert row["cost_usd"] == pytest.approx(0.05)
    assert row["files_changed"] == 3
    assert row["error_message"] is None
    assert row["timestamp"] == "2026-04-10T12:00:00Z"


# ---------------------------------------------------------------------------
# Test 3 — get_project_stats returns correct aggregated totals
# ---------------------------------------------------------------------------


async def test_get_project_stats_returns_correct_aggregates(monkeypatch, tmp_path):
    """After multiple inserts get_project_stats should sum/average correctly."""
    _patch_db_path(monkeypatch, tmp_path)

    tasks = [
        ("t-a1", "proj-beta", "frontend_developer", "Build UI", "completed", 10.0, 0.10, 2),
        ("t-a2", "proj-beta", "backend_developer", "Build API", "completed", 20.0, 0.20, 4),
        ("t-a3", "proj-beta", "test_engineer", "Write tests", "failed", 5.0, 0.05, 0),
    ]
    for args in tasks:
        await project_stats.record_task_completion(
            task_id=args[0],
            project_id=args[1],
            role=args[2],
            goal=args[3],
            status=args[4],
            duration_seconds=args[5],
            cost_usd=args[6],
            files_changed=args[7],
        )

    stats = await project_stats.get_project_stats("proj-beta")

    assert stats["total_tasks"] == 3
    assert stats["completed_tasks"] == 2
    assert stats["failed_tasks"] == 1
    assert stats["total_duration_s"] == pytest.approx(35.0)
    assert stats["avg_duration_s"] == pytest.approx(35.0 / 3)
    assert stats["total_cost_usd"] == pytest.approx(0.35)
    assert stats["total_files_changed"] == 6
    assert stats["success_rate"] == pytest.approx(2 / 3)
    assert set(stats["roles"]) == {"frontend_developer", "backend_developer", "test_engineer"}


# ---------------------------------------------------------------------------
# Test 4 — get_agent_performance returns per-role breakdown with correct counts
# ---------------------------------------------------------------------------


async def test_get_agent_performance_returns_per_role_breakdown(monkeypatch, tmp_path):
    """get_agent_performance should aggregate by role across all projects."""
    _patch_db_path(monkeypatch, tmp_path)

    records = [
        ("t-p1", "proj-1", "backend_developer", "Task A", "completed", 30.0, 0.30, 1),
        ("t-p2", "proj-2", "backend_developer", "Task B", "completed", 10.0, 0.10, 2),
        ("t-p3", "proj-1", "frontend_developer", "Task C", "failed", 20.0, 0.20, 0),
    ]
    for r in records:
        await project_stats.record_task_completion(
            task_id=r[0],
            project_id=r[1],
            role=r[2],
            goal=r[3],
            status=r[4],
            duration_seconds=r[5],
            cost_usd=r[6],
            files_changed=r[7],
        )

    perf = await project_stats.get_agent_performance()

    # backend_developer: 2 tasks, both completed
    assert "backend_developer" in perf
    bd = perf["backend_developer"]
    assert bd["total_tasks"] == 2
    assert bd["completed_tasks"] == 2
    assert bd["failed_tasks"] == 0
    assert bd["success_rate"] == pytest.approx(1.0)
    assert bd["avg_duration_s"] == pytest.approx(20.0)  # (30+10)/2
    assert bd["total_cost_usd"] == pytest.approx(0.40)

    # frontend_developer: 1 task, failed
    assert "frontend_developer" in perf
    fd = perf["frontend_developer"]
    assert fd["total_tasks"] == 1
    assert fd["completed_tasks"] == 0
    assert fd["failed_tasks"] == 1
    assert fd["success_rate"] == pytest.approx(0.0)
    assert fd["avg_duration_s"] == pytest.approx(20.0)
    assert fd["total_cost_usd"] == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# Test 5 — Failed task stored correctly and reflected in success_rate
# ---------------------------------------------------------------------------


async def test_failed_task_stored_and_reflected_in_success_rate(monkeypatch, tmp_path):
    """A task with status='failed' and an error_message must be stored and
    lower the success_rate in both get_project_stats and get_agent_performance."""
    _patch_db_path(monkeypatch, tmp_path)

    await project_stats.record_task_completion(
        task_id="t-ok",
        project_id="proj-gamma",
        role="test_engineer",
        goal="Green suite",
        status="completed",
        duration_seconds=5.0,
        cost_usd=0.01,
        files_changed=1,
    )
    await project_stats.record_task_completion(
        task_id="t-fail",
        project_id="proj-gamma",
        role="test_engineer",
        goal="Red suite",
        status="failed",
        duration_seconds=2.0,
        cost_usd=0.005,
        files_changed=0,
        error_message="RuntimeError: assertion failed",
    )

    # Verify error_message is persisted
    import aiosqlite

    fake_db = project_stats._DB_PATH
    async with aiosqlite.connect(str(fake_db)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT error_message, status FROM task_executions WHERE task_id = ?",
            ("t-fail",),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert row["status"] == "failed"
    assert row["error_message"] == "RuntimeError: assertion failed"

    # get_project_stats must reflect 50 % success rate
    stats = await project_stats.get_project_stats("proj-gamma")
    assert stats["total_tasks"] == 2
    assert stats["completed_tasks"] == 1
    assert stats["failed_tasks"] == 1
    assert stats["success_rate"] == pytest.approx(0.5)

    # get_agent_performance must also reflect the failure
    perf = await project_stats.get_agent_performance()
    te = perf["test_engineer"]
    assert te["total_tasks"] == 2
    assert te["completed_tasks"] == 1
    assert te["failed_tasks"] == 1
    assert te["success_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Test 6 — Upsert: re-recording same task_id updates, does not duplicate
# ---------------------------------------------------------------------------


async def test_record_task_completion_upserts_on_conflict(monkeypatch, tmp_path):
    """Calling record_task_completion twice with the same task_id should update
    the existing row (ON CONFLICT … DO UPDATE) instead of inserting a duplicate."""
    _patch_db_path(monkeypatch, tmp_path)

    await project_stats.record_task_completion(
        task_id="t-upsert",
        project_id="proj-delta",
        role="backend_developer",
        goal="First attempt",
        status="failed",
        duration_seconds=1.0,
        cost_usd=0.01,
        files_changed=0,
        error_message="Timeout",
    )
    # Overwrite with a successful retry
    await project_stats.record_task_completion(
        task_id="t-upsert",
        project_id="proj-delta",
        role="backend_developer",
        goal="First attempt (retry)",
        status="completed",
        duration_seconds=5.0,
        cost_usd=0.05,
        files_changed=2,
        error_message=None,
    )

    stats = await project_stats.get_project_stats("proj-delta")
    # Only 1 logical task (upsert)
    assert stats["total_tasks"] == 1
    assert stats["completed_tasks"] == 1
    assert stats["failed_tasks"] == 0
    assert stats["success_rate"] == pytest.approx(1.0)
    assert stats["total_duration_s"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Test 7 — Empty project returns zero-value stats, not an error
# ---------------------------------------------------------------------------


async def test_get_project_stats_when_no_tasks_returns_zeros(monkeypatch, tmp_path):
    """Querying a project_id with no rows should return all-zero stats safely."""
    _patch_db_path(monkeypatch, tmp_path)

    stats = await project_stats.get_project_stats("proj-nonexistent")

    assert stats["total_tasks"] == 0
    assert stats["completed_tasks"] == 0
    assert stats["failed_tasks"] == 0
    assert stats["total_duration_s"] == pytest.approx(0.0)
    assert stats["avg_duration_s"] == pytest.approx(0.0)
    assert stats["total_cost_usd"] == pytest.approx(0.0)
    assert stats["total_files_changed"] == 0
    assert stats["success_rate"] == pytest.approx(0.0)
    assert stats["roles"] == []
