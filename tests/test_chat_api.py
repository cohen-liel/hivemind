"""Tests for src/api/chat.py — Chat business logic layer.

Tests channel CRUD, message send/edit/delete, pagination,
read receipts, and WebSocket event builders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.chat import (
    archive_channel,
    build_chat_message_event,
    build_typing_event,
    create_channel,
    delete_message,
    edit_message,
    get_channel,
    get_message_history,
    get_read_receipts,
    list_channels,
    mark_read,
    send_message,
    unarchive_channel,
    _channel_to_dict,
    _iso,
    _message_to_dict,
    _DEFAULT_PAGE_SIZE,
    _MAX_PAGE_SIZE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def _mock_channel(**kw):
    defaults = {
        "id": "chan-1",
        "name": "general",
        "channel_type": "circle",
        "circle_id": "circle-1",
        "project_id": None,
        "description": "General chat",
        "is_archived": False,
        "created_by": "user-1",
        "created_at": _NOW,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _mock_message(**kw):
    defaults = {
        "id": "msg-1",
        "channel_id": "chan-1",
        "sender_id": "user-1",
        "content": "Hello world",
        "message_type": "text",
        "parent_message_id": None,
        "is_edited": False,
        "metadata_json": None,
        "created_at": _NOW,
        "deleted_at": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _mock_receipt(**kw):
    defaults = {
        "id": "rcpt-1",
        "message_id": "msg-1",
        "user_id": "user-2",
        "read_at": _NOW,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _setup_execute(session, value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    result.scalars.return_value.all.return_value = value if isinstance(value, list) else [value]
    session.execute = AsyncMock(return_value=result)
    return result


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_iso_when_none_should_return_none(self):
        assert _iso(None) is None

    def test_iso_when_datetime_should_format(self):
        assert _iso(_NOW) == "2026-03-20T12:00:00+00:00"

    def test_channel_to_dict_should_contain_all_keys(self):
        ch = _mock_channel()
        d = _channel_to_dict(ch)
        assert d["id"] == "chan-1"
        assert d["name"] == "general"
        assert d["channel_type"] == "circle"
        assert d["is_archived"] is False

    def test_message_to_dict_should_contain_all_keys(self):
        msg = _mock_message()
        d = _message_to_dict(msg)
        assert d["id"] == "msg-1"
        assert d["content"] == "Hello world"
        assert d["is_edited"] is False
        assert d["sender_id"] == "user-1"


# ---------------------------------------------------------------------------
# Channel CRUD
# ---------------------------------------------------------------------------


class TestCreateChannel:
    @pytest.mark.asyncio
    async def test_create_when_valid_should_return_channel_dict(self):
        session = _mock_session()
        result = await create_channel(
            session, name="dev-chat", channel_type="circle",
            created_by="user-1", circle_id="c1",
        )
        assert result["name"] == "dev-chat"
        assert result["channel_type"] == "circle"
        assert result["circle_id"] == "c1"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_when_project_channel_should_set_project_id(self):
        session = _mock_session()
        result = await create_channel(
            session, name="proj-chat", channel_type="project",
            created_by="user-1", project_id="p1",
        )
        assert result["project_id"] == "p1"


class TestGetChannel:
    @pytest.mark.asyncio
    async def test_get_when_exists_should_return_dict(self):
        session = _mock_session()
        channel = _mock_channel()
        _setup_execute(session, channel)

        d = await get_channel(session, "chan-1")
        assert d is not None
        assert d["id"] == "chan-1"

    @pytest.mark.asyncio
    async def test_get_when_not_exists_should_return_none(self):
        session = _mock_session()
        _setup_execute(session, None)

        d = await get_channel(session, "nonexistent")
        assert d is None


class TestListChannels:
    @pytest.mark.asyncio
    async def test_list_when_channels_exist_should_return_list(self):
        session = _mock_session()
        channels = [_mock_channel(id="c1"), _mock_channel(id="c2")]
        result = MagicMock()
        result.scalars.return_value.all.return_value = channels
        session.execute = AsyncMock(return_value=result)

        result_list = await list_channels(session, circle_id="circle-1")
        assert len(result_list) == 2

    @pytest.mark.asyncio
    async def test_list_when_empty_should_return_empty_list(self):
        session = _mock_session()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)

        result_list = await list_channels(session)
        assert result_list == []


class TestArchiveChannel:
    @pytest.mark.asyncio
    async def test_archive_when_exists_should_return_true(self):
        session = _mock_session()
        channel = _mock_channel()
        _setup_execute(session, channel)

        archived = await archive_channel(session, "chan-1")
        assert archived is True
        assert channel.is_archived is True

    @pytest.mark.asyncio
    async def test_archive_when_not_exists_should_return_false(self):
        session = _mock_session()
        _setup_execute(session, None)

        archived = await archive_channel(session, "nonexistent")
        assert archived is False


class TestUnarchiveChannel:
    @pytest.mark.asyncio
    async def test_unarchive_when_exists_should_return_true(self):
        session = _mock_session()
        channel = _mock_channel(is_archived=True)
        _setup_execute(session, channel)

        result = await unarchive_channel(session, "chan-1")
        assert result is True
        assert channel.is_archived is False

    @pytest.mark.asyncio
    async def test_unarchive_when_not_exists_should_return_false(self):
        session = _mock_session()
        _setup_execute(session, None)

        result = await unarchive_channel(session, "nonexistent")
        assert result is False


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_when_valid_should_return_message_dict(self):
        session = _mock_session()
        result = await send_message(
            session, channel_id="chan-1", sender_id="user-1",
            content="Hello!", message_type="text",
        )
        assert result["content"] == "Hello!"
        assert result["sender_id"] == "user-1"
        assert result["channel_id"] == "chan-1"
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_when_threaded_should_set_parent_id(self):
        session = _mock_session()
        result = await send_message(
            session, channel_id="chan-1", sender_id="user-1",
            content="Reply", parent_message_id="msg-0",
        )
        assert result["parent_message_id"] == "msg-0"

    @pytest.mark.asyncio
    async def test_send_when_metadata_should_store(self):
        session = _mock_session()
        meta = {"emoji": "👍"}
        result = await send_message(
            session, channel_id="chan-1", sender_id="user-1",
            content="With meta", metadata=meta,
        )
        assert result["metadata"] == meta


class TestEditMessage:
    @pytest.mark.asyncio
    async def test_edit_when_sender_should_update_content(self):
        session = _mock_session()
        msg = _mock_message(sender_id="user-1")
        _setup_execute(session, msg)

        result = await edit_message(session, "msg-1", "user-1", "Edited!")
        assert msg.content == "Edited!"
        assert msg.is_edited is True
        assert result is not None

    @pytest.mark.asyncio
    async def test_edit_when_not_sender_should_return_none(self):
        session = _mock_session()
        _setup_execute(session, None)  # query returns nothing for wrong sender

        result = await edit_message(session, "msg-1", "user-999", "Hacked!")
        assert result is None


class TestDeleteMessage:
    @pytest.mark.asyncio
    async def test_delete_when_sender_should_soft_delete(self):
        session = _mock_session()
        msg = _mock_message(sender_id="user-1")
        _setup_execute(session, msg)

        deleted = await delete_message(session, "msg-1", "user-1")
        assert deleted is True
        assert msg.deleted_at is not None

    @pytest.mark.asyncio
    async def test_delete_when_not_sender_should_return_false(self):
        session = _mock_session()
        _setup_execute(session, None)

        deleted = await delete_message(session, "msg-1", "user-999")
        assert deleted is False


class TestGetMessageHistory:
    @pytest.mark.asyncio
    async def test_history_when_messages_exist_should_return_paginated(self):
        session = _mock_session()
        msgs = [_mock_message(id=f"msg-{i}") for i in range(3)]
        result = MagicMock()
        result.scalars.return_value.all.return_value = msgs
        session.execute = AsyncMock(return_value=result)

        data = await get_message_history(session, "chan-1", limit=50)
        assert len(data["messages"]) == 3
        assert data["has_more"] is False
        assert data["cursor"] == "msg-2"

    @pytest.mark.asyncio
    async def test_history_when_has_more_should_flag_true(self):
        session = _mock_session()
        # Return limit+1 messages to trigger has_more
        msgs = [_mock_message(id=f"msg-{i}") for i in range(4)]
        result = MagicMock()
        result.scalars.return_value.all.return_value = msgs
        session.execute = AsyncMock(return_value=result)

        data = await get_message_history(session, "chan-1", limit=3)
        assert data["has_more"] is True
        assert len(data["messages"]) == 3

    @pytest.mark.asyncio
    async def test_history_when_empty_should_return_no_cursor(self):
        session = _mock_session()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)

        data = await get_message_history(session, "chan-1")
        assert data["messages"] == []
        assert data["has_more"] is False
        assert data["cursor"] is None

    @pytest.mark.asyncio
    async def test_history_when_limit_exceeds_max_should_clamp(self):
        session = _mock_session()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)

        # Should not crash even with huge limit
        data = await get_message_history(session, "chan-1", limit=500)
        assert data["messages"] == []


# ---------------------------------------------------------------------------
# Read Receipts
# ---------------------------------------------------------------------------


class TestReadReceipts:
    @pytest.mark.asyncio
    async def test_mark_read_when_first_time_should_create_receipt(self):
        session = _mock_session()
        _setup_execute(session, None)  # No existing receipt

        result = await mark_read(session, "msg-1", "user-2")
        assert result is True
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_read_when_already_read_should_be_idempotent(self):
        session = _mock_session()
        existing = _mock_receipt()
        _setup_execute(session, existing)

        result = await mark_read(session, "msg-1", "user-2")
        assert result is True
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_read_receipts_should_return_list(self):
        session = _mock_session()
        receipts = [_mock_receipt(user_id="u1"), _mock_receipt(user_id="u2")]
        result = MagicMock()
        result.scalars.return_value.all.return_value = receipts
        session.execute = AsyncMock(return_value=result)

        result_list = await get_read_receipts(session, "msg-1")
        assert len(result_list) == 2
        assert result_list[0]["user_id"] == "u1"


# ---------------------------------------------------------------------------
# WebSocket event builders
# ---------------------------------------------------------------------------


class TestEventBuilders:
    def test_build_typing_event_should_have_correct_shape(self):
        event = build_typing_event("chan-1", "user-1", True)
        assert event["type"] == "chat_typing"
        assert event["channel_id"] == "chan-1"
        assert event["user_id"] == "user-1"
        assert event["is_typing"] is True
        assert "timestamp" in event

    def test_build_typing_event_when_not_typing_should_flag_false(self):
        event = build_typing_event("chan-1", "user-1", False)
        assert event["is_typing"] is False

    def test_build_chat_message_event_should_wrap_message(self):
        msg = {"id": "msg-1", "content": "Hello"}
        event = build_chat_message_event(msg)
        assert event["type"] == "chat_message"
        assert event["message"]["id"] == "msg-1"
        assert "timestamp" in event


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_page_size_is_50(self):
        assert _DEFAULT_PAGE_SIZE == 50

    def test_max_page_size_is_200(self):
        assert _MAX_PAGE_SIZE == 200
