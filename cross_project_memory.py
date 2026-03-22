"""
Cross-Project Memory — Learn from past projects to improve future ones.

Stores and retrieves reusable knowledge across projects:
- Technology patterns (e.g., "FastAPI + Docker needs specific Dockerfile config")
- Common pitfalls and their solutions
- Preferred conventions and configurations

**v2 (OSS Outsourcing)**: Uses ChromaDB for semantic vector search when available,
falling back to the original JSON-based store otherwise.  ChromaDB enables:
- True semantic search ("find lessons about Docker networking" matches
  "container port mapping issues" even without keyword overlap)
- Automatic embedding generation (no external API needed — uses local model)
- Persistent storage with efficient retrieval at scale

Configuration via environment:
    MEMORY_BACKEND       — "chroma" or "json" (default: "chroma")
    CHROMA_PERSIST_DIR   — ChromaDB storage directory (default: {store_dir}/chroma_db)

References:
    - ChromaDB: https://github.com/chroma-core/chroma
    - Original code review suggestion: "Use a Vector Database to allow Hivemind
      to 'improve over time' as it builds more projects."
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

import os

MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "chroma").lower()

# ---------------------------------------------------------------------------
# Cross-Project Memory Store (unified interface)
# ---------------------------------------------------------------------------

_MEMORY_FILE = "cross_project_memory.json"


class CrossProjectMemory:
    """Persistent cross-project knowledge store.

    Stores lessons learned, technology patterns, and conventions
    that can be shared across projects.

    Automatically uses ChromaDB for semantic search when available,
    falling back to JSON-based keyword matching otherwise.
    """

    def __init__(self, store_dir: str | Path):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.store_dir / _MEMORY_FILE

        # Try to initialize ChromaDB backend
        self._chroma_client = None
        self._chroma_collection = None
        self._use_chroma = False

        if MEMORY_BACKEND == "chroma":
            self._init_chroma()

        # Always load JSON data (used as fallback and for non-searchable fields)
        self._data: dict[str, Any] = self._load_json()

        # If ChromaDB is freshly initialized, migrate existing JSON data into it
        if self._use_chroma and self._data.get("lessons"):
            self._migrate_json_to_chroma()

    def _init_chroma(self) -> None:
        """Initialize ChromaDB client and collection."""
        try:
            import chromadb

            chroma_dir = os.getenv(
                "CHROMA_PERSIST_DIR",
                str(self.store_dir / "chroma_db"),
            )
            Path(chroma_dir).mkdir(parents=True, exist_ok=True)

            self._chroma_client = chromadb.PersistentClient(path=chroma_dir)
            self._chroma_collection = self._chroma_client.get_or_create_collection(
                name="cross_project_memory",
                metadata={
                    "hnsw:space": "cosine",
                    "description": "Lessons, patterns, and conventions from past projects",
                },
            )
            self._use_chroma = True
            logger.info(
                f"[CrossMemory] ChromaDB initialized at {chroma_dir} "
                f"({self._chroma_collection.count()} documents)"
            )

        except ImportError:
            logger.info(
                "[CrossMemory] ChromaDB not installed — using JSON fallback. "
                "Install with: pip install chromadb"
            )
        except Exception as e:
            logger.warning(
                f"[CrossMemory] Failed to initialize ChromaDB: {e} — using JSON fallback"
            )

    def _migrate_json_to_chroma(self) -> None:
        """One-time migration of existing JSON lessons into ChromaDB."""
        if not self._use_chroma or not self._chroma_collection:
            return

        existing_count = self._chroma_collection.count()
        if existing_count > 0:
            return  # Already migrated

        lessons = self._data.get("lessons", [])
        if not lessons:
            return

        ids = []
        documents = []
        metadatas = []

        for i, lesson in enumerate(lessons):
            doc_id = f"migrated_{i}_{int(lesson.get('timestamp', 0))}"
            doc_text = lesson.get("lesson", "")
            if not doc_text:
                continue

            ids.append(doc_id)
            documents.append(doc_text)
            metadatas.append({
                "project_id": lesson.get("project_id", ""),
                "category": lesson.get("category", ""),
                "severity": lesson.get("severity", "info"),
                "tech_stack": json.dumps(lesson.get("tech_stack", [])),
                "timestamp": lesson.get("timestamp", 0),
                "type": "lesson",
            })

        if ids:
            # ChromaDB has a batch limit; chunk if needed
            batch_size = 100
            for start in range(0, len(ids), batch_size):
                end = start + batch_size
                self._chroma_collection.add(
                    ids=ids[start:end],
                    documents=documents[start:end],
                    metadatas=metadatas[start:end],
                )

            logger.info(f"[CrossMemory] Migrated {len(ids)} lessons from JSON to ChromaDB")

    # --- JSON persistence (always maintained as backup) ---

    def _load_json(self) -> dict[str, Any]:
        """Load memory from JSON disk file."""
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[CrossMemory] Failed to load JSON: {e}")
        return {
            "lessons": [],
            "tech_patterns": {},
            "conventions": {},
            "version": 2,
        }

    def _save_json(self) -> None:
        """Persist memory to JSON disk file."""
        try:
            self._file.write_text(
                json.dumps(self._data, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"[CrossMemory] Failed to save JSON: {e}")

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

        Stores in both ChromaDB (for semantic search) and JSON (for backup).
        """
        entry = {
            "project_id": project_id,
            "category": category,
            "lesson": lesson,
            "tech_stack": tech_stack or [],
            "severity": severity,
            "timestamp": time.time(),
        }

        # Store in ChromaDB for semantic search
        if self._use_chroma and self._chroma_collection:
            try:
                doc_id = f"lesson_{project_id}_{int(time.time() * 1000)}"
                self._chroma_collection.add(
                    ids=[doc_id],
                    documents=[lesson],
                    metadatas=[{
                        "project_id": project_id,
                        "category": category,
                        "severity": severity,
                        "tech_stack": json.dumps(tech_stack or []),
                        "timestamp": time.time(),
                        "type": "lesson",
                    }],
                )
            except Exception as e:
                logger.warning(f"[CrossMemory] ChromaDB add failed: {e}")

        # Always store in JSON as backup
        self._data.setdefault("lessons", []).append(entry)
        if len(self._data["lessons"]) > 200:
            self._data["lessons"] = self._data["lessons"][-200:]
        self._save_json()

        logger.info(f"[CrossMemory] Added lesson: {category} — {lesson[:80]}")

    def get_lessons(
        self,
        category: str | None = None,
        tech_stack: list[str] | None = None,
        limit: int = 10,
        query: str | None = None,
    ) -> list[dict]:
        """Retrieve relevant lessons.

        When ChromaDB is available and a query or tech_stack is provided,
        uses semantic search for better relevance.  Falls back to keyword
        matching on the JSON store otherwise.
        """
        # Try semantic search with ChromaDB
        if self._use_chroma and self._chroma_collection and (query or tech_stack):
            try:
                search_text = query or " ".join(tech_stack or [])
                where_filter = None
                if category:
                    where_filter = {"category": category}

                results = self._chroma_collection.query(
                    query_texts=[search_text],
                    n_results=min(limit, 20),
                    where=where_filter if where_filter else None,
                )

                if results and results.get("documents") and results["documents"][0]:
                    lessons = []
                    for i, doc in enumerate(results["documents"][0]):
                        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                        distance = results["distances"][0][i] if results.get("distances") else 0
                        lessons.append({
                            "lesson": doc,
                            "project_id": meta.get("project_id", ""),
                            "category": meta.get("category", ""),
                            "severity": meta.get("severity", "info"),
                            "tech_stack": json.loads(meta.get("tech_stack", "[]")),
                            "timestamp": meta.get("timestamp", 0),
                            "relevance_score": 1.0 - distance,  # cosine distance → similarity
                        })
                    return lessons[:limit]

            except Exception as e:
                logger.warning(f"[CrossMemory] ChromaDB query failed: {e} — falling back to JSON")

        # Fallback: JSON-based keyword matching (original logic)
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

        Stored in both ChromaDB (semantic) and JSON (structured).
        """
        # Store in ChromaDB
        if self._use_chroma and self._chroma_collection:
            try:
                doc_text = f"{pattern_key}: {description}"
                if config_snippet:
                    doc_text += f"\nConfig: {config_snippet[:500]}"

                doc_id = f"pattern_{pattern_key}"
                # Upsert to handle updates
                self._chroma_collection.upsert(
                    ids=[doc_id],
                    documents=[doc_text],
                    metadatas=[{
                        "pattern_key": pattern_key,
                        "project_id": project_id,
                        "timestamp": time.time(),
                        "type": "tech_pattern",
                    }],
                )
            except Exception as e:
                logger.warning(f"[CrossMemory] ChromaDB pattern add failed: {e}")

        # Always store in JSON
        patterns = self._data.setdefault("tech_patterns", {})
        patterns[pattern_key] = {
            "description": description,
            "config_snippet": config_snippet,
            "project_id": project_id,
            "timestamp": time.time(),
            "use_count": patterns.get(pattern_key, {}).get("use_count", 0) + 1,
        }
        self._save_json()
        logger.info(f"[CrossMemory] Recorded tech pattern: {pattern_key}")

    def get_tech_patterns(self, keywords: list[str] | None = None) -> dict[str, Any]:
        """Retrieve technology patterns, optionally filtered by keywords.

        Uses ChromaDB semantic search when available for better matching.
        """
        # Try semantic search
        if self._use_chroma and self._chroma_collection and keywords:
            try:
                results = self._chroma_collection.query(
                    query_texts=[" ".join(keywords)],
                    n_results=10,
                    where={"type": "tech_pattern"},
                )
                if results and results.get("documents") and results["documents"][0]:
                    patterns = {}
                    for i, doc in enumerate(results["documents"][0]):
                        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                        key = meta.get("pattern_key", f"pattern_{i}")
                        # Try to get full data from JSON if available
                        json_pattern = self._data.get("tech_patterns", {}).get(key)
                        if json_pattern:
                            patterns[key] = json_pattern
                        else:
                            patterns[key] = {
                                "description": doc,
                                "project_id": meta.get("project_id", ""),
                                "timestamp": meta.get("timestamp", 0),
                            }
                    return patterns
            except Exception as e:
                logger.debug(f"[CrossMemory] ChromaDB pattern query failed: {e}")

        # Fallback: JSON keyword matching
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
        """Record a project convention."""
        conventions = self._data.setdefault("conventions", {})
        conventions[key] = {
            "value": value,
            "project_id": project_id,
            "timestamp": time.time(),
        }
        self._save_json()

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
        When ChromaDB is available, uses semantic search to find the most
        relevant lessons for the current task — even without keyword overlap.
        """
        parts: list[str] = []
        char_budget = max_tokens * 4  # ~4 chars per token

        # Relevant lessons (semantic search when available)
        lessons = self.get_lessons(
            tech_stack=tech_stack,
            query=task,  # NEW: pass the task as semantic query
            limit=5,
        )
        if lessons:
            parts.append("<cross_project_lessons>")
            for lesson in lessons:
                entry = f"- [{lesson.get('severity', 'info').upper()}] {lesson['lesson']}"
                if lesson.get("category"):
                    entry += f" (category: {lesson['category']})"
                if lesson.get("relevance_score"):
                    entry += f" [relevance: {lesson['relevance_score']:.2f}]"
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
        """Automatically extract lessons from completed task outputs."""
        count = 0
        for output in outputs:
            status = output.get("status", "")
            summary = output.get("summary", "")
            issues = output.get("issues", [])

            if status == "failed" and summary:
                self.add_lesson(
                    project_id=project_id,
                    category="failure",
                    lesson=f"Task failed: {summary[:200]}",
                    tech_stack=tech_stack,
                    severity="warning",
                )
                count += 1

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

    # --- Semantic search (new capability) ---

    def semantic_search(self, query: str, limit: int = 10) -> list[dict]:
        """Search all memory (lessons + patterns) by semantic similarity.

        This is a new capability enabled by ChromaDB — it finds relevant
        knowledge even when there's no keyword overlap with the query.

        Falls back to returning recent lessons if ChromaDB is unavailable.
        """
        if self._use_chroma and self._chroma_collection:
            try:
                results = self._chroma_collection.query(
                    query_texts=[query],
                    n_results=limit,
                )
                if results and results.get("documents") and results["documents"][0]:
                    items = []
                    for i, doc in enumerate(results["documents"][0]):
                        meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                        distance = results["distances"][0][i] if results.get("distances") else 0
                        items.append({
                            "text": doc,
                            "type": meta.get("type", "unknown"),
                            "category": meta.get("category", ""),
                            "project_id": meta.get("project_id", ""),
                            "relevance_score": 1.0 - distance,
                        })
                    return items
            except Exception as e:
                logger.warning(f"[CrossMemory] Semantic search failed: {e}")

        # Fallback: return most recent lessons
        return [
            {"text": l["lesson"], "type": "lesson", **l}
            for l in self._data.get("lessons", [])[-limit:]
        ]

    # --- Stats ---

    @property
    def stats(self) -> dict[str, int | str]:
        """Return memory statistics."""
        base = {
            "lessons": len(self._data.get("lessons", [])),
            "tech_patterns": len(self._data.get("tech_patterns", {})),
            "conventions": len(self._data.get("conventions", {})),
            "backend": "chroma" if self._use_chroma else "json",
        }
        if self._use_chroma and self._chroma_collection:
            try:
                base["chroma_documents"] = self._chroma_collection.count()
            except Exception:
                base["chroma_documents"] = -1
        return base
