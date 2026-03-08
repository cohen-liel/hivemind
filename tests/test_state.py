"""Tests for the state.py module — global state management."""
import asyncio
import re

import pytest

import state


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level globals before each test to avoid cross-contamination."""
    state.active_sessions.clear()
    state.current_project.clear()
    state.user_last_message.clear()
    yield
    # Cleanup after test too
    state.active_sessions.clear()
    state.current_project.clear()
    state.user_last_message.clear()


# ── Module-level defaults ──────────────────────────────────────────


def test_active_sessions_starts_empty():
    """active_sessions should be an empty dict by default (after reset)."""
    assert state.active_sessions == {}
    assert isinstance(state.active_sessions, dict)


def test_current_project_starts_empty():
    """current_project should be an empty dict by default."""
    assert state.current_project == {}
    assert isinstance(state.current_project, dict)


def test_user_last_message_starts_empty():
    """user_last_message should be an empty dict by default."""
    assert state.user_last_message == {}
    assert isinstance(state.user_last_message, dict)


def test_project_name_regex_exists():
    """PROJECT_NAME_RE should be a compiled regex pattern."""
    assert isinstance(state.PROJECT_NAME_RE, re.Pattern)


def test_project_name_regex_valid_names():
    """PROJECT_NAME_RE should accept valid project names."""
    valid = ["my-project", "project_1", "Hello World", "test123", "A"]
    for name in valid:
        assert state.PROJECT_NAME_RE.match(name), f"Should match: {name!r}"


def test_project_name_regex_invalid_names():
    """PROJECT_NAME_RE should reject names with special characters."""
    invalid = ["project@home", "test/path", "bad!name", "no.dots", ""]
    for name in invalid:
        assert not state.PROJECT_NAME_RE.match(name), f"Should NOT match: {name!r}"


# ── get_manager() ──────────────────────────────────────────────────


def test_get_manager_returns_none_when_empty():
    """get_manager should return (None, None) when no sessions exist."""
    manager, user_id = state.get_manager("nonexistent")
    assert manager is None
    assert user_id is None


def test_get_manager_finds_registered_manager():
    """get_manager should find a manager after it's been added to active_sessions."""
    fake_manager = object()  # Any object works — we just need a reference
    state.active_sessions[42] = {"my-project": fake_manager}

    found, user_id = state.get_manager("my-project")
    assert found is fake_manager
    assert user_id == 42


def test_get_manager_returns_none_for_wrong_project():
    """get_manager should return None for a project_id that doesn't exist."""
    fake_manager = object()
    state.active_sessions[1] = {"project-a": fake_manager}

    found, user_id = state.get_manager("project-b")
    assert found is None
    assert user_id is None


def test_get_manager_searches_all_users():
    """get_manager should search across all users, not just the first one."""
    mgr_a = object()
    mgr_b = object()
    state.active_sessions[1] = {"project-a": mgr_a}
    state.active_sessions[2] = {"project-b": mgr_b}

    found, uid = state.get_manager("project-b")
    assert found is mgr_b
    assert uid == 2


# ── get_all_managers() ─────────────────────────────────────────────


def test_get_all_managers_empty():
    """get_all_managers should return an empty list when no sessions exist."""
    result = state.get_all_managers()
    assert result == []


def test_get_all_managers_returns_all():
    """get_all_managers should return all (user_id, project_id, manager) tuples."""
    mgr1 = object()
    mgr2 = object()
    mgr3 = object()
    state.active_sessions[1] = {"proj-a": mgr1, "proj-b": mgr2}
    state.active_sessions[2] = {"proj-c": mgr3}

    result = state.get_all_managers()
    assert len(result) == 3

    # Check all managers are present (order may vary by dict iteration)
    managers_found = {m for _, _, m in result}
    assert managers_found == {mgr1, mgr2, mgr3}

    # Check user_ids and project_ids are correct
    tuples = {(uid, pid) for uid, pid, _ in result}
    assert (1, "proj-a") in tuples
    assert (1, "proj-b") in tuples
    assert (2, "proj-c") in tuples


# ── register_manager() ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_manager_adds_to_active_sessions():
    """register_manager should add the manager under user_id + project_id."""
    fake_manager = object()
    await state.register_manager(10, "new-project", fake_manager)

    assert 10 in state.active_sessions
    assert "new-project" in state.active_sessions[10]
    assert state.active_sessions[10]["new-project"] is fake_manager


@pytest.mark.asyncio
async def test_register_manager_multiple_projects():
    """register_manager should allow multiple projects per user."""
    mgr1 = object()
    mgr2 = object()
    await state.register_manager(5, "proj-x", mgr1)
    await state.register_manager(5, "proj-y", mgr2)

    assert len(state.active_sessions[5]) == 2
    assert state.active_sessions[5]["proj-x"] is mgr1
    assert state.active_sessions[5]["proj-y"] is mgr2


# ── unregister_manager() ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_unregister_manager_removes_project():
    """unregister_manager should remove the project from active_sessions."""
    fake_manager = object()
    state.active_sessions[7] = {"to-remove": fake_manager, "to-keep": object()}

    await state.unregister_manager(7, "to-remove")

    assert "to-remove" not in state.active_sessions[7]
    assert "to-keep" in state.active_sessions[7]


@pytest.mark.asyncio
async def test_unregister_manager_removes_user_when_empty():
    """unregister_manager should remove the user entry when they have no more projects."""
    fake_manager = object()
    state.active_sessions[99] = {"only-project": fake_manager}

    await state.unregister_manager(99, "only-project")

    assert 99 not in state.active_sessions


@pytest.mark.asyncio
async def test_unregister_manager_nonexistent_is_safe():
    """unregister_manager should not raise when user_id doesn't exist."""
    # Should not raise
    await state.unregister_manager(999, "ghost-project")


@pytest.mark.asyncio
async def test_register_then_get_manager():
    """End-to-end: register a manager, then find it with get_manager."""
    mgr = object()
    await state.register_manager(1, "e2e-project", mgr)

    found, uid = state.get_manager("e2e-project")
    assert found is mgr
    assert uid == 1


@pytest.mark.asyncio
async def test_unregister_then_get_manager_returns_none():
    """After unregistering, get_manager should return None."""
    mgr = object()
    await state.register_manager(1, "temp-project", mgr)
    await state.unregister_manager(1, "temp-project")

    found, uid = state.get_manager("temp-project")
    assert found is None
    assert uid is None
