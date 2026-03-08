"""Proof that we can write and run tests in this project.

Written based on ACTUAL source code in state.py and config.py.
"""
import sys
import os
from datetime import datetime

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_write_proof():
    """This test proves the agent can write files."""
    timestamp = datetime.now().isoformat()
    assert isinstance(timestamp, str)
    assert "2026" in timestamp  # We know the current year
    print(f"Test written and executed at: {timestamp}")


def test_import_state():
    """Test that we can import project modules."""
    import state

    assert hasattr(state, "active_sessions")
    assert hasattr(state, "current_project")
    assert isinstance(state.active_sessions, dict)
    print(f"state.active_sessions type = {type(state.active_sessions)}")
    print(f"state.current_project type = {type(state.current_project)}")


def test_import_config():
    """Test that we can import config and its ACTUAL attributes."""
    import config

    # These are the REAL attribute names in config.py:
    assert hasattr(config, "MAX_TURNS_PER_CYCLE")
    assert hasattr(config, "MAX_BUDGET_USD")
    assert hasattr(config, "SDK_MAX_RETRIES")
    assert hasattr(config, "ORCHESTRATOR_SYSTEM_PROMPT")
    assert hasattr(config, "SUB_AGENT_PROMPTS")

    assert isinstance(config.MAX_TURNS_PER_CYCLE, int)
    assert isinstance(config.MAX_BUDGET_USD, float)
    assert config.MAX_TURNS_PER_CYCLE > 0
    assert config.MAX_BUDGET_USD > 0

    print(f"MAX_TURNS_PER_CYCLE = {config.MAX_TURNS_PER_CYCLE}")
    print(f"MAX_BUDGET_USD = {config.MAX_BUDGET_USD}")
    print(f"SDK_MAX_RETRIES = {config.SDK_MAX_RETRIES}")


@pytest.mark.asyncio
async def test_state_register_manager():
    """Test registering and retrieving a project manager.

    NOTE: register_manager is async and takes (user_id: int, project_id: str, manager).
    """
    import state

    # Clean up first
    state.active_sessions.clear()

    fake_manager = {"name": "test"}  # Any object works as a stand-in
    await state.register_manager(42, "test-project", fake_manager)
    result, user_id = state.get_manager("test-project")
    assert result == {"name": "test"}
    assert user_id == 42

    # Clean up
    state.active_sessions.clear()
    print("register_manager works correctly!")


def test_project_name_regex():
    """Test the project name regex validation.

    ACTUAL regex is: ^[a-zA-Z0-9 _-]+$
    This DOES allow spaces (they're in the character class).
    """
    import state

    # Valid names (letters, digits, spaces, underscores, hyphens)
    assert state.PROJECT_NAME_RE.match("my-project")
    assert state.PROJECT_NAME_RE.match("test_123")
    assert state.PROJECT_NAME_RE.match("name with spaces")  # spaces ARE allowed

    # Invalid names
    assert not state.PROJECT_NAME_RE.match("")            # empty
    assert not state.PROJECT_NAME_RE.match("bad@name")    # special char @
    assert not state.PROJECT_NAME_RE.match("path/slash")  # slash
    assert not state.PROJECT_NAME_RE.match("no.dots")     # dots

    print("PROJECT_NAME_RE works correctly!")
