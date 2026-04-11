"""tests/test_dag_api.py — pytest tests for DAG API endpoints.

Covers:
  GET /api/projects/{project_id}/dag          — current graph (nodes + edges)
  GET /api/projects/{project_id}/dag/history  — ordered execution snapshots

All tests are fully isolated: no live orchestrator or LangGraph instance is
required.  We mock _resolve_project_dir at the api.dag module level and
either provide a real SQLite DB (via tmp_path) or mock the DB path.

Naming convention: test_<what>_when_<condition>_should_<expected>
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with only the DAG router mounted."""
    from api.dag import router as dag_router

    app = FastAPI()
    app.include_router(dag_router)
    return app


def _make_db(tmp_path: Path, project_id: str, rows: list[dict]) -> Path:
    """Create a dag_checkpoints.db with the given rows under tmp_path/.hivemind/.

    Each row dict must have keys: round_num, status, checkpoint_json, created_at.
    """
    db_dir = tmp_path / ".hivemind"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "dag_checkpoints.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE dag_checkpoints (
            project_id TEXT NOT NULL,
            round_num  INTEGER NOT NULL,
            status     TEXT NOT NULL,
            checkpoint TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    for row in rows:
        conn.execute(
            "INSERT INTO dag_checkpoints (project_id, round_num, status, checkpoint, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                project_id,
                row["round_num"],
                row["status"],
                row["checkpoint_json"],
                row["created_at"],
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _make_graph_json(tasks: list[dict]) -> str:
    """Return a serialised task-graph JSON string compatible with _build_nodes_and_edges."""
    return json.dumps({"tasks": tasks})


def _make_checkpoint_json(tasks: list[dict], completed: dict[str, dict] | None = None) -> str:
    """Return a serialised checkpoint JSON string."""
    return json.dumps(
        {
            "graph_json": _make_graph_json(tasks),
            "completed_tasks": completed or {},
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TASKS_FIXTURE = [
    {"id": "task_001", "role": "backend_developer", "goal": "Set up DB", "depends_on": []},
    {
        "id": "task_002",
        "role": "frontend_developer",
        "goal": "Build UI",
        "depends_on": ["task_001"],
    },
    {
        "id": "task_003",
        "role": "test_engineer",
        "goal": "Write tests",
        "depends_on": ["task_001", "task_002"],
    },
]

COMPLETED_FIXTURE = {
    "task_001": {"status": "completed"},
    "task_002": {"status": "completed"},
}

PROJECT_ID = "my-test-project"


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/dag — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dag_when_project_exists_should_return_200(tmp_path):
    """GET /dag for a known project returns HTTP 200 with project_id, nodes, edges."""
    checkpoint_json = _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE)
    _make_db(
        tmp_path,
        PROJECT_ID,
        [
            {
                "round_num": 0,
                "status": "running",
                "checkpoint_json": checkpoint_json,
                "created_at": time.time(),
            }
        ],
    )
    project_dir = str(tmp_path)

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=project_dir)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag")

    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == PROJECT_ID
    assert "nodes" in body
    assert "edges" in body


@pytest.mark.asyncio
async def test_get_dag_when_project_exists_should_return_valid_node_fields(tmp_path):
    """Each node must contain id, role, goal, status, and depends_on fields."""
    checkpoint_json = _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE)
    _make_db(
        tmp_path,
        PROJECT_ID,
        [
            {
                "round_num": 0,
                "status": "running",
                "checkpoint_json": checkpoint_json,
                "created_at": time.time(),
            }
        ],
    )

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag")

    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    assert len(nodes) == len(TASKS_FIXTURE), "Should have one node per task"

    required_fields = {"id", "role", "goal", "status", "depends_on"}
    for node in nodes:
        missing = required_fields - set(node.keys())
        assert not missing, f"Node {node.get('id')} is missing fields: {missing}"
        # depends_on must be a list
        assert isinstance(node["depends_on"], list), "depends_on must be a list"
        # status must be a non-empty string
        assert isinstance(node["status"], str) and node["status"], (
            "status must be a non-empty string"
        )


@pytest.mark.asyncio
async def test_get_dag_when_project_exists_should_reflect_completed_status(tmp_path):
    """Completed tasks should have status='completed'; pending ones 'pending'."""
    checkpoint_json = _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE)
    _make_db(
        tmp_path,
        PROJECT_ID,
        [
            {
                "round_num": 0,
                "status": "running",
                "checkpoint_json": checkpoint_json,
                "created_at": time.time(),
            }
        ],
    )

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag")

    nodes_by_id = {n["id"]: n for n in resp.json()["nodes"]}
    assert nodes_by_id["task_001"]["status"] == "completed"
    assert nodes_by_id["task_002"]["status"] == "completed"
    assert nodes_by_id["task_003"]["status"] == "pending"  # not in COMPLETED_FIXTURE


@pytest.mark.asyncio
async def test_get_dag_edges_should_reference_valid_node_ids(tmp_path):
    """All edge source and target values must correspond to existing node IDs."""
    checkpoint_json = _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE)
    _make_db(
        tmp_path,
        PROJECT_ID,
        [
            {
                "round_num": 0,
                "status": "running",
                "checkpoint_json": checkpoint_json,
                "created_at": time.time(),
            }
        ],
    )

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag")

    body = resp.json()
    node_ids = {n["id"] for n in body["nodes"]}
    for edge in body["edges"]:
        assert edge["source"] in node_ids, f"Edge source '{edge['source']}' not in nodes"
        assert edge["target"] in node_ids, f"Edge target '{edge['target']}' not in nodes"


@pytest.mark.asyncio
async def test_get_dag_edges_should_encode_depends_on_relationships(tmp_path):
    """Edges should encode the depends_on relationships from the task graph."""
    checkpoint_json = _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE)
    _make_db(
        tmp_path,
        PROJECT_ID,
        [
            {
                "round_num": 0,
                "status": "running",
                "checkpoint_json": checkpoint_json,
                "created_at": time.time(),
            }
        ],
    )

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag")

    edges = resp.json()["edges"]
    edge_pairs = {(e["source"], e["target"]) for e in edges}

    # task_002 depends on task_001 → edge task_001 → task_002
    assert ("task_001", "task_002") in edge_pairs
    # task_003 depends on task_001 and task_002
    assert ("task_001", "task_003") in edge_pairs
    assert ("task_002", "task_003") in edge_pairs


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/dag — 404 cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dag_when_unknown_project_should_return_404():
    """GET /dag for an unknown project_id returns HTTP 404."""
    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/projects/nonexistent-project/dag")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_dag_when_no_db_file_should_return_404(tmp_path):
    """GET /dag when project dir exists but has no DB file returns HTTP 404."""
    app = _make_app()
    # tmp_path exists but contains no dag_checkpoints.db
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_dag_when_db_has_no_rows_should_return_404(tmp_path):
    """GET /dag when DB exists but has no rows for the project returns HTTP 404."""
    # Create an empty DB (no rows for our project_id)
    _make_db(tmp_path, PROJECT_ID, [])  # no rows

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/dag/history — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dag_history_when_project_exists_should_return_200(tmp_path):
    """GET /dag/history for a known project returns HTTP 200 with snapshots list."""
    now = time.time()
    rows = [
        {
            "round_num": 0,
            "status": "running",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, {}),
            "created_at": now - 100.0,
        },
        {
            "round_num": 1,
            "status": "running",
            "checkpoint_json": _make_checkpoint_json(
                TASKS_FIXTURE, {"task_001": {"status": "completed"}}
            ),
            "created_at": now - 50.0,
        },
        {
            "round_num": 2,
            "status": "completed",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE),
            "created_at": now,
        },
    ]
    _make_db(tmp_path, PROJECT_ID, rows)

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == PROJECT_ID
    assert "snapshots" in body
    assert isinstance(body["snapshots"], list)


@pytest.mark.asyncio
async def test_get_dag_history_snapshots_should_have_required_fields(tmp_path):
    """Each snapshot must contain timestamp, event_type, round_num, nodes, and edges."""
    now = time.time()
    rows = [
        {
            "round_num": 0,
            "status": "running",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, {}),
            "created_at": now - 60.0,
        },
        {
            "round_num": 1,
            "status": "completed",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE),
            "created_at": now,
        },
    ]
    _make_db(tmp_path, PROJECT_ID, rows)

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 2

    required_fields = {"timestamp", "event_type", "round_num", "nodes", "edges"}
    for snapshot in snapshots:
        missing = required_fields - set(snapshot.keys())
        assert not missing, f"Snapshot is missing fields: {missing}"
        assert isinstance(snapshot["timestamp"], int | float)
        assert isinstance(snapshot["event_type"], str) and snapshot["event_type"]
        assert isinstance(snapshot["round_num"], int)
        assert isinstance(snapshot["nodes"], list)
        assert isinstance(snapshot["edges"], list)


@pytest.mark.asyncio
async def test_get_dag_history_snapshots_should_be_ordered_by_round_num(tmp_path):
    """Snapshots must be returned in ascending round_num order (oldest first)."""
    now = time.time()
    # Insert rows in non-sequential order to ensure sorting is applied
    rows = [
        {
            "round_num": 2,
            "status": "completed",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE),
            "created_at": now,
        },
        {
            "round_num": 0,
            "status": "running",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, {}),
            "created_at": now - 200.0,
        },
        {
            "round_num": 1,
            "status": "running",
            "checkpoint_json": _make_checkpoint_json(
                TASKS_FIXTURE, {"task_001": {"status": "completed"}}
            ),
            "created_at": now - 100.0,
        },
    ]
    _make_db(tmp_path, PROJECT_ID, rows)

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 3
    round_nums = [s["round_num"] for s in snapshots]
    assert round_nums == sorted(round_nums), "Snapshots must be in ascending round_num order"


@pytest.mark.asyncio
async def test_get_dag_history_timestamps_should_be_present_and_numeric(tmp_path):
    """Every snapshot timestamp must be a numeric unix epoch value."""
    now = time.time()
    rows = [
        {
            "round_num": 0,
            "status": "running",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, {}),
            "created_at": now - 50.0,
        },
        {
            "round_num": 1,
            "status": "completed",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE),
            "created_at": now,
        },
    ]
    _make_db(tmp_path, PROJECT_ID, rows)

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    snapshots = resp.json()["snapshots"]
    for snapshot in snapshots:
        ts = snapshot["timestamp"]
        assert isinstance(ts, int | float), f"timestamp must be numeric, got {type(ts)}"
        assert ts > 0, "timestamp must be a positive unix epoch"


@pytest.mark.asyncio
async def test_get_dag_history_first_snapshot_event_type_should_be_dag_started(tmp_path):
    """Round 0 with no previous completed tasks should produce event_type='dag_started'."""
    now = time.time()
    rows = [
        {
            "round_num": 0,
            "status": "running",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, {}),
            "created_at": now,
        },
    ]
    _make_db(tmp_path, PROJECT_ID, rows)

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["event_type"] == "dag_started"


@pytest.mark.asyncio
async def test_get_dag_history_final_snapshot_event_type_should_be_dag_completed(tmp_path):
    """A checkpoint with status='completed' should produce event_type='dag_completed'."""
    now = time.time()
    rows = [
        {
            "round_num": 0,
            "status": "running",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, {}),
            "created_at": now - 10.0,
        },
        {
            "round_num": 1,
            "status": "completed",  # <-- this triggers dag_completed
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, COMPLETED_FIXTURE),
            "created_at": now,
        },
    ]
    _make_db(tmp_path, PROJECT_ID, rows)

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == 2
    last = snapshots[-1]
    assert last["event_type"] == "dag_completed"


@pytest.mark.asyncio
async def test_get_dag_history_snapshot_count_should_match_checkpoint_rows(tmp_path):
    """Number of snapshots returned should equal the number of valid checkpoint rows."""
    now = time.time()
    num_rows = 5
    rows = [
        {
            "round_num": i,
            "status": "running" if i < num_rows - 1 else "completed",
            "checkpoint_json": _make_checkpoint_json(TASKS_FIXTURE, {}),
            "created_at": now + i,
        }
        for i in range(num_rows)
    ]
    _make_db(tmp_path, PROJECT_ID, rows)

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    assert resp.status_code == 200
    snapshots = resp.json()["snapshots"]
    assert len(snapshots) == num_rows


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/dag/history — 404 cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dag_history_when_unknown_project_should_return_404():
    """GET /dag/history for an unknown project_id returns HTTP 404."""
    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/projects/nonexistent-project/dag/history")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_dag_history_when_no_db_file_should_return_404(tmp_path):
    """GET /dag/history when project dir exists but has no DB file returns HTTP 404."""
    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_dag_history_when_db_has_no_rows_should_return_404(tmp_path):
    """GET /dag/history when DB exists but has no rows for the project returns HTTP 404."""
    _make_db(tmp_path, PROJECT_ID, [])  # empty DB

    app = _make_app()
    with patch("api.dag._resolve_project_dir", new=AsyncMock(return_value=str(tmp_path))):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/projects/{PROJECT_ID}/dag/history")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Unit tests for internal helpers (pure, no HTTP layer)
# ---------------------------------------------------------------------------


def test_build_nodes_and_edges_should_produce_correct_node_count():
    """_build_nodes_and_edges should return one node per task."""
    from api.dag import _build_nodes_and_edges

    graph_json = _make_graph_json(TASKS_FIXTURE)
    nodes, edges = _build_nodes_and_edges(graph_json, {})
    assert len(nodes) == len(TASKS_FIXTURE)


def test_build_nodes_and_edges_should_produce_correct_edge_count():
    """_build_nodes_and_edges should produce one edge per depends_on entry."""
    from api.dag import _build_nodes_and_edges

    # task_002 has 1 dep, task_003 has 2 deps → 3 edges total
    graph_json = _make_graph_json(TASKS_FIXTURE)
    _, edges = _build_nodes_and_edges(graph_json, {})
    assert len(edges) == 3


def test_build_nodes_and_edges_when_task_in_completed_should_use_provided_status():
    """Tasks in completed_tasks map should get their status from that map."""
    from api.dag import _build_nodes_and_edges

    graph_json = _make_graph_json(TASKS_FIXTURE)
    completed = {"task_001": {"status": "failed"}}
    nodes, _ = _build_nodes_and_edges(graph_json, completed)

    task001 = next(n for n in nodes if n.id == "task_001")
    assert task001.status == "failed"


def test_build_nodes_and_edges_when_task_not_in_completed_should_be_pending():
    """Tasks not in completed_tasks map should default to 'pending'."""
    from api.dag import _build_nodes_and_edges

    graph_json = _make_graph_json(TASKS_FIXTURE)
    nodes, _ = _build_nodes_and_edges(graph_json, {})

    for node in nodes:
        assert node.status == "pending"


def test_build_nodes_and_edges_when_invalid_json_should_return_empty():
    """_build_nodes_and_edges should return ([], []) for invalid graph_json."""
    from api.dag import _build_nodes_and_edges

    nodes, edges = _build_nodes_and_edges("not valid json {{{{", {})
    assert nodes == []
    assert edges == []


def test_infer_event_type_round_zero_no_prev_should_be_dag_started():
    """Round 0 with empty prev_completed → 'dag_started'."""
    from api.dag import _infer_event_type

    event = _infer_event_type(
        round_num=0,
        current_completed={},
        prev_completed={},
        checkpoint_status="running",
    )
    assert event == "dag_started"


def test_infer_event_type_completed_status_should_be_dag_completed():
    """checkpoint_status='completed' → 'dag_completed'."""
    from api.dag import _infer_event_type

    event = _infer_event_type(
        round_num=1,
        current_completed=COMPLETED_FIXTURE,
        prev_completed={},
        checkpoint_status="completed",
    )
    assert event == "dag_completed"


def test_infer_event_type_failed_status_should_be_dag_failed():
    """checkpoint_status='failed' → 'dag_failed'."""
    from api.dag import _infer_event_type

    event = _infer_event_type(
        round_num=2,
        current_completed=COMPLETED_FIXTURE,
        prev_completed={},
        checkpoint_status="failed",
    )
    assert event == "dag_failed"


def test_infer_event_type_new_completed_task_should_be_task_completed():
    """New task with status='completed' relative to previous round → 'task_completed'."""
    from api.dag import _infer_event_type

    prev = {}
    current = {"task_001": {"status": "completed"}}
    event = _infer_event_type(
        round_num=1,
        current_completed=current,
        prev_completed=prev,
        checkpoint_status="running",
    )
    assert event == "task_completed"


def test_infer_event_type_new_failed_task_should_be_task_failed():
    """New task with status='failed' relative to previous round → 'task_failed'."""
    from api.dag import _infer_event_type

    prev = {}
    current = {"task_001": {"status": "failed"}}
    event = _infer_event_type(
        round_num=1,
        current_completed=current,
        prev_completed=prev,
        checkpoint_status="running",
    )
    assert event == "task_failed"
