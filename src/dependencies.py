"""FastAPI dependency injection for the platform persistence layer.

Usage in any router or endpoint::

    from fastapi import Depends
    from src.dependencies import get_conversation_store, get_memory_store, get_project_manager
    from src.storage.conversation_store import ConversationStore
    from src.storage.memory_store import MemoryStore
    from src.projects.project_manager import ProjectManager

    @router.get("/history/{conv_id}")
    async def get_history(
        conv_id: str,
        store: ConversationStore = Depends(get_conversation_store),
    ):
        return await store.get_conversation_history(conv_id)

These dependencies are safe to use in both REST endpoints and background tasks.
Each ``Depends`` call re-uses the module-level cached session factory, so there
is no per-request engine creation overhead.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.database import get_session_factory
from src.projects.project_manager import ProjectManager
from src.storage.conversation_store import ConversationStore
from src.storage.memory_store import MemoryStore

# ─────────────────────────────────────────────────────────────────────────────
# Store dependencies
# ─────────────────────────────────────────────────────────────────────────────


def get_conversation_store() -> ConversationStore:
    """Return a ConversationStore backed by the platform session factory.

    The session factory is a module-level singleton (created once per process)
    so this function is cheap to call on every request.

    Example::

        @router.get("/conversations/{project_id}")
        async def list_conversations(
            project_id: str,
            store: ConversationStore = Depends(get_conversation_store),
        ):
            return await store.list_conversations(project_id)
    """
    factory: async_sessionmaker[AsyncSession] = get_session_factory()
    return ConversationStore(factory)


def get_memory_store() -> MemoryStore:
    """Return a MemoryStore backed by the platform session factory.

    Example::

        @router.get("/memory/{project_id}")
        async def get_memory(
            project_id: str,
            store: MemoryStore = Depends(get_memory_store),
        ):
            return await store.get_all_memory(project_id)
    """
    factory: async_sessionmaker[AsyncSession] = get_session_factory()
    return MemoryStore(factory)


# ─────────────────────────────────────────────────────────────────────────────
# Project manager dependency
# ─────────────────────────────────────────────────────────────────────────────


def get_project_manager() -> ProjectManager:
    """Return a ProjectManager backed by the platform session factory.

    The ProjectManager is stateless (no per-request mutable state) and safe to
    call on every request.  The session factory it holds is a module-level
    singleton, so there is no per-request engine creation overhead.

    Isolation mode is read once from the ``ISOLATION_MODE`` env var when the
    first request is handled.  Changing the env var at runtime requires a server
    restart.

    Example::

        @router.post("/api/projects")
        async def create_project(
            req: CreateProjectRequest,
            mgr: ProjectManager = Depends(get_project_manager),
        ):
            return await mgr.create_project(req.name, config=req.config)
    """
    factory: async_sessionmaker[AsyncSession] = get_session_factory()
    return ProjectManager(factory)
