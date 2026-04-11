"""
Per-project execution statistics tracked in SQLite at .hivemind/stats.db.

Schema: task_executions table with per-task metrics.
Provides async functions to record completions and query aggregated stats.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

_HIVEMIND_DIR = Path(".hivemind")
_DB_PATH = _HIVEMIND_DIR / "stats.db"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS task_executions (
    task_id          TEXT    PRIMARY KEY,
    project_id       TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    goal             TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    duration_seconds REAL    NOT NULL DEFAULT 0.0,
    cost_usd         REAL    NOT NULL DEFAULT 0.0,
    files_changed    INTEGER NOT NULL DEFAULT 0,
    error_message    TEXT    NULL,
    timestamp        TEXT    NOT NULL
);
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_te_project_id ON task_executions(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_te_role       ON task_executions(role);",
    "CREATE INDEX IF NOT EXISTS idx_te_status     ON task_executions(status);",
    "CREATE INDEX IF NOT EXISTS idx_te_timestamp  ON task_executions(timestamp);",
]


def _ensure_hivemind_dir() -> Path:
    _HIVEMIND_DIR.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


async def _init_db(db: aiosqlite.Connection) -> None:
    """Create the table and indexes if they don't exist yet."""
    await db.execute(_CREATE_TABLE_SQL)
    for idx_sql in _CREATE_INDEXES_SQL:
        await db.execute(idx_sql)
    await db.commit()


async def init_stats_db() -> None:
    """Ensure the stats database and schema exist.

    Call once at application startup so the first real write never has to
    create the table from scratch inside an active request.
    """
    db_path = _ensure_hivemind_dir()
    async with aiosqlite.connect(str(db_path)) as db:
        await _init_db(db)


async def record_task_completion(
    task_id: str,
    project_id: str,
    role: str,
    goal: str,
    status: str,
    duration_seconds: float,
    cost_usd: float,
    files_changed: int,
    error_message: str | None = None,
    *,
    timestamp: str | None = None,
) -> None:
    """Insert a task execution record into stats.db.

    Args:
        task_id:          Unique task identifier (primary key).
        project_id:       Project this task belongs to.
        role:             Agent role (e.g. "backend_developer").
        goal:             Human-readable task description.
        status:           "completed", "failed", "cancelled", etc.
        duration_seconds: Wall-clock seconds the task took.
        cost_usd:         Estimated API cost in USD.
        files_changed:    Number of files touched by the agent.
        error_message:    Error detail if the task failed (None otherwise).
        timestamp:        ISO 8601 UTC timestamp; defaults to now().
    """
    import datetime

    if timestamp is None:
        timestamp = datetime.datetime.now(datetime.UTC).isoformat()

    db_path = _ensure_hivemind_dir()
    async with aiosqlite.connect(str(db_path)) as db:
        await _init_db(db)
        await db.execute(
            """
            INSERT INTO task_executions (
                task_id, project_id, role, goal, status,
                duration_seconds, cost_usd, files_changed,
                error_message, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                project_id       = excluded.project_id,
                role             = excluded.role,
                goal             = excluded.goal,
                status           = excluded.status,
                duration_seconds = excluded.duration_seconds,
                cost_usd         = excluded.cost_usd,
                files_changed    = excluded.files_changed,
                error_message    = excluded.error_message,
                timestamp        = excluded.timestamp
            """,
            (
                task_id,
                project_id,
                role,
                goal,
                status,
                duration_seconds,
                cost_usd,
                files_changed,
                error_message,
                timestamp,
            ),
        )
        await db.commit()


async def get_project_stats(project_id: str) -> dict:
    """Return aggregated execution statistics for a single project.

    Returns a dict with:
        total_tasks      (int)
        completed_tasks  (int)
        failed_tasks     (int)
        total_duration_s (float)   — sum of all task durations
        avg_duration_s   (float)   — average task duration
        total_cost_usd   (float)
        total_files_changed (int)
        success_rate     (float)   — fraction 0.0–1.0
        roles            (list[str]) — distinct roles used
    """
    db_path = _ensure_hivemind_dir()
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await _init_db(db)

        async with db.execute(
            """
            SELECT
                COUNT(*)                                         AS total_tasks,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
                SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS failed_tasks,
                COALESCE(SUM(duration_seconds), 0.0)             AS total_duration_s,
                COALESCE(AVG(duration_seconds), 0.0)             AS avg_duration_s,
                COALESCE(SUM(cost_usd), 0.0)                     AS total_cost_usd,
                COALESCE(SUM(files_changed), 0)                  AS total_files_changed
            FROM task_executions
            WHERE project_id = ?
            """,
            (project_id,),
        ) as cursor:
            row = await cursor.fetchone()

        total = int(row["total_tasks"] or 0)
        completed = int(row["completed_tasks"] or 0)
        failed = int(row["failed_tasks"] or 0)
        success_rate = (completed / total) if total > 0 else 0.0

        async with db.execute(
            "SELECT DISTINCT role FROM task_executions WHERE project_id = ? ORDER BY role",
            (project_id,),
        ) as cursor:
            roles = [r["role"] async for r in cursor]

    return {
        "project_id": project_id,
        "total_tasks": total,
        "completed_tasks": completed,
        "failed_tasks": failed,
        "total_duration_s": float(row["total_duration_s"] or 0.0),
        "avg_duration_s": float(row["avg_duration_s"] or 0.0),
        "total_cost_usd": float(row["total_cost_usd"] or 0.0),
        "total_files_changed": int(row["total_files_changed"] or 0),
        "success_rate": success_rate,
        "roles": roles,
    }


async def get_agent_performance() -> dict[str, dict]:
    """Return per-role performance aggregated across all projects.

    Returns a dict keyed by role name, each value containing:
        avg_duration_s  (float)
        success_rate    (float)   — fraction 0.0–1.0
        total_cost_usd  (float)
        total_tasks     (int)
        completed_tasks (int)
        failed_tasks    (int)
    """
    db_path = _ensure_hivemind_dir()
    async with aiosqlite.connect(str(db_path)) as db:
        db.row_factory = aiosqlite.Row
        await _init_db(db)

        async with db.execute(
            """
            SELECT
                role,
                COUNT(*)                                              AS total_tasks,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
                SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) AS failed_tasks,
                COALESCE(AVG(duration_seconds), 0.0)                  AS avg_duration_s,
                COALESCE(SUM(cost_usd), 0.0)                          AS total_cost_usd
            FROM task_executions
            GROUP BY role
            ORDER BY role
            """,
        ) as cursor:
            rows = await cursor.fetchall()

    result: dict[str, dict] = {}
    for row in rows:
        total = int(row["total_tasks"] or 0)
        completed = int(row["completed_tasks"] or 0)
        failed = int(row["failed_tasks"] or 0)
        success_rate = (completed / total) if total > 0 else 0.0

        result[row["role"]] = {
            "total_tasks": total,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "avg_duration_s": float(row["avg_duration_s"] or 0.0),
            "total_cost_usd": float(row["total_cost_usd"] or 0.0),
            "success_rate": success_rate,
        }

    return result
