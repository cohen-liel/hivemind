"""Shared application state — the single source of truth for the web dashboard.

This module holds all runtime globals (active sessions, singletons) and provides
thread-safe helpers to register / look-up / unregister orchestrator managers.

All public functions validate their inputs and log significant operations.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from orchestrator import OrchestratorManager
from sdk_client import ClaudeSDKManager
from session_manager import SessionManager
from skills_registry import scan_skills

logger = logging.getLogger(__name__)

# ── Global mutable state ──────────────────────────────────────────────

_state_lock = asyncio.Lock()
_init_lock = asyncio.Lock()   # separate lock so initialize() doesn't deadlock get_manager()
_initialized = False

# user_id -> {project_id -> OrchestratorManager}
active_sessions: dict[int, dict[str, OrchestratorManager]] = {}

# user_id -> currently-focused project_id
current_project: dict[int, str] = {}

# Singletons (populated by initialize())
sdk_client: ClaudeSDKManager | None = None
session_mgr: SessionManager | None = None

# Valid project name pattern
PROJECT_NAME_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9 _-]+$")

# Per-user rate limiting: user_id -> last message timestamp
user_last_message: dict[int, float] = {}


# ── Initialization ────────────────────────────────────────────────────

async def initialize() -> None:
    """Create SDK + SessionManager singletons.  Safe to call once.

    Raises:
        RuntimeError: If SessionManager.initialize() fails (DB issues).
    """
    global sdk_client, session_mgr, _initialized

    async with _init_lock:
        if _initialized:
            return

        if sdk_client is None:
            sdk_client = ClaudeSDKManager()
            logger.info("SDK client initialized")

        if session_mgr is None:
            session_mgr = SessionManager()
            await session_mgr.initialize()
            logger.info("Session manager initialized")

        # Scan available skills
        skills = scan_skills()
        logger.info("Loaded %d skills: %s", len(skills), list(skills.keys()))

        _initialized = True


# ── Helper functions ──────────────────────────────────────────────────

def get_manager(project_id: str) -> tuple[OrchestratorManager | None, int | None]:
    """Find an OrchestratorManager by *project_id* across all users.

    Takes a snapshot of ``active_sessions`` to avoid ``RuntimeError:
    dictionary changed size during iteration`` when register/unregister
    happen concurrently.

    Args:
        project_id: The project identifier to search for.

    Returns:
        A ``(manager, user_id)`` tuple, or ``(None, None)`` if not found.
    """
    if not isinstance(project_id, str) or not project_id:
        logger.warning("get_manager called with invalid project_id: %r", project_id)
        return None, None

    # Snapshot: dict() copies the outer dict; inner dicts are short-lived
    # lookups so a shallow copy is sufficient.
    snapshot = dict(active_sessions)
    for user_id, sessions in snapshot.items():
        inner = dict(sessions)  # snapshot inner dict too
        if project_id in inner:
            return inner[project_id], user_id
    return None, None


def get_all_managers() -> list[tuple[int, str, Any]]:
    """Return a flat list of ``(user_id, project_id, manager)`` across all users.

    Takes snapshots of both outer and inner dicts to prevent iteration
    errors when sessions are modified concurrently.

    Returns:
        A new list (safe to iterate/mutate without affecting global state).
    """
    result: list[tuple[int, str, Any]] = []
    snapshot = dict(active_sessions)
    for user_id, sessions in snapshot.items():
        inner = dict(sessions)
        for project_id, manager in inner.items():
            result.append((user_id, project_id, manager))
    return result


async def register_manager(
    user_id: int,
    project_id: str,
    manager: Any,
) -> None:
    """Register an OrchestratorManager for a user + project.

    Args:
        user_id: Numeric user identifier.
        project_id: The project identifier (must be non-empty string).
        manager: The OrchestratorManager instance to register.

    Raises:
        ValueError: If *user_id* or *project_id* are invalid.
    """
    if not isinstance(user_id, int):
        raise ValueError(f"user_id must be an int, got {type(user_id).__name__}")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError(f"project_id must be a non-empty string, got {project_id!r}")

    async with _state_lock:
        if user_id not in active_sessions:
            active_sessions[user_id] = {}
        active_sessions[user_id][project_id] = manager
    logger.debug("Registered manager for user=%d project=%s", user_id, project_id)


async def unregister_manager(user_id: int, project_id: str) -> None:
    """Remove an OrchestratorManager for a user + project.

    Safe to call even if the user or project doesn't exist.

    Args:
        user_id: Numeric user identifier.
        project_id: The project identifier to remove.
    """
    async with _state_lock:
        if user_id in active_sessions:
            removed = active_sessions[user_id].pop(project_id, None)
            if not active_sessions[user_id]:
                del active_sessions[user_id]
            if removed is not None:
                logger.debug("Unregistered manager for user=%d project=%s", user_id, project_id)
        # Cascade: clear current_project pointer if it pointed at removed project
        if current_project.get(user_id) == project_id:
            del current_project[user_id]


def is_valid_project_name(name: str) -> bool:
    """Check whether *name* matches PROJECT_NAME_RE.

    Args:
        name: The candidate project name.

    Returns:
        ``True`` if the name contains only alphanumerics, spaces, hyphens,
        and underscores (and is non-empty).
    """
    if not isinstance(name, str):
        return False
    return bool(PROJECT_NAME_RE.match(name))
