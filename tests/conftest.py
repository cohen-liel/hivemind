"""Shared fixtures for the test suite."""
import sys
import os

import pytest

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def clean_state():
    """Reset global state before/after each test to prevent cross-contamination."""
    import state

    # Save originals
    saved_sessions = dict(state.active_sessions)
    saved_project = dict(state.current_project)
    saved_last_msg = dict(state.user_last_message)

    # Clear before test
    state.active_sessions.clear()
    state.current_project.clear()
    state.user_last_message.clear()

    yield

    # Restore after test
    state.active_sessions.clear()
    state.active_sessions.update(saved_sessions)
    state.current_project.clear()
    state.current_project.update(saved_project)
    state.user_last_message.clear()
    state.user_last_message.update(saved_last_msg)
