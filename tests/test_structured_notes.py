"""Tests for structured_notes module."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from structured_notes import (
    Note,
    NoteCategory,
    StructuredNotes,
)


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory."""
    return str(tmp_path)


@pytest.fixture
def notes(tmp_project):
    """Create a StructuredNotes instance."""
    sn = StructuredNotes(project_dir=tmp_project)
    sn.init_session("Build a web app")
    return sn


# ── Init and persistence tests ───────────────────────────────────────────────


class TestInitSession:
    def test_creates_notes_md(self, tmp_project):
        sn = StructuredNotes(project_dir=tmp_project)
        sn.init_session("Test goal")
        assert os.path.exists(sn.notes_md_path)

    def test_md_contains_goal(self, tmp_project):
        sn = StructuredNotes(project_dir=tmp_project)
        sn.init_session("Build a REST API")
        with open(sn.notes_md_path) as f:
            content = f.read()
        assert "Build a REST API" in content

    def test_loads_existing_notes(self, tmp_project):
        # Write some existing notes
        existing = [
            {
                "id": "note_1",
                "category": "decision",
                "title": "Use PostgreSQL",
                "content": "We chose PostgreSQL for the database",
                "author_role": "backend_developer",
                "author_task_id": "t1",
                "tags": ["database"],
                "timestamp": "2025-01-01T00:00:00+00:00",
            }
        ]
        json_path = os.path.join(tmp_project, ".notes.json")
        with open(json_path, "w") as f:
            json.dump(existing, f)

        sn = StructuredNotes(project_dir=tmp_project)
        sn.init_session("Continue work")
        assert len(sn.notes) == 1
        assert sn.notes[0].title == "Use PostgreSQL"


# ── Add note tests ───────────────────────────────────────────────────────────


class TestAddNote:
    def test_add_note_basic(self, notes):
        note = notes.add_note(
            category=NoteCategory.DECISION,
            title="Use JWT for auth",
            content="JWT tokens with 1h expiry",
            author_role="backend_developer",
            author_task_id="t1",
        )
        assert note.id == "note_1"
        assert note.category == NoteCategory.DECISION
        assert len(notes.notes) == 1

    def test_add_note_with_tags(self, notes):
        note = notes.add_note(
            category=NoteCategory.API,
            title="POST /api/login",
            content="Accepts email and password, returns JWT",
            author_role="backend_developer",
            author_task_id="t1",
            tags=["auth", "login"],
        )
        assert note.tags == ["auth", "login"]

    def test_add_note_persists_to_json(self, notes):
        notes.add_note(
            category=NoteCategory.SCHEMA,
            title="Users table",
            content="id, email, password_hash, created_at",
            author_role="database_expert",
            author_task_id="t2",
        )
        assert os.path.exists(notes.notes_json_path)
        with open(notes.notes_json_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["title"] == "Users table"

    def test_add_note_persists_to_md(self, notes):
        notes.add_note(
            category=NoteCategory.GOTCHA,
            title="SQLite doesn't support ALTER COLUMN",
            content="Use a migration workaround",
            author_role="database_expert",
            author_task_id="t2",
        )
        with open(notes.notes_md_path) as f:
            content = f.read()
        assert "SQLite" in content

    def test_multiple_notes_increment_id(self, notes):
        n1 = notes.add_note(NoteCategory.DECISION, "A", "a", "r", "t1")
        n2 = notes.add_note(NoteCategory.DECISION, "B", "b", "r", "t2")
        assert n1.id == "note_1"
        assert n2.id == "note_2"


# ── Get relevant notes tests ────────────────────────────────────────────────


class TestGetRelevantNotes:
    def _populate(self, notes):
        notes.add_note(
            NoteCategory.DECISION,
            "Use PostgreSQL",
            "Chosen for reliability",
            "db",
            "t1",
            ["database"],
        )
        notes.add_note(NoteCategory.API, "POST /login", "Auth endpoint", "backend", "t2", ["auth"])
        notes.add_note(
            NoteCategory.SCHEMA, "Users table", "id, email, hash", "db", "t3", ["database", "users"]
        )
        notes.add_note(
            NoteCategory.GOTCHA, "Rate limit on API", "Max 100 req/min", "backend", "t4", ["api"]
        )
        notes.add_note(NoteCategory.TODO, "Add tests", "Need unit tests", "pm", "t5", ["testing"])

    def test_get_all_notes(self, notes):
        self._populate(notes)
        result = notes.get_relevant_notes()
        assert len(result) == 5

    def test_filter_by_category(self, notes):
        self._populate(notes)
        result = notes.get_relevant_notes(categories=[NoteCategory.API])
        # Should include API notes + universal (DECISION, GOTCHA)
        categories = {n.category for n in result}
        assert NoteCategory.API in categories

    def test_filter_by_tags(self, notes):
        self._populate(notes)
        result = notes.get_relevant_notes(tags=["database"])
        assert any("PostgreSQL" in n.title for n in result)
        assert any("Users" in n.title for n in result)

    def test_keyword_matching(self, notes):
        self._populate(notes)
        result = notes.get_relevant_notes(task_goal="Build database schema for users")
        # Should rank database-related notes higher
        assert len(result) > 0

    def test_max_notes_limit(self, notes):
        for i in range(30):
            notes.add_note(NoteCategory.CONTEXT, f"Note {i}", f"Content {i}", "r", f"t{i}")
        result = notes.get_relevant_notes(max_notes=10)
        assert len(result) <= 10

    def test_decisions_and_gotchas_always_included(self, notes):
        self._populate(notes)
        result = notes.get_relevant_notes(categories=[NoteCategory.TODO])
        categories = {n.category for n in result}
        # DECISION and GOTCHA should be included as universal
        assert NoteCategory.DECISION in categories or NoteCategory.GOTCHA in categories


# ── Build context tests ──────────────────────────────────────────────────────


class TestBuildNotesContext:
    def test_empty_context(self, notes):
        ctx = notes.build_notes_context()
        assert ctx == ""

    def test_context_with_notes(self, notes):
        notes.add_note(NoteCategory.DECISION, "Use React", "Frontend framework", "frontend", "t1")
        ctx = notes.build_notes_context()
        assert "Shared Knowledge Base" in ctx
        assert "Use React" in ctx

    def test_context_filtered_by_role(self, notes):
        notes.add_note(NoteCategory.API, "POST /login", "Auth endpoint", "backend", "t1", ["auth"])
        notes.add_note(
            NoteCategory.SCHEMA, "Users table", "Schema details", "db", "t2", ["database"]
        )
        ctx = notes.build_notes_context(task_goal="Build authentication")
        assert len(ctx) > 0


# ── Note serialization tests ────────────────────────────────────────────────


class TestNoteSerialization:
    def test_to_dict_and_back(self):
        note = Note(
            id="note_1",
            category=NoteCategory.DECISION,
            title="Use TypeScript",
            content="For type safety",
            author_role="frontend_developer",
            author_task_id="t1",
            tags=["typescript", "frontend"],
        )
        d = note.to_dict()
        restored = Note.from_dict(d)
        assert restored.id == note.id
        assert restored.category == note.category
        assert restored.title == note.title
        assert restored.tags == note.tags

    def test_to_markdown(self):
        note = Note(
            id="note_1",
            category=NoteCategory.API,
            title="GET /users",
            content="Returns list of users",
            author_role="backend_developer",
            author_task_id="t1",
            tags=["api", "users"],
        )
        md = note.to_markdown()
        assert "API" in md
        assert "GET /users" in md
        assert "`api`" in md


# ── Session summary tests ───────────────────────────────────────────────────


class TestGetSessionSummary:
    def test_empty_summary(self, notes):
        s = notes.get_session_summary()
        assert s["total_notes"] == 0

    def test_summary_with_notes(self, notes):
        notes.add_note(NoteCategory.DECISION, "A", "a", "backend", "t1")
        notes.add_note(NoteCategory.DECISION, "B", "b", "backend", "t2")
        notes.add_note(NoteCategory.API, "C", "c", "frontend", "t3")
        s = notes.get_session_summary()
        assert s["total_notes"] == 3
        assert s["by_category"]["decision"] == 2
        assert s["by_category"]["api"] == 1
        assert s["by_author"]["backend"] == 2
        assert s["by_author"]["frontend"] == 1
