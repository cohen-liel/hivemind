"""Circles management API router.

Provides REST endpoints for Circle CRUD, member management, and related queries.
All responses use RFC 7807 Problem Detail format for errors.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from dashboard.routers import _problem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/circles", tags=["circles"])


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class CreateCircleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Circle display name")
    description: str | None = Field(None, max_length=2000)
    avatar_url: str | None = Field(None, max_length=1024)
    settings: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return v.strip()


class UpdateCircleRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    avatar_url: str | None = Field(None, max_length=1024)
    settings: dict[str, Any] | None = None


class AddMemberRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    role: str = Field(default="member", pattern=r"^(owner|admin|member|viewer)$")


class ChangeMemberRoleRequest(BaseModel):
    role: str = Field(..., pattern=r"^(owner|admin|member|viewer)$")


class InviteMemberRequest(BaseModel):
    user_id: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------


async def _get_session():
    """Get an async DB session."""
    from src.db.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        yield session


def _get_user_id(request: Request) -> str:
    """Extract user ID from request state (set by auth middleware)."""
    return getattr(request.state, "user_id", "anonymous")


# ---------------------------------------------------------------------------
# Circle CRUD
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_circle(request: Request, body: CreateCircleRequest):
    """Create a new circle. The creator is automatically added as owner."""
    from src.api.circles import create_circle as _create
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await _create(
                session,
                name=body.name,
                created_by=user_id,
                description=body.description,
                avatar_url=body.avatar_url,
                settings=body.settings,
            )
            await session.commit()
            return result
    except Exception as exc:
        logger.error("Failed to create circle: %s", exc, exc_info=True)
        return _problem(500, "Failed to create circle")


@router.get("")
async def list_circles(request: Request):
    """List all circles the current user belongs to."""
    from src.api.circles import list_user_circles
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            circles = await list_user_circles(session, user_id)
            return {"circles": circles}
    except Exception as exc:
        logger.error("Failed to list circles: %s", exc, exc_info=True)
        return _problem(500, "Failed to list circles")


@router.get("/{circle_id}")
async def get_circle(circle_id: str):
    """Get a single circle by ID."""
    from src.api.circles import get_circle as _get
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            circle = await _get(session, circle_id)
            if circle is None:
                return _problem(404, f"Circle '{circle_id}' not found")
            return circle
    except Exception as exc:
        logger.error("Failed to get circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to get circle")


@router.patch("/{circle_id}")
async def update_circle(circle_id: str, body: UpdateCircleRequest, request: Request):
    """Update a circle's fields. Requires admin or owner role."""
    from src.api.circles import get_member_role, update_circle as _update
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            role = await get_member_role(session, circle_id, user_id)
            if role not in ("owner", "admin"):
                return _problem(403, "Only circle owners and admins can update circle settings")

            result = await _update(
                session,
                circle_id,
                name=body.name,
                description=body.description,
                avatar_url=body.avatar_url,
                settings=body.settings,
            )
            if result is None:
                return _problem(404, f"Circle '{circle_id}' not found")
            await session.commit()
            return result
    except Exception as exc:
        logger.error("Failed to update circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to update circle")


@router.delete("/{circle_id}", status_code=204)
async def delete_circle(circle_id: str, request: Request):
    """Delete a circle. Requires owner role."""
    from src.api.circles import delete_circle as _delete, get_member_role
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            role = await get_member_role(session, circle_id, user_id)
            if role != "owner":
                return _problem(403, "Only circle owners can delete circles")

            deleted = await _delete(session, circle_id)
            if not deleted:
                return _problem(404, f"Circle '{circle_id}' not found")
            await session.commit()
            return None
    except Exception as exc:
        logger.error("Failed to delete circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to delete circle")


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{circle_id}/members")
async def list_members(circle_id: str):
    """List all members of a circle."""
    from src.api.circles import list_members as _list
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            members = await _list(session, circle_id)
            return {"members": members}
    except Exception as exc:
        logger.error("Failed to list members for circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to list members")


@router.post("/{circle_id}/members", status_code=201)
async def add_member(circle_id: str, body: AddMemberRequest, request: Request):
    """Add a member to a circle. Requires admin or owner role."""
    from src.api.circles import add_member as _add, get_member_role
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            role = await get_member_role(session, circle_id, user_id)
            if role not in ("owner", "admin"):
                return _problem(403, "Only circle owners and admins can add members")

            result = await _add(session, circle_id, body.user_id, body.role)
            if result is None:
                return _problem(409, "User is already a member of this circle")
            await session.commit()
            return result
    except Exception as exc:
        logger.error("Failed to add member to circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to add member")


@router.delete("/{circle_id}/members/{user_id}")
async def remove_member(circle_id: str, user_id: str, request: Request):
    """Remove a member from a circle. Requires admin or owner role."""
    from src.api.circles import remove_member as _remove, get_member_role
    from src.db.database import get_session_factory

    requesting_user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            role = await get_member_role(session, circle_id, requesting_user_id)
            if role not in ("owner", "admin") and requesting_user_id != user_id:
                return _problem(403, "Only circle owners/admins can remove other members")

            removed = await _remove(session, circle_id, user_id)
            if not removed:
                return _problem(404, "Member not found in circle")
            await session.commit()
            return {"status": "removed"}
    except Exception as exc:
        logger.error("Failed to remove member from circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to remove member")


@router.patch("/{circle_id}/members/{user_id}/role")
async def change_role(circle_id: str, user_id: str, body: ChangeMemberRoleRequest, request: Request):
    """Change a member's role. Requires owner role."""
    from src.api.circles import change_member_role, get_member_role
    from src.db.database import get_session_factory

    requesting_user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            role = await get_member_role(session, circle_id, requesting_user_id)
            if role != "owner":
                return _problem(403, "Only circle owners can change member roles")

            result = await change_member_role(session, circle_id, user_id, body.role)
            if result is None:
                return _problem(404, "Member not found in circle")
            await session.commit()
            return result
    except Exception as exc:
        logger.error("Failed to change role in circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to change role")


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@router.post("/{circle_id}/invitations", status_code=201)
async def invite_member(circle_id: str, body: InviteMemberRequest, request: Request):
    """Send an invitation to a user to join the circle."""
    from src.api.circles import create_invitation, get_member_role
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            role = await get_member_role(session, circle_id, user_id)
            if role not in ("owner", "admin"):
                return _problem(403, "Only circle owners and admins can send invitations")

            result = await create_invitation(session, circle_id, body.user_id, user_id)
            await session.commit()
            return result
    except Exception as exc:
        logger.error("Failed to create invitation for circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to create invitation")


@router.post("/invitations/{invitation_id}/accept")
async def accept_invitation(invitation_id: str, request: Request):
    """Accept a circle invitation."""
    from src.api.circles import accept_invitation as _accept
    from src.db.database import get_session_factory

    user_id = _get_user_id(request)
    factory = get_session_factory()
    try:
        async with factory() as session:
            accepted = await _accept(session, invitation_id, user_id)
            if not accepted:
                return _problem(404, "Invitation not found or already used")
            await session.commit()
            return {"status": "accepted"}
    except Exception as exc:
        logger.error("Failed to accept invitation %s: %s", invitation_id, exc, exc_info=True)
        return _problem(500, "Failed to accept invitation")


# ---------------------------------------------------------------------------
# Circle Projects
# ---------------------------------------------------------------------------


@router.get("/{circle_id}/projects")
async def list_projects(circle_id: str):
    """List all projects in a circle."""
    from src.api.circles import list_circle_projects
    from src.db.database import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as session:
            projects = await list_circle_projects(session, circle_id)
            return {"projects": projects}
    except Exception as exc:
        logger.error("Failed to list projects for circle %s: %s", circle_id, exc, exc_info=True)
        return _problem(500, "Failed to list projects")
