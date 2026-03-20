"""File Output Manager — JIT Context for inter-agent artifact passing.

Instead of passing full text summaries between agents (which degrades like
a game of telephone), this module maintains a registry of **real files**
produced by each task.  Downstream agents receive lightweight file-path
references and read the source of truth directly.

Inspired by Anthropic's multi-agent research system and the JIT Context
pattern from "Memory in the Age of AI Agents" (arXiv:2512.13564).

Integration points:
    dag_executor._run_single_task  — call ``registry.register(output)`` after
                                     each task completion (successful or failed).
    dag_executor._run_single_task  — call ``registry.enhance_prompt(...)``
                                     before building the agent prompt.
    dag_executor._run_single_task  — call ``registry.validate_pre_execution(...)``
                                     before task execution starts.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts import TaskInput, TaskOutput, TaskStatus

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Maximum artifact file size in bytes (500 KB) — files larger than this are
# truncated when read for context injection.
ARTIFACT_MAX_SIZE_BYTES: int = 512_000  # 500 KB

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
    size_bytes: int = 0  # file size at registration time
    truncated: bool = False  # True if file exceeded ARTIFACT_MAX_SIZE_BYTES
    partial: bool = False  # True if registered from a failed task


# ── Artifact Registry ────────────────────────────────────────────────────────


class ArtifactRegistry:
    """Tracks all file artifacts produced during a DAG execution.

    Lifecycle:
        1. Created once per ``execute_graph`` call.
        2. After each task (successful or failed), ``register(output)`` is called.
        3. Before each task prompt is built, ``validate_pre_execution(task)``
           checks that expected upstream artifacts are available.
        4. Before each task prompt is built, ``enhance_prompt(task, prompt)``
           injects XML-structured file references for upstream dependencies.
    """

    def __init__(self, project_dir: str) -> None:
        self._project_dir = project_dir
        # task_id -> list of ArtifactRef
        self._refs: dict[str, list[ArtifactRef]] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, output: TaskOutput, *, allow_partial: bool = False) -> int:
        """Extract file references from a task output.

        For completed tasks, registers all artifacts normally.
        For failed tasks (when allow_partial=True), registers whatever artifacts
        exist on disk so downstream agents can access partial context.

        Returns the number of artifacts registered.
        """
        is_partial = output.status != TaskStatus.COMPLETED
        if is_partial and not allow_partial:
            return 0

        refs: list[ArtifactRef] = []

        # 1. Structured artifacts (typed, with metadata)
        for art in output.structured_artifacts:
            path = art.file_path
            if not path:
                continue
            ref = self._build_ref(
                task_id=output.task_id,
                path=path,
                description=art.title,
                partial=is_partial,
            )
            if ref is not None:
                refs.append(ref)

        # 2. Plain artifact paths (list[str] of file paths)
        seen_paths = {r.path for r in refs}
        for path in output.artifacts:
            if path in seen_paths:
                continue
            ref = self._build_ref(
                task_id=output.task_id,
                path=path,
                description=f"File produced by task {output.task_id}",
                partial=is_partial,
            )
            if ref is not None:
                refs.append(ref)
                seen_paths.add(path)

        self._refs[output.task_id] = refs
        if refs:
            log_fn = logger.info if not is_partial else logger.warning
            log_fn(
                "[FileOutputManager] Registered %d %sartifacts from task %s: %s",
                len(refs),
                "partial " if is_partial else "",
                output.task_id,
                [r.path for r in refs],
                extra={
                    "task_id": output.task_id,
                    "artifact_count": len(refs),
                    "partial": is_partial,
                    "paths": [r.path for r in refs],
                },
            )
        return len(refs)

    # ── Pre-execution Validation ─────────────────────────────────────────

    def validate_pre_execution(self, task: TaskInput) -> list[str]:
        """Validate that all expected upstream artifacts are available before execution.

        Checks:
        1. Each context_from dependency has registered artifacts in the registry.
        2. Each expected_input_artifact_type from the task can be found in
           upstream structured artifacts.

        Returns a list of warning messages (empty = all clear).
        Does NOT block execution — warnings are logged and returned for the caller.
        """
        warnings: list[str] = []

        # Check 1: context_from tasks have registered artifacts
        for upstream_id in task.context_from:
            upstream_refs = self._refs.get(upstream_id)
            if upstream_refs is None:
                msg = (
                    f"Task '{task.id}' depends on context from '{upstream_id}', "
                    f"but no artifacts were registered for that task"
                )
                warnings.append(msg)
                logger.warning(
                    "[FileOutputManager] Pre-execution: %s",
                    msg,
                    extra={
                        "task_id": task.id,
                        "upstream_id": upstream_id,
                        "validation": "missing_context_artifacts",
                    },
                )
            elif len(upstream_refs) == 0:
                msg = (
                    f"Task '{task.id}' depends on context from '{upstream_id}', "
                    f"but that task produced zero artifacts"
                )
                warnings.append(msg)
                logger.warning(
                    "[FileOutputManager] Pre-execution: %s",
                    msg,
                    extra={
                        "task_id": task.id,
                        "upstream_id": upstream_id,
                        "validation": "empty_context_artifacts",
                    },
                )
            else:
                # Check for partial artifacts from failed upstream
                partial_refs = [r for r in upstream_refs if r.partial]
                if partial_refs:
                    msg = (
                        f"Task '{task.id}' will receive {len(partial_refs)} partial "
                        f"artifact(s) from failed task '{upstream_id}' — context may be incomplete"
                    )
                    warnings.append(msg)
                    logger.warning(
                        "[FileOutputManager] Pre-execution: %s",
                        msg,
                        extra={
                            "task_id": task.id,
                            "upstream_id": upstream_id,
                            "partial_count": len(partial_refs),
                            "validation": "partial_artifacts",
                        },
                    )

        # Check 2: expected input artifact types are available
        if task.expected_input_artifact_types:
            # Collect all structured artifact types from upstream tasks
            available_types: set[str] = set()
            for upstream_id in task.context_from:
                for ref in self._refs.get(upstream_id, []):
                    # file_type from ArtifactRef is not the same as ArtifactType;
                    # we check the description which contains the artifact title
                    available_types.add(ref.file_type)

            for expected_type in task.expected_input_artifact_types:
                # We can't precisely match ArtifactType to file refs, but we can
                # check if the upstream task at least has *some* registered artifacts
                upstream_has_any = any(
                    len(self._refs.get(uid, [])) > 0 for uid in task.context_from
                )
                if not upstream_has_any:
                    msg = (
                        f"Task '{task.id}' expects artifact type '{expected_type.value}' "
                        f"from upstream tasks {task.context_from}, but no upstream "
                        f"artifacts are available"
                    )
                    warnings.append(msg)
                    logger.warning(
                        "[FileOutputManager] Pre-execution: %s",
                        msg,
                        extra={
                            "task_id": task.id,
                            "expected_type": expected_type.value,
                            "validation": "missing_expected_artifact_type",
                        },
                    )

        # Check 3: input_artifacts declared on the task exist on disk
        for artifact_path in task.input_artifacts:
            resolved = self._resolve(artifact_path)
            if not os.path.exists(resolved):
                msg = (
                    f"Task '{task.id}' declares input_artifact '{artifact_path}' "
                    f"but the file does not exist on disk"
                )
                warnings.append(msg)
                logger.warning(
                    "[FileOutputManager] Pre-execution: %s",
                    msg,
                    extra={
                        "task_id": task.id,
                        "artifact_path": artifact_path,
                        "validation": "missing_input_artifact_file",
                    },
                )

        return warnings

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
                            size_bytes=_safe_file_size(resolved),
                        )
                    )
                    seen.add(path)
        return refs

    def enhance_prompt(self, task: TaskInput, prompt: str) -> str:
        """Inject XML-structured file artifact references into the agent prompt.

        Produces machine-parseable <upstream_artifacts> sections with metadata
        about each file, including size warnings and partial-artifact notices.
        """
        refs = self.get_refs_for_task(task)
        if not refs:
            return prompt

        lines = [
            "",
            "<upstream_artifacts>",
            "  <instruction>Read these files directly — they are the source of truth. "
            "Do not rely on summary text when the actual file is available.</instruction>",
        ]

        for ref in refs:
            attrs = f"task='{ref.task_id}' type='{ref.file_type}'"
            if ref.truncated:
                attrs += " truncated='true'"
            if ref.partial:
                attrs += " partial='true'"
            if ref.size_bytes > 0:
                size_kb = ref.size_bytes / 1024
                attrs += f" size_kb='{size_kb:.1f}'"

            lines.append(f"  <artifact path='{ref.path}' {attrs}>")
            lines.append(f"    <description>{ref.description}</description>")
            if ref.truncated:
                lines.append(
                    f"    <warning>File exceeds {ARTIFACT_MAX_SIZE_BYTES // 1024}KB limit "
                    f"— content may be truncated when read by downstream agents</warning>"
                )
            if ref.partial:
                lines.append(
                    "    <warning>This artifact is from a failed task — "
                    "content may be incomplete or incorrect</warning>"
                )
            lines.append("  </artifact>")

        lines.append("</upstream_artifacts>")
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
                {
                    "path": r.path,
                    "type": r.file_type,
                    "description": r.description,
                    "size_bytes": r.size_bytes,
                    "truncated": r.truncated,
                    "partial": r.partial,
                }
                for r in refs
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
        partial_count = 0
        truncated_count = 0
        for r in all_refs:
            type_counts[r.file_type] = type_counts.get(r.file_type, 0) + 1
            if r.partial:
                partial_count += 1
            if r.truncated:
                truncated_count += 1
        return {
            "total_tasks": len(self._refs),
            "total_artifacts": len(all_refs),
            "by_type": type_counts,
            "partial_artifacts": partial_count,
            "truncated_artifacts": truncated_count,
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _resolve(self, path: str) -> str:
        """Resolve a path relative to project_dir if not absolute."""
        if os.path.isabs(path):
            return path
        return os.path.join(self._project_dir, path)

    def _build_ref(
        self,
        *,
        task_id: str,
        path: str,
        description: str,
        partial: bool = False,
    ) -> ArtifactRef | None:
        """Build an ArtifactRef with size validation, or None if file missing."""
        resolved = self._resolve(path)
        if not os.path.exists(resolved):
            return None

        size_bytes = _safe_file_size(resolved)
        truncated = size_bytes > ARTIFACT_MAX_SIZE_BYTES

        if truncated:
            logger.warning(
                "[FileOutputManager] Artifact '%s' from task '%s' exceeds %dKB limit "
                "(%dKB) — will be marked as truncated for downstream consumers",
                path,
                task_id,
                ARTIFACT_MAX_SIZE_BYTES // 1024,
                size_bytes // 1024,
                extra={
                    "task_id": task_id,
                    "path": path,
                    "size_bytes": size_bytes,
                    "limit_bytes": ARTIFACT_MAX_SIZE_BYTES,
                    "validation": "artifact_size_exceeded",
                },
            )

        return ArtifactRef(
            task_id=task_id,
            path=path,
            file_type=infer_file_type(path),
            description=description,
            size_bytes=size_bytes,
            truncated=truncated,
            partial=partial,
        )


def _safe_file_size(path: str) -> int:
    """Get file size in bytes, returning 0 on error."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
