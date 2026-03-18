"""File Output Manager — JIT Context for inter-agent artifact passing.

Instead of passing full text summaries between agents (which degrades like
a game of telephone), this module maintains a registry of **real files**
produced by each task.  Downstream agents receive lightweight file-path
references and read the source of truth directly.

Inspired by Anthropic's multi-agent research system and the JIT Context
pattern from "Memory in the Age of AI Agents" (arXiv:2512.13564).

Integration points:
    dag_executor._run_single_task  — call ``registry.register(output)`` after
                                     each successful task completion.
    dag_executor._run_single_task  — call ``registry.enhance_prompt(...)``
                                     before building the agent prompt.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts import TaskInput, TaskOutput, TaskStatus

logger = logging.getLogger(__name__)

# ── File-type inference ──────────────────────────────────────────────────────

_EXT_MAP: dict[str, str] = {
    ".ts": "code",
    ".tsx": "code",
    ".js": "code",
    ".jsx": "code",
    ".py": "code",
    ".go": "code",
    ".rs": "code",
    ".java": "code",
    ".sql": "code",
    ".sh": "code",
    ".css": "code",
    ".scss": "code",
    ".html": "markup",
    ".xml": "markup",
    ".svg": "markup",
    ".json": "data",
    ".yaml": "data",
    ".yml": "data",
    ".toml": "data",
    ".csv": "data",
    ".env": "data",
    ".md": "doc",
    ".txt": "doc",
    ".rst": "doc",
    ".png": "asset",
    ".jpg": "asset",
    ".gif": "asset",
    ".ico": "asset",
    ".woff": "asset",
    ".woff2": "asset",
    ".ttf": "asset",
    ".lock": "lockfile",
}


def infer_file_type(path: str) -> str:
    """Return a human-friendly file type label based on extension."""
    ext = Path(path).suffix.lower()
    return _EXT_MAP.get(ext, "file")


# ── Artifact Reference ───────────────────────────────────────────────────────


@dataclass
class ArtifactRef:
    """A lightweight pointer to a file produced by a task."""

    task_id: str
    path: str  # relative to project root
    file_type: str  # code | data | doc | asset | ...
    description: str  # from Artifact.title or auto-generated


# ── Artifact Registry ────────────────────────────────────────────────────────


class ArtifactRegistry:
    """Tracks all file artifacts produced during a DAG execution.

    Lifecycle:
        1. Created once per ``execute_graph`` call.
        2. After each successful task, ``register(output)`` is called.
        3. Before each task prompt is built, ``enhance_prompt(task, prompt)``
           injects file references for upstream dependencies.
    """

    def __init__(self, project_dir: str) -> None:
        self._project_dir = project_dir
        # task_id -> list of ArtifactRef
        self._refs: dict[str, list[ArtifactRef]] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, output: TaskOutput) -> int:
        """Extract file references from a completed task output.

        Returns the number of artifacts registered.
        """
        if output.status != TaskStatus.COMPLETED:
            return 0

        refs: list[ArtifactRef] = []

        # 1. Structured artifacts (typed, with metadata)
        for art in output.structured_artifacts:
            path = art.file_path
            if not path:
                continue
            resolved = self._resolve(path)
            if resolved and os.path.exists(resolved):
                refs.append(
                    ArtifactRef(
                        task_id=output.task_id,
                        path=path,
                        file_type=infer_file_type(path),
                        description=art.title,
                    )
                )

        # 2. Plain artifact paths (list[str] of file paths)
        seen_paths = {r.path for r in refs}
        for path in output.artifacts:
            if path in seen_paths:
                continue
            resolved = self._resolve(path)
            if resolved and os.path.exists(resolved):
                refs.append(
                    ArtifactRef(
                        task_id=output.task_id,
                        path=path,
                        file_type=infer_file_type(path),
                        description=f"File produced by task {output.task_id}",
                    )
                )
                seen_paths.add(path)

        self._refs[output.task_id] = refs
        if refs:
            logger.info(
                "[FileOutputManager] Registered %d artifacts from task %s: %s",
                len(refs),
                output.task_id,
                [r.path for r in refs],
            )
        return len(refs)

    # ── Prompt Enhancement ───────────────────────────────────────────────

    def get_refs_for_task(self, task: TaskInput) -> list[ArtifactRef]:
        """Collect artifact refs from all upstream tasks (context_from)."""
        refs: list[ArtifactRef] = []
        seen: set[str] = set()
        for upstream_id in task.context_from:
            for ref in self._refs.get(upstream_id, []):
                if ref.path not in seen:
                    refs.append(ref)
                    seen.add(ref.path)
        # Also include input_artifacts declared on the task
        for path in task.input_artifacts:
            if path not in seen:
                resolved = self._resolve(path)
                if resolved and os.path.exists(resolved):
                    refs.append(
                        ArtifactRef(
                            task_id="input",
                            path=path,
                            file_type=infer_file_type(path),
                            description=f"Input artifact: {Path(path).name}",
                        )
                    )
                    seen.add(path)
        return refs

    def enhance_prompt(self, task: TaskInput, prompt: str) -> str:
        """Inject file artifact references into the agent prompt.

        Adds a clearly-delimited section telling the agent which files
        from upstream tasks are available and should be read directly.
        """
        refs = self.get_refs_for_task(task)
        if not refs:
            return prompt

        lines = [
            "",
            "## Upstream Artifacts (read these files directly — they are the source of truth)",
            "",
        ]
        for ref in refs:
            lines.append(f"- **{ref.path}** ({ref.file_type}) — {ref.description}")
        lines.append("")
        lines.append(
            "IMPORTANT: Read the files listed above instead of relying on "
            "summary text. They contain the actual, up-to-date content."
        )
        lines.append("")

        return prompt + "\n".join(lines)

    # ── Manifest ─────────────────────────────────────────────────────────

    def save_manifest(self, path: str | None = None) -> str:
        """Write a JSON manifest of all registered artifacts.

        Returns the path to the manifest file.
        """
        manifest_path = path or os.path.join(
            self._project_dir, ".hivemind", "artifact_manifest.json"
        )
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)

        data: dict[str, Any] = {}
        for task_id, refs in self._refs.items():
            data[task_id] = [
                {"path": r.path, "type": r.file_type, "description": r.description} for r in refs
            ]

        with open(manifest_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info("[FileOutputManager] Manifest saved to %s", manifest_path)
        return manifest_path

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return summary statistics."""
        all_refs = [r for refs in self._refs.values() for r in refs]
        type_counts: dict[str, int] = {}
        for r in all_refs:
            type_counts[r.file_type] = type_counts.get(r.file_type, 0) + 1
        return {
            "total_tasks": len(self._refs),
            "total_artifacts": len(all_refs),
            "by_type": type_counts,
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _resolve(self, path: str) -> str:
        """Resolve a path relative to project_dir if not absolute."""
        if os.path.isabs(path):
            return path
        return os.path.join(self._project_dir, path)
