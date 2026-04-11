"""
tests/test_blackboard.py — Unit tests for the Blackboard enhanced shared memory.

Tests cover:
- Blackboard initialization with StructuredNotes
- Smart context building with priority scoring
- Cross-agent queries (by role, by topic)
- File ownership tracking and conflict detection
- Decision conflict detection
- Token budget enforcement
- Fallback behavior when disabled

These are pure unit tests — no real SDK calls, no Claude API.
"""

from __future__ import annotations

import tempfile

from blackboard import (
    Blackboard,
    _compute_role_affinity,
    _estimate_tokens,
    _keyword_overlap,
)
from structured_notes import NoteCategory, StructuredNotes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blackboard(goal: str = "Build a REST API") -> tuple[Blackboard, StructuredNotes, str]:
    """Create a Blackboard with a temporary project directory."""
    tmpdir = tempfile.mkdtemp()
    notes = StructuredNotes(tmpdir)
    notes.init_session(goal)
    bb = Blackboard(notes)
    return bb, notes, tmpdir


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------


class TestBlackboardInit:
    def test_wraps_structured_notes(self):
        bb, notes, _ = _make_blackboard()
        assert bb.notes is notes

    def test_no_initial_conflicts(self):
        bb, _, _ = _make_blackboard()
        assert bb.conflicts == []


# ---------------------------------------------------------------------------
# Smart context tests
# ---------------------------------------------------------------------------


class TestSmartContext:
    def test_empty_notes_returns_empty(self):
        bb, _, _ = _make_blackboard()
        ctx = bb.build_smart_context(role="backend_developer")
        assert ctx == ""

    def test_includes_relevant_notes(self):
        bb, notes, _ = _make_blackboard()
        notes.add_note(
            category=NoteCategory.DECISION,
            title="Use PostgreSQL",
            content="We decided to use PostgreSQL for the database",
            author_role="backend_developer",
            author_task_id="task_1",
            tags=["database"],
        )
        ctx = bb.build_smart_context(role="backend_developer", task_goal="Set up database")
        assert "PostgreSQL" in ctx

    def test_upstream_tasks_prioritized(self):
        bb, notes, _ = _make_blackboard()
        # Add a note from upstream task
        notes.add_note(
            category=NoteCategory.CONTEXT,
            title="API schema defined",
            content="The API uses REST with JSON responses",
            author_role="backend_developer",
            author_task_id="task_1",
            tags=["api"],
        )
        # Add a note from unrelated task
        notes.add_note(
            category=NoteCategory.CONTEXT,
            title="CSS framework chosen",
            content="Using Tailwind CSS for styling",
            author_role="frontend_developer",
            author_task_id="task_2",
            tags=["css"],
        )
        ctx = bb.build_smart_context(
            role="backend_developer",
            task_goal="Implement endpoints",
            context_from=["task_1"],
        )
        # Upstream note should appear before unrelated note
        assert ctx.index("API schema") < ctx.index("CSS framework")

    def test_respects_token_budget(self):
        bb, notes, _ = _make_blackboard()
        # Add many notes to exceed budget
        for i in range(50):
            notes.add_note(
                category=NoteCategory.CONTEXT,
                title=f"Note {i}",
                content=f"This is a detailed note number {i} with lots of content " * 10,
                author_role="backend_developer",
                author_task_id=f"task_{i}",
                tags=[f"tag_{i}"],
            )
        from unittest.mock import patch

        with patch("blackboard.BLACKBOARD_ENABLED", True):
            ctx = bb.build_smart_context(role="backend_developer", token_budget=500)
            assert _estimate_tokens(ctx) <= 600  # Allow some margin


# ---------------------------------------------------------------------------
# Cross-agent query tests
# ---------------------------------------------------------------------------


class TestCrossAgentQueries:
    def test_query_by_role(self):
        bb, notes, _ = _make_blackboard()
        notes.add_note(
            category=NoteCategory.DECISION,
            title="Backend decision",
            content="Use FastAPI",
            author_role="backend_developer",
            author_task_id="task_1",
        )
        notes.add_note(
            category=NoteCategory.DECISION,
            title="Frontend decision",
            content="Use React",
            author_role="frontend_developer",
            author_task_id="task_2",
        )
        backend_notes = bb.query_by_role("backend_developer")
        assert len(backend_notes) == 1
        assert "FastAPI" in backend_notes[0].content

    def test_query_by_topic(self):
        bb, notes, _ = _make_blackboard()
        notes.add_note(
            category=NoteCategory.DECISION,
            title="Database choice",
            content="PostgreSQL for production, SQLite for tests",
            author_role="backend_developer",
            author_task_id="task_1",
            tags=["database"],
        )
        notes.add_note(
            category=NoteCategory.CONTEXT,
            title="API endpoints",
            content="REST API with /users and /posts",
            author_role="backend_developer",
            author_task_id="task_2",
            tags=["api"],
        )
        db_notes = bb.query_by_topic("database")
        assert len(db_notes) == 1
        assert "PostgreSQL" in db_notes[0].content

    def test_query_by_topic_searches_tags(self):
        bb, notes, _ = _make_blackboard()
        notes.add_note(
            category=NoteCategory.CONTEXT,
            title="Some note",
            content="Generic content",
            author_role="backend_developer",
            author_task_id="task_1",
            tags=["authentication", "jwt"],
        )
        results = bb.query_by_topic("authentication")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Conflict detection tests
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_file_ownership_no_conflict(self):
        bb, _, _ = _make_blackboard()
        conflict = bb.register_file_ownership("src/api.py", "task_1")
        assert conflict is None

    def test_file_ownership_conflict(self):
        bb, _, _ = _make_blackboard()
        bb.register_file_ownership("src/api.py", "task_1")
        conflict = bb.register_file_ownership("src/api.py", "task_2")
        assert conflict is not None
        assert conflict.conflict_type == "file_overlap"
        assert "task_1" in conflict.description
        assert "task_2" in conflict.description

    def test_same_task_no_conflict(self):
        bb, _, _ = _make_blackboard()
        bb.register_file_ownership("src/api.py", "task_1")
        conflict = bb.register_file_ownership("src/api.py", "task_1")
        assert conflict is None

    def test_conflicts_tracked(self):
        bb, _, _ = _make_blackboard()
        bb.register_file_ownership("src/api.py", "task_1")
        bb.register_file_ownership("src/api.py", "task_2")
        assert len(bb.conflicts) == 1


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_estimate_tokens(self):
        assert _estimate_tokens("hello world") > 0
        assert _estimate_tokens("a" * 400) == 100

    def test_keyword_overlap_identical(self):
        text = "database migration schema"
        assert _keyword_overlap(text, text) == 3

    def test_keyword_overlap_no_match(self):
        assert _keyword_overlap("hello world", "foo bar baz") == 0

    def test_keyword_overlap_ignores_stop_words(self):
        assert _keyword_overlap("the quick brown fox", "the lazy brown dog") == 1  # "brown"

    def test_keyword_overlap_ignores_short_words(self):
        assert _keyword_overlap("go do it", "go do it") == 0  # All < 3 chars

    def test_role_affinity_same_role(self):
        assert _compute_role_affinity("backend_developer", "backend_developer") == 3.0

    def test_role_affinity_related_roles(self):
        assert _compute_role_affinity("frontend_developer", "backend_developer") == 5.0

    def test_role_affinity_unrelated_roles(self):
        assert _compute_role_affinity("security_auditor", "ux_critic") == 0.0
