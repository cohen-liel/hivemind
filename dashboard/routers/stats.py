"""Stats endpoints — per-project execution statistics from project_stats.py.

Provides:
    GET /api/stats/projects  — aggregated stats for every project that has run
"""

from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("dashboard.api")

router = APIRouter(tags=["stats"])


class ProjectStatsItem(BaseModel):
    """Per-project aggregated execution statistics."""

    project_id: str = Field(..., examples=["my-project-123"])
    total_runs: int = Field(..., ge=0, examples=[42])
    failed_runs: int = Field(..., ge=0, examples=[3])
    last_run_at: str | None = Field(None, examples=["2026-04-10T07:03:00Z"])
    avg_duration_s: float = Field(..., ge=0.0, examples=[12.4])


async def _get_all_project_stats() -> list[ProjectStatsItem]:
    """Query the stats DB and return one record per project.

    Returns an empty list when the DB doesn't exist yet or has no rows.
    """
    from pathlib import Path

    db_path = Path(".hivemind") / "stats.db"
    if not db_path.exists():
        return []

    try:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    project_id,
                    COUNT(*)                                              AS total_runs,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)   AS failed_runs,
                    MAX(timestamp)                                         AS last_run_at,
                    COALESCE(AVG(duration_seconds), 0.0)                  AS avg_duration_s
                FROM task_executions
                GROUP BY project_id
                ORDER BY last_run_at DESC
                """,
            ) as cursor:
                rows = await cursor.fetchall()

        return [
            ProjectStatsItem(
                project_id=row["project_id"],
                total_runs=int(row["total_runs"] or 0),
                failed_runs=int(row["failed_runs"] or 0),
                last_run_at=row["last_run_at"],
                avg_duration_s=float(row["avg_duration_s"] or 0.0),
            )
            for row in rows
        ]
    except Exception as exc:
        logger.error("Failed to query project stats: %s", exc, exc_info=True)
        return []


@router.get(
    "/api/stats/projects",
    response_model=list[ProjectStatsItem],
    summary="List per-project execution statistics",
    response_description="Array of project stats records (empty when no runs recorded yet)",
)
async def get_all_project_stats():
    """Return aggregated execution statistics for every project that has run.

    Each item in the array contains:

    - **project_id** — unique project identifier
    - **total_runs** — total number of task executions recorded
    - **failed_runs** — number of tasks that ended in `failed` status
    - **last_run_at** — ISO 8601 UTC timestamp of the most recent execution
    - **avg_duration_s** — mean task duration in seconds across all runs

    Returns an empty array `[]` when no projects have run yet.
    """
    items = await _get_all_project_stats()
    return items
