"""Semantic Memory — ChromaDB-based replacement for JSON keyword search.

Drop-in replacement for CrossProjectMemory.get_lessons() and
CrossProjectMemory.build_context_for_task(). All other methods
(add_lesson, record_tech_pattern, set_convention, etc.) are preserved
by subclassing CrossProjectMemory and only overriding search.

Benchmark proof (h2h_memory.py):
    - HiveMind JSON keyword: Recall@5 = 47%, Precision@5 = 45%
    - ChromaDB semantic:     Recall@5 = 79%, Precision@5 = 62%
    - ChromaDB won 6/10 queries, HiveMind won 1/10, 3 ties
    - Key wins: "password hashing" (0.33 -> 1.0), "background jobs" (0.0 -> 1.0)

Dependencies:
    pip install chromadb

License: Apache 2.0 — compatible with HiveMind's Apache 2.0
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from cross_project_memory import CrossProjectMemory

logger = logging.getLogger(__name__)

_chroma_client = None
_load_failed = False


def _get_chroma():
    """Lazy-load ChromaDB client."""
    global _chroma_client, _load_failed
    if _load_failed:
        return None
    if _chroma_client is not None:
        return _chroma_client
    try:
        import chromadb
        _chroma_client = chromadb
        logger.info("[SemanticMemory] ChromaDB loaded successfully")
        return _chroma_client
    except ImportError:
        _load_failed = True
        logger.warning("[SemanticMemory] ChromaDB not installed, using JSON fallback")
        return None


class SemanticMemory(CrossProjectMemory):
    """CrossProjectMemory with ChromaDB-powered semantic search.

    Inherits all storage methods from CrossProjectMemory (JSON-based).
    Overrides search methods to use ChromaDB vector similarity.
    The JSON file remains the source of truth for persistence;
    ChromaDB is rebuilt from it on startup.
    """

    def __init__(self, store_dir: str | Path):
        super().__init__(store_dir)
        self._collection = None
        self._synced_count = 0
        self._init_chroma()

    def _init_chroma(self) -> None:
        """Initialize ChromaDB collection from existing JSON lessons."""
        chroma = _get_chroma()
        if chroma is None:
            return
        try:
            persist_dir = str(self.store_dir / "chroma_db")
            client = chroma.PersistentClient(path=persist_dir)
            self._collection = client.get_or_create_collection(
                name="lessons",
                metadata={"hnsw:space": "cosine"},
            )
            self._sync_lessons_to_chroma()
        except Exception as e:
            logger.warning("[SemanticMemory] ChromaDB init failed: %s", e)
            self._collection = None

    def _sync_lessons_to_chroma(self) -> None:
        """Sync JSON lessons to ChromaDB collection."""
        if self._collection is None:
            return
        lessons = self._data.get("lessons", [])
        if len(lessons) == self._synced_count:
            return
        for i, lesson in enumerate(lessons[self._synced_count:], start=self._synced_count):
            doc = (
                f"{lesson.get('lesson', '')} "
                f"[category: {lesson.get('category', '')}] "
                f"[tech: {', '.join(lesson.get('tech_stack', []))}] "
                f"[severity: {lesson.get('severity', 'info')}]"
            )
            self._collection.add(
                documents=[doc],
                ids=[f"lesson_{i}"],
                metadatas=[{
                    "category": lesson.get("category", ""),
                    "severity": lesson.get("severity", "info"),
                    "project_id": lesson.get("project_id", ""),
                }],
            )
        self._synced_count = len(lessons)
        logger.info("[SemanticMemory] Synced %d lessons to ChromaDB", self._synced_count)

    def add_lesson(self, project_id: str, category: str, lesson: str,
                   tech_stack: list[str] | None = None, severity: str = "info") -> None:
        """Add lesson to both JSON and ChromaDB."""
        super().add_lesson(project_id, category, lesson, tech_stack, severity)
        self._sync_lessons_to_chroma()

    def get_lessons(self, category: str | None = None,
                    tech_stack: list[str] | None = None,
                    limit: int = 10, query: str | None = None) -> list[dict]:
        """Retrieve lessons using semantic search when query is provided.

        Args:
            category: Optional category filter.
            tech_stack: Optional tech stack filter.
            limit: Maximum results.
            query: Natural language query for semantic search.
        """
        if query and self._collection is not None:
            return self._semantic_search(query, category, limit)
        return super().get_lessons(category=category, tech_stack=tech_stack, limit=limit)

    def _semantic_search(self, query: str, category: str | None, limit: int) -> list[dict]:
        """Perform semantic search using ChromaDB."""
        try:
            where_filter = {"category": category} if category else None
            results = self._collection.query(
                query_texts=[query],
                n_results=min(limit, self._synced_count) if self._synced_count > 0 else limit,
                where=where_filter,
            )
            lessons = self._data.get("lessons", [])
            found = []
            for doc_id in results.get("ids", [[]])[0]:
                try:
                    idx = int(doc_id.split("_")[1])
                    if idx < len(lessons):
                        found.append(lessons[idx])
                except (IndexError, ValueError):
                    continue
            return found
        except Exception as e:
            logger.warning("[SemanticMemory] Semantic search failed: %s", e)
            return super().get_lessons(category=category, limit=limit)

    def build_context_for_task(self, task: str,
                               tech_stack: list[str] | None = None,
                               max_tokens: int = 1500) -> str:
        """Build context using semantic search for lesson retrieval."""
        if self._collection is None:
            return super().build_context_for_task(task, tech_stack, max_tokens)

        parts: list[str] = []
        char_budget = max_tokens * 4

        lessons = self._semantic_search(task, category=None, limit=5)
        if lessons:
            parts.append("<cross_project_lessons>")
            for lesson in lessons:
                entry = f"- [{lesson.get('severity', 'info').upper()}] {lesson['lesson']}"
                if lesson.get("category"):
                    entry += f" (category: {lesson['category']})"
                parts.append(entry)
            parts.append("</cross_project_lessons>")

        task_words = task.lower().split()[:10]
        patterns = self.get_tech_patterns(keywords=task_words)
        if patterns:
            parts.append("<cross_project_patterns>")
            for key, val in list(patterns.items())[:3]:
                parts.append(f"- {key}: {val.get('description', '')}")
                if val.get("config_snippet"):
                    parts.append(f"  Config: {val['config_snippet'][:300]}")
            parts.append("</cross_project_patterns>")

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
