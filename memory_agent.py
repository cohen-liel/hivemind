"""
Memory Agent — The project's long-term memory and knowledge manager.

The Memory Agent runs AFTER each DAG execution round and:
1. Reads all TaskOutputs (including structured artifacts)
2. Updates the PROJECT_MANIFEST.md with new knowledge
3. Maintains an append-only decision log
4. Writes a structured MemorySnapshot for the PM to consume next session
5. Detects cross-agent inconsistencies (e.g. frontend expects API that backend didn't build)

The Memory Agent does NOT write code. It only reads, summarises, and writes
knowledge files to .hivemind/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# state is imported lazily inside functions to avoid circular import
# (memory_agent → state → orchestrator → sdk_client → claude_agent_sdk)
from contracts import (
    ArtifactType,
    MemorySnapshot,
    TaskGraph,
    TaskOutput,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Memory Agent System Prompt
# ---------------------------------------------------------------------------

MEMORY_SYSTEM_PROMPT = (
    "<role>\n"
    "You are the Memory Agent — the project's long-term memory and knowledge manager.\n"
    "You read all task outputs and produce a structured JSON MemorySnapshot.\n"
    "You do NOT write code. You only read, analyse, and summarise.\n"
    "</role>\n\n"
    "<input>\n"
    "You receive:\n"
    "  - The current project manifest (if it exists)\n"
    "  - All task outputs from the latest execution round\n"
    "  - Structured artifacts produced by agents\n"
    "</input>\n\n"
    "<output_schema>\n"
    "Produce a JSON MemorySnapshot with these fields:\n"
    "  - architecture_summary: Current architecture in 3-5 sentences\n"
    "  - tech_stack: Technology choices, e.g. {'frontend': 'React+TS', 'backend': 'FastAPI'}\n"
    "  - key_decisions: Important architectural decisions (APPEND to existing, never replace)\n"
    "  - known_issues: Unresolved issues or tech debt\n"
    "  - api_surface: Current API endpoints [{method, path, description}]\n"
    "  - db_tables: Current database tables\n"
    "  - file_map: Key files and their purpose, e.g. {'src/api/auth.py': 'JWT authentication'}\n"
    "  - last_updated_by: The task ID that triggered this update\n"
    "</output_schema>\n\n"
    "<rules>\n"
    "1. NEVER delete existing key_decisions — only append new ones\n"
    "2. Merge new information with existing — do not overwrite unless correcting an error\n"
    "3. Be concise but complete — this is the PM's primary context for future tasks\n"
    "4. If agents produced conflicting artifacts, note it in known_issues\n"
    "5. Track which agent produced which artifact — this helps debug 'telephone game' issues\n"
    "</rules>\n\n"
    "<output_format>\n"
    "OUTPUT ONLY THE JSON. No markdown, no explanation. Start with { and end with }.\n"
    "</output_format>"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def update_project_memory(
    project_dir: str,
    project_id: str,
    graph: TaskGraph,
    outputs: list[TaskOutput],
    use_llm: bool = True,
    on_stream: Callable | None = None,
) -> MemorySnapshot:
    """
    Run the Memory Agent to update project knowledge after a DAG execution.

    Args:
        project_dir: Project working directory
        project_id: Project identifier
        graph: The executed TaskGraph
        outputs: All TaskOutputs from the execution
        use_llm: If True, use Claude to generate the snapshot. If False, use
                 heuristic-only mode (cheaper, for small tasks).

    Returns:
        The updated MemorySnapshot
    """
    if not outputs:
        logger.info("[Memory] No outputs to process — skipping memory update.")
        return MemorySnapshot(project_id=project_id)

    forge_dir = Path(project_dir) / ".hivemind"
    forge_dir.mkdir(parents=True, exist_ok=True)

    # Load existing memory
    existing = _load_existing_snapshot(forge_dir, project_id)

    if use_llm and _should_use_llm(outputs):
        snapshot = await _llm_update(project_dir, project_id, graph, outputs, existing, on_stream)
    else:
        snapshot = _heuristic_update(project_id, graph, outputs, existing)

    # Write the snapshot
    _save_snapshot(forge_dir, snapshot)

    # Write human-readable manifest
    _write_manifest(forge_dir, snapshot, graph)

    # Write artifact index
    _write_artifact_index(forge_dir, outputs)

    # Write decision log (append-only)
    _append_decision_log(forge_dir, graph, outputs)

    # Detect cross-agent inconsistencies
    inconsistencies = detect_inconsistencies(outputs)
    if inconsistencies:
        logger.warning(f"[Memory] Cross-agent inconsistencies detected: {inconsistencies}")
        snapshot.known_issues.extend(inconsistencies)
        _save_snapshot(forge_dir, snapshot)

    logger.info(
        f"[Memory] Updated project memory: {len(snapshot.key_decisions)} decisions, "
        f"{len(snapshot.file_map)} files mapped, {len(snapshot.known_issues)} issues"
    )
    return snapshot


# ---------------------------------------------------------------------------
# LLM-based memory update (for complex tasks)
# ---------------------------------------------------------------------------


async def _llm_update(
    project_dir: str,
    project_id: str,
    graph: TaskGraph,
    outputs: list[TaskOutput],
    existing: MemorySnapshot | None,
    on_stream: Callable | None = None,
) -> MemorySnapshot:
    """Use Claude to generate a rich MemorySnapshot from task outputs."""
    import state

    sdk = state.sdk_client
    if sdk is None:
        logger.warning("[Memory] SDK not available, falling back to heuristic mode")
        return _heuristic_update(project_id, graph, outputs, existing)

    prompt = _build_memory_prompt(project_id, graph, outputs, existing)

    try:
        response = await sdk.query_with_retry(
            prompt=prompt,
            system_prompt=MEMORY_SYSTEM_PROMPT,
            cwd=project_dir,
            max_turns=2,  # Memory agent only thinks, minimal tool use
            max_budget_usd=0.50,  # Memory queries are cheap
            allowed_tools=[],  # No tools — read-only analysis
            agent_role="memory",  # FIX: Pass role for correct timeout (was missing)
            on_stream=on_stream,
        )

        if response.is_error:
            logger.warning(f"[Memory] LLM error: {response.error_message}. Using heuristic.")
            return _heuristic_update(project_id, graph, outputs, existing)

        snapshot = _parse_memory_response(response.text, project_id, existing)
        snapshot.cumulative_cost_usd = (
            (existing.cumulative_cost_usd if existing else 0.0)
            + sum(o.cost_usd for o in outputs)
            + response.cost_usd
        )
        return snapshot

    except asyncio.CancelledError:
        raise  # Never swallow cancellation
    except Exception as exc:
        logger.warning(
            f"[Memory] LLM update failed: {type(exc).__name__}: {exc}. Using heuristic fallback.",
            exc_info=True,
        )
        return _heuristic_update(project_id, graph, outputs, existing)


def _build_memory_prompt(
    project_id: str,
    graph: TaskGraph,
    outputs: list[TaskOutput],
    existing: MemorySnapshot | None,
) -> str:
    """Build the prompt for the Memory Agent using XML structure."""
    parts = [
        f"<project_id>{project_id}</project_id>",
        f"<vision>{graph.vision}</vision>",
    ]

    if existing:
        parts.append(
            f"<existing_memory>\n"
            f"Merge with this — do NOT replace, only append and update.\n"
            f"{existing.model_dump_json(indent=2)}\n"
            f"</existing_memory>"
        )

    parts.append("<task_outputs>")
    for output in outputs:
        task = graph.get_task(output.task_id)
        role = task.role if task else "unknown"
        parts.append(
            f"  <task_result id='{output.task_id}' role='{role}' status='{output.status.value}'>\n"
            f"    <summary>{output.summary}</summary>\n"
            f"    <files>{', '.join(output.artifacts[:10]) or 'none'}</files>\n"
            f"    <issues>{'; '.join(output.issues[:5]) or 'none'}</issues>"
        )
        if output.structured_artifacts:
            parts.append("    <artifacts>")
            for art in output.structured_artifacts:
                parts.append(
                    f"      <artifact type='{art.type.value}'>\n"
                    f"        <title>{art.title}</title>\n"
                    f"        <summary>{art.summary}</summary>"
                )
                if art.data:
                    data_str = json.dumps(art.data, indent=2)[:800]
                    parts.append(f"        <data>{data_str}</data>")
                parts.append("      </artifact>")
            parts.append("    </artifacts>")
        parts.append("  </task_result>")
    parts.append("</task_outputs>")

    parts.append("\nProduce the MemorySnapshot JSON now.")
    return "\n".join(parts)


def _parse_memory_response(
    raw_text: str,
    project_id: str,
    existing: MemorySnapshot | None,
) -> MemorySnapshot:
    """Parse the Memory Agent's response into a MemorySnapshot."""
    import re

    json_re = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

    candidates: list[str] = []
    for match in json_re.finditer(raw_text):
        candidates.append(match.group(1).strip())

    # Try raw JSON
    start = raw_text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(raw_text)):
            if raw_text[i] == "{":
                depth += 1
            elif raw_text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw_text[start : i + 1])
                    break

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            data.setdefault("project_id", project_id)
            return MemorySnapshot(**data)
        except Exception:
            continue

    # Fallback: return existing or empty
    logger.warning("[Memory] Could not parse LLM response, returning existing/empty snapshot")
    if existing:
        return existing
    return MemorySnapshot(project_id=project_id)


# ---------------------------------------------------------------------------
# Heuristic-based memory update (cheap, for simple tasks)
# ---------------------------------------------------------------------------


def _heuristic_update(
    project_id: str,
    graph: TaskGraph,
    outputs: list[TaskOutput],
    existing: MemorySnapshot | None,
) -> MemorySnapshot:
    """Build a MemorySnapshot from task outputs without using an LLM."""
    snapshot = existing.model_copy(deep=True) if existing else MemorySnapshot(project_id=project_id)

    # Collect all artifacts and files
    all_files: dict[str, str] = dict(snapshot.file_map)
    all_api_endpoints: list[dict[str, str]] = list(snapshot.api_surface)
    all_tables: list[str] = list(snapshot.db_tables)
    new_decisions: list[str] = []
    new_issues: list[str] = []

    for output in outputs:
        if not output.is_successful():
            if output.issues:
                new_issues.extend(output.issues[:2])
            continue

        # Map files from artifacts
        for file_path in output.artifacts:
            all_files[file_path] = output.summary[:80]

        # Extract structured artifact data
        for art in output.structured_artifacts:
            if art.type == ArtifactType.API_CONTRACT and art.data:
                endpoints = art.data.get("endpoints", [])
                for ep in endpoints:
                    if isinstance(ep, dict):
                        all_api_endpoints.append(ep)

            elif art.type == ArtifactType.SCHEMA and art.data:
                tables = art.data.get("tables", [])
                if isinstance(tables, list):
                    all_tables.extend(t for t in tables if t not in all_tables)

            elif art.type == ArtifactType.ARCHITECTURE and art.data:
                decisions = art.data.get("decisions", [])
                if isinstance(decisions, list):
                    new_decisions.extend(decisions)

            elif art.type == ArtifactType.FILE_MANIFEST and art.data:
                files = art.data.get("files", {})
                if isinstance(files, dict):
                    all_files.update(files)

    # Update snapshot
    snapshot.file_map = all_files
    snapshot.api_surface = _dedupe_endpoints(all_api_endpoints)
    snapshot.db_tables = list(dict.fromkeys(all_tables))
    # Bound key_decisions to last 100 entries — prevents unbounded growth across sessions
    combined = snapshot.key_decisions + new_decisions
    snapshot.key_decisions = combined[-100:] if len(combined) > 100 else combined
    snapshot.known_issues = list(dict.fromkeys(snapshot.known_issues + new_issues))
    snapshot.cumulative_cost_usd = snapshot.cumulative_cost_usd + sum(o.cost_usd for o in outputs)
    snapshot.last_updated_by = outputs[-1].task_id if outputs else ""

    # Generate architecture_summary if not set (heuristic mode)
    if not snapshot.architecture_summary and graph.vision:
        roles_used = list(dict.fromkeys(t.role for t in graph.tasks))
        snapshot.architecture_summary = (
            f"{graph.vision}. "
            f"Agents involved: {', '.join(roles_used)}. "
            f"Tasks: {len(graph.tasks)}, "
            f"Successful: {sum(1 for o in outputs if o.is_successful())}/{len(outputs)}."
        )

    return snapshot


def _dedupe_endpoints(endpoints: list[dict[str, str]]) -> list[dict[str, str]]:
    """Deduplicate API endpoints by method+path."""
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for ep in endpoints:
        key = f"{ep.get('method', 'GET')} {ep.get('path', '')}"
        if key not in seen:
            seen.add(key)
            result.append(ep)
    return result


# ---------------------------------------------------------------------------
# Cross-agent inconsistency detection
# ---------------------------------------------------------------------------


def detect_inconsistencies(outputs: list[TaskOutput]) -> list[str]:
    """Detect cross-agent inconsistencies from structured artifacts.

    For example:
    - Frontend expects an API endpoint that backend didn't create
    - Tests reference a function that doesn't exist in the codebase
    """
    inconsistencies: list[str] = []

    # Collect all API contracts from backend
    backend_endpoints: set[str] = set()
    # Collect all API calls from frontend
    frontend_api_calls: set[str] = set()

    for output in outputs:
        for art in output.structured_artifacts:
            if art.type == ArtifactType.API_CONTRACT and art.data:
                endpoints = art.data.get("endpoints", [])
                for ep in endpoints:
                    if isinstance(ep, dict):
                        backend_endpoints.add(f"{ep.get('method', 'GET')} {ep.get('path', '')}")

            if art.type == ArtifactType.COMPONENT_MAP and art.data:
                api_calls = art.data.get("api_calls", [])
                for call in api_calls:
                    if isinstance(call, str):
                        frontend_api_calls.add(call)

    # Check for frontend calling APIs that backend didn't build
    if backend_endpoints and frontend_api_calls:
        missing = frontend_api_calls - backend_endpoints
        for endpoint in missing:
            inconsistencies.append(
                f"Frontend expects API '{endpoint}' but backend didn't create it"
            )

    return inconsistencies


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _should_use_llm(outputs: list[TaskOutput]) -> bool:
    """Decide if we need an LLM for memory update (complex tasks) or heuristic is enough."""
    # Use LLM if there are many tasks
    if len(outputs) >= 3:
        return True
    # Use LLM if there are complex artifacts (not just file_manifest)
    complex_types = {
        ArtifactType.API_CONTRACT,
        ArtifactType.SCHEMA,
        ArtifactType.ARCHITECTURE,
        ArtifactType.COMPONENT_MAP,
        ArtifactType.SECURITY_REPORT,
    }
    for o in outputs:
        if any(a.type in complex_types for a in o.structured_artifacts):
            return True
    # Use LLM if there are failures (need analysis)
    return any(not o.is_successful() for o in outputs)


def _load_existing_snapshot(forge_dir: Path, project_id: str) -> MemorySnapshot | None:
    """Load existing MemorySnapshot from disk."""
    snapshot_path = forge_dir / "memory_snapshot.json"
    if snapshot_path.exists():
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            return MemorySnapshot(**data)
        except Exception as exc:
            logger.warning(f"[Memory] Failed to load snapshot: {exc}")
    return None


def _save_snapshot(forge_dir: Path, snapshot: MemorySnapshot) -> None:
    """Save MemorySnapshot to disk atomically (write-then-rename)."""
    snapshot_path = forge_dir / "memory_snapshot.json"
    _atomic_write(snapshot_path, snapshot.model_dump_json(indent=2))


def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically using a sibling .tmp file.

    Prevents partial reads on concurrent access — os.replace() is atomic
    on POSIX when src and dst are on the same filesystem (guaranteed here
    because both are under the same project directory).
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding=encoding)
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the .tmp file on failure
        tmp_path.unlink(missing_ok=True)
        raise


def _write_manifest(forge_dir: Path, snapshot: MemorySnapshot, graph: TaskGraph) -> None:
    """Write a human-readable PROJECT_MANIFEST.md from the snapshot."""
    lines = [
        "# Project Manifest",
        f"\n> Last updated by: {snapshot.last_updated_by}",
        f"> Cumulative cost: ${snapshot.cumulative_cost_usd:.4f}",
        "",
    ]

    if snapshot.architecture_summary:
        lines.append(f"## Architecture\n{snapshot.architecture_summary}\n")

    if snapshot.tech_stack:
        lines.append("## Tech Stack")
        for key, val in snapshot.tech_stack.items():
            lines.append(f"- **{key}**: {val}")
        lines.append("")

    if snapshot.key_decisions:
        lines.append("## Key Decisions")
        for d in snapshot.key_decisions[-20:]:  # Keep last 20
            lines.append(f"- {d}")
        lines.append("")

    if snapshot.api_surface:
        lines.append("## API Surface")
        for ep in snapshot.api_surface:
            method = ep.get("method", "GET")
            path = ep.get("path", "")
            desc = ep.get("description", "")
            lines.append(f"- `{method} {path}` — {desc}")
        lines.append("")

    if snapshot.db_tables:
        lines.append("## Database Tables")
        for t in snapshot.db_tables:
            lines.append(f"- {t}")
        lines.append("")

    if snapshot.file_map:
        lines.append("## Key Files")
        for path, desc in sorted(snapshot.file_map.items())[:50]:
            lines.append(f"- `{path}` — {desc}")
        lines.append("")

    if snapshot.known_issues:
        lines.append("## Known Issues")
        for issue in snapshot.known_issues[-10:]:  # Keep last 10
            lines.append(f"- ⚠️ {issue}")
        lines.append("")

    manifest_path = forge_dir / "PROJECT_MANIFEST.md"
    _atomic_write(manifest_path, "\n".join(lines))


def _write_artifact_index(forge_dir: Path, outputs: list[TaskOutput]) -> None:
    """Write an index of all structured artifacts for easy lookup."""
    artifacts_dir = forge_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    index: list[dict[str, Any]] = []
    for output in outputs:
        for art in output.structured_artifacts:
            index.append(
                {
                    "task_id": output.task_id,
                    "type": art.type.value,
                    "title": art.title,
                    "file_path": art.file_path,
                    "summary": art.summary,
                }
            )

    if index:
        index_path = artifacts_dir / "artifact_index.json"
        _atomic_write(index_path, json.dumps(index, indent=2))


MAX_DECISION_LOG_BYTES = 500_000  # 500KB max before rotation


def _append_decision_log(
    forge_dir: Path,
    graph: TaskGraph,
    outputs: list[TaskOutput],
) -> None:
    """Append to the decision log (rotated when too large)."""
    log_path = forge_dir / "decision_log.md"

    # Rotate if log is too large
    if log_path.exists() and log_path.stat().st_size > MAX_DECISION_LOG_BYTES:
        archive_path = forge_dir / "decision_log.old.md"
        try:
            content = log_path.read_text(encoding="utf-8")
            half = len(content) // 2
            cut_point = content.find("\n## ", half)
            if cut_point > 0:
                _atomic_write(archive_path, content[:cut_point])
                _atomic_write(log_path, content[cut_point:])
        except Exception as exc:
            logger.warning("[Memory] Decision log rotation failed (non-fatal): %s", exc)

    lines = [
        f"\n## {time.strftime('%Y-%m-%d %H:%M')} — {graph.vision[:80]}",
        f"Tasks: {len(outputs)} | "
        f"Success: {sum(1 for o in outputs if o.is_successful())} | "
        f"Cost: ${sum(o.cost_usd for o in outputs):.4f}",
    ]

    for output in outputs:
        if output.is_successful():
            for art in output.structured_artifacts:
                if art.type == ArtifactType.ARCHITECTURE and art.data:
                    decisions = art.data.get("decisions", [])
                    for d in decisions:
                        lines.append(f"- **Decision**: {d}")

    # Append (create if doesn't exist)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Proactive Memory Injection — "Lessons Learned" for the Orchestrator
# ---------------------------------------------------------------------------


def get_lessons_learned(project_dir: str, user_message: str = "") -> str:
    """Extract relevant lessons learned from past tasks for Orchestrator context.

    This implements the "Proactive Memory" pattern from the improvement plan:
    Before the Orchestrator plans, inject relevant experience from past failures
    and successful patterns to avoid repeating mistakes.

    Args:
        project_dir: Project working directory
        user_message: The user's task request (used to filter relevant lessons)

    Returns:
        An XML-formatted string of lessons learned, or empty string if none.
    """
    forge_dir = Path(project_dir) / ".hivemind"
    if not forge_dir.exists():
        return ""

    lessons: list[str] = []

    # 1. Load existing memory snapshot
    snapshot = _load_existing_snapshot(forge_dir, "")
    if snapshot:
        # Inject known issues as lessons
        if snapshot.known_issues:
            lessons.extend(
                [
                    f"Past unresolved issue: {issue}"
                    for issue in snapshot.known_issues[-5:]  # Last 5 most recent
                ]
            )

        # Inject key decisions as context
        if snapshot.key_decisions:
            lessons.extend(
                [
                    f"Established decision: {decision}"
                    for decision in snapshot.key_decisions[-8:]  # Last 8 decisions
                ]
            )

    # 2. Load decision log for recent patterns
    log_path = forge_dir / "decision_log.md"
    if log_path.exists():
        try:
            log_content = log_path.read_text(encoding="utf-8")
            # Extract the last 2 sessions from the log
            sessions = log_content.split("\n## ")
            recent_sessions = sessions[-3:] if len(sessions) >= 3 else sessions
            for session in recent_sessions:
                if "Decision" in session:
                    # Extract decision lines
                    for line in session.splitlines():
                        if line.startswith("- **Decision**:"):
                            decision = line.replace("- **Decision**:", "").strip()
                            if decision and decision not in lessons:
                                lessons.append(f"Historical decision: {decision}")
        except Exception as exc:
            logger.warning("[Memory] Failed to load decision log for lessons: %s", exc)

    # 3. Load experience file if it exists (written by agents themselves)
    experience_path = forge_dir / ".experience.md"
    if experience_path.exists():
        try:
            exp_content = experience_path.read_text(encoding="utf-8")
            # Extract bullet points from the experience file (first 2000 chars)
            for line in exp_content[:2000].splitlines():
                line = line.strip()
                if line.startswith(("- ", "* ", "•")) and len(line) > 10:
                    lesson = line.lstrip("- *•").strip()
                    if lesson not in lessons:
                        lessons.append(f"Agent experience: {lesson}")
        except Exception as exc:
            logger.warning("[Memory] Failed to load experience file: %s", exc)

    if not lessons:
        return ""

    # Cap at 10 most relevant lessons to avoid context bloat
    lessons = lessons[:10]

    parts = [
        "<lessons_learned>",
        "The Memory Agent has extracted these lessons from past executions.",
        "Use them to avoid repeating mistakes and maintain consistency:\n",
    ]
    for i, lesson in enumerate(lessons, 1):
        parts.append(f"  {i}. {lesson}")
    parts.append("</lessons_learned>")

    return "\n".join(parts)


def save_experience_note(project_dir: str, note: str, category: str = "general") -> None:
    """Save a note to the project's experience file for future Orchestrator context.

    Agents can call this to record insights that should influence future planning.
    The Orchestrator will see these via get_lessons_learned() on the next task.

    Args:
        project_dir: Project working directory
        note: The lesson or experience to record
        category: Category tag (e.g., "failure", "success", "pattern")
    """
    forge_dir = Path(project_dir) / ".hivemind"
    forge_dir.mkdir(parents=True, exist_ok=True)

    experience_path = forge_dir / ".experience.md"
    timestamp = time.strftime("%Y-%m-%d %H:%M")
    line = f"- [{category}] {note}  _{timestamp}_\n"

    try:
        with open(experience_path, "a", encoding="utf-8") as f:
            f.write(line)
        logger.info("[Memory] Saved experience note: %s", note[:80])
    except Exception as exc:
        logger.warning("[Memory] Failed to save experience note: %s", exc)
