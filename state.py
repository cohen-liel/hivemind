"""Shared application state — the single source of truth for all interfaces.

Both the web dashboard (FastAPI) and the Telegram bot import from here
instead of reaching into each other's module globals.
"""
from __future__ import annotations

import asyncio
import logging
import re

from orchestrator import OrchestratorManager
from sdk_client import ClaudeSDKManager
from session_manager import SessionManager
from skills_registry import scan_skills

logger = logging.getLogger(__name__)

# ── Global mutable state ──────────────────────────────────────────────

_state_lock = asyncio.Lock()

# user_id -> {project_id -> OrchestratorManager}
active_sessions: dict[int, dict[str, OrchestratorManager]] = {}

# user_id -> currently-focused project_id
current_project: dict[int, str] = {}

# Singletons (populated by initialize())
sdk_client: ClaudeSDKManager | None = None
session_mgr: SessionManager | None = None

# Valid project name pattern
PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9 _-]+$")

# Per-user rate limiting: user_id -> last message timestamp
user_last_message: dict[int, float] = {}

# Map bot message_id -> project_id for reply-to project detection (Telegram only)
msg_to_project: dict[int, str] = {}


# ── Initialization ────────────────────────────────────────────────────

async def initialize():
    """Create SDK + SessionManager singletons. Safe to call once."""
    global sdk_client, session_mgr

    if sdk_client is None:
        sdk_client = ClaudeSDKManager()
        logger.info("SDK client initialized")

    if session_mgr is None:
        session_mgr = SessionManager()
        await session_mgr.initialize()
        logger.info("Session manager initialized")

    # Scan available skills
    skills = scan_skills()
    logger.info(f"Loaded {len(skills)} skills: {list(skills.keys())}")


# ── Helper functions ──────────────────────────────────────────────────

def get_manager(project_id: str) -> tuple[OrchestratorManager | None, int | None]:
    """Find an OrchestratorManager by project_id across all users."""
    for user_id, sessions in active_sessions.items():
        if project_id in sessions:
            return sessions[project_id], user_id
    return None, None


def get_all_managers() -> list[tuple[int, str, OrchestratorManager]]:
    """Return a flat list of (user_id, project_id, manager) across all users."""
    result = []
    for user_id, sessions in active_sessions.items():
        for project_id, manager in sessions.items():
            result.append((user_id, project_id, manager))
    return result


async def register_manager(user_id: int, project_id: str, manager: OrchestratorManager):
    """Register an OrchestratorManager for a user+project."""
    async with _state_lock:
        if user_id not in active_sessions:
            active_sessions[user_id] = {}
        active_sessions[user_id][project_id] = manager


async def unregister_manager(user_id: int, project_id: str):
    """Remove an OrchestratorManager for a user+project."""
    async with _state_lock:
        if user_id in active_sessions:
            active_sessions[user_id].pop(project_id, None)
            if not active_sessions[user_id]:
                del active_sessions[user_id]
