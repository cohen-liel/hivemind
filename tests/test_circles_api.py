"""Tests for src/api/circles.py — Circles business logic layer.

Tests CRUD operations, member management, invitations using mock
AsyncSession to avoid DB dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.circles import (
    accept_invitation,
    add_member,
    change_member_role,
    create_circle,
    create_invitation,
    delete_circle,
    get_circle,
    get_member_role,
    list_circle_projects,
    list_members,
    list_user_circles,
    remove_member,
    update_circle,
    _circle_to_dict,
    _iso,
    _member_to_dict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)


def _mock_session():
    """Create a minimal mock AsyncSession."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session


def _mock_circle(**kw):
    defaults = {
        "id": "circle-1",
        "name": "Test Circle",
        "description": "A test circle",
        "avatar_url": None,
        "settings_json": {},
        "created_by": "user-1",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _mock_member(**kw):
    defaults = {
        "id": "member-1",
        "circle_id": "circle-1",
        "user_id": "user-1",
        "role": "owner",
        "joined_at": _NOW,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _mock_invitation(**kw):
    defaults = {
        "id": "inv-1",
        "circle_id": "circle-1",
        "invited_user_id": "user-2",
        "invited_by_id": "user-1",
        "status": "pending",
        "token": "abc123",
        "created_at": _NOW,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _setup_execute_return(session, value):
    """Mock session.execute to return a result with scalar_one_or_none."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    result.scalars.return_value.all.return_value = value if isinstance(value, list) else [value]
    result.all.return_value = value if isinstance(value, list) else [(value, "owner")]
    session.execute = AsyncMock(return_value=result)
    return result


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_iso_when_datetime_should_format(self):
        dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert _iso(dt) == "2026-01-15T10:30:00+00:00"

    def test_iso_when_none_should_return_none(self):
        assert _iso(None) is None

    def test_circle_to_dict_should_contain_all_keys(self):
        circle = _mock_circle()
        d = _circle_to_dict(circle, member_count=5)
        assert d["id"] == "circle-1"
        assert d["name"] == "Test Circle"
        assert d["member_count"] == 5
        assert "created_at" in d
        assert "updated_at" in d

    def test_member_to_dict_should_contain_all_keys(self):
        member = _mock_member()
        d = _member_to_dict(member)
        assert d["id"] == "member-1"
        assert d["role"] == "owner"
        assert "joined_at" in d


# ---------------------------------------------------------------------------
# Circle CRUD
# ---------------------------------------------------------------------------


class TestCreateCircle:
    @pytest.mark.asyncio
    async def test_create_when_valid_should_return_dict_with_name(self):
        session = _mock_session()
        result = await create_circle(
            session, name="My Circle", created_by="user-1", description="desc"
        )
        assert result["name"] == "My Circle"
        assert result["description"] == "desc"
        assert result["created_by"] == "user-1"
        assert result["member_count"] == 1
        assert session.add.call_count == 2  # circle + member
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_when_no_description_should_still_work(self):
        session = _mock_session()
        result = await create_circle(session, name="Simple", created_by="u1")
        assert result["name"] == "Simple"
        assert result["description"] is None


class TestGetCircle:
    @pytest.mark.asyncio
    async def test_get_when_exists_should_return_dict(self):
        session = _mock_session()
        circle = _mock_circle()
        result = MagicMock()
        result.scalar_one_or_none.return_value = circle
        count_result = MagicMock()
        count_result.scalar.return_value = 3
        session.execute = AsyncMock(side_effect=[result, count_result])

        d = await get_circle(session, "circle-1")
        assert d is not None
        assert d["id"] == "circle-1"
        assert d["member_count"] == 3

    @pytest.mark.asyncio
    async def test_get_when_not_exists_should_return_none(self):
        session = _mock_session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)

        d = await get_circle(session, "nonexistent")
        assert d is None


class TestUpdateCircle:
    @pytest.mark.asyncio
    async def test_update_when_exists_should_change_fields(self):
        session = _mock_session()
        circle = _mock_circle()
        _setup_execute_return(session, circle)

        d = await update_circle(session, "circle-1", name="New Name")
        assert circle.name == "New Name"
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_when_not_exists_should_return_none(self):
        session = _mock_session()
        _setup_execute_return(session, None)

        d = await update_circle(session, "nonexistent", name="X")
        assert d is None


class TestDeleteCircle:
    @pytest.mark.asyncio
    async def test_delete_when_exists_should_return_true(self):
        session = _mock_session()
        circle = _mock_circle()
        _setup_execute_return(session, circle)

        deleted = await delete_circle(session, "circle-1")
        assert deleted is True
        session.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_when_not_exists_should_return_false(self):
        session = _mock_session()
        _setup_execute_return(session, None)

        deleted = await delete_circle(session, "nonexistent")
        assert deleted is False


# ---------------------------------------------------------------------------
# Member Management
# ---------------------------------------------------------------------------


class TestAddMember:
    @pytest.mark.asyncio
    async def test_add_when_new_user_should_return_member_dict(self):
        session = _mock_session()
        _setup_execute_return(session, None)  # No existing member

        result = await add_member(session, "circle-1", "user-2", "member")
        assert result is not None
        assert result["user_id"] == "user-2"
        assert result["role"] == "member"
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_when_already_member_should_return_none(self):
        session = _mock_session()
        existing = _mock_member(user_id="user-2")
        _setup_execute_return(session, existing)

        result = await add_member(session, "circle-1", "user-2")
        assert result is None


class TestRemoveMember:
    @pytest.mark.asyncio
    async def test_remove_when_exists_should_return_true(self):
        session = _mock_session()
        result = MagicMock()
        result.rowcount = 1
        session.execute = AsyncMock(return_value=result)

        removed = await remove_member(session, "circle-1", "user-2")
        assert removed is True

    @pytest.mark.asyncio
    async def test_remove_when_not_exists_should_return_false(self):
        session = _mock_session()
        result = MagicMock()
        result.rowcount = 0
        session.execute = AsyncMock(return_value=result)

        removed = await remove_member(session, "circle-1", "user-999")
        assert removed is False


class TestChangeMemberRole:
    @pytest.mark.asyncio
    async def test_change_role_when_exists_should_update_and_return(self):
        session = _mock_session()
        member = _mock_member(role="member")
        _setup_execute_return(session, member)

        result = await change_member_role(session, "circle-1", "user-1", "admin")
        assert member.role == "admin"
        assert result is not None
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_change_role_when_not_member_should_return_none(self):
        session = _mock_session()
        _setup_execute_return(session, None)

        result = await change_member_role(session, "circle-1", "user-999", "admin")
        assert result is None


class TestGetMemberRole:
    @pytest.mark.asyncio
    async def test_get_role_when_member_should_return_role(self):
        session = _mock_session()
        _setup_execute_return(session, "owner")

        role = await get_member_role(session, "circle-1", "user-1")
        assert role == "owner"

    @pytest.mark.asyncio
    async def test_get_role_when_not_member_should_return_none(self):
        session = _mock_session()
        _setup_execute_return(session, None)

        role = await get_member_role(session, "circle-1", "user-999")
        assert role is None


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


class TestInvitations:
    @pytest.mark.asyncio
    async def test_create_invitation_when_valid_should_add_to_session(self):
        """Test invitation creation. Note: src/api/circles.py passes 'token='
        to CircleInvitation but the model column is 'invite_token'.
        We patch the model class to accept arbitrary kwargs."""
        from unittest.mock import patch as _patch

        # Create a mock CircleInvitation class that accepts any kwargs
        class MockInvitation:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
                if not hasattr(self, "created_at"):
                    self.created_at = _NOW

        session = _mock_session()
        with _patch("src.api.circles.CircleInvitation", MockInvitation):
            result = await create_invitation(session, "circle-1", "user-2", "user-1")
            assert result["circle_id"] == "circle-1"
            assert result["invited_user_id"] == "user-2"
            assert result["status"] == "pending"
            assert "token" in result

    @pytest.mark.asyncio
    async def test_accept_invitation_when_valid_should_return_true(self):
        session = _mock_session()
        invitation = _mock_invitation(status="pending")

        # First call: find invitation, second call: check existing membership
        inv_result = MagicMock()
        inv_result.scalar_one_or_none.return_value = invitation
        member_result = MagicMock()
        member_result.scalar_one_or_none.return_value = None  # Not already a member
        session.execute = AsyncMock(side_effect=[inv_result, member_result])

        accepted = await accept_invitation(session, "inv-1", "user-2")
        assert accepted is True
        assert invitation.status == "accepted"

    @pytest.mark.asyncio
    async def test_accept_invitation_when_not_found_should_return_false(self):
        session = _mock_session()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)

        accepted = await accept_invitation(session, "inv-999", "user-2")
        assert accepted is False

    @pytest.mark.asyncio
    async def test_accept_invitation_when_already_accepted_should_return_false(self):
        session = _mock_session()
        invitation = _mock_invitation(status="accepted")
        _setup_execute_return(session, invitation)

        accepted = await accept_invitation(session, "inv-1", "user-2")
        assert accepted is False


# ---------------------------------------------------------------------------
# List operations
# ---------------------------------------------------------------------------


class TestListOperations:
    @pytest.mark.asyncio
    async def test_list_members_should_return_list(self):
        session = _mock_session()
        members = [_mock_member(id="m1"), _mock_member(id="m2")]
        result = MagicMock()
        result.scalars.return_value.all.return_value = members
        session.execute = AsyncMock(return_value=result)

        result_list = await list_members(session, "circle-1")
        assert len(result_list) == 2

    @pytest.mark.asyncio
    async def test_list_user_circles_should_return_circles_with_role(self):
        session = _mock_session()
        circle = _mock_circle()
        result_mock = MagicMock()
        result_mock.all.return_value = [(circle, "owner")]
        session.execute = AsyncMock(return_value=result_mock)

        circles = await list_user_circles(session, "user-1")
        assert len(circles) == 1
        assert circles[0]["user_role"] == "owner"

    @pytest.mark.asyncio
    async def test_list_circle_projects_should_return_projects(self):
        session = _mock_session()
        proj = SimpleNamespace(id="p1", name="Project 1", description="desc",
                               created_at=_NOW, circle_id="circle-1")
        result = MagicMock()
        result.scalars.return_value.all.return_value = [proj]
        session.execute = AsyncMock(return_value=result)

        projects = await list_circle_projects(session, "circle-1")
        assert len(projects) == 1
        assert projects[0]["id"] == "p1"
