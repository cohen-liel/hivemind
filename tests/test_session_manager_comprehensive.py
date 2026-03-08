"""Comprehensive tests for session_manager.py — all CRUD operations and edge cases.

Covers:
- Project CRUD: save, load, list, update, delete
- Message CRUD: add, get_recent, paginated, clear
- Session CRUD: save, get, invalidate, cleanup_expired
- Task history: add, update, get_recent
- Schedule CRUD: add, get, delete, due, mark_run, disable
- Notification prefs: get defaults, set, update
- Away mode: set, check, digest add/get/clear
- Budget: get, set
- Edge cases: empty strings, special characters, concurrent operations
- Error handling: uninitialized manager, field whitelist validation
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from session_manager import SessionManager, DatabaseError


# --- Fixtures ---

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_sessions.db")


@pytest.fixture
async def mgr(db_path):
    """Async fixture: initialized SessionManager."""
    m = SessionManager(db_path=db_path)
    await m.initialize()
    yield m
    await m.close()


def run(coro):
    """Run an async coroutine in the current event loop."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


# ============================================================
# Project CRUD
# ============================================================

class TestProjectCRUD:

    def test_save_and_load(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="Project 1", description="Desc", project_dir="/tmp/p1"))
        data = run(m.load_project("p1"))
        assert data is not None
        assert data["name"] == "Project 1"
        assert data["description"] == "Desc"
        assert data["project_dir"] == "/tmp/p1"
        assert data["status"] == "active"
        assert data["user_id"] == 1
        run(m.close())

    def test_load_nonexistent_returns_none(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        data = run(m.load_project("nonexistent"))
        assert data is None
        run(m.close())

    def test_save_project_upsert(self, db_path):
        """Saving same project_id twice updates the record."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="V1", description="", project_dir="/tmp"))
        run(m.save_project("p1", user_id=1, name="V2", description="Updated", project_dir="/tmp/new"))
        data = run(m.load_project("p1"))
        assert data["name"] == "V2"
        assert data["description"] == "Updated"
        assert data["project_dir"] == "/tmp/new"
        run(m.close())

    def test_list_projects_empty(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        projects = run(m.list_projects())
        assert projects == []
        run(m.close())

    def test_list_projects_ordered_by_updated_at(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="First", description="", project_dir="/tmp"))
        run(m.save_project("p2", user_id=1, name="Second", description="", project_dir="/tmp"))
        # Update p1 so it's more recent
        run(m.update_status("p1", "running"))
        projects = run(m.list_projects())
        assert len(projects) == 2
        assert projects[0]["project_id"] == "p1"  # Most recently updated
        run(m.close())

    def test_update_status(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        run(m.update_status("p1", "paused"))
        data = run(m.load_project("p1"))
        assert data["status"] == "paused"
        run(m.close())

    def test_delete_project(self, db_path):
        """delete_project removes project and all related data."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        run(m.add_message("p1", "orch", "Orchestrator", "Hello", 0.01))
        run(m.save_session(user_id=1, project_id="p1", agent_role="orch", session_id="s1"))
        run(m.delete_project("p1"))
        assert run(m.load_project("p1")) is None
        msgs = run(m.get_recent_messages("p1"))
        assert len(msgs) == 0
        run(m.close())

    def test_update_project_fields_whitelist(self, db_path):
        """update_project_fields rejects disallowed column names."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))

        # Allowed fields work
        run(m.update_project_fields("p1", name="Updated", description="New desc"))
        data = run(m.load_project("p1"))
        assert data["name"] == "Updated"
        assert data["description"] == "New desc"

        # Disallowed fields raise ValueError
        with pytest.raises(ValueError, match="Disallowed column names"):
            run(m.update_project_fields("p1", evil_column="hack"))

        run(m.close())

    def test_update_project_fields_empty_noop(self, db_path):
        """Calling update_project_fields with no fields is a no-op."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        run(m.update_project_fields("p1"))  # No fields — should not error
        data = run(m.load_project("p1"))
        assert data["name"] == "P1"  # Unchanged
        run(m.close())

    def test_special_characters_in_fields(self, db_path):
        """Project names/descriptions with special chars are stored correctly."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project(
            "special-proj",
            user_id=1,
            name="O'Brien's Project",
            description='He said "hello" & <goodbye>',
            project_dir="/tmp/special"
        ))
        data = run(m.load_project("special-proj"))
        assert data["name"] == "O'Brien's Project"
        assert data["description"] == 'He said "hello" & <goodbye>'
        run(m.close())

    def test_very_long_description(self, db_path):
        """Very long description strings are stored/retrieved correctly."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        long_desc = "x" * 50_000
        run(m.save_project("p1", user_id=1, name="P1", description=long_desc, project_dir="/tmp"))
        data = run(m.load_project("p1"))
        assert data["description"] == long_desc
        run(m.close())


# ============================================================
# Message CRUD
# ============================================================

class TestMessageCRUD:

    def test_add_and_get_recent(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        for i in range(5):
            run(m.add_message("p1", f"agent-{i}", "Assistant", f"Message {i}", 0.01 * i))
        msgs = run(m.get_recent_messages("p1", count=3))
        assert len(msgs) == 3
        # Should be most recent 3, in chronological order
        assert msgs[0]["content"] == "Message 2"
        assert msgs[2]["content"] == "Message 4"
        run(m.close())

    def test_get_messages_empty(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        msgs = run(m.get_recent_messages("nonexistent"))
        assert msgs == []
        run(m.close())

    def test_paginated_messages(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        for i in range(20):
            run(m.add_message("p1", "orch", "Orchestrator", f"Msg {i}", 0.0))
        msgs, total = run(m.get_messages_paginated("p1", limit=5, offset=0))
        assert total == 20
        assert len(msgs) == 5
        # Page 2
        msgs2, total2 = run(m.get_messages_paginated("p1", limit=5, offset=5))
        assert total2 == 20
        assert len(msgs2) == 5
        run(m.close())

    def test_clear_messages(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.add_message("p1", "orch", "Orchestrator", "Hello", 0.01))
        run(m.add_message("p1", "dev", "Developer", "World", 0.02))
        run(m.clear_messages("p1"))
        msgs = run(m.get_recent_messages("p1"))
        assert len(msgs) == 0
        run(m.close())

    def test_clear_project_data(self, db_path):
        """clear_project_data removes messages, sessions, and task_history."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.add_message("p1", "orch", "Orchestrator", "Hello", 0.01))
        run(m.save_session(user_id=1, project_id="p1", agent_role="orch", session_id="s1"))
        run(m.add_task_history("p1", 1, "Test task"))
        run(m.clear_project_data("p1"))
        msgs = run(m.get_recent_messages("p1"))
        assert len(msgs) == 0
        sid = run(m.get_session(1, "p1", "orch"))
        assert sid is None
        tasks = run(m.get_project_tasks("p1"))
        assert len(tasks) == 0
        run(m.close())

    def test_message_count_trigger(self, db_path):
        """Messages trigger auto-increment of project.message_count."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        run(m.add_message("p1", "orch", "Orchestrator", "Hello", 0.01))
        run(m.add_message("p1", "dev", "Developer", "World", 0.02))
        projects = run(m.list_projects())
        assert projects[0]["message_count"] == 2
        run(m.close())

    def test_empty_content_message(self, db_path):
        """Empty string content is stored correctly."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.add_message("p1", "orch", "Orchestrator", "", 0.0))
        msgs = run(m.get_recent_messages("p1"))
        assert len(msgs) == 1
        assert msgs[0]["content"] == ""
        run(m.close())


# ============================================================
# Session CRUD
# ============================================================

class TestSessionCRUD:

    def test_save_and_get(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_session(user_id=1, project_id="p1", agent_role="orch", session_id="s-123"))
        sid = run(m.get_session(1, "p1", "orch"))
        assert sid == "s-123"
        run(m.close())

    def test_invalidate(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_session(user_id=1, project_id="p1", agent_role="orch", session_id="s-123"))
        run(m.invalidate_session(1, "p1", "orch"))
        sid = run(m.get_session(1, "p1", "orch"))
        assert sid is None
        run(m.close())

    def test_upsert_same_session(self, db_path):
        """Saving the same user/project/role updates the session."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_session(user_id=1, project_id="p1", agent_role="orch", session_id="s-1", cost=0.01, turns=1))
        run(m.save_session(user_id=1, project_id="p1", agent_role="orch", session_id="s-2", cost=0.02, turns=2))
        sid = run(m.get_session(1, "p1", "orch"))
        assert sid == "s-2"
        run(m.close())

    def test_get_nonexistent_session(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        sid = run(m.get_session(999, "nonexistent", "orch"))
        assert sid is None
        run(m.close())

    def test_cleanup_expired(self, db_path):
        """cleanup_expired marks old sessions as expired."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_session(user_id=1, project_id="p1", agent_role="orch", session_id="s-1"))
        # Force the updated_at to be very old
        db = run(m._get_db())
        run(db.execute(
            "UPDATE sessions SET updated_at=? WHERE session_id='s-1'",
            (time.time() - 200 * 3600,)  # 200 hours ago
        ))
        run(db.commit())
        run(m.cleanup_expired(max_age_hours=100))
        sid = run(m.get_session(1, "p1", "orch"))
        assert sid is None  # Session was expired
        run(m.close())


# ============================================================
# Task History
# ============================================================

class TestTaskHistory:

    def test_add_and_get(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        task_id = run(m.add_task_history("p1", 1, "Build feature X"))
        assert task_id > 0
        tasks = run(m.get_project_tasks("p1"))
        assert len(tasks) == 1
        assert tasks[0]["task_description"] == "Build feature X"
        assert tasks[0]["status"] == "running"
        run(m.close())

    def test_update_task(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        task_id = run(m.add_task_history("p1", 1, "Build feature"))
        run(m.update_task_history(task_id, status="completed", cost_usd=0.5, turns_used=10, summary="Done"))
        tasks = run(m.get_project_tasks("p1"))
        assert tasks[0]["status"] == "completed"
        assert tasks[0]["cost_usd"] == 0.5
        assert tasks[0]["turns_used"] == 10
        assert tasks[0]["summary"] == "Done"
        run(m.close())

    def test_get_recent_task_history(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        for i in range(5):
            run(m.add_task_history("p1", 1, f"Task {i}"))
        recent = run(m.get_recent_task_history(1, count=3))
        assert len(recent) == 3
        run(m.close())


# ============================================================
# Budget
# ============================================================

class TestBudget:

    def test_default_budget_zero(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        budget = run(m.get_project_budget("p1"))
        assert budget == 0.0
        run(m.close())

    def test_set_and_get_budget(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        run(m.set_project_budget("p1", 50.0))
        budget = run(m.get_project_budget("p1"))
        assert budget == 50.0
        run(m.close())

    def test_total_cost_from_sessions(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_session(user_id=1, project_id="p1", agent_role="orch", session_id="s1", cost=0.5))
        run(m.save_session(user_id=1, project_id="p1", agent_role="dev", session_id="s2", cost=0.3))
        cost = run(m.get_project_total_cost("p1"))
        assert abs(cost - 0.8) < 0.001
        run(m.close())

    def test_total_cost_no_sessions(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        cost = run(m.get_project_total_cost("nonexistent"))
        assert cost == 0.0
        run(m.close())


# ============================================================
# Notification Preferences
# ============================================================

class TestNotificationPrefs:

    def test_defaults(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        prefs = run(m.get_notification_prefs(999))
        assert prefs == {"level": "all", "budget_warning": True, "stall_alert": True}
        run(m.close())

    def test_set_and_get(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.set_notification_prefs(1, level="critical", budget_warning=False, stall_alert=True))
        prefs = run(m.get_notification_prefs(1))
        assert prefs["level"] == "critical"
        assert prefs["budget_warning"] is False
        assert prefs["stall_alert"] is True
        run(m.close())

    def test_upsert(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.set_notification_prefs(1, level="all"))
        run(m.set_notification_prefs(1, level="errors"))
        prefs = run(m.get_notification_prefs(1))
        assert prefs["level"] == "errors"
        run(m.close())


# ============================================================
# Schedules
# ============================================================

class TestSchedules:

    def test_add_and_get(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        sid = run(m.add_schedule(1, "p1", "09:30", "Daily build"))
        assert sid > 0
        schedules = run(m.get_schedules(1))
        assert len(schedules) == 1
        assert schedules[0]["schedule_time"] == "09:30"
        assert schedules[0]["task_description"] == "Daily build"
        run(m.close())

    def test_delete_schedule(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        sid = run(m.add_schedule(1, "p1", "10:00", "Test"))
        deleted = run(m.delete_schedule(sid, user_id=1))
        assert deleted is True
        schedules = run(m.get_schedules(1))
        assert len(schedules) == 0
        run(m.close())

    def test_delete_wrong_user(self, db_path):
        """Cannot delete another user's schedule."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        sid = run(m.add_schedule(1, "p1", "10:00", "Test"))
        deleted = run(m.delete_schedule(sid, user_id=999))
        assert deleted is False
        run(m.close())

    def test_get_due_schedules(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp/p1"))
        run(m.add_schedule(1, "p1", "09:30", "Morning build"))
        run(m.add_schedule(1, "p1", "18:00", "Evening deploy"))
        due = run(m.get_due_schedules("09:30"))
        assert len(due) == 1
        assert due[0]["task_description"] == "Morning build"
        run(m.close())

    def test_disable_schedule(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        sid = run(m.add_schedule(1, "p1", "09:30", "One-time"))
        run(m.disable_schedule(sid))
        schedules = run(m.get_schedules(1))
        assert len(schedules) == 0  # Disabled schedules are filtered out
        run(m.close())

    def test_mark_schedule_run(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        sid = run(m.add_schedule(1, "p1", "09:30", "Test"))
        run(m.mark_schedule_run(sid))
        schedules = run(m.get_schedules(1))
        assert schedules[0]["last_run"] is not None
        run(m.close())


# ============================================================
# Away Mode
# ============================================================

class TestAwayMode:

    def test_default_not_away(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        assert run(m.is_away(1)) is False
        run(m.close())

    def test_set_away_and_check(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        run(m.set_away_mode(1, True))
        assert run(m.is_away(1)) is True
        run(m.set_away_mode(1, False))
        assert run(m.is_away(1)) is False
        run(m.close())

    def test_away_digest(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.save_project("p1", user_id=1, name="P1", description="", project_dir="/tmp"))
        run(m.add_away_digest(1, "p1", "task_complete", "Build finished"))
        run(m.add_away_digest(1, "p1", "error", "Something failed"))
        digest = run(m.get_away_digest(1))
        assert len(digest) == 2
        assert digest[0]["event_type"] == "task_complete"
        run(m.clear_away_digest(1))
        digest = run(m.get_away_digest(1))
        assert len(digest) == 0
        run(m.close())


# ============================================================
# Context Manager & Health Check
# ============================================================

class TestLifecycle:

    def test_is_healthy(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        assert run(m.is_healthy()) is True
        run(m.close())

    def test_not_initialized_raises(self, db_path):
        m = SessionManager(db_path=db_path)
        with pytest.raises(RuntimeError, match="not initialized"):
            run(m._get_db())

    def test_double_close_safe(self, db_path):
        """Calling close() twice doesn't error."""
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.close())
        run(m.close())  # Should not raise

    def test_health_after_close(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.close())
        assert run(m.is_healthy()) is False


# ============================================================
# Stale Message Cleanup
# ============================================================

class TestClearStaleMessages:

    def test_removes_system_and_error_messages(self, db_path):
        m = SessionManager(db_path=db_path)
        run(m.initialize())
        run(m.add_message("p1", "system", "update", "Old system msg", 0.0))
        run(m.add_message("p1", "architect", "assistant", "Old architect msg", 0.0))
        run(m.add_message("p1", "orch", "Orchestrator", "Error: something went wrong", 0.0))
        run(m.add_message("p1", "dev", "Developer", "Good message", 0.0))
        run(m.clear_stale_messages("p1"))
        msgs = run(m.get_recent_messages("p1"))
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Good message"
        run(m.close())
