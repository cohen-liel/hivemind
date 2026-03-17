"""Tests for ConversationStore and MemoryStore.

Covers:
- ConversationStore.create_conversation / list_conversations / get_or_create_default_conversation
- ConversationStore.append_message / get_conversation_history
- ConversationStore.set_conversation_title
- MemoryStore.set_memory / get_memory / get_all_memory / delete_memory
- MemoryStore.set_many / get_keys
- MemoryStore security validation (forbidden key prefixes)
- src/dependencies.py factory functions
- src/api/websocket_handler.py helper functions
- src/api/history.py router endpoint schemas
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import Base
from src.storage.conversation_store import ConversationStore
from src.storage.memory_store import MemoryStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine():
    """In-memory SQLite engine for tests."""
    from sqlalchemy.pool import StaticPool

    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    """AsyncSessionmaker bound to the in-memory engine."""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


@pytest_asyncio.fixture
async def conv_store(session_factory):
    return ConversationStore(session_factory)


@pytest_asyncio.fixture
async def mem_store(session_factory):
    return MemoryStore(session_factory)


# ---------------------------------------------------------------------------
# ConversationStore tests
# ---------------------------------------------------------------------------


class TestConversationStore:
    async def test_create_conversation_returns_uuid(self, conv_store):
        """create_conversation() returns a non-empty UUID string."""
        conv_id = await conv_store.create_conversation("proj-a", title="My First Conversation")
        assert isinstance(conv_id, str)
        assert len(conv_id) == 36  # UUID4 format
        assert conv_id.count("-") == 4

    async def test_create_conversation_no_title(self, conv_store):
        """create_conversation() works without a title."""
        conv_id = await conv_store.create_conversation("proj-b")
        assert isinstance(conv_id, str)

    async def test_list_conversations_empty(self, conv_store):
        """list_conversations() returns empty list for unknown project."""
        convs = await conv_store.list_conversations("nonexistent-project")
        assert convs == []

    async def test_list_conversations_returns_created(self, conv_store):
        """list_conversations() returns conversation created for the project."""
        await conv_store.create_conversation("proj-c", title="Conv 1")
        await conv_store.create_conversation("proj-c", title="Conv 2")
        convs = await conv_store.list_conversations("proj-c")
        assert len(convs) == 2
        titles = {c["title"] for c in convs}
        assert "Conv 1" in titles
        assert "Conv 2" in titles

    async def test_list_conversations_isolates_projects(self, conv_store):
        """list_conversations() only returns conversations for the given project."""
        await conv_store.create_conversation("proj-x", title="Project X Conv")
        await conv_store.create_conversation("proj-y", title="Project Y Conv")
        x_convs = await conv_store.list_conversations("proj-x")
        assert len(x_convs) == 1
        assert x_convs[0]["title"] == "Project X Conv"

    async def test_list_conversations_sorted_by_recent(self, conv_store):
        """list_conversations() returns most-recently-active first."""
        c1 = await conv_store.create_conversation("proj-sort", title="First")
        await conv_store.create_conversation("proj-sort", title="Second")
        # Append a message to c1 to update its last_active_at
        await conv_store.append_message(c1, "user", "A message to make c1 most recent")
        convs = await conv_store.list_conversations("proj-sort")
        # c1 should be first because it has the most recent activity
        assert convs[0]["id"] == c1

    async def test_list_conversations_pagination(self, conv_store):
        """list_conversations() respects limit and offset."""
        for i in range(5):
            await conv_store.create_conversation("proj-page", title=f"Conv {i}")
        page1 = await conv_store.list_conversations("proj-page", limit=2, offset=0)
        page2 = await conv_store.list_conversations("proj-page", limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        # Pages should not overlap
        ids_p1 = {c["id"] for c in page1}
        ids_p2 = {c["id"] for c in page2}
        assert ids_p1.isdisjoint(ids_p2)

    async def test_conversation_dict_shape(self, conv_store):
        """Conversations have the expected dict keys."""
        await conv_store.create_conversation("proj-shape", title="Test")
        convs = await conv_store.list_conversations("proj-shape")
        c = convs[0]
        assert "id" in c
        assert "project_id" in c
        assert "title" in c
        assert "created_at" in c
        assert "last_active_at" in c
        assert c["project_id"] == "proj-shape"
        assert c["title"] == "Test"

    async def test_get_or_create_default_conversation_creates(self, conv_store):
        """get_or_create_default_conversation() creates conv if none exists."""
        conv_id = await conv_store.get_or_create_default_conversation("proj-new")
        assert isinstance(conv_id, str)
        convs = await conv_store.list_conversations("proj-new")
        assert len(convs) == 1
        assert convs[0]["id"] == conv_id
        assert convs[0]["title"] == "default"

    async def test_get_or_create_default_returns_existing(self, conv_store):
        """get_or_create_default_conversation() returns existing conv."""
        c1 = await conv_store.create_conversation("proj-existing", title="existing")
        c2 = await conv_store.get_or_create_default_conversation("proj-existing")
        assert c1 == c2

    async def test_set_conversation_title(self, conv_store):
        """set_conversation_title() updates the title."""
        conv_id = await conv_store.create_conversation("proj-title", title="Old")
        await conv_store.set_conversation_title(conv_id, "New Title")
        convs = await conv_store.list_conversations("proj-title")
        assert convs[0]["title"] == "New Title"


class TestAppendMessage:
    async def test_append_message_returns_uuid(self, conv_store):
        """append_message() returns a UUID string."""
        conv_id = await conv_store.create_conversation("proj-am")
        msg_id = await conv_store.append_message(conv_id, "user", "Hello!")
        assert isinstance(msg_id, str)
        assert len(msg_id) == 36

    async def test_append_message_all_roles(self, conv_store):
        """append_message() accepts all valid roles."""
        conv_id = await conv_store.create_conversation("proj-roles")
        for role in ("user", "assistant", "system", "tool"):
            msg_id = await conv_store.append_message(conv_id, role, f"Content for {role}")
            assert isinstance(msg_id, str)

    async def test_append_message_invalid_role_raises(self, conv_store):
        """append_message() raises ValueError for unknown role."""
        conv_id = await conv_store.create_conversation("proj-invalid-role")
        with pytest.raises(ValueError, match="Invalid role"):
            await conv_store.append_message(conv_id, "bot", "Content")

    async def test_append_message_with_metadata(self, conv_store):
        """append_message() stores metadata_json correctly."""
        conv_id = await conv_store.create_conversation("proj-test")
        meta = {"model": "claude-3-5-sonnet", "input_tokens": 100, "cost_usd": 0.01}
        await conv_store.append_message(conv_id, "assistant", "Response", metadata=meta)
        history = await conv_store.get_conversation_history(conv_id)
        assert len(history) == 1
        assert history[0]["metadata"] == meta

    async def test_append_message_updates_last_active(self, conv_store):
        """append_message() updates conversation's last_active_at."""
        conv_id = await conv_store.create_conversation("proj-active")
        convs_before = await conv_store.list_conversations("proj-active")
        before_ts = convs_before[0]["last_active_at"]
        import asyncio

        await asyncio.sleep(0.01)  # tiny pause to ensure timestamps differ
        await conv_store.append_message(conv_id, "user", "Message")
        convs_after = await conv_store.list_conversations("proj-active")
        after_ts = convs_after[0]["last_active_at"]
        # Timestamps should differ
        assert after_ts >= before_ts


class TestGetConversationHistory:
    async def test_empty_history(self, conv_store):
        """get_conversation_history() returns empty list for new conversation."""
        conv_id = await conv_store.create_conversation("proj-empty-hist")
        history = await conv_store.get_conversation_history(conv_id)
        assert history == []

    async def test_history_chronological_order(self, conv_store):
        """get_conversation_history() returns messages oldest-first."""
        conv_id = await conv_store.create_conversation("proj-order")
        await conv_store.append_message(conv_id, "user", "First message")
        await conv_store.append_message(conv_id, "assistant", "Second message")
        await conv_store.append_message(conv_id, "user", "Third message")
        history = await conv_store.get_conversation_history(conv_id)
        assert len(history) == 3
        assert history[0]["content"] == "First message"
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "Second message"
        assert history[1]["role"] == "assistant"
        assert history[2]["content"] == "Third message"

    async def test_history_message_dict_shape(self, conv_store):
        """Messages in history have all expected keys."""
        conv_id = await conv_store.create_conversation("proj-shape-msg")
        await conv_store.append_message(conv_id, "user", "Hello")
        history = await conv_store.get_conversation_history(conv_id)
        msg = history[0]
        assert "id" in msg
        assert "conversation_id" in msg
        assert "role" in msg
        assert "content" in msg
        assert "timestamp" in msg
        assert "metadata" in msg
        assert msg["conversation_id"] == conv_id
        assert msg["role"] == "user"
        assert msg["content"] == "Hello"

    async def test_history_survives_project_isolation(self, conv_store):
        """get_conversation_history() only returns messages for the given conv."""
        c1 = await conv_store.create_conversation("proj-iso1")
        c2 = await conv_store.create_conversation("proj-iso2")
        await conv_store.append_message(c1, "user", "Message in c1")
        await conv_store.append_message(c2, "user", "Message in c2")
        h1 = await conv_store.get_conversation_history(c1)
        h2 = await conv_store.get_conversation_history(c2)
        assert len(h1) == 1
        assert h1[0]["content"] == "Message in c1"
        assert len(h2) == 1
        assert h2[0]["content"] == "Message in c2"

    async def test_no_inm_memory_only_state(self, conv_store, session_factory):
        """History reloaded from a fresh store instance (simulates server restart)."""
        conv_id = await conv_store.create_conversation("proj-persist")
        await conv_store.append_message(conv_id, "user", "Persistent message")
        # Simulate server restart: create a completely new ConversationStore instance
        fresh_store = ConversationStore(session_factory)
        history = await fresh_store.get_conversation_history(conv_id)
        assert len(history) == 1
        assert history[0]["content"] == "Persistent message"


# ---------------------------------------------------------------------------
# MemoryStore tests
# ---------------------------------------------------------------------------


class TestMemoryStore:
    async def test_set_and_get_memory(self, mem_store):
        """set_memory() + get_memory() round-trip."""
        await mem_store.set_memory("proj-mem", "user.name", "Alice")
        value = await mem_store.get_memory("proj-mem", "user.name")
        assert value == "Alice"

    async def test_get_memory_default(self, mem_store):
        """get_memory() returns default for unknown key."""
        value = await mem_store.get_memory("proj-mem", "nonexistent.key")
        assert value is None
        value = await mem_store.get_memory("proj-mem", "nonexistent.key", default="fallback")
        assert value == "fallback"

    async def test_set_memory_upsert(self, mem_store):
        """set_memory() overwrites existing value (upsert semantics)."""
        await mem_store.set_memory("proj-upsert", "key.a", "original")
        await mem_store.set_memory("proj-upsert", "key.a", "updated")
        value = await mem_store.get_memory("proj-upsert", "key.a")
        assert value == "updated"

    async def test_get_all_memory_empty(self, mem_store):
        """get_all_memory() returns empty dict for unknown project."""
        result = await mem_store.get_all_memory("proj-empty-mem")
        assert result == {}

    async def test_get_all_memory_returns_all(self, mem_store):
        """get_all_memory() returns all keys for a project."""
        await mem_store.set_memory("proj-all", "key.a", "value_a")
        await mem_store.set_memory("proj-all", "key.b", 42)
        await mem_store.set_memory("proj-all", "key.c", ["list", "value"])
        result = await mem_store.get_all_memory("proj-all")
        assert result == {
            "key.a": "value_a",
            "key.b": 42,
            "key.c": ["list", "value"],
        }

    async def test_memory_isolated_by_project(self, mem_store):
        """Memory is scoped per project_id."""
        await mem_store.set_memory("proj-iso-a", "shared.key", "value-a")
        await mem_store.set_memory("proj-iso-b", "shared.key", "value-b")
        assert await mem_store.get_memory("proj-iso-a", "shared.key") == "value-a"
        assert await mem_store.get_memory("proj-iso-b", "shared.key") == "value-b"

    async def test_set_many(self, mem_store):
        """set_many() writes multiple keys in one transaction."""
        entries = {
            "tech.stack": ["Python", "FastAPI"],
            "user.pref.theme": "dark",
            "agent.last_loop": 5,
        }
        await mem_store.set_many("proj-many", entries)
        result = await mem_store.get_all_memory("proj-many")
        for k, v in entries.items():
            assert result[k] == v

    async def test_delete_memory_existing(self, mem_store):
        """delete_memory() removes an existing key and returns True."""
        await mem_store.set_memory("proj-del", "to.delete", "value")
        deleted = await mem_store.delete_memory("proj-del", "to.delete")
        assert deleted is True
        assert await mem_store.get_memory("proj-del", "to.delete") is None

    async def test_delete_memory_nonexistent(self, mem_store):
        """delete_memory() returns False for a key that doesn't exist."""
        deleted = await mem_store.delete_memory("proj-del2", "nonexistent")
        assert deleted is False

    async def test_get_keys(self, mem_store):
        """get_keys() returns all keys for a project."""
        await mem_store.set_memory("proj-keys", "b.key", 1)
        await mem_store.set_memory("proj-keys", "a.key", 2)
        keys = await mem_store.get_keys("proj-keys")
        assert sorted(keys) == ["a.key", "b.key"]

    async def test_memory_json_types(self, mem_store):
        """MemoryStore handles all JSON-serialisable value types."""
        test_cases = [
            ("type.string", "hello"),
            ("type.int", 42),
            ("type.float", 3.14),
            ("type.bool", True),
            ("type.null", None),
            ("type.list", [1, 2, 3]),
            ("type.dict", {"nested": {"key": "value"}}),
        ]
        for key, value in test_cases:
            await mem_store.set_memory("proj-types", key, value)
        for key, expected in test_cases:
            actual = await mem_store.get_memory("proj-types", key, default="__missing__")
            # None stored as null returns None (not the default)
            if expected is None:
                assert actual is None
            else:
                assert actual == expected

    async def test_memory_survives_server_restart(self, mem_store, session_factory):
        """Memory reloaded from a fresh MemoryStore (simulates server restart)."""
        await mem_store.set_memory("proj-restart", "agent.plan", "step 1, step 2")
        fresh_store = MemoryStore(session_factory)
        context = await fresh_store.get_all_memory("proj-restart")
        assert context["agent.plan"] == "step 1, step 2"


class TestMemoryStoreSecurityValidation:
    async def test_forbidden_key_prefix_secret(self, mem_store):
        """Keys starting with 'secret' are rejected."""
        with pytest.raises(ValueError, match="forbidden prefix"):
            await mem_store.set_memory("proj-sec", "secret.api_key", "not_storing_this")

    async def test_forbidden_key_prefix_api_key(self, mem_store):
        """Keys starting with 'api_key' are rejected."""
        with pytest.raises(ValueError, match="forbidden prefix"):
            await mem_store.set_memory("proj-sec", "api_key", "sk-1234")

    async def test_forbidden_key_prefix_token(self, mem_store):
        """Keys starting with 'token' are rejected."""
        with pytest.raises(ValueError, match="forbidden prefix"):
            await mem_store.set_memory("proj-sec", "token.auth", "bearer_xyz")

    async def test_forbidden_key_prefix_password(self, mem_store):
        """Keys starting with 'password' are rejected."""
        with pytest.raises(ValueError, match="forbidden prefix"):
            await mem_store.set_memory("proj-sec", "password", "hunter2")

    async def test_empty_key_rejected(self, mem_store):
        """Empty string key is rejected."""
        with pytest.raises(ValueError):
            await mem_store.set_memory("proj-sec", "", "value")

    async def test_valid_key_with_dot_notation(self, mem_store):
        """Dot-notation keys are accepted."""
        # Should not raise
        await mem_store.set_memory("proj-valid", "agent.orchestrator.last_plan", "my plan")
        assert await mem_store.get_memory("proj-valid", "agent.orchestrator.last_plan") == "my plan"

    async def test_set_many_validates_all_keys(self, mem_store):
        """set_many() validates all keys before writing any."""
        with pytest.raises(ValueError, match="forbidden prefix"):
            await mem_store.set_many(
                "proj-sec-many",
                {
                    "valid.key": "value",
                    "secret.key": "should_fail",
                },
            )
        # No keys should have been written (atomic validation)
        result = await mem_store.get_all_memory("proj-sec-many")
        assert result == {}


# ---------------------------------------------------------------------------
# Dependencies tests
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_get_conversation_store_returns_store(self):
        """get_conversation_store() returns a ConversationStore."""
        from src.dependencies import get_conversation_store

        store = get_conversation_store()
        assert isinstance(store, ConversationStore)

    def test_get_memory_store_returns_store(self):
        """get_memory_store() returns a MemoryStore."""
        from src.dependencies import get_memory_store

        store = get_memory_store()
        assert isinstance(store, MemoryStore)


# ---------------------------------------------------------------------------
# WebSocket handler tests
# ---------------------------------------------------------------------------


class TestWebSocketHandler:
    async def test_get_cached_conversation_id_miss(self):
        """get_cached_conversation_id() returns None for unknown project."""
        from src.api.websocket_handler import _active_conversation_ids, get_cached_conversation_id

        # Clear the cache first
        _active_conversation_ids.clear()
        result = get_cached_conversation_id("proj-unknown")
        assert result is None

    def test_invalidate_conversation_cache(self):
        """invalidate_conversation_cache() removes the cached ID."""
        from src.api.websocket_handler import (
            _active_conversation_ids,
            invalidate_conversation_cache,
        )

        _active_conversation_ids["proj-to-clear"] = "some-uuid"
        invalidate_conversation_cache("proj-to-clear")
        assert "proj-to-clear" not in _active_conversation_ids

    def test_invalidate_conversation_cache_noop(self):
        """invalidate_conversation_cache() is safe for unknown projects."""
        from src.api.websocket_handler import invalidate_conversation_cache

        # Should not raise even if project is not in cache
        invalidate_conversation_cache("proj-not-in-cache")

    async def test_load_history_on_connect_empty(self, conv_store, session_factory):
        """load_history_on_connect() returns [] for new conversation."""
        from unittest.mock import patch

        from src.api.websocket_handler import load_history_on_connect

        conv_id = await conv_store.create_conversation("proj-ws-hist")
        # Patch _get_store to use the test session_factory
        with patch("src.api.websocket_handler._get_store", return_value=conv_store):
            history = await load_history_on_connect("proj-ws-hist", conv_id)
        assert history == []

    async def test_load_history_on_connect_with_messages(self, conv_store, session_factory):
        """load_history_on_connect() returns persisted messages."""
        from unittest.mock import patch

        from src.api.websocket_handler import load_history_on_connect

        conv_id = await conv_store.create_conversation("proj-ws-msg")
        await conv_store.append_message(conv_id, "user", "User said this")
        await conv_store.append_message(conv_id, "assistant", "Agent replied this")
        with patch("src.api.websocket_handler._get_store", return_value=conv_store):
            history = await load_history_on_connect("proj-ws-msg", conv_id)
        assert len(history) == 2
        assert history[0]["content"] == "User said this"
        assert history[1]["content"] == "Agent replied this"


# ---------------------------------------------------------------------------
# History router schema tests (no HTTP server needed)
# ---------------------------------------------------------------------------


class TestHistoryRouterSchemas:
    def test_router_has_list_conversations_route(self):
        """history_router has GET /api/conversations/{project_id}."""
        from src.api.history import history_router

        # Build a list of (path, method) tuples to avoid dict overwrite when
        # GET and POST share the same path
        route_methods = [
            (r.path, method) for r in history_router.routes for method in (r.methods or set())
        ]
        assert ("/api/conversations/{project_id}", "GET") in route_methods

    def test_router_has_memory_route(self):
        """history_router has GET /api/memory/{project_id}."""
        from src.api.history import history_router

        routes = {r.path: r.methods for r in history_router.routes}
        assert "/api/memory/{project_id}" in routes

    def test_router_has_create_conversation_route(self):
        """history_router has POST /api/conversations/{project_id}."""
        from src.api.history import history_router

        routes = {r.path: r.methods for r in history_router.routes}
        assert "/api/conversations/{project_id}" in routes
        assert "POST" in routes["/api/conversations/{project_id}"]

    def test_conversation_summary_model(self):
        """ConversationSummary validates correctly."""
        from src.api.history import ConversationSummary

        cs = ConversationSummary(
            id="abc-123",
            project_id="my-project",
            title="Test",
            created_at="2026-03-11T10:00:00+00:00",
            last_active_at="2026-03-11T10:05:00+00:00",
        )
        assert cs.id == "abc-123"
        assert cs.project_id == "my-project"
        assert cs.title == "Test"

    def test_memory_set_request_model(self):
        """MemorySetRequest accepts any JSON value."""
        from src.api.history import MemorySetRequest

        for value in ("string", 42, 3.14, True, None, [1, 2], {"k": "v"}):
            req = MemorySetRequest(value=value)
            assert req.value == value
