"""Structured Notes — shared knowledge base between agents during execution.

During DAG execution, agents discover information that other agents need:
architecture decisions, API endpoints, database schemas, gotchas, etc.
Instead of relying on lossy prompt-based context passing, this module
maintains a structured NOTES.md file in the project directory that all
agents can read and write.

Inspired by the A-MEM (Agentic Memory) pattern from the research report:
agents maintain structured notes with context, relevance, and links.

Integration points:
    1. dag_executor._run_single_task — after task completion, call
       add_note() to record what the agent learned.
    2. dag_executor._run_single_task — before task execution, call
       get_relevant_notes() and inject into the prompt.
    3. orchestrator._run_dag_session — call init_session() at start
       and get_session_summary() at end.

Zero external dependencies — writes plain Markdown files.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from config import MAX_STRUCTURED_NOTES

logger = logging.getLogger(__name__)


class NoteCategory(StrEnum):
    """Categories for structured notes."""

    DECISION = "decision"  # Architecture/design decision
    API = "api"  # API endpoint or contract
    SCHEMA = "schema"  # Database schema or data model
    GOTCHA = "gotcha"  # Bug, pitfall, or workaround
    DEPENDENCY = "dependency"  # Package, service, or version info
    CONVENTION = "convention"  # Coding convention or pattern
    TODO = "todo"  # Remaining work or known gap
    CONTEXT = "context"  # General context for other agents


@dataclass
class Note:
    """A single structured note."""

    id: str
    category: NoteCategory
    title: str
    content: str
    author_role: str
    author_task_id: str
    tags: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_markdown(self) -> str:
        """Render this note as a Markdown section."""
        tags_str = ", ".join(f"`{t}`" for t in self.tags) if self.tags else ""
        lines = [
            f"### [{self.category.value.upper()}] {self.title}",
            f"_By {self.author_role} (task: {self.author_task_id}) | {self.timestamp}_",
        ]
        if tags_str:
            lines.append(f"Tags: {tags_str}")
        lines.append("")
        lines.append(self.content)
        lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "id": self.id,
            "category": self.category.value,
            "title": self.title,
            "content": self.content,
            "author_role": self.author_role,
            "author_task_id": self.author_task_id,
            "tags": self.tags,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Note:
        """Deserialize from dict."""
        return cls(
            id=data["id"],
            category=NoteCategory(data["category"]),
            title=data["title"],
            content=data["content"],
            author_role=data["author_role"],
            author_task_id=data["author_task_id"],
            tags=data.get("tags", []),
            timestamp=data.get("timestamp", ""),
        )


@dataclass
class StructuredNotes:
    """Manages a shared knowledge base for agents during execution.

    Notes are stored in two formats:
    1. NOTES.md — human-readable Markdown (agents read this)
    2. .notes.json — machine-readable JSON (for programmatic access)

    Both files live in the project directory.
    """

    project_dir: str
    notes: list[Note] = field(default_factory=list)
    _counter: int = 0

    @property
    def notes_md_path(self) -> str:
        return os.path.join(self.project_dir, "NOTES.md")

    @property
    def notes_json_path(self) -> str:
        return os.path.join(self.project_dir, ".notes.json")

    def init_session(self, goal: str) -> None:
        """Initialize the notes file for a new session."""
        self.notes = []
        self._counter = 0

        # Load existing notes if any
        if os.path.exists(self.notes_json_path):
            try:
                with open(self.notes_json_path) as f:
                    data = json.load(f)
                self.notes = [Note.from_dict(d) for d in data]
                self._counter = len(self.notes)
                logger.info("[StructuredNotes] Loaded %d existing notes", len(self.notes))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("[StructuredNotes] Failed to load existing notes: %s", e)
                self.notes = []

        # Write initial NOTES.md header
        self._write_md(goal)
        logger.info("[StructuredNotes] Initialized for: %s", goal[:80])

    def add_note(
        self,
        category: NoteCategory,
        title: str,
        content: str,
        author_role: str,
        author_task_id: str,
        tags: list[str] | None = None,
    ) -> Note:
        """Add a new note to the knowledge base.

        Args:
            category: Type of note (decision, api, schema, etc.)
            title: Short descriptive title
            content: Full note content
            author_role: The agent role that created this note
            author_task_id: The task ID that created this note
            tags: Optional tags for filtering

        Returns:
            The created Note
        """
        self._counter += 1
        note = Note(
            id=f"note_{self._counter}",
            category=category,
            title=title,
            content=content,
            author_role=author_role,
            author_task_id=author_task_id,
            tags=tags or [],
        )
        self.notes.append(note)
        # Cap notes list to prevent unbounded growth across sessions (FIFO eviction)
        if len(self.notes) > MAX_STRUCTURED_NOTES:
            self.notes = self.notes[-MAX_STRUCTURED_NOTES:]
        self._persist()

        logger.info(
            "[StructuredNotes] Added note %s: [%s] %s (by %s)",
            note.id,
            category.value,
            title,
            author_role,
        )
        return note

    def get_relevant_notes(
        self,
        role: str = "",
        task_goal: str = "",
        categories: list[NoteCategory] | None = None,
        tags: list[str] | None = None,
        max_notes: int = 20,
    ) -> list[Note]:
        """Get notes relevant to a specific agent/task.

        Filtering priority:
        1. Category filter (if specified)
        2. Tag filter (if specified)
        3. Keyword matching on role and goal

        Args:
            role: The requesting agent's role
            task_goal: The task goal for keyword matching
            categories: Filter by note categories
            tags: Filter by tags
            max_notes: Maximum notes to return

        Returns:
            List of relevant notes, most recent first
        """
        filtered = list(self.notes)

        # Filter by category
        if categories:
            cat_set = set(categories)
            filtered = [n for n in filtered if n.category in cat_set]

        # Filter by tags
        if tags:
            tag_set = {t.lower() for t in tags}
            filtered = [n for n in filtered if any(t.lower() in tag_set for t in n.tags)]

        # If no filters applied and we have a goal, do keyword matching
        if not categories and not tags and task_goal:
            keywords = set(task_goal.lower().split())
            # Remove common words
            stop_words = {
                "the",
                "a",
                "an",
                "is",
                "are",
                "for",
                "to",
                "and",
                "or",
                "of",
                "in",
                "on",
                "with",
            }
            keywords -= stop_words

            def relevance(note: Note) -> int:
                text = f"{note.title} {note.content} {' '.join(note.tags)}".lower()
                return sum(1 for kw in keywords if kw in text)

            filtered.sort(key=relevance, reverse=True)

        # Always include DECISION and GOTCHA notes (they're universally useful)
        filtered_ids = {id(n) for n in filtered}
        universal = [
            n
            for n in self.notes
            if n.category in (NoteCategory.DECISION, NoteCategory.GOTCHA)
            and id(n) not in filtered_ids
        ]
        filtered = filtered + universal

        # Most recent first, limited
        return filtered[-max_notes:] if len(filtered) > max_notes else filtered

    def build_notes_context(
        self,
        role: str = "",
        task_goal: str = "",
        categories: list[NoteCategory] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Build a context string from relevant notes for prompt injection.

        Returns a Markdown-formatted string ready to be appended to a prompt.
        """
        relevant = self.get_relevant_notes(role, task_goal, categories, tags)
        if not relevant:
            return ""

        lines = [
            "## Shared Knowledge Base (from other agents)",
            f"_The following {len(relevant)} notes were left by other agents._",
            "",
        ]
        for note in relevant:
            lines.append(note.to_markdown())

        return "\n".join(lines)

    def get_session_summary(self) -> dict[str, Any]:
        """Return a summary of all notes in this session."""
        by_category: dict[str, int] = {}
        by_author: dict[str, int] = {}
        for note in self.notes:
            by_category[note.category.value] = by_category.get(note.category.value, 0) + 1
            by_author[note.author_role] = by_author.get(note.author_role, 0) + 1

        return {
            "total_notes": len(self.notes),
            "by_category": by_category,
            "by_author": by_author,
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _persist(self) -> None:
        """Write notes to both MD and JSON files."""
        self._write_md()
        self._write_json()

    def _write_md(self, goal: str = "") -> None:
        """Write the NOTES.md file."""
        lines = ["# Project Notes", ""]
        if goal:
            lines.append(f"**Goal:** {goal}")
            lines.append("")

        if self.notes:
            # Group by category
            by_cat: dict[str, list[Note]] = {}
            for note in self.notes:
                cat = note.category.value
                if cat not in by_cat:
                    by_cat[cat] = []
                by_cat[cat].append(note)

            for cat, notes in by_cat.items():
                lines.append(f"## {cat.upper()}")
                lines.append("")
                for note in notes:
                    lines.append(note.to_markdown())
        else:
            lines.append("_No notes yet._")
            lines.append("")

        os.makedirs(os.path.dirname(self.notes_md_path) or ".", exist_ok=True)
        with open(self.notes_md_path, "w") as f:
            f.write("\n".join(lines))

    def _write_json(self) -> None:
        """Write the .notes.json file."""
        os.makedirs(os.path.dirname(self.notes_json_path) or ".", exist_ok=True)
        with open(self.notes_json_path, "w") as f:
            json.dump([n.to_dict() for n in self.notes], f, indent=2)
