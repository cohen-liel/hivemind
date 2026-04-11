"""DAG state and execution history API endpoints.

Provides REST endpoints for the React Flow frontend to render the current
task graph and replay execution snapshots.

Routes
------
GET /api/projects/{project_id}/dag            — current graph (nodes + edges)
GET /api/projects/{project_id}/dag/history    — ordered execution snapshots
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dag"])

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DAGNode(BaseModel):
    """Single task node in the DAG visualization."""

    id: str = Field(..., description="Unique task ID")
    role: str = Field(..., description="Agent role assigned to this task")
    goal: str = Field(..., description="Task objective")
    status: str = Field(
        ...,
        description="Current status: pending | running | completed | failed | retrying | blocked | remediation",
    )
    depends_on: list[str] = Field(default_factory=list, description="IDs of dependency tasks")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "task_001",
                "role": "backend_developer",
                "goal": "Implement JWT authentication",
                "status": "completed",
                "depends_on": [],
            }
        }


class DAGEdge(BaseModel):
    """Directed dependency edge between two task nodes."""

    source: str = Field(..., description="ID of the upstream (dependency) task")
    target: str = Field(..., description="ID of the downstream (dependent) task")


class DAGResponse(BaseModel):
    """Current graph state — nodes and edges arrays."""

    project_id: str
    nodes: list[DAGNode]
    edges: list[DAGEdge]

    class Config:
        json_schema_extra = {
            "example": {
                "project_id": "my-project",
                "nodes": [
                    {
                        "id": "task_001",
                        "role": "backend_developer",
                        "goal": "Implement JWT authentication",
                        "status": "completed",
                        "depends_on": [],
                    }
                ],
                "edges": [],
            }
        }


class DAGSnapshot(BaseModel):
    """A single point-in-time snapshot of the DAG during execution."""

    timestamp: float = Field(..., description="Unix epoch timestamp of this snapshot")
    event_type: str = Field(
        ...,
        description="Triggering event: dag_started | task_started | task_completed | task_failed | dag_completed | dag_failed",
    )
    round_num: int = Field(..., description="DAG execution round number")
    nodes: list[DAGNode]
    edges: list[DAGEdge]


class DAGHistoryResponse(BaseModel):
    """Ordered list of execution snapshots for replay."""

    project_id: str
    snapshots: list[DAGSnapshot]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_checkpoint_db_path(project_dir: str) -> Path:
    """Return the path to the dag_checkpoints SQLite database."""
    return Path(project_dir) / ".hivemind" / "dag_checkpoints.db"


def _load_all_checkpoints_sync(db_path: Path, project_id: str) -> list[dict[str, Any]]:
    """Load all DAG checkpoints for a project ordered by round_num ASC (sync, run in executor)."""
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            rows = conn.execute(
                "SELECT round_num, status, checkpoint, created_at FROM dag_checkpoints "
                "WHERE project_id = ? ORDER BY round_num ASC",
                (project_id,),
            ).fetchall()
        return [
            {
                "round_num": row[0],
                "status": row[1],
                "checkpoint_json": row[2],
                "created_at": row[3],
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("Failed to load checkpoints for %s: %s", project_id, exc)
        return []


def _build_nodes_and_edges(
    graph_json: str,
    completed_tasks: dict[str, dict],
) -> tuple[list[DAGNode], list[DAGEdge]]:
    """Build nodes and edges from a TaskGraph JSON + completed task map."""
    try:
        graph_data = json.loads(graph_json)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Failed to parse graph_json: %s", exc)
        return [], []

    tasks = graph_data.get("tasks", [])
    nodes: list[DAGNode] = []
    edges: list[DAGEdge] = []

    for task in tasks:
        task_id = task.get("id", "")
        depends_on: list[str] = task.get("depends_on", [])

        # Determine status from completed_tasks
        if task_id in completed_tasks:
            raw_status = completed_tasks[task_id].get("status", "completed")
        else:
            raw_status = "pending"

        nodes.append(
            DAGNode(
                id=task_id,
                role=task.get("role", "unknown"),
                goal=task.get("goal", ""),
                status=raw_status,
                depends_on=depends_on,
            )
        )

        # Create one edge per dependency
        for dep_id in depends_on:
            edges.append(DAGEdge(source=dep_id, target=task_id))

    return nodes, edges


def _infer_event_type(
    round_num: int,
    current_completed: dict[str, dict],
    prev_completed: dict[str, dict],
    checkpoint_status: str,
) -> str:
    """Infer the triggering event type for a checkpoint snapshot."""
    if round_num == 0 and not prev_completed:
        return "dag_started"

    if checkpoint_status == "completed":
        return "dag_completed"

    if checkpoint_status == "failed":
        return "dag_failed"

    # Find newly completed tasks since previous round
    new_task_ids = set(current_completed.keys()) - set(prev_completed.keys())
    for task_id in new_task_ids:
        task_status = current_completed[task_id].get("status", "")
        if task_status == "failed":
            return "task_failed"
        if task_status == "completed":
            return "task_completed"

    # Fallback: something changed
    return "task_completed"


async def _resolve_project_dir(project_id: str) -> str | None:
    """Resolve project directory from active manager or DB (async)."""
    try:
        from dashboard.routers import _resolve_project_dir as _base_resolve

        return await _base_resolve(project_id)
    except Exception as exc:
        logger.warning("_resolve_project_dir failed for %s: %s", project_id, exc)
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/projects/{project_id}/dag",
    response_model=DAGResponse,
    summary="Get current DAG state",
    responses={404: {"description": "Project not found or no DAG data available"}},
)
async def get_dag(project_id: str) -> DAGResponse:
    """Return the current task graph as nodes and edges arrays.

    Reads from the latest DAG checkpoint stored in
    ``{project_dir}/.hivemind/dag_checkpoints.db``.
    """
    project_dir = await _resolve_project_dir(project_id)
    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")

    db_path = _get_checkpoint_db_path(project_dir)
    if not db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No DAG data found for project '{project_id}'.",
        )

    # Load the latest checkpoint in a thread (SQLite is sync)
    loop = asyncio.get_running_loop()
    raw_rows = await loop.run_in_executor(None, _load_all_checkpoints_sync, db_path, project_id)

    if not raw_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No DAG checkpoints found for project '{project_id}'.",
        )

    # Latest checkpoint = highest round_num
    latest_row = raw_rows[-1]
    try:
        checkpoint_data = json.loads(latest_row["checkpoint_json"])
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("Failed to parse latest checkpoint for %s: %s", project_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to parse DAG checkpoint data.")

    graph_json = checkpoint_data.get("graph_json", "{}")
    completed_tasks: dict[str, dict] = checkpoint_data.get("completed_tasks", {})

    nodes, edges = _build_nodes_and_edges(graph_json, completed_tasks)

    logger.info(
        "GET /api/projects/%s/dag — %d nodes, %d edges (round=%d)",
        project_id,
        len(nodes),
        len(edges),
        latest_row["round_num"],
    )
    return DAGResponse(project_id=project_id, nodes=nodes, edges=edges)


@router.get(
    "/api/projects/{project_id}/dag/history",
    response_model=DAGHistoryResponse,
    summary="Get DAG execution history for replay",
    responses={404: {"description": "Project not found or no DAG history available"}},
)
async def get_dag_history(project_id: str) -> DAGHistoryResponse:
    """Return an ordered list of execution snapshots for replay.

    Each snapshot contains the full graph state (nodes + edges) at the time
    of a DAG execution round, derived from SQLite checkpoint data.
    """
    project_dir = await _resolve_project_dir(project_id)
    if not project_dir:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")

    db_path = _get_checkpoint_db_path(project_dir)
    if not db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No DAG history found for project '{project_id}'.",
        )

    loop = asyncio.get_running_loop()
    raw_rows = await loop.run_in_executor(None, _load_all_checkpoints_sync, db_path, project_id)

    if not raw_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No DAG checkpoints found for project '{project_id}'.",
        )

    snapshots: list[DAGSnapshot] = []
    prev_completed: dict[str, dict] = {}

    for row in raw_rows:
        try:
            checkpoint_data = json.loads(row["checkpoint_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Skipping malformed checkpoint round=%d for %s: %s",
                row["round_num"],
                project_id,
                exc,
            )
            continue

        graph_json = checkpoint_data.get("graph_json", "{}")
        completed_tasks: dict[str, dict] = checkpoint_data.get("completed_tasks", {})
        round_num: int = row["round_num"]
        checkpoint_status: str = row["status"]
        timestamp: float = row["created_at"]

        event_type = _infer_event_type(
            round_num=round_num,
            current_completed=completed_tasks,
            prev_completed=prev_completed,
            checkpoint_status=checkpoint_status,
        )

        nodes, edges = _build_nodes_and_edges(graph_json, completed_tasks)

        snapshots.append(
            DAGSnapshot(
                timestamp=timestamp,
                event_type=event_type,
                round_num=round_num,
                nodes=nodes,
                edges=edges,
            )
        )

        prev_completed = completed_tasks

    logger.info(
        "GET /api/projects/%s/dag/history — %d snapshots",
        project_id,
        len(snapshots),
    )
    return DAGHistoryResponse(project_id=project_id, snapshots=snapshots)
