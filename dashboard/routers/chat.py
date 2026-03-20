"""Chat API router — REST endpoints and WebSocket integration.

Provides REST endpoints for channel management, message history,
read receipts, and WebSocket-based real-time messaging (send, typing).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator

from dashboard.routers import _problem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class CreateChannelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    channel_type: str = Field(default="circle", pattern=r"^(circle|project|dm)$")
    circle_id: str | None = None
    project_id: str | None = None
    description: str | None = Field(None, max_length=2000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return v.strip()


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10_000)
    message_type: str = Field(default="text", pattern=r"^(text|system|file|code)$")
    parent_message_id: str | None = None
    metadata: dict[str, Any] | None = None


class EditMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10_000)


class MarkReadRequest(BaseModel):
    message_id: str = Field(..., min_length=1)


def _get_user_id(request: Request) -> str:
    """Extract user ID from request state (set by auth middleware)."""
    return getattr(request.state, "user_id", "anonymous")


# ---------------------------------------------------------------------------
# Channel Endpoints
# ---------------------------------------------------------------------------


@router.post("/channels", status_code=201)
async def create_channel(request: Request, body: CreateChannelRequest):
    """Create a new chat channel."""
    from src.api.chat import create_channel as _create
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await _create(
                session,
                name=body.name,
                channel_type=body.channel_type,
                created_by=user_id,
                circle_id=body.circle_id,
                project_id=body.project_id,
                description=body.description,
            )
            await session.commit()
            return result
    except Exception as exc:
        logger.error("Failed to create channel: %s", exc, exc_info=True)
        return _problem(500, "Failed to create channel")


@router.get("/channels")
async def list_channels(
    circle_id: str | None = Query(None),
    project_id: str | None = Query(None),
    include_archived: bool = Query(False),
):
    """List chat channels, optionally filtered by circle or project."""
    from src.api.chat import list_channels as _list
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            channels = await _list(
                session,
                circle_id=circle_id,
                project_id=project_id,
                include_archived=include_archived,
            )
            return {"channels": channels}
    except Exception as exc:
        logger.error("Failed to list channels: %s", exc, exc_info=True)
        return _problem(500, "Failed to list channels")


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: str):
    """Get a single channel by ID."""
    from src.api.chat import get_channel as _get
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            channel = await _get(session, channel_id)
            if channel is None:
                return _problem(404, f"Channel '{channel_id}' not found")
            return channel
    except Exception as exc:
        logger.error("Failed to get channel %s: %s", channel_id, exc, exc_info=True)
        return _problem(500, "Failed to get channel")


@router.post("/channels/{channel_id}/archive")
async def archive_channel(channel_id: str, request: Request):
    """Archive a channel."""
    from src.api.chat import archive_channel as _archive
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            archived = await _archive(session, channel_id)
            if not archived:
                return _problem(404, f"Channel '{channel_id}' not found")
            await session.commit()
            return {"status": "archived"}
    except Exception as exc:
        logger.error("Failed to archive channel %s: %s", channel_id, exc, exc_info=True)
        return _problem(500, "Failed to archive channel")


@router.post("/channels/{channel_id}/unarchive")
async def unarchive_channel(channel_id: str, request: Request):
    """Unarchive a channel."""
    from src.api.chat import unarchive_channel as _unarchive
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            unarchived = await _unarchive(session, channel_id)
            if not unarchived:
                return _problem(404, f"Channel '{channel_id}' not found")
            await session.commit()
            return {"status": "unarchived"}
    except Exception as exc:
        logger.error("Failed to unarchive channel %s: %s", channel_id, exc, exc_info=True)
        return _problem(500, "Failed to unarchive channel")


# ---------------------------------------------------------------------------
# Message Endpoints
# ---------------------------------------------------------------------------


@router.post("/channels/{channel_id}/messages", status_code=201)
async def send_message(channel_id: str, body: SendMessageRequest, request: Request):
    """Send a message to a channel. Also broadcasts via EventBus."""
    from src.api.chat import build_chat_message_event, send_message as _send
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await _send(
                session,
                channel_id=channel_id,
                sender_id=user_id,
                content=body.content,
                message_type=body.message_type,
                parent_message_id=body.parent_message_id,
                metadata=body.metadata,
            )
            await session.commit()

            # Broadcast via EventBus
            try:
                from dashboard.events import event_bus

                event = build_chat_message_event(result)
                event["channel_id"] = channel_id
                await event_bus.publish(event)
            except Exception:
                pass  # Non-critical — message is persisted regardless

            return result
    except Exception as exc:
        logger.error("Failed to send message to channel %s: %s", channel_id, exc, exc_info=True)
        return _problem(500, "Failed to send message")


@router.get("/channels/{channel_id}/messages")
async def get_messages(
    channel_id: str,
    before: str | None = Query(None, description="Cursor: fetch messages before this ID"),
    after: str | None = Query(None, description="Cursor: fetch messages after this ID"),
    limit: int = Query(50, ge=1, le=200),
):
    """Get paginated message history for a channel."""
    from src.api.chat import get_message_history
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await get_message_history(
                session, channel_id, before=before, after=after, limit=limit,
            )
            return result
    except Exception as exc:
        logger.error("Failed to get messages for channel %s: %s", channel_id, exc, exc_info=True)
        return _problem(500, "Failed to get messages")


@router.patch("/messages/{message_id}")
async def edit_message(message_id: str, body: EditMessageRequest, request: Request):
    """Edit a message. Only the sender can edit."""
    from src.api.chat import edit_message as _edit
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await _edit(session, message_id, user_id, body.content)
            if result is None:
                return _problem(404, "Message not found or you are not the sender")
            await session.commit()
            return result
    except Exception as exc:
        logger.error("Failed to edit message %s: %s", message_id, exc, exc_info=True)
        return _problem(500, "Failed to edit message")


@router.delete("/messages/{message_id}")
async def delete_message(message_id: str, request: Request):
    """Soft-delete a message. Only the sender can delete."""
    from src.api.chat import delete_message as _delete
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            deleted = await _delete(session, message_id, user_id)
            if not deleted:
                return _problem(404, "Message not found or you are not the sender")
            await session.commit()
            return {"status": "deleted"}
    except Exception as exc:
        logger.error("Failed to delete message %s: %s", message_id, exc, exc_info=True)
        return _problem(500, "Failed to delete message")


# ---------------------------------------------------------------------------
# Read Receipts
# ---------------------------------------------------------------------------


@router.post("/messages/{message_id}/read")
async def mark_read(message_id: str, request: Request):
    """Mark a message as read. Idempotent."""
    from src.api.chat import mark_read as _mark
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            await _mark(session, message_id, user_id)
            await session.commit()
            return {"status": "read"}
    except Exception as exc:
        logger.error("Failed to mark read for message %s: %s", message_id, exc, exc_info=True)
        return _problem(500, "Failed to mark read")


@router.get("/messages/{message_id}/receipts")
async def get_receipts(message_id: str):
    """Get read receipts for a message."""
    from src.api.chat import get_read_receipts
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            receipts = await get_read_receipts(session, message_id)
            return {"receipts": receipts}
    except Exception as exc:
        logger.error("Failed to get receipts for message %s: %s", message_id, exc, exc_info=True)
        return _problem(500, "Failed to get receipts")


# ---------------------------------------------------------------------------
# WebSocket Chat Handler
# ---------------------------------------------------------------------------


@router.websocket("/ws/{channel_id}")
async def chat_websocket(websocket: WebSocket, channel_id: str):
    """WebSocket endpoint for real-time chat in a channel.

    Supported client messages:
    - {"type": "message", "content": "...", "message_type": "text"}
    - {"type": "typing", "is_typing": true/false}
    - {"type": "read", "message_id": "..."}
    """
    await websocket.accept()

    # Extract user info
    user_id = "anonymous"
    try:
        if hasattr(websocket, "state") and hasattr(websocket.state, "user_id"):
            user_id = websocket.state.user_id
    except Exception:
        pass

    # Subscribe to EventBus for this channel
    subscriber_queue = None
    try:
        import asyncio

        from dashboard.events import event_bus

        subscriber_id = f"chat_{channel_id}_{user_id}"
        subscriber_queue = await event_bus.subscribe(subscriber_id)

        async def _relay_events():
            """Forward matching EventBus events to this WebSocket client."""
            while True:
                try:
                    event = await subscriber_queue.get()
                    if event.get("channel_id") == channel_id:
                        await websocket.send_json(event)
                except asyncio.CancelledError:
                    break
                except Exception:
                    break

        relay_task = asyncio.create_task(_relay_events())

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type", "")

                if msg_type == "message":
                    from src.api.chat import build_chat_message_event, send_message as _send
                    from src.db.database import get_session_factory

                    factory = get_session_factory()
                    async with factory() as session:
                        result = await _send(
                            session,
                            channel_id=channel_id,
                            sender_id=user_id,
                            content=data.get("content", ""),
                            message_type=data.get("message_type", "text"),
                            parent_message_id=data.get("parent_message_id"),
                        )
                        await session.commit()

                    event = build_chat_message_event(result)
                    event["channel_id"] = channel_id
                    await event_bus.publish(event)

                elif msg_type == "typing":
                    from src.api.chat import build_typing_event

                    event = build_typing_event(
                        channel_id, user_id, data.get("is_typing", True)
                    )
                    await event_bus.publish(event)

                elif msg_type == "read":
                    from src.api.chat import mark_read as _mark
                    from src.db.database import get_session_factory

                    message_id = data.get("message_id", "")
                    if message_id:
                        factory = get_session_factory()
                        async with factory() as session:
                            await _mark(session, message_id, user_id)
                            await session.commit()

        except WebSocketDisconnect:
            logger.debug("[Chat WS] Client disconnected: %s", subscriber_id)
        finally:
            relay_task.cancel()
            await event_bus.unsubscribe(subscriber_queue)

    except Exception as exc:
        logger.error("[Chat WS] WebSocket error: %s", exc, exc_info=True)
        try:
            await websocket.close()
        except Exception:
            pass
