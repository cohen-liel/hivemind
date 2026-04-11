"""LangGraph-based DAG Executor — full replacement for dag_executor.py.

This module replaces the custom while-loop DAG executor with a proper
LangGraph StateGraph that leverages:
  - **Checkpointing**: SQLite checkpoint store for fault-tolerance and resume
  - **Typed State**: All execution context as LangGraph state channels
  - **Conditional Routing**: Dynamic task selection via conditional edges
  - **Reducers**: Annotated list channels for parallel fan-out/fan-in

Architecture:
  Parent Graph: select_batch → execute_batch → post_batch → (loop or end)
  review_code runs once at completion as a final quality gate.

All features from the original dag_executor.py are preserved:
  - Two-Phase Architecture (work + mandatory summary)
  - Config-driven turns/timeout/budget from AGENT_REGISTRY
  - Structured Notes / Blackboard cross-agent context
  - Artifact Registry / JIT Context injection
  - Codebase symbol scanning (DRY prevention)
  - Git diff injection for quality/review roles
  - Dynamic task injection (discovered_tasks)
  - Dynamic Spawner (model cascade on failure)
  - Watchdog loop (background monitoring)
  - Progress milestone events
  - CancelledError drain logic
  - Circuit breaker handling
  - File-level lock manager
  - SQLite checkpoint store for resume
  - Activity logging to .hivemind/activity.jsonl

The public API is identical to the original dag_executor.execute_graph().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from collections.abc import Awaitable, Callable
from operator import add
from pathlib import Path
from typing import Annotated, Any, Literal

# MemorySaver removed — it tries to serialize ALL state values via msgpack,
# crashing on non-serializable objects (ClaudeSDKManager, callbacks, etc.).
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

import config as cfg
from complexity import READER_ROLES_ENUM, WRITER_ROLES_ENUM
from contracts import (
    AgentRole,
    ArtifactContractError,
    ArtifactType,
    DAGCheckpoint,
    FailureCategory,
    TaskGraph,
    TaskInput,
    TaskOutput,
    TaskStatus,
    classify_failure,
    compute_task_complexity,
    create_remediation_task,
    extract_task_output,
    get_retry_strategy,
    task_input_to_prompt,
    validate_artifact_contracts,
)
from dynamic_spawner import DynamicSpawner
from file_output_manager import ArtifactRegistry
from git_discipline import commit_single_task, executor_commit
from isolated_query import isolated_query
from sdk_client import CircuitOpenError
from skills_registry import build_skill_prompt, select_skills_for_task
from structured_notes import NoteCategory, StructuredNotes

logger = logging.getLogger(__name__)

# Prevent GC of fire-and-forget event broadcast tasks
_background_event_tasks: set[asyncio.Task] = set()

# ── Constants (from centralised config.py) ────────────────────────────────

MAX_TASK_RETRIES: int = cfg.MAX_TASK_RETRIES
MAX_REMEDIATION_DEPTH: int = cfg.MAX_REMEDIATION_DEPTH
MAX_TOTAL_REMEDIATIONS: int = cfg.MAX_TOTAL_REMEDIATIONS
MAX_ROUNDS: int = cfg.MAX_DAG_ROUNDS
MAX_DYNAMIC_TASKS: int = int(os.getenv("MAX_DYNAMIC_TASKS", "10"))

# Two-Phase Architecture constants
_SUMMARY_PHASE_TURNS = 5
_MIN_WORK_TURNS_FOR_SUMMARY = 3

# Git diff char limit for review agents
_REVIEW_DIFF_CHAR_LIMIT = 12000

# Roles that write/modify files — imported from complexity.py
_WRITER_ROLES = WRITER_ROLES_ENUM
_READER_ROLES = READER_ROLES_ENUM

# Failure categories that should NOT be retried
_NO_RETRY_CATEGORIES = {
    FailureCategory.UNCLEAR_GOAL,
    FailureCategory.PERMISSION,
    FailureCategory.EXTERNAL,
}

# Valid roles that agents can propose for dynamic tasks
_VALID_SPAWN_ROLES: set[str] = {
    r.value for r in AgentRole if r not in (AgentRole.PM, AgentRole.ORCHESTRATOR)
}


# ── Progress Event Emission ───────────────────────────────────────────────


def _fire_event(on_event: Callable | None, event: dict) -> None:
    """Schedule an async on_event callback as a fire-and-forget task."""
    if on_event is None:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _task = asyncio.ensure_future(on_event(event))
            _background_event_tasks.add(_task)
            _task.add_done_callback(_background_event_tasks.discard)
    except Exception:
        pass


def _make_step_description(milestone: str, task_name: str) -> str:
    """Build a human-readable step description from milestone and task name."""
    _MILESTONE_LABELS = {
        "preparing": "Preparing",
        "agent_working": "Working on",
        "writing_files": "Writing files for",
        "summarising": "Summarising",
        "complete": "Completed",
        "failed": "Failed",
    }
    label = _MILESTONE_LABELS.get(milestone, milestone.replace("_", " ").title())
    short_name = task_name[:80].strip() if task_name else ""
    if not short_name:
        return label
    return f"{label}: {short_name}"


def _emit_task_progress(
    on_event: Callable | None,
    project_id: str,
    task_id: str,
    milestone: str,
    task_start_time: float,
    estimated_total_s: float = 0.0,
    task_name: str = "",
) -> None:
    """Emit a TASK_PROGRESS event if not throttled (max 2/sec/task)."""
    try:
        from dashboard.events import task_progress_throttler
        from src.api.websocket_handler import build_task_progress_event

        throttle_key = f"tp:{task_id}"
        if not task_progress_throttler.should_emit(throttle_key):
            return

        elapsed = time.monotonic() - task_start_time
        est_remaining = max(0.0, estimated_total_s - elapsed) if estimated_total_s > 0 else 0.0

        event = build_task_progress_event(
            project_id=project_id,
            task_id=task_id,
            milestone=milestone,
            elapsed_s=elapsed,
            est_remaining_s=est_remaining,
        )
        if task_name:
            event["task_name"] = task_name[:120]
        event["step_description"] = _make_step_description(milestone, task_name)
        _fire_event(on_event, event)
    except Exception:
        pass


def _emit_dag_progress(
    on_event: Callable | None,
    project_id: str,
    completed_count: int,
    total_count: int,
    graph_start_time: float,
) -> None:
    """Emit a DAG_PROGRESS aggregate event with completion % and ETA."""
    try:
        from src.api.websocket_handler import build_dag_progress_event

        elapsed = time.monotonic() - graph_start_time
        if completed_count > 0:
            avg_per_task = elapsed / completed_count
            remaining_tasks = total_count - completed_count
            est_remaining = avg_per_task * remaining_tasks
        else:
            est_remaining = 0.0

        event = build_dag_progress_event(
            project_id=project_id,
            completed=completed_count,
            total=total_count,
            elapsed_s=elapsed,
            est_remaining_s=est_remaining,
        )
        _fire_event(on_event, event)
    except Exception:
        pass


def _emit_activity_log(
    task: TaskInput,
    output: TaskOutput,
    elapsed: float,
    post_git_status: str,
    project_dir: str,
    on_event: Callable | None = None,
) -> None:
    """Append a structured activity log entry to .hivemind/activity.jsonl."""
    activity_entry = {
        "timestamp": time.time(),
        "agent": task.role,
        "task": task.id,
        "status": "completed" if output.is_successful() else "failed",
        "summary": output.summary if output.summary else "",
        "files_changed": post_git_status[:200] if post_git_status else "",
        "duration_s": round(elapsed, 1),
    }

    try:
        forge_dir = Path(project_dir) / ".hivemind"
        forge_dir.mkdir(parents=True, exist_ok=True)
        activity_file = forge_dir / "activity.jsonl"
        with open(activity_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(activity_entry) + "\n")
    except OSError:
        pass

    if on_event is not None:
        event = {"type": "agent_activity", **activity_entry}
        _fire_event(on_event, event)


# ── File-Level Lock Manager ──────────────────────────────────────────────


class FileLockManager:
    """Manages per-file asyncio locks to prevent writer conflicts across graphs."""

    _instance: FileLockManager | None = None

    def __new__(cls) -> FileLockManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._locks: dict[str, asyncio.Lock] = {}
            cls._instance._meta_lock = asyncio.Lock()
        return cls._instance

    async def acquire_files(
        self,
        file_paths: list[str],
        timeout: float | None = None,
    ) -> bool:
        if not file_paths:
            return True
        if timeout is None:
            timeout = cfg.FILE_LOCK_TIMEOUT
        sorted_paths = sorted(set(file_paths))
        async with self._meta_lock:
            for fp in sorted_paths:
                if fp not in self._locks:
                    self._locks[fp] = asyncio.Lock()
        acquired: list[str] = []
        deadline = asyncio.get_event_loop().time() + timeout
        try:
            for fp in sorted_paths:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    self._release_paths(acquired)
                    return False
                try:
                    await asyncio.wait_for(self._locks[fp].acquire(), timeout=remaining)
                    acquired.append(fp)
                except TimeoutError:
                    self._release_paths(acquired)
                    return False
        except Exception:
            self._release_paths(acquired)
            raise
        return True

    def release_files(self, file_paths: list[str]) -> None:
        self._release_paths(sorted(set(file_paths)))

    def _release_paths(self, paths: list[str]) -> None:
        for fp in paths:
            lock = self._locks.get(fp)
            if lock is not None and lock.locked():
                lock.release()

    @property
    def active_locks(self) -> int:
        return sum(1 for lk in self._locks.values() if lk.locked())


_file_lock_manager = FileLockManager()


# ── CancelledError drain ─────────────────────────────────────────────────


def _drain_cancellations(label: str = "", *, stop_event: asyncio.Event | None = None) -> int:
    """Drain pending cancellations from the current asyncio task."""
    if stop_event is not None and stop_event.is_set():
        return 0
    ct = asyncio.current_task()
    if ct is None or not hasattr(ct, "uncancel"):
        return 0
    drained = 0
    while ct.cancelling() > 0:
        ct.uncancel()
        drained += 1
    if drained and label:
        logger.debug(f"[LG-DAG] Drained {drained} cancellation(s) {label}")
    return drained


# ── SQLite Checkpoint Store ──────────────────────────────────────────────

_checkpoint_locks: dict[str, asyncio.Lock] = {}
_checkpoint_locks_meta = asyncio.Lock()


async def _get_checkpoint_lock(project_id: str) -> asyncio.Lock:
    async with _checkpoint_locks_meta:
        if project_id not in _checkpoint_locks:
            _checkpoint_locks[project_id] = asyncio.Lock()
        return _checkpoint_locks[project_id]


def _get_checkpoint_db(project_dir: str) -> str:
    hive_dir = Path(project_dir) / ".hivemind"
    hive_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(hive_dir / "dag_checkpoints.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dag_checkpoints (
                project_id   TEXT NOT NULL,
                round_num    INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'running',
                checkpoint   TEXT NOT NULL,
                created_at   REAL NOT NULL,
                PRIMARY KEY (project_id, round_num)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_checkpoint_project
            ON dag_checkpoints(project_id, created_at DESC)
        """)
        conn.commit()
        conn.close()
    except Exception:
        pass
    return db_path


def _save_checkpoint(project_dir: str, checkpoint: DAGCheckpoint) -> None:
    db_path = _get_checkpoint_db(project_dir)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute(
            "INSERT OR REPLACE INTO dag_checkpoints (project_id, round_num, status, checkpoint, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                checkpoint.project_id,
                checkpoint.round_num,
                checkpoint.status,
                checkpoint.model_dump_json(),
                checkpoint.created_at,
            ),
        )
        conn.execute(
            "DELETE FROM dag_checkpoints WHERE project_id = ? AND round_num NOT IN "
            "(SELECT round_num FROM dag_checkpoints WHERE project_id = ? ORDER BY round_num DESC LIMIT 10)",
            (checkpoint.project_id, checkpoint.project_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _load_latest_checkpoint(project_dir: str, project_id: str) -> DAGCheckpoint | None:
    db_path = _get_checkpoint_db(project_dir)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute(
            "SELECT checkpoint FROM dag_checkpoints WHERE project_id = ? AND status = 'running' ORDER BY round_num DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        conn.close()
        if row:
            return DAGCheckpoint.model_validate_json(row[0])
    except Exception:
        pass
    return None


def _clear_checkpoints(project_dir: str, project_id: str) -> None:
    db_path = _get_checkpoint_db(project_dir)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("DELETE FROM dag_checkpoints WHERE project_id = ?", (project_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


async def _async_save_checkpoint(project_dir: str, checkpoint: DAGCheckpoint) -> None:
    lock = await _get_checkpoint_lock(checkpoint.project_id)
    async with lock:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _save_checkpoint, project_dir, checkpoint)


async def restore_graph_from_checkpoint(
    project_dir: str,
    project_id: str,
) -> tuple[TaskGraph | None, dict[str, TaskOutput], dict[str, int], int]:
    """Restore a DAG execution from the latest checkpoint."""
    checkpoint = _load_latest_checkpoint(project_dir, project_id)
    if checkpoint is None:
        return None, {}, {}, 0
    try:
        graph = TaskGraph.model_validate_json(checkpoint.graph_json)
        completed = {
            tid: TaskOutput.model_validate(data) for tid, data in checkpoint.completed_tasks.items()
        }
        logger.info(
            f"[LG-DAG] Restored checkpoint: project={project_id} "
            f"round={checkpoint.round_num} completed={len(completed)}/{len(graph.tasks)}"
        )
        return graph, completed, checkpoint.retries, checkpoint.round_num
    except Exception as exc:
        logger.warning(f"[LG-DAG] Checkpoint restore failed: {exc}")
        return None, {}, {}, 0


# ── Per-role config helpers ──────────────────────────────────────────────


def _get_max_turns(role: str) -> int:
    """Return the max_turns limit for a role from AGENT_REGISTRY."""
    from config import get_agent_turns

    return get_agent_turns(role)


def _get_task_timeout(role: str, task: TaskInput | None = None) -> int:
    """Return wall-clock timeout (seconds) with adaptive complexity scaling."""
    from config import get_agent_timeout

    base = get_agent_timeout(role)
    if task is not None:
        complexity = compute_task_complexity(task)
        scale_factor = 1.0 + (complexity - 1.0) * 0.25
        return max(int(base * scale_factor), 30)
    return base


def _get_task_budget(role: str) -> float:
    """Return the per-task budget (USD) from AGENT_REGISTRY."""
    from config import get_agent_budget

    return get_agent_budget(role)


# ── Git helpers ──────────────────────────────────────────────────────────


async def _git_status(project_dir: str, task_id: str, stage: str) -> str:
    """Run `git status --porcelain` and return output."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=cfg.SUBPROCESS_SHORT_TIMEOUT)
        return stdout.decode().strip()
    except asyncio.CancelledError:
        _drain_cancellations(f"({stage} git status for {task_id})")
        if proc:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
        return ""
    except Exception:
        return ""


async def _get_git_diff_for_review(project_dir: str) -> str:
    """Run git diff HEAD and return actual code changes for review agents."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "HEAD",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=cfg.SUBPROCESS_SHORT_TIMEOUT)
        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            proc2 = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--cached",
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await asyncio.wait_for(
                proc2.communicate(), timeout=cfg.SUBPROCESS_SHORT_TIMEOUT
            )
            raw = stdout2.decode("utf-8", errors="replace").strip()
        if not raw:
            return ""
        if len(raw) <= _REVIEW_DIFF_CHAR_LIMIT:
            return raw
        truncated = raw[:_REVIEW_DIFF_CHAR_LIMIT]
        last_hunk = truncated.rfind("\n@@")
        if last_hunk > _REVIEW_DIFF_CHAR_LIMIT // 2:
            truncated = truncated[:last_hunk]
        return truncated + f"\n... [diff truncated — {len(raw)} total chars]"
    except Exception:
        return ""


# ── Symbol extraction ────────────────────────────────────────────────────

_SYMBOL_PATTERNS = re.compile(
    r"^\+\s*(?:"
    r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"
    r"|(?:export\s+)?(?:const|let|var)\s+(\w+)\s*="
    r"|(?:export\s+)?class\s+(\w+)"
    r"|(?:export\s+)?interface\s+(\w+)"
    r"|(?:export\s+)?type\s+(\w+)\s*="
    r"|(?:export\s+)?enum\s+(\w+)"
    r"|def\s+(\w+)\s*\("
    r"|class\s+(\w+)\s*[:\(]"
    r"|(\w+)\s*:\s*\w+\s*="
    r")",
    re.MULTILINE,
)


async def _extract_symbols_from_diff(project_dir: str, artifacts: list[str]) -> str:
    """Parse git diff for added function/class/const definitions."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "HEAD",
            "--diff-filter=AM",
            "--",
            *[a for a in artifacts[:20] if not a.startswith(".")],
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=cfg.SUBPROCESS_SHORT_TIMEOUT)
        diff_text = stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""
    if not diff_text:
        return ""
    file_symbols: dict[str, list[str]] = {}
    current_file = ""
    for line in diff_text.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:].split("/")[-1]
        elif line.startswith("+") and not line.startswith("+++") and current_file:
            match = _SYMBOL_PATTERNS.match(line)
            if match:
                symbol = next(g for g in match.groups() if g)
                if not symbol.startswith("_"):
                    file_symbols.setdefault(current_file, []).append(symbol)
    if not file_symbols:
        return ""
    lines = []
    for fname, syms in file_symbols.items():
        unique = list(dict.fromkeys(syms))[:15]
        lines.append(f"{fname}: {', '.join(unique)}")
    return "\n".join(lines)


_SCAN_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".css", ".html"}
_SCAN_SYMBOL_RE = re.compile(
    r"(?:"
    r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"
    r"|(?:export\s+)?(?:const|let|var)\s+(\w+)\s*="
    r"|(?:export\s+)?class\s+(\w+)"
    r"|(?:export\s+)?interface\s+(\w+)"
    r"|(?:export\s+)?type\s+(\w+)\s*="
    r"|(?:export\s+)?enum\s+(\w+)"
    r"|^def\s+(\w+)\s*\("
    r"|^class\s+(\w+)\s*[:\(]"
    r")"
)
_SCAN_CHAR_LIMIT = 4000


async def _scan_codebase_symbols(project_dir: str) -> str:
    """Scan source files for exported function/class/const names."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        all_files = stdout.decode("utf-8", errors="replace").strip().split("\n")
    except Exception:
        return ""

    _SKIP_DIRS = {
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".git",
        ".hivemind",
    }
    source_files = []
    for f in all_files:
        if not f:
            continue
        parts = f.split("/")
        if any(p in _SKIP_DIRS for p in parts):
            continue
        if Path(f).suffix in _SCAN_EXTENSIONS:
            source_files.append(f)

    if not source_files:
        return ""

    file_symbols: dict[str, list[str]] = {}
    for fpath in source_files[:100]:
        full_path = Path(project_dir) / fpath
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        symbols = []
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "#", "/*", "*", "import ", "from ")):
                continue
            match = _SCAN_SYMBOL_RE.search(stripped)
            if match:
                symbol = next((g for g in match.groups() if g), None)
                if symbol and not symbol.startswith("_") and len(symbol) > 2:
                    symbols.append(symbol)
        if symbols:
            file_symbols[fpath] = list(dict.fromkeys(symbols))[:20]

    if not file_symbols:
        return ""

    lines = []
    total_len = 0
    for fpath, syms in sorted(file_symbols.items()):
        line = f"{fpath}: {', '.join(syms)}"
        if total_len + len(line) > _SCAN_CHAR_LIMIT:
            lines.append(f"... ({len(file_symbols) - len(lines)} more files)")
            break
        lines.append(line)
        total_len += len(line) + 1
    return "\n".join(lines)


# ── Artifact validation ──────────────────────────────────────────────────


def _validate_artifacts(output: TaskOutput, project_dir: str) -> TaskOutput:
    """Verify that artifacts claimed by the agent actually exist on disk."""
    if not output.artifacts:
        return output
    project_path = Path(project_dir).resolve()
    verified: list[str] = []
    phantom: list[str] = []
    for artifact_path in output.artifacts:
        found = False
        for candidate in [
            project_path / artifact_path.lstrip("/"),
            Path(artifact_path),
        ]:
            try:
                resolved = candidate.resolve()
                if resolved.is_relative_to(project_path) and resolved.exists():
                    found = True
                    break
            except (ValueError, OSError):
                continue
        if found:
            verified.append(artifact_path)
        else:
            phantom.append(artifact_path)

    # Check for files outside project boundary
    outside: list[str] = []
    for artifact_path in list(verified):
        try:
            resolved = (
                Path(artifact_path).resolve()
                if Path(artifact_path).is_absolute()
                else (project_path / artifact_path.lstrip("/")).resolve()
            )
            if not resolved.is_relative_to(project_path):
                outside.append(artifact_path)
                verified.remove(artifact_path)
        except (ValueError, OSError):
            pass

    if outside:
        output.issues.append(
            f"Artifact validation: {len(outside)} files outside project boundary rejected"
        )
        output.confidence = max(output.confidence - 0.2, 0.1)
    if phantom:
        output.artifacts = verified
        output.issues.append(f"Artifact validation: {len(phantom)} claimed files not found on disk")
        if len(verified) == 0 and len(phantom) > 0:
            output.confidence = max(output.confidence - 0.3, 0.1)
        else:
            phantom_ratio = len(phantom) / (len(verified) + len(phantom))
            output.confidence = max(output.confidence - (phantom_ratio * 0.2), 0.1)
    return output


def _check_required_artifact_types(task: TaskInput, output: TaskOutput) -> None:
    """Warn if an agent didn't produce its required artifact types."""
    if not task.required_artifacts:
        return
    produced_types = {a.type for a in output.structured_artifacts}
    missing = set(task.required_artifacts) - produced_types
    if missing:
        missing_names = [m.value for m in missing]
        logger.warning(f"[LG-DAG] Task {task.id} missing required artifacts: {missing_names}")
        output.issues.append(f"Missing required artifacts: {', '.join(missing_names)}")


# ── Reducers ─────────────────────────────────────────────────────────────


def _merge_completed(
    old: dict[str, TaskOutput], new: dict[str, TaskOutput]
) -> dict[str, TaskOutput]:
    merged = {**old}
    merged.update(new)
    return merged


def _merge_dicts(old: dict, new: dict) -> dict:
    merged = {**old}
    merged.update(new)
    return merged


def _sum_float(old: float, new: float) -> float:
    return old + new


def _max_int(old: int, new: int) -> int:
    return max(old, new)


# ── LangGraph State ─────────────────────────────────────────────────────


class DAGState(TypedDict):
    """Full execution state for the LangGraph DAG executor."""

    # Core graph data (set once at init)
    graph: TaskGraph
    project_dir: str
    specialist_prompts: dict[str, str]
    sdk: Any
    max_budget_usd: float

    # Mutable execution state (updated by nodes)
    completed: Annotated[dict[str, TaskOutput], _merge_completed]
    retries: Annotated[dict[str, int], _merge_dicts]
    session_ids: Annotated[dict[str, str], _merge_dicts]
    total_cost: Annotated[float, _sum_float]
    remediation_count: Annotated[int, _max_int]
    task_counter: Annotated[int, _max_int]
    round_num: Annotated[int, _max_int]
    healing_history: Annotated[list[dict], add]

    # Batch state
    current_batch: list[TaskInput]
    batch_results: Annotated[list[TaskOutput], add]
    blackboard_notes: Annotated[list[str], add]

    # Context layers (set once at init)
    structured_notes: Any  # StructuredNotes
    blackboard: Any  # Blackboard
    artifact_registry: Any  # ArtifactRegistry
    dynamic_spawner: Any  # DynamicSpawner

    # Per-round state
    codebase_symbols: str
    dynamic_task_count: Annotated[int, _max_int]
    model_overrides: Annotated[dict[str, str], _merge_dicts]
    user_message: str
    graph_start_time: float

    # Status
    status: str

    # Callbacks (set once at init)
    on_task_start: Any
    on_task_done: Any
    on_remediation: Any
    on_agent_stream: Any
    on_agent_tool_use: Any
    on_event: Any
    commit_approval_callback: Any


# ── Batch planning ───────────────────────────────────────────────────────


def _plan_batches(tasks: list[TaskInput]) -> list[list[TaskInput]]:
    """Split ready tasks into sequential batches respecting file conflicts."""
    if not tasks:
        return []
    readers = [t for t in tasks if t.role in _READER_ROLES]
    writers = [t for t in tasks if t.role in _WRITER_ROLES]
    others = [t for t in tasks if t.role not in _READER_ROLES and t.role not in _WRITER_ROLES]
    batches: list[list[TaskInput]] = []
    if writers:
        batches.extend(_split_writers_by_conflicts(writers))
    parallel_batch = readers + others
    if parallel_batch:
        batches.append(parallel_batch)
    return batches


def _split_writers_by_conflicts(writers: list[TaskInput]) -> list[list[TaskInput]]:
    """Group writer tasks into sequential batches to avoid file conflicts."""
    batches: list[list[TaskInput]] = []
    claimed_files: set[str] = set()
    current_batch: list[TaskInput] = []
    for task in writers:
        if not task.files_scope:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                claimed_files = set()
            batches.append([task])
            continue
        scope = set(task.files_scope)
        if scope & claimed_files:
            if current_batch:
                batches.append(current_batch)
            current_batch = [task]
            claimed_files = scope
        else:
            current_batch.append(task)
            claimed_files |= scope
    if current_batch:
        batches.append(current_batch)
    return batches


# ── Node: select_batch ───────────────────────────────────────────────────


async def select_batch(state: DAGState) -> dict:
    """Select the next batch of ready tasks to execute."""
    graph = state["graph"]
    completed = state["completed"]
    round_num = state["round_num"] + 1

    ready = graph.ready_tasks(completed)

    if not ready:
        if graph.is_complete(completed):
            return {"status": "completed", "round_num": round_num, "current_batch": []}

        has_failures = any(not out.is_successful() for out in completed.values())
        if has_failures:
            return {"status": "failed", "round_num": round_num, "current_batch": []}

        return {"status": "failed", "round_num": round_num, "current_batch": []}

    if round_num > MAX_ROUNDS:
        return {"status": "max_rounds", "round_num": round_num, "current_batch": []}

    batches = _plan_batches(ready)
    first_batch = batches[0] if batches else ready[:1]

    # Scan codebase symbols before dispatching
    codebase_symbols = ""
    try:
        codebase_symbols = await _scan_codebase_symbols(state["project_dir"])
    except Exception:
        pass

    logger.info(
        f"[LG-DAG] Round {round_num}: "
        f"completed={len(completed)}/{len(graph.tasks)} "
        f"ready={[t.id for t in ready]} "
        f"batch={[t.id for t in first_batch]}"
    )

    return {
        "round_num": round_num,
        "current_batch": first_batch,
        "batch_results": [],
        "codebase_symbols": codebase_symbols,
    }


# ── Two-Phase Summary ────────────────────────────────────────────────────


async def _run_summary_phase(
    task: TaskInput,
    sdk: Any,
    project_dir: str,
    session_id: str,
    system_prompt: str,
    work_cost: float,
    work_turns: int,
    role_name: str = "developer",
    work_tool_uses: list[str] | None = None,
) -> TaskOutput | None:
    """Phase 2: extract structured JSON from the agent (tools disabled)."""
    # Build artifact examples
    _artifact_examples = []
    for art_type in task.required_artifacts or []:
        _artifact_examples.append(
            '    {"type": "'
            + art_type.value
            + '", "title": "'
            + art_type.value.replace("_", " ").title()
            + '", "data": {"summary": "..."}}'
        )
    if not task.required_artifacts or ArtifactType.FILE_MANIFEST not in (
        task.required_artifacts or []
    ):
        _artifact_examples.append(
            '    {"type": "file_manifest", "title": "Files Modified", "data": {"files": {"path/to/file.py": "what was done"}}}'
        )
    _artifacts_str = (
        ",\n".join(_artifact_examples)
        if _artifact_examples
        else (
            '    {"type": "file_manifest", "title": "Files Modified", "data": {"files": {"path/to/file.py": "description"}}}'
        )
    )

    summary_prompt = (
        "Your work phase is complete. Now produce ONLY the required JSON output block.\n"
        "Do NOT do any more work. Do NOT use any tools.\n\n"
        "Reflect on everything you did and produce an accurate JSON summary:\n\n"
        "```json\n"
        "{\n"
        f'  "task_id": "{task.id}",\n'
        '  "status": "completed",\n'
        '  "summary": "what you did in 2-3 sentences",\n'
        '  "artifacts": ["list/of/files/you/created/or/modified.py"],\n'
        '  "issues": [],\n'
        '  "blockers": [],\n'
        '  "followups": ["any remaining work"],\n'
        '  "confidence": 0.95,\n'
        '  "structured_artifacts": [\n'
        f"{_artifacts_str}\n"
        "  ],\n"
        '  "discovered_tasks": []\n'
        "}\n"
        "```\n\n"
        "IMPORTANT about discovered_tasks: If during your work you discovered that\n"
        "additional tasks are needed that were NOT in the original plan, list them.\n"
        "Each discovered task needs: goal (what to do), role (which agent), reason (why).\n"
        "Leave empty [] if no additional tasks are needed.\n\n"
        "IMPORTANT: Output ONLY the JSON block above. No explanations, no tools."
    )

    try:
        _summary_task = asyncio.create_task(
            isolated_query(
                sdk,
                prompt=summary_prompt,
                system_prompt=system_prompt,
                cwd=project_dir,
                session_id=session_id,
                max_turns=_SUMMARY_PHASE_TURNS,
                max_budget_usd=5.0,
                tools=[],
                max_retries=0,
            ),
            name=f"dag-{task.id}-summary",
        )
        deadline = asyncio.get_running_loop().time() + 180
        cancel_hits = 0
        while not _summary_task.done():
            try:
                remaining = max(0.1, deadline - asyncio.get_running_loop().time())
                await asyncio.wait({_summary_task}, timeout=remaining)
                break
            except asyncio.CancelledError:
                cancel_hits += 1
                _drain_cancellations(f"(summary for {task.id})")
                if _summary_task.done():
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    _summary_task.cancel()
                    break

        summary_response = None
        if _summary_task.done() and not _summary_task.cancelled():
            exc = _summary_task.exception()
            if not exc:
                summary_response = _summary_task.result()

        if summary_response is None:
            return None
    except Exception:
        return None

    if summary_response.is_error:
        return None

    output = extract_task_output(
        summary_response.text, task.id, role_name, tool_uses=work_tool_uses
    )
    output.cost_usd = work_cost + summary_response.cost_usd
    output.input_tokens = summary_response.input_tokens
    output.output_tokens = summary_response.output_tokens
    output.total_tokens = summary_response.total_tokens
    output.turns_used = work_turns + summary_response.num_turns

    if output.is_successful() and output.confidence > 0.0:
        return output
    return None


# ── Dynamic task injection ───────────────────────────────────────────────


def _inject_discovered_tasks(
    graph: TaskGraph,
    source_task: TaskInput,
    output: TaskOutput,
    dynamic_task_count: int,
    task_counter: int,
    on_event: Any = None,
) -> int:
    """Inject agent-discovered tasks into the live graph."""
    if not output.discovered_tasks:
        return 0
    if source_task.is_remediation or getattr(source_task, "_is_dynamic", False):
        return 0
    remaining = MAX_DYNAMIC_TASKS - dynamic_task_count
    if remaining <= 0:
        return 0

    injected = 0
    for dt in output.discovered_tasks[:remaining]:
        role_str = dt.role.lower().strip()
        if role_str not in _VALID_SPAWN_ROLES:
            continue
        if len(dt.goal.strip()) < 10:
            continue
        task_counter += 1
        task_id = f"dyn_{task_counter:03d}"
        deps = [source_task.id] if dt.depends_on_source else []
        new_task = TaskInput(
            id=task_id,
            role=AgentRole(role_str),
            goal=dt.goal.strip(),
            depends_on=deps,
            context_from=[source_task.id] if dt.depends_on_source else [],
            constraints=[f"Discovered by {source_task.role} during {source_task.id}: {dt.reason}"],
            required_artifacts=[ArtifactType.FILE_MANIFEST],
        )
        object.__setattr__(new_task, "_is_dynamic", True)
        graph.add_task(new_task)
        injected += 1
        logger.info(f"[LG-DAG] DYNAMIC TASK INJECTED: {task_id} ({role_str}) goal='{dt.goal[:80]}'")

    if injected > 0 and on_event:
        _fire_event(
            on_event, {"type": "task_graph", "graph": graph.model_dump(), "timestamp": time.time()}
        )
    return injected


# ── Failure handling ─────────────────────────────────────────────────────


def _remediation_depth(task: TaskInput, graph_tasks: list[TaskInput] | None = None) -> int:
    """Count how deep in the remediation chain this task is."""
    if not task.is_remediation:
        return 0
    depth = 1
    if graph_tasks and task.original_task_id:
        task_map = {t.id: t for t in graph_tasks}
        current_id = task.original_task_id
        seen: set[str] = {task.id}
        while current_id in task_map and current_id not in seen:
            parent = task_map[current_id]
            seen.add(current_id)
            if parent.is_remediation:
                depth += 1
                current_id = parent.original_task_id
            else:
                break
    return depth


# ── Node: execute_batch ──────────────────────────────────────────────────


async def execute_batch(state: DAGState) -> dict:
    """Execute all tasks in the current batch with Two-Phase Architecture."""
    batch = state["current_batch"]
    if not batch:
        return {"batch_results": []}

    graph = state["graph"]
    completed = state["completed"]
    specialist_prompts = state["specialist_prompts"]
    sdk = state["sdk"]
    project_dir = state["project_dir"]
    session_ids = state["session_ids"]
    on_task_start = state.get("on_task_start")
    on_task_done = state.get("on_task_done")
    on_event = state.get("on_event")
    structured_notes = state.get("structured_notes")
    blackboard = state.get("blackboard")
    artifact_registry = state.get("artifact_registry")
    graph_start_time = state.get("graph_start_time", time.monotonic())

    async def _run_one_task(task: TaskInput) -> TaskOutput:
        """Execute a single task using Two-Phase Architecture."""
        role_name = task.role
        max_turns = _get_max_turns(role_name)
        task_timeout = _get_task_timeout(role_name, task=task)

        if on_task_start:
            try:
                await on_task_start(task)
            except Exception:
                pass

        # Milestone 1: preparing
        _task_start_mono = time.monotonic()
        _est_total = float(task_timeout)
        _task_goal = task.goal or ""
        _emit_task_progress(
            on_event,
            graph.project_id,
            task.id,
            "preparing",
            _task_start_mono,
            _est_total,
            task_name=_task_goal,
        )

        # Build prompt
        context_outputs = {tid: completed[tid] for tid in task.context_from if tid in completed}
        prompt = task_input_to_prompt(
            task,
            context_outputs,
            graph_vision=graph.vision,
            graph_epics=graph.epic_breakdown,
            user_message=state.get("user_message", ""),
        )

        # System prompt
        system_prompt = specialist_prompts.get(
            role_name,
            specialist_prompts.get("backend_developer", "You are an expert software engineer."),
        )

        # Project boundary
        try:
            from project_context import build_project_header

            _boundary = build_project_header(graph.project_id, project_dir)
            if _boundary and _boundary not in system_prompt:
                system_prompt = _boundary + "\n\n" + system_prompt
        except Exception:
            pass

        # Skills injection
        try:
            skill_names = select_skills_for_task(role_name, task.goal)
            if skill_names:
                system_prompt = system_prompt + build_skill_prompt(skill_names)
        except Exception:
            pass

        # JIT Context from artifact registry
        if artifact_registry:
            try:
                enhanced = artifact_registry.enhance_prompt(task, prompt)
                if enhanced != prompt:
                    prompt = enhanced
            except Exception:
                pass

        # Blackboard context
        if blackboard:
            try:
                bb_ctx = blackboard.build_smart_context(
                    role=role_name, task_goal=task.goal, context_from=task.context_from
                )
                if bb_ctx:
                    prompt += f"\n\n{bb_ctx}"
            except Exception:
                if structured_notes:
                    try:
                        notes_ctx = structured_notes.build_notes_context(
                            role=role_name, task_goal=task.goal
                        )
                        if notes_ctx:
                            prompt += f"\n\n{notes_ctx}"
                    except Exception:
                        pass
        elif structured_notes:
            try:
                notes_ctx = structured_notes.build_notes_context(
                    role=role_name, task_goal=task.goal
                )
                if notes_ctx:
                    prompt += f"\n\n{notes_ctx}"
            except Exception:
                pass

        # Codebase symbol map
        codebase_symbols = state.get("codebase_symbols", "")
        if codebase_symbols:
            prompt += (
                "\n\n<existing_codebase_symbols>\n"
                "IMPORTANT: The following functions, classes, and constants ALREADY EXIST.\n"
                "Do NOT re-implement them — import and reuse instead.\n\n"
                f"{codebase_symbols}\n"
                "</existing_codebase_symbols>"
            )

        # Git diff for quality/review roles
        _QUALITY_ROLES = {"reviewer", "security_auditor", "test_engineer", "tester"}
        if role_name in _QUALITY_ROLES:
            try:
                _diff = await _get_git_diff_for_review(project_dir)
                if _diff:
                    prompt += (
                        "\n\n<code_diff>\n"
                        "Below is the actual git diff of ALL changes made by agents.\n"
                        "Check for: DRY violations, dead variables, naming inconsistencies.\n\n"
                        f"{_diff}\n"
                        "</code_diff>"
                    )
            except Exception:
                pass

        # Two-Phase: reserve turns for summary
        work_turns = max(max_turns - _SUMMARY_PHASE_TURNS, max_turns // 2)
        work_timeout = max(task_timeout - 90, task_timeout // 2)

        prompt += (
            f"\n\n⚠️ TURN BUDGET (Phase 1): You have {work_turns} turns for this work phase. "
            f"Every tool call consumes 1 turn. "
            f"When you have ~10 turns remaining, focus on finishing. "
            f"A mandatory Phase 2 will follow with {_SUMMARY_PHASE_TURNS} tool-free turns for JSON summary."
        )

        t0 = time.monotonic()
        session_key = f"{graph.project_id}:{role_name}:{task.id}"
        session_id = session_ids.get(session_key)

        logger.info(
            f"[LG-DAG] Task {task.id} ({role_name}): PHASE 1 (WORK) — "
            f"max_turns={work_turns}/{max_turns}, timeout={work_timeout}s/{task_timeout}s"
        )

        # Milestone 2: agent_working
        _emit_task_progress(
            on_event,
            graph.project_id,
            task.id,
            "agent_working",
            _task_start_mono,
            _est_total,
            task_name=_task_goal,
        )

        # Agent output logging
        log_dir = Path(project_dir) / ".hivemind" / "agent_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        agent_log_file = log_dir / f"{task.id}_{role_name}.log"
        _log_start = time.monotonic()

        def _write_log(entry: str) -> None:
            try:
                elapsed = time.monotonic() - _log_start
                with open(agent_log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{elapsed:7.1f}s] {entry}\n")
            except OSError:
                pass

        _write_log(f"=== Task {task.id} ({role_name}) started ===")
        _write_log(f"Goal: {task.goal[:200]}")

        # Streaming callbacks
        _on_stream = None
        _on_tool_use = None
        on_agent_stream = state.get("on_agent_stream")
        on_agent_tool_use = state.get("on_agent_tool_use")

        if on_agent_stream:

            async def _on_stream(text):
                try:
                    _write_log(f"STREAM: {text[:500]}")
                    await on_agent_stream(role_name, text, task.id)
                except Exception:
                    pass
        else:

            async def _on_stream(text):
                _write_log(f"STREAM: {text[:500]}")

        if on_agent_tool_use:

            async def _on_tool_use(tool_name, tool_info="", tool_input=None):
                try:
                    _write_log(f"TOOL: {tool_name} | {tool_info[:200]}")
                    if tool_name in ("Write", "Edit", "NotebookEdit", "write_file", "edit_file"):
                        _emit_task_progress(
                            on_event,
                            graph.project_id,
                            task.id,
                            "writing_files",
                            _task_start_mono,
                            _est_total,
                            task_name=_task_goal,
                        )
                    await on_agent_tool_use(role_name, tool_name, tool_info, task.id)
                except Exception:
                    pass
        else:

            async def _on_tool_use(tool_name, tool_info="", tool_input=None):
                _write_log(f"TOOL: {tool_name} | {tool_info[:200]}")

        # Snapshot git status before (logged inside _git_status)
        await _git_status(project_dir, task.id, "PRE-RUN")

        # Check for model override from Dynamic Spawner
        _model_override = state.get("model_overrides", {}).get(task.id)

        # Phase 1: WORK
        response = None
        work_had_error = False
        try:
            from config import get_agent_timeout as _get_role_timeout

            _role_timeout = _get_role_timeout(role_name)

            response = await isolated_query(
                sdk,
                prompt=prompt,
                system_prompt=system_prompt,
                cwd=project_dir,
                session_id=session_id,
                max_turns=work_turns,
                max_budget_usd=_get_task_budget(role_name),
                on_stream=_on_stream,
                on_tool_use=_on_tool_use,
                per_message_timeout=_role_timeout,
                model=_model_override,
            )
            if response.is_error and "timeout" in response.error_message.lower():
                work_had_error = True
            elif response.is_error:
                work_had_error = True
        except TimeoutError:
            work_had_error = True
            logger.warning(
                f"[LG-DAG] Task {task.id}: WORK phase timeout after {time.monotonic() - t0:.0f}s"
            )
        except asyncio.CancelledError:
            _drain_cancellations(f"(work phase for {task.id})")
            work_had_error = True
        except CircuitOpenError as exc:
            _write_log(f"=== CIRCUIT BREAKER OPEN: {exc} ===")
            output = TaskOutput(
                task_id=task.id,
                status=TaskStatus.FAILED,
                summary=f"Circuit breaker open — SDK backend failing ({exc.failures} failures)",
                issues=["Circuit breaker is open"],
                failure_details=str(exc),
                confidence=0.0,
            )
            output.failure_category = FailureCategory.EXTERNAL
            if on_task_done:
                try:
                    await on_task_done(task, output)
                except Exception:
                    pass
            return output
        except Exception as e:
            logger.error(f"[LG-DAG] Task {task.id}: exception: {e}", exc_info=True)
            output = TaskOutput(
                task_id=task.id,
                status=TaskStatus.FAILED,
                summary=f"Agent exception: {e}",
                issues=[str(e)[:300]],
                failure_details=str(e)[:500],
                confidence=0.0,
            )
            output.failure_category = classify_failure(output)
            if on_task_done:
                try:
                    await on_task_done(task, output)
                except Exception:
                    pass
            return output

        # Extract work phase data
        work_session_id = None
        work_cost = 0.0
        work_input_tokens = work_output_tokens = work_total_tokens = work_turns_used = 0
        work_text = ""
        if response is not None:
            work_session_id = response.session_id or None
            work_cost = response.cost_usd
            work_input_tokens = response.input_tokens
            work_output_tokens = response.output_tokens
            work_total_tokens = response.total_tokens
            work_turns_used = response.num_turns
            work_text = response.text
            if response.session_id:
                session_ids[session_key] = response.session_id
            if response.is_error:
                work_had_error = True
        else:
            work_session_id = session_ids.get(session_key)

        # Snapshot git status after
        post_git_status = await _git_status(project_dir, task.id, "POST-RUN")

        # Try to parse JSON from work phase
        output: TaskOutput | None = None
        if work_text:
            work_tool_uses = response.tool_uses if response is not None else None
            output = extract_task_output(work_text, task.id, role_name, tool_uses=work_tool_uses)
            output.cost_usd = work_cost
            output.input_tokens = work_input_tokens
            output.output_tokens = work_output_tokens
            output.total_tokens = work_total_tokens
            output.turns_used = work_turns_used

        # Phase 2: SUMMARY — mandatory JSON extraction
        _missing_required = set()
        if task.required_artifacts and output and output.structured_artifacts:
            _produced = {a.type for a in output.structured_artifacts}
            _missing_required = set(task.required_artifacts) - _produced
        elif task.required_artifacts and (output is None or not output.structured_artifacts):
            _missing_required = set(task.required_artifacts)

        needs_summary = (
            work_session_id
            and (work_turns_used >= _MIN_WORK_TURNS_FOR_SUMMARY or work_had_error)
            and (
                output is None
                or not output.is_successful()
                or output.confidence <= 0.90
                or bool(_missing_required)
            )
        )

        if needs_summary:
            _emit_task_progress(
                on_event,
                graph.project_id,
                task.id,
                "summarising",
                _task_start_mono,
                _est_total,
                task_name=_task_goal,
            )
            summary_output = await _run_summary_phase(
                task=task,
                sdk=sdk,
                project_dir=project_dir,
                session_id=work_session_id,
                system_prompt=system_prompt,
                work_cost=work_cost,
                work_turns=work_turns_used,
                role_name=role_name,
                work_tool_uses=response.tool_uses if response is not None else None,
            )
            if summary_output is not None:
                if output is None or summary_output.confidence > output.confidence:
                    output = summary_output
                else:
                    output.cost_usd = work_cost + (summary_output.cost_usd - work_cost)
                    output.input_tokens = work_input_tokens + summary_output.input_tokens
                    output.output_tokens = work_output_tokens + summary_output.output_tokens
                    output.total_tokens = work_total_tokens + summary_output.total_tokens
                    output.turns_used = work_turns_used + (
                        summary_output.turns_used - work_turns_used
                    )
        elif output is None:
            output = TaskOutput(
                task_id=task.id,
                status=TaskStatus.FAILED,
                summary=f"Agent produced no output (turns={work_turns_used}, error={work_had_error})",
                issues=["No output from work phase and no session for summary phase"],
                cost_usd=work_cost,
                input_tokens=work_input_tokens,
                output_tokens=work_output_tokens,
                total_tokens=work_total_tokens,
                turns_used=work_turns_used,
                confidence=0.0,
            )

        # Artifact validation
        if output.is_successful() and output.artifacts:
            output = _validate_artifacts(output, project_dir)

        # Register artifacts
        if output.is_successful() and artifact_registry:
            try:
                artifact_registry.register(output)
            except Exception:
                pass

        # Blackboard file ownership
        if output.is_successful() and output.artifacts and blackboard:
            try:
                for art_path in output.artifacts:
                    blackboard.register_file_ownership(art_path, task.id)
            except Exception:
                pass

        # Reflexion
        try:
            from reflexion import run_reflexion, should_reflect

            if should_reflect(task, output):
                output, verdict = await run_reflexion(
                    task=task,
                    output=output,
                    session_id=session_ids.get(session_key),
                    system_prompt=system_prompt,
                    project_dir=project_dir,
                    sdk=sdk,
                    on_stream=_on_stream,
                )
                if structured_notes:
                    try:
                        structured_notes.add_note(
                            category=NoteCategory.CONTEXT,
                            title=f"Reflexion for {task.id}",
                            content=verdict.summary(),
                            author_role=role_name,
                            author_task_id=task.id,
                            tags=[task.id, "reflexion"],
                        )
                    except Exception:
                        pass
        except Exception:
            pass

        # Structured notes
        if structured_notes:
            try:
                if output.is_successful() and output.summary:
                    _files_info = ""
                    if output.artifacts:
                        _files_info = f"\nFiles changed: {', '.join(output.artifacts[:10])}"
                    structured_notes.add_note(
                        category=NoteCategory.CONTEXT,
                        title=f"Task {task.id} completed",
                        content=output.summary[:500] + _files_info,
                        author_role=role_name,
                        author_task_id=task.id,
                        tags=[task.id, role_name],
                    )
                if output.issues:
                    for issue in output.issues[:3]:
                        structured_notes.add_note(
                            category=NoteCategory.GOTCHA,
                            title=f"Issue in {task.id}",
                            content=issue,
                            author_role=role_name,
                            author_task_id=task.id,
                            tags=[task.id, role_name],
                        )
            except Exception:
                pass

        # Symbol extraction
        if output.is_successful() and output.artifacts and structured_notes:
            try:
                symbols = await _extract_symbols_from_diff(project_dir, output.artifacts)
                if symbols:
                    structured_notes.add_note(
                        category=NoteCategory.CONVENTION,
                        title=f"Symbols defined by {task.id}",
                        content=symbols,
                        author_role=role_name,
                        author_task_id=task.id,
                        tags=[task.id, role_name, "symbols"],
                    )
            except Exception:
                pass

        # Dynamic DAG: discovered tasks
        if output.is_successful() and output.discovered_tasks:
            try:
                _inject_discovered_tasks(
                    graph,
                    task,
                    output,
                    state.get("dynamic_task_count", 0),
                    state["task_counter"],
                    on_event,
                )
            except Exception:
                pass

        # Detect max_turns exhaustion
        total_turns = max_turns
        if output.turns_used >= total_turns and not output.is_successful():
            output.confidence = max(output.confidence - 0.3, 0.1)
            if not output.failure_category:
                output.failure_category = FailureCategory.TIMEOUT
                output.failure_details = (
                    f"Agent exhausted max_turns ({output.turns_used}/{total_turns})"
                )
            if not output.summary or output.summary.strip() == "":
                output.status = TaskStatus.FAILED
        elif not output.is_successful() and not output.failure_category:
            output.failure_category = classify_failure(output)

        # Validate required artifacts
        if output.is_successful() and task.required_artifacts:
            _check_required_artifact_types(task, output)

        total_elapsed = time.monotonic() - t0

        # Activity log
        _emit_activity_log(task, output, total_elapsed, post_git_status, project_dir, on_event)

        # Milestone: complete/failed
        _final_milestone = "complete" if output.is_successful() else "failed"
        _emit_task_progress(
            on_event,
            graph.project_id,
            task.id,
            _final_milestone,
            _task_start_mono,
            0.0,
            task_name=_task_goal,
        )
        try:
            from dashboard.events import task_progress_throttler

            task_progress_throttler.reset(f"tp:{task.id}")
        except Exception:
            pass

        logger.info(
            f"[LG-DAG] Task {task.id} ({role_name}): "
            f"status={output.status.value}, confidence={output.confidence:.2f}, "
            f"turns={output.turns_used}, cost=${output.cost_usd:.4f}, "
            f"elapsed={total_elapsed:.1f}s"
        )

        _write_log(f"=== Task {task.id} FINISHED in {total_elapsed:.1f}s ===")

        if on_task_done:
            try:
                total_tasks = len(graph.tasks)
                done_tasks = sum(1 for t in graph.tasks if t.id in completed) + 1
                output.progress = f"{done_tasks}/{total_tasks}"
                await on_task_done(task, output)
            except Exception:
                pass

        # DAG_PROGRESS event
        _done_count = sum(1 for t in graph.tasks if t.id in completed) + 1
        _emit_dag_progress(
            on_event, graph.project_id, _done_count, len(graph.tasks), graph_start_time
        )

        return output

    # Execute batch with file locking and semaphore
    async def _run_with_locks(task: TaskInput) -> TaskOutput:
        """Run task with file-level locks for writers."""
        is_writer = task.role in _WRITER_ROLES
        locked_files: list[str] = []
        if is_writer and task.files_scope:
            locked_files = list(task.files_scope)
            acquired = await _file_lock_manager.acquire_files(locked_files)
            if not acquired:
                return TaskOutput(
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    summary="Timed out waiting for file locks",
                    issues=["Could not acquire file-level locks"],
                    failure_details=f"File lock timeout for: {locked_files}",
                    confidence=0.0,
                )
        try:
            return await _run_one_task(task)
        finally:
            if locked_files:
                _file_lock_manager.release_files(locked_files)

    # Launch batch
    if len(batch) == 1:
        results = [await _run_with_locks(batch[0])]
    else:
        subtasks = [asyncio.create_task(_run_with_locks(t), name=f"dag-{t.id}") for t in batch]
        # Use cancellation-resilient wait
        cancel_hits = 0
        while not all(t.done() for t in subtasks):
            try:
                await asyncio.wait(subtasks, return_when=asyncio.ALL_COMPLETED)
                break
            except asyncio.CancelledError:
                cancel_hits += 1
                _drain_cancellations("(batch wait)")

        raw_results = []
        for st in subtasks:
            if st.cancelled():
                raw_results.append(asyncio.CancelledError())
            elif st.exception() is not None:
                raw_results.append(st.exception())
            else:
                raw_results.append(st.result())

        results = []
        _all_cancelled = all(isinstance(r, asyncio.CancelledError) for r in raw_results)
        for task_item, raw in zip(batch, raw_results, strict=False):
            if isinstance(raw, asyncio.CancelledError):
                err = TaskOutput(
                    task_id=task_item.id,
                    status=TaskStatus.FAILED,
                    summary="Task cancelled (spurious CancelledError)",
                    issues=["CancelledError — likely anyio cancel-scope leak"],
                    failure_details="asyncio.CancelledError (spurious)",
                    confidence=0.0,
                )
                err.failure_category = classify_failure(err)
                results.append(err)
            elif isinstance(raw, BaseException):
                err = TaskOutput(
                    task_id=task_item.id,
                    status=TaskStatus.FAILED,
                    summary=f"Exception: {type(raw).__name__}: {str(raw)[:200]}",
                    issues=[str(raw)[:300]],
                    failure_details=str(raw)[:500],
                    confidence=0.0,
                )
                err.failure_category = classify_failure(err)
                results.append(err)
            else:
                results.append(raw)

    # Build state updates
    new_completed = {r.task_id: r for r in results}
    new_cost = sum(r.cost_usd for r in results)
    new_notes = []
    for r in results:
        if r.is_successful() and r.summary:
            new_notes.append(f"[{r.task_id}] {r.summary[:200]}")
        if r.issues:
            for issue in r.issues[:2]:
                new_notes.append(f"[{r.task_id} issue] {issue[:150]}")

    return {
        "completed": new_completed,
        "total_cost": new_cost,
        "batch_results": results,
        "blackboard_notes": new_notes,
    }


# ── Node: post_batch ────────────────────────────────────────────────────


async def post_batch(state: DAGState) -> dict:
    """Post-batch: git commit, failure handling (retry/remediation), checkpointing."""
    graph = state["graph"]
    completed = state["completed"]
    batch_results = state["batch_results"]
    project_dir = state["project_dir"]
    remediation_count = state["remediation_count"]
    task_counter = state["task_counter"]
    on_remediation = state.get("on_remediation")
    dynamic_spawner = state.get("dynamic_spawner")
    commit_approval_callback = state.get("commit_approval_callback")

    # batch_results uses the `add` reducer so it accumulates across rounds.
    # Only process results from the CURRENT batch to avoid re-handling old
    # failures and creating duplicate remediation tasks.
    current_batch_ids = {t.id for t in state.get("current_batch", [])}
    batch_results = [r for r in batch_results if r.task_id in current_batch_ids]

    # Per-task commits
    for result in batch_results:
        if not result.is_successful():
            continue
        # Find original task
        task = None
        for t in graph.tasks:
            if t.id == result.task_id:
                task = t
                break
        if task is None:
            continue

        # Successful remediation unblocks downstream
        if task.is_remediation and task.original_task_id:
            completed[task.original_task_id] = result

        # Approval gate
        _commit_approved = True
        if commit_approval_callback:
            try:
                desc = (
                    f"Task **{task.id}** ({task.role}) completed.\n"
                    f"Summary: {result.summary[:200]}\n"
                    f"Files: {', '.join(result.artifacts[:5]) if result.artifacts else 'none'}"
                )
                _commit_approved = await commit_approval_callback(desc)
            except Exception:
                _commit_approved = True

        if _commit_approved:
            try:
                _goal = task.goal if task else ""
                _role = task.role if task else ""
                committed = await commit_single_task(
                    project_dir,
                    result,
                    task_goal=_goal,
                    task_role=_role,
                )
                if committed:
                    logger.info(f"[LG-DAG] Task {result.task_id} committed: {committed}")
            except Exception:
                pass

    # Fallback round commit
    _round_approved = True
    if commit_approval_callback:
        try:
            _round_approved = await commit_approval_callback(
                f"Round {state['round_num']} completed. Commit remaining changes?"
            )
        except Exception:
            _round_approved = True

    if _round_approved:
        try:
            successful = [r for r in batch_results if r.is_successful()]
            if successful:
                await executor_commit(project_dir, successful, state["round_num"])
        except Exception:
            pass

    # Handle failures — retry, model switch, or remediation
    new_healing = []
    new_remediation_count = remediation_count
    new_task_counter = task_counter
    new_retries = {}
    new_model_overrides = {}
    tasks_to_uncomplete = []

    for result in batch_results:
        if result.is_successful():
            continue

        task = None
        for t in graph.tasks:
            if t.id == result.task_id:
                task = t
                break
        if task is None:
            continue

        category = result.failure_category or classify_failure(result)
        strategy = get_retry_strategy(category)
        max_retries_for_cat = int(strategy["max_retries"])
        remediation_allowed = bool(strategy["remediation_allowed"])

        current_retries = state["retries"].get(task.id, 0)

        # Direct retry
        if current_retries < max_retries_for_cat and not result.is_terminal():
            new_retries[task.id] = current_retries + 1
            tasks_to_uncomplete.append(task.id)
            logger.warning(
                f"[LG-DAG] Task {task.id} failed ({category.value}), "
                f"retrying ({current_retries + 1}/{max_retries_for_cat})"
            )
            continue

        # Dynamic Spawner — model switch
        if dynamic_spawner:
            try:
                alt_model = dynamic_spawner.get_respawn_model(task=task, output=result)
                if alt_model:
                    new_model_overrides[task.id] = alt_model
                    tasks_to_uncomplete.append(task.id)
                    new_healing.append(
                        {
                            "task_id": task.id,
                            "action": "model_switch",
                            "from_model": "default",
                            "to_model": alt_model,
                            "reason": f"Dynamic Spawner: {category.value} failure",
                        }
                    )
                    continue
            except Exception:
                pass

        # Remediation
        if remediation_allowed and new_remediation_count < MAX_TOTAL_REMEDIATIONS:
            depth = _remediation_depth(task, graph.tasks)
            if depth < MAX_REMEDIATION_DEPTH:
                new_task_counter += 1
                remediation = create_remediation_task(
                    failed_task=task,
                    failed_output=result,
                    task_counter=new_task_counter,
                )
                if remediation:
                    graph.add_task(remediation)
                    new_remediation_count += 1
                    new_healing.append(
                        {
                            "action": "remediation_created",
                            "failed_task": task.id,
                            "failure_category": category.value,
                            "remediation_task": remediation.id,
                            "detail": f"Auto-created {remediation.id} ({remediation.role}) to fix {task.id}",
                        }
                    )
                    if on_remediation:
                        try:
                            await on_remediation(task, result, remediation)
                        except Exception:
                            pass

    # Remove tasks that need retry from completed
    uncomplete_map = {}
    for tid in tasks_to_uncomplete:
        if tid in completed:
            uncomplete_map[tid] = completed[tid]
            # We can't truly remove from LangGraph state with a reducer,
            # but the retry logic handles it: we set status to mark for re-execution

    # Checkpoint
    try:
        checkpoint = DAGCheckpoint(
            project_id=graph.project_id,
            graph_json=graph.model_dump_json(),
            completed_tasks={tid: out.model_dump() for tid, out in completed.items()},
            retries={**state["retries"], **new_retries},
            total_cost=state["total_cost"],
            remediation_count=new_remediation_count,
            healing_history=state["healing_history"] + new_healing,
            round_num=state["round_num"],
            created_at=time.time(),
            status="running",
        )
        await _async_save_checkpoint(project_dir, checkpoint)
    except Exception:
        pass

    # Determine status
    if graph.is_complete(completed):
        status = "completed"
    else:
        status = "running"

    return {
        "remediation_count": new_remediation_count,
        "task_counter": new_task_counter,
        "healing_history": new_healing,
        "retries": new_retries,
        "model_overrides": new_model_overrides,
        "status": status,
    }


# ── Node: review_code ───────────────────────────────────────────────────


async def review_code(state: DAGState) -> dict:
    """Final quality gate — read-only critique after all tasks complete.

    The reviewer does NOT modify code (ACC-Collab Critic pattern).  It produces
    a structured critique.  If it finds issues, they are logged to the
    blackboard so a future remediation cycle can address them.

    After any code changes are committed, tests are re-run.  If the review
    step (via ruff or other automated fixers) broke tests, the commit is
    reverted (Test-After-Review safety net).
    """
    if state["status"] != "completed":
        return {}

    project_dir = state["project_dir"]
    sdk = state["sdk"]
    specialist_prompts = state["specialist_prompts"]

    # ── Record pre-review HEAD so we can revert if tests break ──
    pre_review_head = None
    try:
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if head_result.returncode == 0:
            pre_review_head = head_result.stdout.strip()
    except Exception:
        pass

    py_files = []
    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = [
            d
            for d in dirs
            if d not in (".git", "__pycache__", ".pytest_cache", ".hivemind", "node_modules")
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                fpath = os.path.join(root, fn)
                try:
                    with open(fpath) as f:
                        content = f.read()
                    py_files.append((os.path.relpath(fpath, project_dir), content))
                except Exception:
                    pass

    if not py_files:
        return {}

    files_text = ""
    for rel, content in py_files:
        files_text += f"\n### {rel}\n```python\n{content}\n```\n"

    review_prompt = (
        "You are a senior code reviewer performing a READ-ONLY critique.\n"
        "Review these Python files and produce a structured critique.\n"
        "Focus on: bugs, error handling, type safety, code duplication, style.\n"
        "DO NOT modify any files. Only READ and ANALYSE.\n\n"
        "Output your review as:\n"
        "## ISSUES\n"
        "- [SEVERITY] file:line — description\n\n"
        "## SUGGESTIONS\n"
        "- file — what to improve and why\n\n"
        "## VERDICT\n"
        "PASS or NEEDS_FIX\n\n"
        f"Project files:\n{files_text}"
    )

    system_prompt = specialist_prompts.get("reviewer", "You are an expert code reviewer.")
    try:
        from project_context import build_project_header

        _boundary = build_project_header(state["graph"].project_id, project_dir)
        if _boundary and _boundary not in system_prompt:
            system_prompt = _boundary + "\n\n" + system_prompt
    except Exception:
        pass

    review_notes: list[str] = []
    review_cost = 0.0

    try:
        response = await asyncio.wait_for(
            isolated_query(
                sdk,
                prompt=review_prompt,
                system_prompt=system_prompt,
                cwd=project_dir,
                max_turns=10,
                max_budget_usd=1.0,
                allowed_tools=["Read", "Glob", "Grep"],
            ),
            timeout=180,
        )
        review_cost = response.cost_usd
        review_text = response.text if response else ""
        review_notes.append(f"[review] Reviewed {len(py_files)} files (read-only critique)")
        if "NEEDS_FIX" in review_text:
            review_notes.append("[review] Verdict: NEEDS_FIX — see critique for details")
            # Extract issues for blackboard
            for line in review_text.split("\n"):
                line = line.strip()
                if line.startswith("- [") and "]" in line:
                    review_notes.append(f"[review] {line[:200]}")
    except Exception as e:
        logger.warning("review_code: critique failed (non-fatal): %s", e)
        review_notes.append("[review] Critique skipped due to error")

    # ── Automated lint/format pass (non-destructive) ──
    try:
        subprocess.run(
            ["ruff", "check", "--fix", "--quiet"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        subprocess.run(
            ["ruff", "format", "--quiet"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, Exception):
        pass

    # ── Commit lint/format changes if any ──
    has_review_commit = False
    try:
        subprocess.run(["git", "add", "-A"], cwd=project_dir, capture_output=True, text=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            subprocess.run(
                ["git", "commit", "-m", "review: automated lint/format fixes"],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
            has_review_commit = True
    except Exception:
        pass

    # ── Test-After-Review: verify lint/format didn't break anything ──
    if has_review_commit and pre_review_head:
        try:
            test_result = subprocess.run(
                ["python3", "-m", "pytest", "--tb=short", "-q", "--no-header", "--timeout=30"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if test_result.returncode != 0:
                logger.warning(
                    "review_code: tests FAILED after lint/format — reverting to %s",
                    pre_review_head[:8],
                )
                subprocess.run(
                    ["git", "reset", "--hard", pre_review_head],
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                )
                review_notes.append(
                    "[review] Lint/format changes REVERTED — tests failed after review"
                )
                has_review_commit = False
            else:
                review_notes.append("[review] Post-review tests PASSED")
        except FileNotFoundError:
            pass  # pytest not installed — skip test verification
        except subprocess.TimeoutExpired:
            logger.warning("review_code: post-review test run timed out")
        except Exception as e:
            logger.debug("review_code: post-review test check failed (non-fatal): %s", e)

    return {
        "total_cost": review_cost,
        "blackboard_notes": review_notes,
    }


# ── Routing ──────────────────────────────────────────────────────────────


def should_continue(state: DAGState) -> Literal["select_batch", "review_code", "__end__"]:
    """Conditional edge: continue executing, review code, or stop."""
    status = state["status"]
    if status == "completed":
        return "review_code"
    elif status in ("failed", "max_rounds"):
        return END
    return "select_batch"


# ── Build the LangGraph ─────────────────────────────────────────────────


def build_dag_graph() -> StateGraph:
    """Build and compile the LangGraph DAG executor graph."""
    workflow = StateGraph(DAGState)
    workflow.add_node("select_batch", select_batch)
    workflow.add_node("execute_batch", execute_batch)
    workflow.add_node("post_batch", post_batch)
    workflow.add_node("review_code", review_code)
    workflow.add_edge(START, "select_batch")
    workflow.add_edge("select_batch", "execute_batch")
    workflow.add_edge("execute_batch", "post_batch")
    workflow.add_edge("review_code", END)
    workflow.add_conditional_edges(
        "post_batch",
        should_continue,
        {"select_batch": "select_batch", "review_code": "review_code", END: END},
    )
    return workflow


# ── ExecutionResult ─────────────────────────────────────────────────────


class ExecutionResult:
    """Result of a graph execution, including healing history."""

    def __init__(
        self,
        outputs: list[TaskOutput],
        total_cost: float,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_tokens: int = 0,
        success_count: int = 0,
        failure_count: int = 0,
        remediation_count: int = 0,
        healing_history: list[dict[str, str]] | None = None,
    ):
        self.outputs = outputs
        self.total_cost = total_cost
        self.total_input_tokens = total_input_tokens
        self.total_output_tokens = total_output_tokens
        self.total_tokens = total_tokens
        self.success_count = success_count
        self.failure_count = failure_count
        self.remediation_count = remediation_count
        self.healing_history = healing_history or []

    @property
    def all_successful(self) -> bool:
        return self.failure_count == 0

    def summary_text(self) -> str:
        lines = [
            f"Tasks: {self.success_count + self.failure_count} total, "
            f"{self.success_count} succeeded, {self.failure_count} failed",
            f"Remediations: {self.remediation_count}",
            f"Total cost: ${self.total_cost:.4f} | Tokens: {self.total_tokens}",
        ]
        if self.healing_history:
            lines.append("\nSelf-healing actions:")
            for h in self.healing_history:
                lines.append(f"  - {h.get('action', 'unknown')}: {h.get('detail', '')}")
        return "\n".join(lines)


# ── Watchdog ─────────────────────────────────────────────────────────────

# Note: The LangGraph version uses a simplified watchdog that runs as a
# background task during execute_graph(). Task-level timeout is handled
# by isolated_query's per_message_timeout and asyncio.wait_for in the
# original, but here the LangGraph executor manages the flow.


# ── Public API: execute_graph ────────────────────────────────────────────


async def execute_graph(
    graph: TaskGraph,
    project_dir: str,
    specialist_prompts: dict[str, str],
    sdk_client=None,
    on_task_start: Callable[[TaskInput], Awaitable[None]] | None = None,
    on_task_done: Callable[[TaskInput, TaskOutput], Awaitable[None]] | None = None,
    on_remediation: Callable[[TaskInput, TaskOutput, TaskInput], Awaitable[None]] | None = None,
    on_agent_stream: Callable | None = None,
    on_agent_tool_use: Callable | None = None,
    on_event: Callable | None = None,
    max_budget_usd: float = 50.0,
    session_id_store: dict[str, str] | None = None,
    max_concurrent_tasks: int | None = None,
    commit_approval_callback: Callable[[str], Awaitable[bool]] | None = None,
) -> ExecutionResult:
    """Execute a TaskGraph using LangGraph — drop-in replacement.

    Same signature and return type as the original dag_executor.execute_graph().
    """
    import state as app_state

    sdk = sdk_client or app_state.sdk_client
    if sdk is None:
        raise RuntimeError("SDK client not initialized")

    # Pre-execution: validate artifact contracts
    contract_mismatches = validate_artifact_contracts(graph)
    if contract_mismatches:
        explicit = [m for m in contract_mismatches if "inferred check" not in m]
        if explicit:
            raise ArtifactContractError(explicit)

    # Initialize context layers
    _structured_notes = StructuredNotes(project_dir)
    _structured_notes.init_session(graph.vision)
    try:
        from blackboard import Blackboard

        _blackboard = Blackboard(_structured_notes)
    except Exception:
        _blackboard = None
    _artifact_registry = ArtifactRegistry(project_dir)
    _dynamic_spawner = DynamicSpawner()

    # Build and compile — no checkpointer needed for in-memory execution.
    # MemorySaver tries to msgpack-serialize ALL state values (including
    # non-serializable objects like ClaudeSDKManager, callbacks, etc.)
    # which crashes with: TypeError: Type is not msgpack serializable.
    workflow = build_dag_graph()
    compiled = workflow.compile()

    _graph_start_time = time.monotonic()

    initial_state: DAGState = {
        "graph": graph,
        "project_dir": project_dir,
        "specialist_prompts": specialist_prompts,
        "sdk": sdk,
        "max_budget_usd": max_budget_usd,
        "completed": {},
        "retries": {},
        "session_ids": session_id_store or {},
        "total_cost": 0.0,
        "remediation_count": 0,
        "task_counter": len(graph.tasks),
        "round_num": 0,
        "healing_history": [],
        "current_batch": [],
        "batch_results": [],
        "blackboard_notes": [],
        "structured_notes": _structured_notes,
        "blackboard": _blackboard,
        "artifact_registry": _artifact_registry,
        "dynamic_spawner": _dynamic_spawner,
        "codebase_symbols": "",
        "dynamic_task_count": 0,
        "model_overrides": {},
        "user_message": getattr(graph, "user_message", "") or "",
        "graph_start_time": _graph_start_time,
        "status": "running",
        "on_task_start": on_task_start,
        "on_task_done": on_task_done,
        "on_remediation": on_remediation,
        "on_agent_stream": on_agent_stream,
        "on_agent_tool_use": on_agent_tool_use,
        "on_event": on_event,
        "commit_approval_callback": commit_approval_callback,
    }

    config = {
        "configurable": {
            "thread_id": f"dag-{graph.project_id}-{int(time.time())}",
        }
    }

    logger.info(
        f"[LG-DAG] Starting execution: project={graph.project_id}, "
        f"tasks={len(graph.tasks)}, budget=${max_budget_usd}"
    )

    # Run the graph
    final_state = await compiled.ainvoke(initial_state, config)

    elapsed = time.monotonic() - _graph_start_time

    # Save artifact manifest
    try:
        _artifact_registry.save_manifest()
    except Exception:
        pass

    # Final checkpoint
    _final_status = "completed" if final_state["status"] == "completed" else "interrupted"
    try:
        checkpoint = DAGCheckpoint(
            project_id=graph.project_id,
            graph_json=graph.model_dump_json(),
            completed_tasks={
                tid: out.model_dump() for tid, out in final_state["completed"].items()
            },
            retries=final_state["retries"],
            total_cost=final_state["total_cost"],
            remediation_count=final_state["remediation_count"],
            healing_history=final_state["healing_history"],
            round_num=final_state["round_num"],
            created_at=time.time(),
            status=_final_status,
        )
        await _async_save_checkpoint(project_dir, checkpoint)
        if _final_status == "completed":
            _clear_checkpoints(project_dir, graph.project_id)
    except Exception:
        pass

    # Build result
    completed = final_state["completed"]
    outputs = list(completed.values())
    total_input_tokens = sum(o.input_tokens for o in outputs)
    total_output_tokens = sum(o.output_tokens for o in outputs)
    total_tokens = sum(o.total_tokens for o in outputs)

    result = ExecutionResult(
        outputs=outputs,
        total_cost=final_state["total_cost"],
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_tokens=total_tokens,
        success_count=sum(1 for o in outputs if o.is_successful()),
        failure_count=sum(1 for o in outputs if not o.is_successful()),
        remediation_count=final_state["remediation_count"],
        healing_history=final_state["healing_history"],
    )

    logger.info(
        f"[LG-DAG] Execution complete in {elapsed:.1f}s: "
        f"{result.summary_text()}, status={final_state['status']}"
    )

    return result


# ── Summary helper ───────────────────────────────────────────────────────


def build_execution_summary(graph: TaskGraph, result: ExecutionResult) -> str:
    """Build a human-readable summary of the graph execution."""
    output_map = {o.task_id: o for o in result.outputs}
    _total_k = result.total_tokens / 1000 if result.total_tokens else 0
    lines = [
        f"## Execution Summary — {graph.vision}",
        f"Tasks: {result.success_count + result.failure_count}/{len(graph.tasks)} executed "
        f"({result.success_count} succeeded, {result.failure_count} failed)",
        f"Self-healing: {result.remediation_count} remediation tasks created",
        f"Tokens: {_total_k:.1f}K",
        "",
    ]
    for task in graph.tasks:
        output = output_map.get(task.id)
        if output:
            if output.is_successful():
                icon = "✅"
            elif output.status == TaskStatus.FAILED:
                icon = "❌"
            else:
                icon = "⚠️"
            prefix = "🔧 " if task.is_remediation else ""
            lines.append(f"{icon} {prefix}[{task.id}] {task.role}: {output.summary[:120]}")
            if output.structured_artifacts:
                art_names = [a.title for a in output.structured_artifacts[:3]]
                lines.append(f"   Artifacts: {', '.join(art_names)}")
            if output.artifacts:
                lines.append(f"   Files: {', '.join(output.artifacts[:5])}")
            if output.issues:
                lines.append(f"   Issues: {'; '.join(output.issues[:2])}")
            if output.failure_category:
                lines.append(f"   Failure: {output.failure_category.value}")
        else:
            lines.append(f"⏭️  [{task.id}] {task.role}: Not executed")

    if result.healing_history:
        lines.append("\n### Self-Healing Actions")
        for h in result.healing_history:
            lines.append(f"  🔧 {h.get('detail', '')}")

    return "\n".join(lines)
