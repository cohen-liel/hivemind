"""Chat — business logic layer for real-time messaging.

Provides async functions for channel management, message CRUD, read receipts,
and typing indicator payloads. Router endpoints in dashboard/routers/chat.py
and the WebSocket handler call these functions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ChatChannel, ChatMessage, MessageReadReceipt

logger = logging.getLogger(__name__)

# Default page size for message history
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200


# ---------------------------------------------------------------------------
# Channel Management
# ---------------------------------------------------------------------------


async def create_channel(
    session: AsyncSession,
    *,
    name: str,
    channel_type: str = "circle",
    created_by: str,
    circle_id: str | None = None,
    project_id: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a new chat channel."""
    channel = ChatChannel(
        id=str(uuid.uuid4()),
        name=name,
        channel_type=channel_type,
        circle_id=circle_id,
        project_id=project_id,
        description=description,
        created_by=created_by,
        is_archived=False,
    )
    session.add(channel)
    await session.flush()

    logger.info("[Chat] Created channel '%s' (id=%s) type=%s", name, channel.id, channel_type)
    return _channel_to_dict(channel)


async def get_channel(session: AsyncSession, channel_id: str) -> dict[str, Any] | None:
    """Get a single channel by ID."""
    stmt = select(ChatChannel).where(ChatChannel.id == channel_id)
    result = await session.execute(stmt)
    channel = result.scalar_one_or_none()
    if channel is None:
        return None
    return _channel_to_dict(channel)


async def list_channels(
    session: AsyncSession,
    *,
    circle_id: str | None = None,
    project_id: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """List channels, optionally filtered by circle or project."""
    stmt = select(ChatChannel)

    conditions = []
    if circle_id is not None:
        conditions.append(ChatChannel.circle_id == circle_id)
    if project_id is not None:
        conditions.append(ChatChannel.project_id == project_id)
    if not include_archived:
        conditions.append(ChatChannel.is_archived == False)  # noqa: E712

    if conditions:
        stmt = stmt.where(and_(*conditions))

    stmt = stmt.order_by(ChatChannel.created_at.desc())
    result = await session.execute(stmt)
    channels = result.scalars().all()
    return [_channel_to_dict(c) for c in channels]


async def archive_channel(session: AsyncSession, channel_id: str) -> bool:
    """Archive a channel. Returns True if updated."""
    stmt = select(ChatChannel).where(ChatChannel.id == channel_id)
    result = await session.execute(stmt)
    channel = result.scalar_one_or_none()
    if channel is None:
        return False
    channel.is_archived = True
    await session.flush()
    logger.info("[Chat] Archived channel %s", channel_id)
    return True


async def unarchive_channel(session: AsyncSession, channel_id: str) -> bool:
    """Unarchive a channel. Returns True if updated."""
    stmt = select(ChatChannel).where(ChatChannel.id == channel_id)
    result = await session.execute(stmt)
    channel = result.scalar_one_or_none()
    if channel is None:
        return False
    channel.is_archived = False
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def send_message(
    session: AsyncSession,
    *,
    channel_id: str,
    sender_id: str,
    content: str,
    message_type: str = "text",
    parent_message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a message to a channel."""
    msg = ChatMessage(
        id=str(uuid.uuid4()),
        channel_id=channel_id,
        sender_id=sender_id,
        content=content,
        message_type=message_type,
        parent_message_id=parent_message_id,
        metadata_json=metadata,
    )
    session.add(msg)
    await session.flush()

    return _message_to_dict(msg)


async def get_message_history(
    session: AsyncSession,
    channel_id: str,
    *,
    before: str | None = None,
    after: str | None = None,
    limit: int = _DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Get paginated message history for a channel.

    Uses cursor-based pagination (before/after message IDs).
    Returns messages in reverse chronological order (newest first).
    """
    limit = min(limit, _MAX_PAGE_SIZE)

    stmt = select(ChatMessage).where(
        and_(
            ChatMessage.channel_id == channel_id,
            ChatMessage.deleted_at.is_(None),
        )
    )

    if before:
        # Get the created_at of the cursor message
        cursor_stmt = select(ChatMessage.created_at).where(ChatMessage.id == before)
        cursor_result = await session.execute(cursor_stmt)
        cursor_dt = cursor_result.scalar_one_or_none()
        if cursor_dt:
            stmt = stmt.where(ChatMessage.created_at < cursor_dt)

    if after:
        cursor_stmt = select(ChatMessage.created_at).where(ChatMessage.id == after)
        cursor_result = await session.execute(cursor_stmt)
        cursor_dt = cursor_result.scalar_one_or_none()
        if cursor_dt:
            stmt = stmt.where(ChatMessage.created_at > cursor_dt)

    stmt = stmt.order_by(ChatMessage.created_at.desc()).limit(limit + 1)
    result = await session.execute(stmt)
    messages = result.scalars().all()

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    return {
        "messages": [_message_to_dict(m) for m in messages],
        "has_more": has_more,
        "cursor": messages[-1].id if messages else None,
    }


async def edit_message(
    session: AsyncSession,
    message_id: str,
    sender_id: str,
    new_content: str,
) -> dict[str, Any] | None:
    """Edit a message. Only the sender can edit. Returns updated message or None."""
    stmt = select(ChatMessage).where(
        and_(ChatMessage.id == message_id, ChatMessage.sender_id == sender_id)
    )
    result = await session.execute(stmt)
    msg = result.scalar_one_or_none()
    if msg is None:
        return None

    msg.content = new_content
    msg.is_edited = True
    await session.flush()
    return _message_to_dict(msg)


async def delete_message(
    session: AsyncSession,
    message_id: str,
    sender_id: str,
) -> bool:
    """Soft-delete a message. Only the sender can delete."""
    stmt = select(ChatMessage).where(
        and_(ChatMessage.id == message_id, ChatMessage.sender_id == sender_id)
    )
    result = await session.execute(stmt)
    msg = result.scalar_one_or_none()
    if msg is None:
        return False

    msg.deleted_at = datetime.now(timezone.utc)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# Read Receipts
# ---------------------------------------------------------------------------


async def mark_read(
    session: AsyncSession,
    message_id: str,
    user_id: str,
) -> bool:
    """Mark a message as read by a user. Idempotent."""
    # Check if already read
    stmt = select(MessageReadReceipt).where(
        and_(
            MessageReadReceipt.message_id == message_id,
            MessageReadReceipt.user_id == user_id,
        )
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is not None:
        return True  # Already marked

    receipt = MessageReadReceipt(
        id=str(uuid.uuid4()),
        message_id=message_id,
        user_id=user_id,
    )
    session.add(receipt)
    await session.flush()
    return True


async def get_read_receipts(
    session: AsyncSession,
    message_id: str,
) -> list[dict[str, Any]]:
    """Get all read receipts for a message."""
    stmt = select(MessageReadReceipt).where(MessageReadReceipt.message_id == message_id)
    result = await session.execute(stmt)
    receipts = result.scalars().all()
    return [
        {
            "user_id": r.user_id,
            "read_at": _iso(r.read_at),
        }
        for r in receipts
    ]


# ---------------------------------------------------------------------------
# Typing Indicator (WebSocket-only, not persisted)
# ---------------------------------------------------------------------------


def build_typing_event(
    channel_id: str,
    user_id: str,
    is_typing: bool,
) -> dict[str, Any]:
    """Build a typing indicator event for WebSocket broadcast."""
    import time

    return {
        "type": "chat_typing",
        "channel_id": channel_id,
        "user_id": user_id,
        "is_typing": is_typing,
        "timestamp": time.time(),
    }


def build_chat_message_event(message: dict[str, Any]) -> dict[str, Any]:
    """Build a chat message event for WebSocket broadcast."""
    import time

    return {
        "type": "chat_message",
        "message": message,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _channel_to_dict(channel: ChatChannel) -> dict[str, Any]:
    return {
        "id": channel.id,
        "name": channel.name,
        "channel_type": channel.channel_type,
        "circle_id": channel.circle_id,
        "project_id": channel.project_id,
        "description": channel.description,
        "is_archived": channel.is_archived,
        "created_by": channel.created_by,
        "created_at": _iso(channel.created_at),
    }


def _message_to_dict(msg: ChatMessage) -> dict[str, Any]:
    return {
        "id": msg.id,
        "channel_id": msg.channel_id,
        "sender_id": msg.sender_id,
        "content": msg.content,
        "message_type": msg.message_type,
        "parent_message_id": msg.parent_message_id,
        "is_edited": msg.is_edited,
        "metadata": msg.metadata_json,
        "created_at": _iso(msg.created_at),
    }


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()
