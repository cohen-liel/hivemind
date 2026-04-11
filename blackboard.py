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
    5. **Time-decay scoring** — note priority decays exponentially over time.
    6. **Semantic clustering** — groups related notes by keyword similarity.
    7. **Brain summary** — produces executive digest of blackboard state.
    8. **Unified complexity classifier** — single adaptive classifier replacing
       three independent mechanisms (orch_watchdog, contracts, orch_context).

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
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import config as cfg
from structured_notes import Note, NoteCategory, StructuredNotes

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
BLACKBOARD_ENABLED: bool = cfg._get("BLACKBOARD_ENABLED", "false", str).lower() == "true"
BLACKBOARD_CONTEXT_BUDGET: int = cfg._get("BLACKBOARD_CONTEXT_BUDGET", "4000", int)

# Time-decay: half-life in seconds (default 30 minutes — notes lose half
# their time bonus after 30 min of inactivity).
DECAY_HALF_LIFE: float = cfg._get("BLACKBOARD_DECAY_HALF_LIFE", "1800", float)

# Clustering: minimum keyword overlap to group notes into the same cluster
CLUSTER_MIN_OVERLAP: int = cfg._get("BLACKBOARD_CLUSTER_MIN_OVERLAP", "2", int)


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


@dataclass
class NoteCluster:
    """A cluster of semantically related notes."""

    cluster_id: str
    label: str
    note_ids: list[str] = field(default_factory=list)
    keywords: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label,
            "note_ids": self.note_ids,
            "keywords": sorted(self.keywords),
        }


# ── Unified Complexity Classifier ────────────────────────────────────────


class ComplexityLevel:
    """Unified complexity levels with both label and numeric score."""

    SIMPLE = "SIMPLE"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"
    EPIC = "EPIC"


@dataclass
class ComplexityResult:
    """Result of the unified complexity classifier."""

    level: str  # SIMPLE | MEDIUM | LARGE | EPIC
    score: float  # 1.0 – 5.0 continuous score
    min_rounds: int  # Minimum orchestrator rounds
    timeout_multiplier: float  # 1.0x – 2.0x for adaptive timeouts
    context_priority: int  # 0 (INFO), 2 (HIGH), 3 (CRITICAL)
    factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "score": round(self.score, 2),
            "min_rounds": self.min_rounds,
            "timeout_multiplier": round(self.timeout_multiplier, 2),
            "context_priority": self.context_priority,
            "factors": self.factors,
        }


# Keyword patterns for text-based classification (from orch_watchdog)
_EPIC_PATTERNS = [
    "build an app",
    "build a app",
    "create an app",
    "develop an app",
    "build a system",
    "create a system",
    "full application",
    "complete app",
    "full stack",
    "fullstack",
    "from scratch",
    "entire system",
    "build a website",
    "create a website",
    "build a platform",
    "saas",
    "e-commerce",
    "ecommerce",
    "real-time app",
    "microservice",
    "full implementation",
    "complete system",
    "build me",
    "create me",
    "write me a complete",
]

_LARGE_PATTERNS = [
    "authentication",
    "auth system",
    "new feature",
    "add feature",
    "refactor",
    "add module",
    "create service",
    "implement",
    "integrate",
    "database schema",
    "api endpoint",
    "rest api",
    "graphql",
    "user management",
    "payment",
    "notification",
]

_MEDIUM_PATTERNS = [
    "add",
    "update",
    "change",
    "modify",
    "improve",
    "enhance",
    "create",
    "write",
    "make",
    "implement",
    "migrate",
]

# Context priority keywords (from orch_context)
_CRITICAL_KEYWORDS = ("FAILED", "ERROR", "BLOCKED", "CRITICAL")
_HIGH_KEYWORDS = ("NEEDS_FOLLOWUP", "WARNING", "⚠")

# Complex roles that need more time (from contracts)
_COMPLEX_ROLES = {"reviewer", "test_engineer", "security_auditor"}


def classify_complexity(
    text: str = "",
    *,
    acceptance_criteria: list[str] | None = None,
    constraints: list[str] | None = None,
    files_scope: list[str] | None = None,
    depends_on: list[str] | None = None,
    role: str = "",
    is_remediation: bool = False,
    context_entry: str = "",
) -> ComplexityResult:
    """Unified adaptive complexity classifier.

    Replaces three independent mechanisms:
    - orch_watchdog.estimate_task_complexity (text pattern → label)
    - contracts.compute_task_complexity (structured fields → 1.0-5.0)
    - orch_context.classify_context_priority (context keywords → priority)

    Can be called with any combination of inputs — uses whatever is available.

    Args:
        text: Free-text task description or goal.
        acceptance_criteria: List of acceptance criteria strings.
        constraints: List of constraint strings.
        files_scope: List of files in scope.
        depends_on: List of dependency task IDs.
        role: Agent role (e.g. "reviewer", "backend_developer").
        is_remediation: Whether this is a remediation/fix task.
        context_entry: Context entry string for priority classification.

    Returns:
        ComplexityResult with unified level, score, and derived parameters.
    """
    score = 1.0
    factors: list[str] = []

    # ── Text-based pattern matching (orch_watchdog heritage) ──
    if text:
        t = text.lower()
        if any(p in t for p in _EPIC_PATTERNS):
            score += 3.5
            factors.append("text_pattern=EPIC")
        elif any(p in t for p in _LARGE_PATTERNS) or len(text.split()) > 60:
            score += 2.0
            factors.append("text_pattern=LARGE")
        elif any(p in t for p in _MEDIUM_PATTERNS):
            score += 1.0
            factors.append("text_pattern=MEDIUM")
        # Goal length as additional signal
        if len(text) > 500:
            score += 0.3
            factors.append("long_goal>500")
        elif len(text) > 200:
            score += 0.15
            factors.append("long_goal>200")

    # ── Structured field scoring (contracts heritage) ──
    if acceptance_criteria:
        bonus = min(len(acceptance_criteria) * 0.2, 1.5)
        score += bonus
        factors.append(f"criteria({len(acceptance_criteria)})=+{bonus:.1f}")

    if constraints:
        bonus = min(len(constraints) * 0.15, 0.6)
        score += bonus
        factors.append(f"constraints({len(constraints)})=+{bonus:.1f}")

    if files_scope:
        bonus = min(len(files_scope) * 0.1, 0.8)
        score += bonus
        factors.append(f"files({len(files_scope)})=+{bonus:.1f}")

    if depends_on:
        bonus = min(len(depends_on) * 0.1, 0.5)
        score += bonus
        factors.append(f"deps({len(depends_on)})=+{bonus:.1f}")

    if is_remediation:
        score += 0.5
        factors.append("remediation=+0.5")

    if role.lower() in _COMPLEX_ROLES:
        score += 0.3
        factors.append(f"complex_role({role})=+0.3")

    # ── Context priority classification (orch_context heritage) ──
    context_priority = 0
    if context_entry:
        upper = context_entry.upper()
        if any(kw in upper for kw in _CRITICAL_KEYWORDS):
            context_priority = 3
            factors.append("context=CRITICAL")
        elif any(kw in upper for kw in _HIGH_KEYWORDS):
            context_priority = 2
            factors.append("context=HIGH")

    # Clamp to [1.0, 5.0]
    score = max(1.0, min(score, 5.0))

    # Derive discrete level from continuous score
    if score >= 4.0:
        level = ComplexityLevel.EPIC
    elif score >= 3.0:
        level = ComplexityLevel.LARGE
    elif score >= 2.0:
        level = ComplexityLevel.MEDIUM
    else:
        level = ComplexityLevel.SIMPLE

    # Derive min_rounds (from orch_watchdog's usage)
    min_rounds_map = {"SIMPLE": 2, "MEDIUM": 3, "LARGE": 4, "EPIC": 8}
    min_rounds = min_rounds_map[level]

    # Derive timeout multiplier: linear interpolation 1.0x at score=1.0, 2.0x at score=5.0
    timeout_multiplier = 1.0 + (score - 1.0) * 0.25

    return ComplexityResult(
        level=level,
        score=score,
        min_rounds=min_rounds,
        timeout_multiplier=timeout_multiplier,
        context_priority=context_priority,
        factors=factors,
    )


# ── Blackboard Class ─────────────────────────────────────────────────────


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
        - Time-decay (exponential decay based on note age)
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

        # Time-decay — exponential decay based on note age
        time_bonus = _compute_time_decay(note.timestamp)
        if time_bonus > 0:
            score += time_bonus
            reasons.append(f"time_decay=+{time_bonus:.1f}")

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

    # ── Semantic Clustering ──────────────────────────────────────────────

    def cluster_notes(self) -> list[NoteCluster]:
        """Group related notes into semantic clusters based on keyword overlap.

        Uses single-linkage clustering: notes with >= CLUSTER_MIN_OVERLAP shared
        meaningful keywords are placed in the same cluster. This allows agents
        and the brain summary to see related knowledge grouped together.

        Returns:
            List of NoteCluster objects, sorted by cluster size (largest first).
        """
        all_notes = self._notes.notes
        if not all_notes:
            return []

        # Extract meaningful keywords for each note
        note_keywords: dict[str, set[str]] = {}
        for note in all_notes:
            text = f"{note.title} {note.content} {' '.join(note.tags)}"
            kws = _extract_keywords(text)
            note_keywords[note.id] = kws

        # Build adjacency via keyword overlap (single-linkage clustering)
        parent: dict[str, str] = {n.id: n.id for n in all_notes}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        note_list = list(all_notes)
        for i, na in enumerate(note_list):
            for nb in note_list[i + 1 :]:
                overlap = len(note_keywords[na.id] & note_keywords[nb.id])
                if overlap >= CLUSTER_MIN_OVERLAP:
                    union(na.id, nb.id)

        # Group notes by cluster root
        cluster_map: dict[str, list[str]] = defaultdict(list)
        cluster_kws: dict[str, set[str]] = defaultdict(set)
        for note in all_notes:
            root = find(note.id)
            cluster_map[root].append(note.id)
            cluster_kws[root] |= note_keywords[note.id]

        # Build cluster objects
        clusters: list[NoteCluster] = []
        for idx, (root, note_ids) in enumerate(cluster_map.items()):
            kws = cluster_kws[root]
            top_keywords = sorted(
                kws,
                key=lambda k: sum(1 for nid in note_ids if k in note_keywords.get(nid, set())),
                reverse=True,
            )[:5]
            label = ", ".join(top_keywords) if top_keywords else f"cluster_{idx}"
            clusters.append(
                NoteCluster(
                    cluster_id=f"cluster_{idx}",
                    label=label,
                    note_ids=note_ids,
                    keywords=kws,
                )
            )

        clusters.sort(key=lambda c: len(c.note_ids), reverse=True)
        return clusters

    # ── Brain Summary ────────────────────────────────────────────────────

    def get_brain_summary(self) -> dict[str, Any]:
        """Produce an executive digest of the blackboard state.

        Returns a structured summary including:
        - Total notes count and breakdown by category and role
        - Top-scored notes (most impactful knowledge)
        - Semantic clusters (related knowledge groups)
        - Active conflicts
        - File ownership map
        - Staleness analysis (notes older than 1 hour flagged)

        This powers the /api/projects/{id}/brain-summary endpoint.
        """
        all_notes = self._notes.notes
        now = datetime.now(UTC)

        # Category breakdown
        by_category: dict[str, int] = defaultdict(int)
        by_role: dict[str, int] = defaultdict(int)
        by_task: dict[str, int] = defaultdict(int)
        stale_notes: list[str] = []

        for note in all_notes:
            by_category[note.category.value] += 1
            by_role[note.author_role] += 1
            by_task[note.author_task_id] += 1
            # Flag notes older than 1 hour as stale
            try:
                ts = datetime.fromisoformat(note.timestamp)
                if (now - ts).total_seconds() > 3600:
                    stale_notes.append(note.id)
            except (ValueError, TypeError):
                pass

        # Top-scored notes (scored without a specific role/goal for general importance)
        scored = [self._score_note(note) for note in all_notes]
        scored.sort(key=lambda s: s.score, reverse=True)
        top_notes = [
            {
                "id": sn.note.id,
                "category": sn.note.category.value,
                "title": sn.note.title,
                "score": round(sn.score, 1),
                "author": sn.note.author_role,
                "task": sn.note.author_task_id,
                "reasons": sn.match_reasons,
            }
            for sn in scored[:10]
        ]

        # Semantic clusters
        clusters = self.cluster_notes()
        cluster_summaries = [c.to_dict() for c in clusters]

        # Conflict summary
        conflict_list = [
            {
                "type": c.conflict_type,
                "description": c.description,
                "severity": c.severity,
                "between": [c.note_a_id, c.note_b_id],
            }
            for c in self._conflicts
        ]

        return {
            "total_notes": len(all_notes),
            "by_category": dict(by_category),
            "by_role": dict(by_role),
            "by_task": dict(by_task),
            "top_notes": top_notes,
            "clusters": cluster_summaries,
            "conflicts": conflict_list,
            "file_ownership": dict(self._file_owners),
            "stale_note_ids": stale_notes,
            "health": {
                "has_conflicts": len(self._conflicts) > 0,
                "stale_ratio": round(len(stale_notes) / max(len(all_notes), 1), 2),
                "cluster_count": len(clusters),
            },
        }

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


_STOP_WORDS = frozenset(
    {
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
)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return max(1, len(text) // 4)


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords (3+ chars, no stop words) from text."""
    words = set(re.findall(r"\w+", text.lower())) - _STOP_WORDS
    return {w for w in words if len(w) >= 3}


def _keyword_overlap(text_a: str, text_b: str) -> int:
    """Count shared meaningful keywords between two texts."""
    words_a = _extract_keywords(text_a)
    words_b = _extract_keywords(text_b)
    return len(words_a & words_b)


def _compute_time_decay(timestamp: str) -> float:
    """Compute time-decay bonus using exponential decay.

    Recent notes get up to +5.0 bonus. The bonus decays with a configurable
    half-life (default 30 minutes). A note that is exactly 1 half-life old
    gets +2.5. After ~3 half-lives the bonus is negligible (<0.6).

    Args:
        timestamp: ISO-format timestamp string from the note.

    Returns:
        Float bonus in [0.0, 5.0].
    """
    max_bonus = 5.0
    try:
        note_time = datetime.fromisoformat(timestamp)
        now = datetime.now(UTC)
        # Ensure both are timezone-aware for comparison
        if note_time.tzinfo is None:
            note_time = note_time.replace(tzinfo=UTC)
        age_seconds = max(0.0, (now - note_time).total_seconds())
        # Exponential decay: bonus * 2^(-age / half_life)
        decay_factor = math.pow(2.0, -age_seconds / DECAY_HALF_LIFE)
        return round(max_bonus * decay_factor, 2)
    except (ValueError, TypeError):
        # If timestamp is unparseable, give a middle-ground bonus
        return 2.5


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
