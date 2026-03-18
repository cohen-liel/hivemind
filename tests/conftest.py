"""Shared fixtures for the test suite."""

# ── Mock claude_agent_sdk (private package, not available in CI) ─────────────
import sys
import types
from unittest.mock import MagicMock

if "claude_agent_sdk" not in sys.modules:
    # Create a proper package mock that supports attribute and submodule access
    _sdk_pkg = types.ModuleType("claude_agent_sdk")
    _sdk_pkg.__path__ = []  # Make it a package so submodule imports work
    _sdk_pkg.ClaudeAgentOptions = type("ClaudeAgentOptions", (), {})
    _sdk_pkg.ClaudeSDKClient = MagicMock()

    _internal = types.ModuleType("claude_agent_sdk._internal")
    _internal.__path__ = []
    _internal.message_parser = MagicMock()

    _types = MagicMock()
    _types.__path__ = []
    _types.__name__ = "claude_agent_sdk.types"

    _msg_parser = types.ModuleType("claude_agent_sdk._internal.message_parser")
    _msg_parser.parse_message = MagicMock()

    sys.modules["claude_agent_sdk"] = _sdk_pkg
    sys.modules["claude_agent_sdk._internal"] = _internal
    sys.modules["claude_agent_sdk._internal.message_parser"] = _msg_parser
    sys.modules["claude_agent_sdk.types"] = _types
# ─────────────────────────────────────────────────────────────────────────────


import os
import sys

import pytest

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Disable API key auth during tests so the test client doesn't get 401s.
# config.py calls load_dotenv() at import time which reads .env into os.environ.
# We must: (1) let config import, (2) clear the env var, (3) patch the cached
# module-level values so create_app() sees AUTH_ENABLED=False.
import config as _cfg

os.environ.pop("DASHBOARD_API_KEY", None)
_cfg.DASHBOARD_API_KEY = ""
_cfg.AUTH_ENABLED = False

# Disable sandbox path validation so tests can use tempfile directories.
_cfg.SANDBOX_ENABLED = False

# Disable device-token authentication during tests.
_cfg.DEVICE_AUTH_ENABLED = False


@pytest.fixture(autouse=True)
def clean_state():
    """Reset global state before/after each test to prevent cross-contamination.

    Cleans ALL mutable globals from state.py:
      - active_sessions   (dict)
      - current_project    (dict)
      - user_last_message  (dict)
      - sdk_client         (singleton, may be set by initialize())
      - session_mgr        (singleton, may be set by initialize())
      - _initialized       (bool, guards double-init)
      - server_start_time  (float)
    """
    import state

    # Save originals
    saved_sessions = dict(state.active_sessions)
    saved_project = dict(state.current_project)
    saved_last_msg = dict(state.user_last_message)
    saved_sdk_client = state.sdk_client
    saved_session_mgr = state.session_mgr
    saved_initialized = state._initialized
    saved_start_time = state.server_start_time

    # Clear before test
    state.active_sessions.clear()
    state.current_project.clear()
    state.user_last_message.clear()
    state.sdk_client = None
    state.session_mgr = None
    state._initialized = False

    yield

    # Restore after test
    state.active_sessions.clear()
    state.active_sessions.update(saved_sessions)
    state.current_project.clear()
    state.current_project.update(saved_project)
    state.user_last_message.clear()
    state.user_last_message.update(saved_last_msg)
    state.sdk_client = saved_sdk_client
    state.session_mgr = saved_session_mgr
    state._initialized = saved_initialized
    state.server_start_time = saved_start_time
