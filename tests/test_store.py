import asyncio
import os
import pytest
from pathlib import Path
from session_manager import SessionManager


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_sessions.db")


@pytest.fixture
def session_mgr(db_path):
    mgr = SessionManager(db_path=db_path)
    asyncio.get_event_loop().run_until_complete(mgr.initialize())
    yield mgr
    asyncio.get_event_loop().run_until_complete(mgr.close())


def run(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_save_and_load_project(session_mgr):
    run(session_mgr.save_project("test_proj", user_id=123, name="Test", description="A test", project_dir="/tmp/test"))
    data = run(session_mgr.load_project("test_proj"))
    assert data is not None
    assert data["name"] == "Test"
    assert data["status"] == "active"
    assert data["project_dir"] == "/tmp/test"


def test_add_and_get_messages(session_mgr):
    run(session_mgr.add_message("proj1", "orchestrator", "Orchestrator", "Hello", 0.01))
    run(session_mgr.add_message("proj1", "developer", "Developer", "Working on it", 0.02))
    messages = run(session_mgr.get_recent_messages("proj1", count=10))
    assert len(messages) == 2
    assert messages[0]["agent_name"] == "orchestrator"
    assert messages[0]["content"] == "Hello"
    assert messages[0]["cost_usd"] == 0.01
    assert messages[1]["agent_name"] == "developer"


def test_list_projects(session_mgr):
    run(session_mgr.save_project("proj1", user_id=1, name="Project 1", description="", project_dir="/tmp/p1"))
    run(session_mgr.save_project("proj2", user_id=1, name="Project 2", description="", project_dir="/tmp/p2"))
    projects = run(session_mgr.list_projects())
    assert len(projects) == 2
    names = [p["name"] for p in projects]
    assert "Project 1" in names and "Project 2" in names


def test_session_crud(session_mgr):
    # Save session
    run(session_mgr.save_session(user_id=1, project_id="proj1", agent_role="orchestrator", session_id="sess-123", cost=0.05, turns=3))

    # Get session
    sid = run(session_mgr.get_session(user_id=1, project_id="proj1", agent_role="orchestrator"))
    assert sid == "sess-123"

    # Invalidate
    run(session_mgr.invalidate_session(user_id=1, project_id="proj1", agent_role="orchestrator"))
    sid = run(session_mgr.get_session(user_id=1, project_id="proj1", agent_role="orchestrator"))
    assert sid is None


def test_update_status(session_mgr):
    run(session_mgr.save_project("proj1", user_id=1, name="P1", description="", project_dir="/tmp"))
    run(session_mgr.update_status("proj1", "paused"))
    data = run(session_mgr.load_project("proj1"))
    assert data["status"] == "paused"
