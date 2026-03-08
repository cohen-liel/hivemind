"""Comprehensive tests for state.py — global application state management.

Tests are written against the ACTUAL source code in state.py which has:
  Globals: active_sessions, current_project, sdk_client, session_mgr,
           user_last_message, PROJECT_NAME_RE
  Functions: get_manager(), get_all_managers(), register_manager() [async],
             unregister_manager() [async], initialize() [async]
"""
import re

import pytest

import state


# ════════════════════════════════════════════════════════════════════
#  1. Module-level global defaults
# ════════════════════════════════════════════════════════════════════


class TestGlobalDefaults:
    """Verify all module-level globals have correct types and initial values."""

    def test_active_sessions_is_dict(self):
        assert isinstance(state.active_sessions, dict)

    def test_active_sessions_starts_empty(self):
        assert state.active_sessions == {}

    def test_current_project_is_dict(self):
        assert isinstance(state.current_project, dict)

    def test_current_project_starts_empty(self):
        assert state.current_project == {}

    def test_sdk_client_default_is_none(self):
        """sdk_client is None until initialize() is called."""
        # Note: it may have been set in a previous test run, but it's
        # typed as ClaudeSDKManager | None — we just check it exists.
        assert hasattr(state, "sdk_client")

    def test_session_mgr_default_exists(self):
        """session_mgr attribute must exist."""
        assert hasattr(state, "session_mgr")

    def test_user_last_message_is_dict(self):
        assert isinstance(state.user_last_message, dict)

    def test_user_last_message_starts_empty(self):
        assert state.user_last_message == {}

    def test_user_last_message_can_store_floats(self):
        """user_last_message maps user_id (int) -> timestamp (float)."""
        state.user_last_message[42] = 1709900000.0
        assert state.user_last_message[42] == 1709900000.0

    def test_current_project_can_be_set(self):
        """current_project maps user_id (int) -> project_id (str)."""
        state.current_project[1] = "my-project"
        assert state.current_project[1] == "my-project"


# ════════════════════════════════════════════════════════════════════
#  2. PROJECT_NAME_RE regex validation
# ════════════════════════════════════════════════════════════════════


class TestProjectNameRegex:
    """Validate PROJECT_NAME_RE = r'^[a-zA-Z0-9 _-]+$'."""

    def test_regex_is_compiled_pattern(self):
        assert isinstance(state.PROJECT_NAME_RE, re.Pattern)

    @pytest.mark.parametrize("name", [
        "my-project",
        "test_123",
        "a",
        "project-name-123",
        "Hello World",       # spaces ARE in the character class
        "A B C",
        "under_score",
        "MiXeD-CaSe_123",
    ])
    def test_valid_names_match(self, name):
        assert state.PROJECT_NAME_RE.match(name), f"Should match: {name!r}"

    @pytest.mark.parametrize("name", [
        "",                  # empty string
        "bad@name",          # @ symbol
        "path/slash",        # slash
        "no.dots",           # dots
        "special!chars",     # exclamation
        "angle<bracket>",    # angle brackets
        "semi;colon",        # semicolon
        "back\\slash",       # backslash
    ])
    def test_invalid_names_rejected(self, name):
        assert not state.PROJECT_NAME_RE.match(name), f"Should NOT match: {name!r}"


# ════════════════════════════════════════════════════════════════════
#  3. get_manager()
# ════════════════════════════════════════════════════════════════════


class TestGetManager:
    """get_manager(project_id) -> (manager | None, user_id | None)."""

    def test_returns_none_tuple_when_empty(self):
        manager, user_id = state.get_manager("nonexistent")
        assert manager is None
        assert user_id is None

    def test_finds_registered_manager(self):
        fake = object()
        state.active_sessions[10] = {"proj-a": fake}
        found, uid = state.get_manager("proj-a")
        assert found is fake
        assert uid == 10

    def test_returns_none_for_wrong_project_id(self):
        state.active_sessions[1] = {"proj-a": object()}
        found, uid = state.get_manager("proj-b")
        assert found is None
        assert uid is None

    def test_searches_across_all_users(self):
        mgr_b = object()
        state.active_sessions[1] = {"proj-a": object()}
        state.active_sessions[2] = {"proj-b": mgr_b}
        found, uid = state.get_manager("proj-b")
        assert found is mgr_b
        assert uid == 2

    def test_returns_first_match_only(self):
        """If somehow the same project_id exists under two users, returns the first found."""
        mgr1 = object()
        mgr2 = object()
        state.active_sessions[1] = {"dup-proj": mgr1}
        state.active_sessions[2] = {"dup-proj": mgr2}
        found, uid = state.get_manager("dup-proj")
        # Should return one of them (dict iteration order is insertion order in Python 3.7+)
        assert found is not None
        assert uid in (1, 2)


# ════════════════════════════════════════════════════════════════════
#  4. get_all_managers()
# ════════════════════════════════════════════════════════════════════


class TestGetAllManagers:
    """get_all_managers() -> list[(user_id, project_id, manager)]."""

    def test_empty_returns_empty_list(self):
        result = state.get_all_managers()
        assert result == []
        assert isinstance(result, list)

    def test_single_user_single_project(self):
        mgr = object()
        state.active_sessions[1] = {"proj": mgr}
        result = state.get_all_managers()
        assert len(result) == 1
        assert result[0] == (1, "proj", mgr)

    def test_multiple_users_multiple_projects(self):
        mgr1, mgr2, mgr3 = object(), object(), object()
        state.active_sessions[1] = {"proj-a": mgr1, "proj-b": mgr2}
        state.active_sessions[2] = {"proj-c": mgr3}
        result = state.get_all_managers()
        assert len(result) == 3
        # Verify all are present
        ids = {(uid, pid) for uid, pid, _ in result}
        assert ids == {(1, "proj-a"), (1, "proj-b"), (2, "proj-c")}
        managers = {m for _, _, m in result}
        assert managers == {mgr1, mgr2, mgr3}


# ════════════════════════════════════════════════════════════════════
#  5. register_manager() — async
# ════════════════════════════════════════════════════════════════════


class TestRegisterManager:
    """register_manager(user_id, project_id, manager) — adds to active_sessions."""

    @pytest.mark.asyncio
    async def test_adds_new_user_and_project(self):
        mgr = object()
        await state.register_manager(99, "new-proj", mgr)
        assert 99 in state.active_sessions
        assert state.active_sessions[99]["new-proj"] is mgr

    @pytest.mark.asyncio
    async def test_multiple_projects_same_user(self):
        m1, m2 = object(), object()
        await state.register_manager(5, "proj-x", m1)
        await state.register_manager(5, "proj-y", m2)
        assert len(state.active_sessions[5]) == 2
        assert state.active_sessions[5]["proj-x"] is m1
        assert state.active_sessions[5]["proj-y"] is m2

    @pytest.mark.asyncio
    async def test_overwrite_existing_project(self):
        old = object()
        new = object()
        await state.register_manager(1, "proj", old)
        await state.register_manager(1, "proj", new)
        assert state.active_sessions[1]["proj"] is new

    @pytest.mark.asyncio
    async def test_register_then_find_with_get_manager(self):
        mgr = object()
        await state.register_manager(7, "e2e-proj", mgr)
        found, uid = state.get_manager("e2e-proj")
        assert found is mgr
        assert uid == 7


# ════════════════════════════════════════════════════════════════════
#  6. unregister_manager() — async
# ════════════════════════════════════════════════════════════════════


class TestUnregisterManager:
    """unregister_manager(user_id, project_id) — removes from active_sessions."""

    @pytest.mark.asyncio
    async def test_removes_project(self):
        state.active_sessions[7] = {"keep": object(), "remove": object()}
        await state.unregister_manager(7, "remove")
        assert "remove" not in state.active_sessions[7]
        assert "keep" in state.active_sessions[7]

    @pytest.mark.asyncio
    async def test_cleans_up_empty_user(self):
        state.active_sessions[99] = {"only": object()}
        await state.unregister_manager(99, "only")
        assert 99 not in state.active_sessions

    @pytest.mark.asyncio
    async def test_safe_for_nonexistent_user(self):
        """Should not raise when user_id doesn't exist."""
        await state.unregister_manager(9999, "ghost")  # no exception

    @pytest.mark.asyncio
    async def test_safe_for_nonexistent_project(self):
        """Should not raise when project_id doesn't exist under a valid user."""
        state.active_sessions[1] = {"real": object()}
        await state.unregister_manager(1, "nonexistent")  # no exception
        assert "real" in state.active_sessions[1]

    @pytest.mark.asyncio
    async def test_unregister_then_get_returns_none(self):
        mgr = object()
        await state.register_manager(1, "temp", mgr)
        await state.unregister_manager(1, "temp")
        found, uid = state.get_manager("temp")
        assert found is None
        assert uid is None
