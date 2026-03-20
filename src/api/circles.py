"""Circles management — business logic layer.

Provides async functions for Circle CRUD, member management, and invitation
handling. Router endpoints in dashboard/routers/circles.py call these
functions. All DB access uses SQLAlchemy async sessions.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import Circle, CircleInvitation, CircleMember, Project

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circle CRUD
# ---------------------------------------------------------------------------


async def create_circle(
    session: AsyncSession,
    *,
    name: str,
    created_by: str,
    description: str | None = None,
    avatar_url: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new circle and add the creator as owner."""
    circle = Circle(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        avatar_url=avatar_url,
        settings_json=settings or {},
        created_by=created_by,
    )
    session.add(circle)

    # Add creator as owner
    member = CircleMember(
        id=str(uuid.uuid4()),
        circle_id=circle.id,
        user_id=created_by,
        role="owner",
    )
    session.add(member)
    await session.flush()

    logger.info("[Circles] Created circle '%s' (id=%s) by user %s", name, circle.id, created_by)
    return _circle_to_dict(circle, member_count=1)


async def get_circle(session: AsyncSession, circle_id: str) -> dict[str, Any] | None:
    """Get a single circle by ID with member count."""
    stmt = select(Circle).where(Circle.id == circle_id)
    result = await session.execute(stmt)
    circle = result.scalar_one_or_none()
    if circle is None:
        return None

    count_stmt = select(func.count()).select_from(CircleMember).where(CircleMember.circle_id == circle_id)
    count_result = await session.execute(count_stmt)
    member_count = count_result.scalar() or 0

    return _circle_to_dict(circle, member_count=member_count)


async def update_circle(
    session: AsyncSession,
    circle_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    avatar_url: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update circle fields. Returns updated circle or None if not found."""
    stmt = select(Circle).where(Circle.id == circle_id)
    result = await session.execute(stmt)
    circle = result.scalar_one_or_none()
    if circle is None:
        return None

    if name is not None:
        circle.name = name
    if description is not None:
        circle.description = description
    if avatar_url is not None:
        circle.avatar_url = avatar_url
    if settings is not None:
        circle.settings_json = settings

    await session.flush()
    return _circle_to_dict(circle)


async def delete_circle(session: AsyncSession, circle_id: str) -> bool:
    """Delete a circle. Returns True if deleted, False if not found."""
    stmt = select(Circle).where(Circle.id == circle_id)
    result = await session.execute(stmt)
    circle = result.scalar_one_or_none()
    if circle is None:
        return False

    await session.delete(circle)
    await session.flush()
    logger.info("[Circles] Deleted circle %s", circle_id)
    return True


async def list_user_circles(session: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    """List all circles a user belongs to."""
    stmt = (
        select(Circle, CircleMember.role)
        .join(CircleMember, CircleMember.circle_id == Circle.id)
        .where(CircleMember.user_id == user_id)
        .order_by(Circle.created_at.desc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    circles = []
    for circle, role in rows:
        d = _circle_to_dict(circle)
        d["user_role"] = role
        circles.append(d)
    return circles


async def list_circle_projects(
    session: AsyncSession, circle_id: str
) -> list[dict[str, Any]]:
    """List all projects in a circle."""
    stmt = select(Project).where(Project.circle_id == circle_id).order_by(Project.created_at.desc())
    result = await session.execute(stmt)
    projects = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": getattr(p, "description", None),
            "created_at": _iso(p.created_at),
        }
        for p in projects
    ]


# ---------------------------------------------------------------------------
# Member Management
# ---------------------------------------------------------------------------


async def list_members(session: AsyncSession, circle_id: str) -> list[dict[str, Any]]:
    """List all members of a circle."""
    stmt = select(CircleMember).where(CircleMember.circle_id == circle_id).order_by(CircleMember.joined_at)
    result = await session.execute(stmt)
    members = result.scalars().all()
    return [_member_to_dict(m) for m in members]


async def add_member(
    session: AsyncSession,
    circle_id: str,
    user_id: str,
    role: str = "member",
) -> dict[str, Any] | None:
    """Add a user to a circle. Returns member dict or None if already exists."""
    # Check for existing membership
    stmt = select(CircleMember).where(
        and_(CircleMember.circle_id == circle_id, CircleMember.user_id == user_id)
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        return None  # Already a member

    member = CircleMember(
        id=str(uuid.uuid4()),
        circle_id=circle_id,
        user_id=user_id,
        role=role,
    )
    session.add(member)
    await session.flush()
    return _member_to_dict(member)


async def remove_member(session: AsyncSession, circle_id: str, user_id: str) -> bool:
    """Remove a user from a circle. Returns True if removed."""
    stmt = delete(CircleMember).where(
        and_(CircleMember.circle_id == circle_id, CircleMember.user_id == user_id)
    )
    result = await session.execute(stmt)
    return result.rowcount > 0


async def change_member_role(
    session: AsyncSession, circle_id: str, user_id: str, new_role: str
) -> dict[str, Any] | None:
    """Change a member's role. Returns updated member or None."""
    stmt = select(CircleMember).where(
        and_(CircleMember.circle_id == circle_id, CircleMember.user_id == user_id)
    )
    result = await session.execute(stmt)
    member = result.scalar_one_or_none()
    if member is None:
        return None

    member.role = new_role
    await session.flush()
    return _member_to_dict(member)


async def get_member_role(session: AsyncSession, circle_id: str, user_id: str) -> str | None:
    """Get a user's role in a circle, or None if not a member."""
    stmt = select(CircleMember.role).where(
        and_(CircleMember.circle_id == circle_id, CircleMember.user_id == user_id)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


async def create_invitation(
    session: AsyncSession,
    circle_id: str,
    invited_user_id: str,
    invited_by_id: str,
) -> dict[str, Any]:
    """Create an invitation for a user to join a circle."""
    invitation = CircleInvitation(
        id=str(uuid.uuid4()),
        circle_id=circle_id,
        invited_user_id=invited_user_id,
        invited_by_id=invited_by_id,
        token=secrets.token_urlsafe(32),
        status="pending",
    )
    session.add(invitation)
    await session.flush()
    return _invitation_to_dict(invitation)


async def accept_invitation(session: AsyncSession, invitation_id: str, user_id: str) -> bool:
    """Accept an invitation. Adds user as member and marks invitation accepted."""
    stmt = select(CircleInvitation).where(
        and_(CircleInvitation.id == invitation_id, CircleInvitation.invited_user_id == user_id)
    )
    result = await session.execute(stmt)
    invitation = result.scalar_one_or_none()
    if invitation is None or invitation.status != "pending":
        return False

    invitation.status = "accepted"
    # Add as member
    await add_member(session, invitation.circle_id, user_id, "member")
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _circle_to_dict(circle: Circle, member_count: int = 0) -> dict[str, Any]:
    return {
        "id": circle.id,
        "name": circle.name,
        "description": circle.description,
        "avatar_url": circle.avatar_url,
        "settings": circle.settings_json,
        "created_by": circle.created_by,
        "created_at": _iso(circle.created_at),
        "updated_at": _iso(circle.updated_at),
        "member_count": member_count,
    }


def _member_to_dict(member: CircleMember) -> dict[str, Any]:
    return {
        "id": member.id,
        "circle_id": member.circle_id,
        "user_id": member.user_id,
        "role": member.role,
        "joined_at": _iso(member.joined_at),
    }


def _invitation_to_dict(invitation: CircleInvitation) -> dict[str, Any]:
    return {
        "id": invitation.id,
        "circle_id": invitation.circle_id,
        "invited_user_id": invitation.invited_user_id,
        "invited_by_id": invitation.invited_by_id,
        "status": invitation.status,
        "token": invitation.token,
        "created_at": _iso(invitation.created_at),
    }


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()
