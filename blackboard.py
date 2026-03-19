"""Blackboard — enhanced shared memory for cross-agent coordination.

Extends the existing ``StructuredNotes`` system with capabilities inspired
by the Blackboard architecture pattern (Erman et al., 1980) and its modern
LLM adaptation (Xu et al., 2025 — "Multi-Agent Blackboard for LLMs").

Key improvements over plain StructuredNotes:
    1. **Priority scoring** — notes are scored by relevance, recency, and
       impact so agents see the most important context first.
    2. **Cross-agent queries** — agents can ask "what does the backend team
       know about X?" without reading all notes.
    3. **Conflict detection** — automatically flags when two agents make
       contradictory decisions or modify overlapping files.
    4. **Context budget** — limits injected context to a token budget so
       agents aren't overwhelmed with irrelevant notes.

Research basis:
    Xu et al. (2025) showed that a Blackboard-based multi-agent system
    improves task success rates by 13–57% over manager-worker patterns,
    primarily by reducing information loss between agents.

Integration:
    Wraps ``StructuredNotes`` — does NOT replace it.  The Blackboard adds
    a scoring/query layer on top while StructuredNotes handles persistence.
    Injected into ``dag_executor._ExecutionContext`` alongside the existing
    ``structured_notes`` field.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import config as cfg
from structured_notes import Note, NoteCategory, StructuredNotes

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
BLACKBOARD_ENABLED: bool = cfg._get("BLACKBOARD_ENABLED", "true", str).lower() == "true"
BLACKBOARD_CONTEXT_BUDGET: int = cfg._get("BLACKBOARD_CONTEXT_BUDGET", "4000", int)


@dataclass
class ScoredNote:
    """A note with a computed relevance score."""

    note: Note
    score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)


@dataclass
class ConflictAlert:
    """Detected conflict between two agents' outputs."""

    note_a_id: str
    note_b_id: str
    conflict_type: str  # "decision", "file_overlap", "convention"
    description: str
    severity: str = "warning"  # "warning" or "critical"


class Blackboard:
    """Enhanced shared memory layer wrapping StructuredNotes.

    Provides intelligent context selection, conflict detection, and
    cross-agent queries on top of the existing note persistence layer.
    """

    def __init__(self, notes: StructuredNotes) -> None:
        self._notes = notes
        self._file_owners: dict[str, str] = {}  # file_path -> task_id
        self._conflicts: list[ConflictAlert] = []

    @property
    def notes(self) -> StructuredNotes:
        """Access the underlying StructuredNotes instance."""
        return self._notes

    @property
    def conflicts(self) -> list[ConflictAlert]:
        """Return all detected conflicts."""
        return list(self._conflicts)

    # ── Scoring Engine ───────────────────────────────────────────────────

    def _score_note(
        self,
        note: Note,
        role: str = "",
        task_goal: str = "",
        context_from: list[str] | None = None,
    ) -> ScoredNote:
        """Score a note's relevance to a specific agent and task.

        Scoring factors:
        - Category weight (decisions > gotchas > context > todo)
        - Recency (newer notes score higher)
        - Role relevance (notes from related roles score higher)
        - Keyword overlap with task goal
        - Upstream dependency (notes from context_from tasks score highest)
        """
        score = 0.0
        reasons: list[str] = []

        # Category weight
        category_weights = {
            NoteCategory.DECISION: 10.0,
            NoteCategory.GOTCHA: 9.0,
            NoteCategory.API: 8.0,
            NoteCategory.SCHEMA: 8.0,
            NoteCategory.CONVENTION: 7.0,
            NoteCategory.DEPENDENCY: 6.0,
            NoteCategory.CONTEXT: 5.0,
            NoteCategory.TODO: 4.0,
        }
        cat_weight = category_weights.get(note.category, 5.0)
        score += cat_weight
        reasons.append(f"category:{note.category.value}={cat_weight}")

        # Upstream dependency bonus — notes from tasks we depend on are critical
        if context_from and note.author_task_id in context_from:
            score += 15.0
            reasons.append("upstream_dependency=+15")

        # Recency — newer notes get a small bonus based on position
        all_notes = self._notes.notes
        if note in all_notes:
            position = all_notes.index(note)
            recency_bonus = min(position * 0.5, 5.0)
            score += recency_bonus
            reasons.append(f"recency=+{recency_bonus:.1f}")

        # Role relevance — related roles score higher
        role_affinity = _compute_role_affinity(role, note.author_role)
        if role_affinity > 0:
            score += role_affinity
            reasons.append(f"role_affinity=+{role_affinity:.1f}")

        # Keyword overlap with task goal
        if task_goal:
            overlap = _keyword_overlap(task_goal, f"{note.title} {note.content}")
            if overlap > 0:
                keyword_bonus = min(overlap * 2.0, 8.0)
                score += keyword_bonus
                reasons.append(f"keyword_overlap({overlap})=+{keyword_bonus:.1f}")

        return ScoredNote(note=note, score=score, match_reasons=reasons)

    # ── Smart Context Builder ────────────────────────────────────────────

    def build_smart_context(
        self,
        role: str = "",
        task_goal: str = "",
        context_from: list[str] | None = None,
        token_budget: int | None = None,
    ) -> str:
        """Build a token-budgeted context string from the most relevant notes.

        Unlike ``StructuredNotes.build_notes_context`` which returns all
        matching notes, this method:
        1. Scores every note for relevance
        2. Sorts by score (highest first)
        3. Includes notes until the token budget is exhausted
        4. Appends conflict alerts if any exist

        Args:
            role: The requesting agent's role.
            task_goal: The task goal for relevance scoring.
            context_from: List of upstream task IDs (highest priority).
            token_budget: Max approximate tokens for context.
                Defaults to BLACKBOARD_CONTEXT_BUDGET.

        Returns:
            Formatted Markdown string ready for prompt injection.
        """
        if not BLACKBOARD_ENABLED:
            # Fall back to basic notes context
            return self._notes.build_notes_context(role=role, task_goal=task_goal)

        budget = token_budget or BLACKBOARD_CONTEXT_BUDGET
        all_notes = self._notes.notes
        if not all_notes:
            return ""

        # Score all notes
        scored = [self._score_note(note, role, task_goal, context_from) for note in all_notes]
        scored.sort(key=lambda s: s.score, reverse=True)

        # Build context within token budget
        lines = [
            "## Shared Knowledge Base (Blackboard)",
            f"_{len(all_notes)} total notes, showing most relevant:_",
            "",
        ]
        current_tokens = _estimate_tokens("\n".join(lines))

        included_count = 0
        for sn in scored:
            note_text = sn.note.to_markdown()
            note_tokens = _estimate_tokens(note_text)
            if current_tokens + note_tokens > budget:
                break
            lines.append(note_text)
            current_tokens += note_tokens
            included_count += 1

        if included_count == 0:
            return ""

        # Append conflict alerts if any
        relevant_conflicts = self._get_relevant_conflicts(role, context_from)
        if relevant_conflicts:
            lines.append("### Conflict Alerts")
            lines.append("")
            for conflict in relevant_conflicts[:3]:  # Cap at 3
                severity_icon = "CRITICAL" if conflict.severity == "critical" else "WARNING"
                lines.append(
                    f"- **[{severity_icon}]** {conflict.description} "
                    f"(between notes {conflict.note_a_id} and {conflict.note_b_id})"
                )
            lines.append("")

        logger.info(
            "[Blackboard] Built context for %s: %d/%d notes, ~%d tokens",
            role,
            included_count,
            len(all_notes),
            current_tokens,
        )

        return "\n".join(lines)

    # ── Cross-Agent Query ────────────────────────────────────────────────

    def query_by_role(self, author_role: str, max_notes: int = 10) -> list[Note]:
        """Get notes written by a specific role.

        Useful for agents that need to know what a specific team member
        discovered without reading all notes.
        """
        return [n for n in self._notes.notes if n.author_role == author_role][:max_notes]

    def query_by_topic(self, topic: str, max_notes: int = 10) -> list[Note]:
        """Search notes by topic keyword matching.

        Searches title, content, and tags for the topic string.
        """
        topic_lower = topic.lower()
        matches = []
        for note in self._notes.notes:
            searchable = f"{note.title} {note.content} {' '.join(note.tags)}".lower()
            if topic_lower in searchable:
                matches.append(note)
        return matches[:max_notes]

    # ── Conflict Detection ───────────────────────────────────────────────

    def register_file_ownership(self, file_path: str, task_id: str) -> ConflictAlert | None:
        """Register that a task modified a file. Detect overlapping writes.

        Returns a ConflictAlert if another task already claimed this file.
        """
        if file_path in self._file_owners:
            existing_task = self._file_owners[file_path]
            if existing_task != task_id:
                conflict = ConflictAlert(
                    note_a_id=existing_task,
                    note_b_id=task_id,
                    conflict_type="file_overlap",
                    description=(
                        f"File '{file_path}' was modified by both "
                        f"task {existing_task} and task {task_id}"
                    ),
                    severity="warning",
                )
                self._conflicts.append(conflict)
                logger.warning(
                    "[Blackboard] File conflict detected: %s modified by %s and %s",
                    file_path,
                    existing_task,
                    task_id,
                )
                return conflict
        self._file_owners[file_path] = task_id
        return None

    def detect_decision_conflicts(self) -> list[ConflictAlert]:
        """Scan decision notes for contradictions.

        Looks for decision notes that reference the same topic but
        have different conclusions. Uses simple keyword overlap.
        """
        decisions = [n for n in self._notes.notes if n.category == NoteCategory.DECISION]
        new_conflicts: list[ConflictAlert] = []

        for i, note_a in enumerate(decisions):
            for note_b in decisions[i + 1 :]:
                # Check if they discuss the same topic
                overlap = _keyword_overlap(note_a.title, note_b.title)
                if overlap >= 2:
                    # Same topic — check if content differs significantly
                    content_overlap = _keyword_overlap(note_a.content, note_b.content)
                    total_words = len(note_a.content.split()) + len(note_b.content.split())
                    if total_words > 0 and content_overlap / max(total_words / 2, 1) < 0.3:
                        conflict = ConflictAlert(
                            note_a_id=note_a.id,
                            note_b_id=note_b.id,
                            conflict_type="decision",
                            description=(
                                f"Potentially conflicting decisions about "
                                f"'{note_a.title}' vs '{note_b.title}'"
                            ),
                            severity="warning",
                        )
                        new_conflicts.append(conflict)
                        self._conflicts.append(conflict)

        return new_conflicts

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _get_relevant_conflicts(
        self,
        role: str,
        context_from: list[str] | None = None,
    ) -> list[ConflictAlert]:
        """Get conflicts relevant to a specific agent."""
        if not self._conflicts:
            return []
        # Return conflicts involving upstream tasks
        if context_from:
            upstream_set = set(context_from)
            return [
                c
                for c in self._conflicts
                if c.note_a_id in upstream_set or c.note_b_id in upstream_set
            ]
        return self._conflicts[:5]  # Return most recent if no filter


# ── Module-level Helpers ─────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return max(1, len(text) // 4)


def _keyword_overlap(text_a: str, text_b: str) -> int:
    """Count shared meaningful keywords between two texts."""
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "for",
        "to",
        "and",
        "or",
        "of",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "not",
        "no",
        "but",
        "if",
        "then",
    }
    words_a = set(re.findall(r"\w+", text_a.lower())) - stop_words
    words_b = set(re.findall(r"\w+", text_b.lower())) - stop_words
    # Only count words with 3+ characters to avoid noise
    words_a = {w for w in words_a if len(w) >= 3}
    words_b = {w for w in words_b if len(w) >= 3}
    return len(words_a & words_b)


# Role affinity map — which roles produce context useful to which other roles
_ROLE_AFFINITY: dict[str, set[str]] = {
    "frontend_developer": {"backend_developer", "ux_critic", "typescript_architect", "designer"},
    "backend_developer": {"frontend_developer", "database_expert", "python_backend", "devops"},
    "database_expert": {"backend_developer", "python_backend"},
    "devops": {"backend_developer", "database_expert", "security_auditor"},
    "reviewer": {
        "frontend_developer",
        "backend_developer",
        "typescript_architect",
        "python_backend",
    },
    "security_auditor": {"backend_developer", "devops", "database_expert"},
    "test_engineer": {"frontend_developer", "backend_developer"},
    "tester": {"frontend_developer", "backend_developer"},
    "ux_critic": {"frontend_developer", "designer"},
    "typescript_architect": {"frontend_developer", "backend_developer"},
    "python_backend": {"backend_developer", "database_expert"},
    "researcher": set(),  # Researchers benefit from everything
    "developer": {"frontend_developer", "backend_developer", "database_expert"},
}


def _compute_role_affinity(requesting_role: str, author_role: str) -> float:
    """Compute affinity score between two roles (0.0 to 5.0)."""
    if requesting_role == author_role:
        return 3.0  # Same role — moderately useful
    related = _ROLE_AFFINITY.get(requesting_role, set())
    if author_role in related:
        return 5.0  # Directly related role — very useful
    # Check reverse affinity
    reverse_related = _ROLE_AFFINITY.get(author_role, set())
    if requesting_role in reverse_related:
        return 4.0  # Reverse relationship — still useful
    return 0.0  # Unrelated roles
