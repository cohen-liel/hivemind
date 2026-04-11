"""
Cross-Project Memory — Learn from past projects to improve future ones.

Stores and retrieves reusable knowledge across projects:
- Technology patterns (e.g., "FastAPI + Docker needs specific Dockerfile config")
- Common pitfalls and their solutions
- Preferred conventions and configurations

Uses a lightweight JSON-based store (no external vector DB required).
Can be upgraded to use Chroma/Pinecone for semantic search in the future.

Suggested in code review: "Use a Vector Database to allow Hivemind to
'improve over time' as it builds more projects."
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from config import MAX_CONVENTIONS, MAX_TECH_PATTERNS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-Project Memory Store
# ---------------------------------------------------------------------------

_MEMORY_FILE = "cross_project_memory.json"


class CrossProjectMemory:
    """Persistent cross-project knowledge store.

    Stores lessons learned, technology patterns, and conventions
    that can be shared across projects.
    """

    def __init__(self, store_dir: str | Path):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.store_dir / _MEMORY_FILE
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """Load memory from disk."""
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[CrossMemory] Failed to load: {e}")
        return {
            "lessons": [],
            "tech_patterns": {},
            "conventions": {},
            "version": 1,
        }

    def _save(self) -> None:
        """Persist memory to disk."""
        try:
            self._file.write_text(
                json.dumps(self._data, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"[CrossMemory] Failed to save: {e}")

    # --- Lessons Learned ---

    def add_lesson(
        self,
        project_id: str,
        category: str,
        lesson: str,
        tech_stack: list[str] | None = None,
        severity: str = "info",
    ) -> None:
        """Record a lesson learned from a project.

        Args:
            project_id: Source project
            category: Category (e.g., 'docker', 'testing', 'deployment')
            lesson: The lesson text
            tech_stack: Related technologies
            severity: 'info' | 'warning' | 'critical'
        """
        entry = {
            "project_id": project_id,
            "category": category,
            "lesson": lesson,
            "tech_stack": tech_stack or [],
            "severity": severity,
            "timestamp": time.time(),
        }
        self._data.setdefault("lessons", []).append(entry)

        # Keep max 200 lessons (FIFO)
        if len(self._data["lessons"]) > 200:
            self._data["lessons"] = self._data["lessons"][-200:]

        self._save()
        logger.info(f"[CrossMemory] Added lesson: {category} — {lesson[:80]}")

    def get_lessons(
        self,
        category: str | None = None,
        tech_stack: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Retrieve relevant lessons.

        Args:
            category: Filter by category
            tech_stack: Filter by technology overlap
            limit: Maximum results
        """
        lessons = self._data.get("lessons", [])

        if category:
            lessons = [l for l in lessons if l.get("category") == category]

        if tech_stack:
            tech_set = {t.lower() for t in tech_stack}
            scored = []
            for lesson in lessons:
                lesson_tech = {t.lower() for t in lesson.get("tech_stack", [])}
                overlap = len(tech_set & lesson_tech)
                if overlap > 0:
                    scored.append((overlap, lesson))
            scored.sort(key=lambda x: (-x[0], -x[1].get("timestamp", 0)))
            lessons = [l for _, l in scored]

        return lessons[:limit]

    # --- Technology Patterns ---

    def record_tech_pattern(
        self,
        pattern_key: str,
        description: str,
        config_snippet: str | None = None,
        project_id: str = "",
    ) -> None:
        """Record a technology configuration pattern.

        Args:
            pattern_key: Unique key (e.g., 'fastapi-docker', 'react-vite-proxy')
            description: What this pattern solves
            config_snippet: Example configuration
            project_id: Source project
        """
        patterns = self._data.setdefault("tech_patterns", {})
        patterns[pattern_key] = {
            "description": description,
            "config_snippet": config_snippet,
            "project_id": project_id,
            "timestamp": time.time(),
            "use_count": patterns.get(pattern_key, {}).get("use_count", 0) + 1,
        }
        # Cap to MAX_TECH_PATTERNS — evict oldest by timestamp
        if len(patterns) > MAX_TECH_PATTERNS:
            oldest = sorted(patterns, key=lambda k: patterns[k].get("timestamp", 0))
            for k in oldest[: len(patterns) - MAX_TECH_PATTERNS]:
                del patterns[k]
        self._save()
        logger.info(f"[CrossMemory] Recorded tech pattern: {pattern_key}")

    def get_tech_patterns(self, keywords: list[str] | None = None) -> dict[str, Any]:
        """Retrieve technology patterns, optionally filtered by keywords."""
        patterns = self._data.get("tech_patterns", {})
        if not keywords:
            return patterns

        kw_lower = [k.lower() for k in keywords]
        return {
            key: val
            for key, val in patterns.items()
            if any(kw in key.lower() or kw in val.get("description", "").lower() for kw in kw_lower)
        }

    # --- Conventions ---

    def set_convention(self, key: str, value: str, project_id: str = "") -> None:
        """Record a project convention (e.g., 'naming: snake_case for Python files').

        Args:
            key: Convention key (e.g., 'python_naming', 'test_structure')
            value: Convention description
            project_id: Source project
        """
        conventions = self._data.setdefault("conventions", {})
        conventions[key] = {
            "value": value,
            "project_id": project_id,
            "timestamp": time.time(),
        }
        # Cap to MAX_CONVENTIONS — evict oldest by timestamp
        if len(conventions) > MAX_CONVENTIONS:
            oldest = sorted(conventions, key=lambda k: conventions[k].get("timestamp", 0))
            for k in oldest[: len(conventions) - MAX_CONVENTIONS]:
                del conventions[k]
        self._save()

    def get_conventions(self) -> dict[str, str]:
        """Get all conventions as {key: value} pairs."""
        return {
            key: entry.get("value", "") for key, entry in self._data.get("conventions", {}).items()
        }

    # --- Context Builder ---

    def build_context_for_task(
        self,
        task: str,
        tech_stack: list[str] | None = None,
        max_tokens: int = 1500,
    ) -> str:
        """Build a context string with relevant cross-project knowledge.

        This is injected into the PM's prompt to leverage past experience.

        Args:
            task: The current task description
            tech_stack: Known technology stack
            max_tokens: Approximate max character budget

        Returns:
            Formatted context string (empty if no relevant knowledge)
        """
        parts: list[str] = []
        char_budget = max_tokens * 4  # ~4 chars per token

        # Relevant lessons
        lessons = self.get_lessons(tech_stack=tech_stack, limit=5)
        if lessons:
            parts.append("<cross_project_lessons>")
            for lesson in lessons:
                entry = f"- [{lesson.get('severity', 'info').upper()}] {lesson['lesson']}"
                if lesson.get("category"):
                    entry += f" (category: {lesson['category']})"
                parts.append(entry)
            parts.append("</cross_project_lessons>")

        # Relevant tech patterns
        task_words = task.lower().split()[:10]
        patterns = self.get_tech_patterns(keywords=task_words)
        if patterns:
            parts.append("<cross_project_patterns>")
            for key, val in list(patterns.items())[:3]:
                parts.append(f"- {key}: {val.get('description', '')}")
                if val.get("config_snippet"):
                    snippet = val["config_snippet"][:300]
                    parts.append(f"  Config: {snippet}")
            parts.append("</cross_project_patterns>")

        # Conventions
        conventions = self.get_conventions()
        if conventions:
            parts.append("<cross_project_conventions>")
            for key, value in list(conventions.items())[:5]:
                parts.append(f"- {key}: {value}")
            parts.append("</cross_project_conventions>")

        result = "\n".join(parts)
        if len(result) > char_budget:
            result = result[:char_budget] + "\n... (truncated)"

        return result

    # --- Auto-extract lessons from task outputs ---

    def extract_lessons_from_outputs(
        self,
        project_id: str,
        outputs: list[dict],
        tech_stack: list[str] | None = None,
    ) -> int:
        """Automatically extract lessons from completed task outputs.

        Looks for patterns like:
        - Failed tasks with known error categories
        - Configuration issues that were resolved
        - Dependency problems

        Returns the number of lessons extracted.
        """
        count = 0
        for output in outputs:
            status = output.get("status", "")
            summary = output.get("summary", "")
            issues = output.get("issues", [])

            # Extract from failures
            if status == "failed" and summary:
                self.add_lesson(
                    project_id=project_id,
                    category="failure",
                    lesson=f"Task failed: {summary[:200]}",
                    tech_stack=tech_stack,
                    severity="warning",
                )
                count += 1

            # Extract from issues
            for issue in issues[:3]:
                if any(
                    kw in issue.lower()
                    for kw in ("config", "dependency", "version", "compatibility")
                ):
                    self.add_lesson(
                        project_id=project_id,
                        category="configuration",
                        lesson=issue[:200],
                        tech_stack=tech_stack,
                        severity="info",
                    )
                    count += 1

        return count

    # --- Stats ---

    @property
    def stats(self) -> dict[str, int]:
        """Return memory statistics."""
        return {
            "lessons": len(self._data.get("lessons", [])),
            "tech_patterns": len(self._data.get("tech_patterns", {})),
            "conventions": len(self._data.get("conventions", {})),
        }
