"""
DAG Executor — The Orchestrator's execution engine.

v2: Self-Healing DAG with Artifact-Based Context Passing.

Key upgrades:
- Self-healing: auto-classifies failures and injects remediation tasks into the DAG
- Artifact passing: downstream agents receive structured artifacts, not just summaries
- Smart retry: different retry strategies based on failure category
- Artifact validation: verifies agents produced their required artifacts
- Enhanced parallelism: better conflict detection using artifact dependencies
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import config as cfg
import state
from complexity import READER_ROLES_ENUM, WRITER_ROLES_ENUM
from contracts import (
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


# ---------------------------------------------------------------------------
# Task Progress Event Emission — throttled, lightweight milestone events
# ---------------------------------------------------------------------------


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
    # Enrich with human-readable fields (backward-compatible additions)
    if task_name:
        event["task_name"] = task_name[:120]
    event["step_description"] = _make_step_description(milestone, task_name)
    _fire_event(on_event, event)


def _emit_dag_progress(
    on_event: Callable | None,
    project_id: str,
    completed_count: int,
    total_count: int,
    graph_start_time: float,
) -> None:
    """Emit a DAG_PROGRESS aggregate event with completion % and ETA."""
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


# ---------------------------------------------------------------------------
# File-Level Lock Manager — singleton across all concurrent graph executions
# ---------------------------------------------------------------------------


class FileLockManager:
    """Manages per-file asyncio locks to prevent writer conflicts across graphs.

    Each file path gets its own asyncio.Lock.  Writer tasks must acquire locks
    for ALL files in their ``files_scope`` before execution begins and release
    them when done.  Reader tasks do not need locks.

    The manager is a process-wide singleton so that locks are shared across
    concurrent graph executions (multiple OrchestratorManagers running in the
    same event loop).
    """

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
        """Acquire locks for all *file_paths* atomically (sorted to avoid deadlocks).

        Args:
            file_paths: List of file paths to lock.
            timeout: Maximum seconds to wait.  ``None`` → use config default.

        Returns:
            ``True`` if all locks acquired, ``False`` on timeout.
        """
        if not file_paths:
            return True

        if timeout is None:
            timeout = cfg.FILE_LOCK_TIMEOUT

        # Sort to impose a global ordering → prevents ABBA deadlocks
        sorted_paths = sorted(set(file_paths))

        # Ensure Lock objects exist for all requested paths
        async with self._meta_lock:
            for fp in sorted_paths:
                if fp not in self._locks:
                    self._locks[fp] = asyncio.Lock()

        # Acquire each lock in order with a global timeout
        acquired: list[str] = []
        deadline = asyncio.get_event_loop().time() + timeout

        try:
            for fp in sorted_paths:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    # Timeout — release everything we acquired
                    self._release_paths(acquired)
                    return False
                try:
                    await asyncio.wait_for(
                        self._locks[fp].acquire(),
                        timeout=remaining,
                    )
                    acquired.append(fp)
                except TimeoutError:
                    self._release_paths(acquired)
                    return False
        except Exception:
            self._release_paths(acquired)
            raise

        return True

    def release_files(self, file_paths: list[str]) -> None:
        """Release locks for the given file paths."""
        self._release_paths(sorted(set(file_paths)))

    def _release_paths(self, paths: list[str]) -> None:
        for fp in paths:
            lock = self._locks.get(fp)
            if lock is not None and lock.locked():
                lock.release()

    @property
    def active_locks(self) -> int:
        """Number of currently held file locks (for observability)."""
        return sum(1 for lk in self._locks.values() if lk.locked())


# Module-level singleton
_file_lock_manager = FileLockManager()


# Per-project checkpoint locks — prevents concurrent checkpoint writes
# for the same project across parallel graph executions.
_checkpoint_locks: dict[str, asyncio.Lock] = {}
_checkpoint_locks_meta = asyncio.Lock()


async def _get_checkpoint_lock(project_id: str) -> asyncio.Lock:
    """Return (lazily create) a per-project checkpoint lock."""
    async with _checkpoint_locks_meta:
        if project_id not in _checkpoint_locks:
            _checkpoint_locks[project_id] = asyncio.Lock()
        return _checkpoint_locks[project_id]


# --- Configuration — pulled from centralised config.py (H-3 fix) ---
# These were previously hardcoded magic numbers; they are now tuneable via env vars.
MAX_TASK_RETRIES: int = cfg.MAX_TASK_RETRIES  # Direct retries per task
MAX_REMEDIATION_DEPTH: int = (
    cfg.MAX_REMEDIATION_DEPTH
)  # Max chain of fix_xxx tasks before giving up
MAX_TOTAL_REMEDIATIONS: int = (
    cfg.MAX_TOTAL_REMEDIATIONS
)  # Max total remediation tasks per graph execution
MAX_ROUNDS: int = cfg.MAX_DAG_ROUNDS  # Safety limit on execution rounds (default 50)

# ---------------------------------------------------------------------------
# DAG Checkpoint Store — SQLite-backed durable checkpoints for resume
# ---------------------------------------------------------------------------

import sqlite3

_CHECKPOINT_DB: str | None = None  # Set lazily to {project_dir}/.hivemind/dag_checkpoints.db


def _get_checkpoint_db(project_dir: str) -> str:
    """Return (and lazily initialise) the SQLite checkpoint DB path."""
    hive_dir = Path(project_dir) / ".hivemind"
    hive_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(hive_dir / "dag_checkpoints.db")
    # Ensure table exists (idempotent)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dag_checkpoints (
                project_id   TEXT NOT NULL,
                round_num    INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'running',
                checkpoint   TEXT NOT NULL,
                created_at   REAL NOT NULL,
                PRIMARY KEY (project_id, round_num)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_checkpoint_project
            ON dag_checkpoints(project_id, created_at DESC)
            """
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning(f"[DAG] Checkpoint DB init failed (non-fatal): {exc}")
    return db_path


def _save_checkpoint(
    project_dir: str,
    checkpoint: DAGCheckpoint,
) -> None:
    """Persist a DAG checkpoint to SQLite (synchronous — called from async context via executor)."""
    db_path = _get_checkpoint_db(project_dir)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute(
            """
            INSERT OR REPLACE INTO dag_checkpoints (project_id, round_num, status, checkpoint, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                checkpoint.project_id,
                checkpoint.round_num,
                checkpoint.status,
                checkpoint.model_dump_json(),
                checkpoint.created_at,
            ),
        )
        # Keep only the last 10 checkpoints per project to bound disk usage
        conn.execute(
            """
            DELETE FROM dag_checkpoints
            WHERE project_id = ? AND round_num NOT IN (
                SELECT round_num FROM dag_checkpoints
                WHERE project_id = ?
                ORDER BY round_num DESC
                LIMIT 10
            )
            """,
            (checkpoint.project_id, checkpoint.project_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning(f"[DAG] Checkpoint save failed (non-fatal): {exc}")


def _load_latest_checkpoint(project_dir: str, project_id: str) -> DAGCheckpoint | None:
    """Load the most recent checkpoint for a project, or None."""
    db_path = _get_checkpoint_db(project_dir)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute(
            """
            SELECT checkpoint FROM dag_checkpoints
            WHERE project_id = ? AND status = 'running'
            ORDER BY round_num DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        conn.close()
        if row:
            return DAGCheckpoint.model_validate_json(row[0])
    except Exception as exc:
        logger.warning(f"[DAG] Checkpoint load failed (non-fatal): {exc}")
    return None


def _clear_checkpoints(project_dir: str, project_id: str) -> None:
    """Remove all checkpoints for a project (called on successful completion)."""
    db_path = _get_checkpoint_db(project_dir)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("DELETE FROM dag_checkpoints WHERE project_id = ?", (project_id,))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning(f"[DAG] Checkpoint cleanup failed (non-fatal): {exc}")


async def _async_save_checkpoint(project_dir: str, checkpoint: DAGCheckpoint) -> None:
    """Save checkpoint without blocking the event loop, with per-project locking."""
    lock = await _get_checkpoint_lock(checkpoint.project_id)
    async with lock:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _save_checkpoint, project_dir, checkpoint)


# Per-role max_turns and timeouts — derived from the SINGLE source of truth
# in config.py AGENT_REGISTRY.  No duplicated maps here.


def _get_max_turns(role: str) -> int:
    """Return the max_turns limit for a given agent role.

    Uses the centralized AGENT_REGISTRY from config.py.
    """
    from config import get_agent_turns

    return get_agent_turns(role)


def _drain_cancellations(label: str = "", *, stop_event: asyncio.Event | None = None) -> int:
    """Drain pending cancellations from the current asyncio task.

    Returns the number of cancellations drained.
    Call this after catching asyncio.CancelledError to prevent it
    from propagating further up the call stack.

    FIX(C-5): If *stop_event* is provided and set, the CancelledError
    is considered legitimate (user-initiated stop) and will NOT be
    drained.  Returns 0 so the caller can re-raise.
    """
    if stop_event is not None and stop_event.is_set():
        if label:
            logger.debug(
                f"[DAG] NOT draining cancellations {label} — stop_event is set (legitimate cancel)"
            )
        return 0
    ct = asyncio.current_task()
    if ct is None or not hasattr(ct, "uncancel"):
        return 0
    drained = 0
    while ct.cancelling() > 0:
        ct.uncancel()
        drained += 1
    if drained and label:
        logger.debug(f"[DAG] Drained {drained} cancellation(s) {label}")
    return drained


def _validate_cwd(cwd: str, project_dir: str) -> None:
    """Raise PermissionError if cwd escapes the project directory.

    This is the DAG executor's last line of defense against cwd path
    escapes before any subprocess is spawned.  All subprocess calls in
    this module must call this before setting cwd= on the process.
    """
    from config import SANDBOX_ENABLED

    if not SANDBOX_ENABLED:
        return
    cwd_abs = str(Path(cwd).resolve())
    proj_abs = str(Path(project_dir).resolve())
    if not cwd_abs.startswith(proj_abs):
        raise PermissionError(f"DAG executor cwd escape: {cwd_abs!r} is outside {proj_abs!r}")


async def _git_status(ctx: _ExecutionContext, task_id: str, stage: str) -> str:
    """Run `git status --porcelain` and return output. Returns '' on any error."""
    _validate_cwd(ctx.project_dir, ctx.project_dir)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            cwd=ctx.project_dir,
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
                pass  # Process already terminated
        return ""
    except Exception as e:
        logger.debug(f"[DAG] Task {task_id}: {stage} git status failed: {e}")
        return ""


def _get_task_timeout(role: str, task: TaskInput | None = None) -> int:
    """Return the wall-clock timeout (seconds) for a given agent role.

    Uses the centralized AGENT_REGISTRY from config.py as the base timeout,
    then applies adaptive scaling based on task complexity when a TaskInput
    is provided.

    Adaptive scaling:
    - complexity 1.0 → 1.0× base timeout
    - complexity 3.0 → 1.5× base timeout
    - complexity 5.0 → 2.0× base timeout
    """
    from config import get_agent_timeout

    base = get_agent_timeout(role)
    if task is not None:
        complexity = compute_task_complexity(task)
        # Linear interpolation: complexity 1→1.0×, 5→1.5× (capped at 1.5×)
        scale_factor = 1.0 + min((complexity - 1.0) * 0.125, 0.5)
        scaled = int(base * scale_factor)
        if scaled != base:
            logger.debug(
                f"[DAG] Adaptive timeout for {task.id} ({role}): "
                f"base={base}s × {scale_factor:.2f} (complexity={complexity:.1f}) = {scaled}s"
            )
        return max(scaled, 30)
    return base


def _get_task_budget(role: str) -> float:
    """Return the per-task budget (USD) for a given agent role.

    Uses the centralized AGENT_REGISTRY from config.py.
    """
    from config import get_agent_budget

    return get_agent_budget(role)


# Roles that write/modify files — imported from complexity.py (single source of truth)
_WRITER_ROLES = WRITER_ROLES_ENUM

# Roles that are read-only / analysis only — imported from complexity.py (single source of truth)
_READER_ROLES = READER_ROLES_ENUM

# Failure categories that should NOT be retried (waste of money)
_NO_RETRY_CATEGORIES = {
    FailureCategory.UNCLEAR_GOAL,
    FailureCategory.PERMISSION,
    FailureCategory.EXTERNAL,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
    """
    Execute a TaskGraph to completion with self-healing.

    Args:
        graph: The PM's execution plan
        project_dir: Working directory for all agents
        specialist_prompts: dict[role_value -> system_prompt]
        sdk_client: ClaudeSDKManager instance (defaults to state.sdk_client)
        on_task_start: Async callback fired when a task begins
        on_task_done: Async callback fired when a task finishes
        on_remediation: Async callback fired when a remediation task is created
        on_event: Async callback for real-time structured events (e.g. agent_activity)
        max_budget_usd: Hard budget cap across the entire graph
        session_id_store: Mutable dict to persist agent session IDs for resume
        max_concurrent_tasks: Maximum DAG nodes to execute simultaneously.
            Defaults to ``cfg.DAG_MAX_CONCURRENT_NODES`` (env: DAG_MAX_CONCURRENT_NODES).
            Set to 1 to force sequential execution; None to use config value.

    Returns:
        ExecutionResult with all outputs, stats, and healing history
    """
    sdk = sdk_client or state.sdk_client
    if sdk is None:
        raise RuntimeError("SDK client not initialized")

    # Resolve concurrency limit — config is the single source of truth
    _concurrency: int = (
        max_concurrent_tasks if max_concurrent_tasks is not None else cfg.DAG_MAX_CONCURRENT_NODES
    )
    _concurrency = max(1, _concurrency)  # always at least 1

    # --- Pre-execution: validate artifact contracts ---
    contract_mismatches = validate_artifact_contracts(graph)
    if contract_mismatches:
        logger.warning(
            f"[DAG] Artifact contract validation found {len(contract_mismatches)} issue(s):"
        )
        for m in contract_mismatches:
            logger.warning(f"[DAG]   - {m}")
        # Raise if any explicit (non-inferred) mismatches exist
        explicit = [m for m in contract_mismatches if "inferred check" not in m]
        if explicit:
            raise ArtifactContractError(explicit)

    ctx = _ExecutionContext(
        graph=graph,
        project_dir=project_dir,
        specialist_prompts=specialist_prompts,
        sdk=sdk,
        max_budget_usd=max_budget_usd,
        session_ids=session_id_store or {},
        on_task_start=on_task_start,
        on_task_done=on_task_done,
        on_remediation=on_remediation,
        on_agent_stream=on_agent_stream,
        on_agent_tool_use=on_agent_tool_use,
        on_event=on_event,
        max_concurrent_tasks=_concurrency,
        commit_approval_callback=commit_approval_callback,
    )

    logger.info(
        f"[DAG] Starting graph execution: project={graph.project_id} "
        f"tasks={len(graph.tasks)} budget=${max_budget_usd} "
        f"max_concurrent_nodes={_concurrency}"
    )

    # Start the watchdog as a background task.
    # It monitors running agents and logs warnings if they exceed their timeout
    # or become idle (no tool calls for too long).
    watchdog_task = asyncio.create_task(
        _watchdog_loop(ctx),
        name="dag-watchdog",
    )

    try:
        return await _execute_graph_inner(ctx, graph, max_budget_usd)
    finally:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
        logger.info("[DAG] Watchdog stopped.")


async def _watchdog_loop(ctx: _ExecutionContext, interval: float = 30.0) -> None:
    """Background watchdog that monitors running agents.

    Every *interval* seconds, checks each running task for:
    - Wall-clock timeout exceeded (logs CRITICAL warning + kills orphan processes)
    - Idle too long (no tool calls for >120s — logs WARNING)

    FIX: Uses per-task pids_before snapshots stored in running_tasks to
    correctly identify only the orphan processes belonging to a specific
    stuck task, instead of killing ALL Claude processes.
    """
    from sdk_client import (
        _async_kill_orphan_claude_processes,
        _async_kill_specific_pids,
        _snapshot_claude_pids,
    )

    IDLE_THRESHOLD = 120  # seconds without a tool call before warning
    IDLE_KILL_THRESHOLD = 300  # seconds without a tool call before force-kill
    # Also detect agents that never started working (0 turns after 60s)
    STARTUP_IDLE_THRESHOLD = 60  # seconds with 0 turns

    while True:
        try:
            await asyncio.sleep(interval)
            now = time.monotonic()

            for task_id, info in list(ctx.running_tasks.items()):
                elapsed = now - info["start_time"]
                idle = now - info["last_activity"]
                timeout = info["timeout"]
                role = info["role"]
                turns = info["turns_used"]

                if elapsed > timeout * 1.5:
                    # Agent has exceeded 150% of its timeout — something is
                    # very wrong.  The asyncio.wait_for should have killed it
                    # already.  This means the subprocess leaked.
                    logger.critical(
                        f"[WATCHDOG] Task {task_id} ({role}) EXCEEDED 150%% timeout! "
                        f"elapsed={elapsed:.0f}s, timeout={timeout}s, turns={turns}. "
                        f"Killing orphan Claude processes."
                    )
                    # Use per-task pids_before snapshot if available.
                    # This ensures we only kill processes spawned FOR this task,
                    # not processes belonging to other concurrent tasks.
                    task_pids_before = info.get("pids_before", None)
                    if task_pids_before is not None:
                        # FIX(C-2): Use async wrapper instead of run_in_executor
                        # to avoid blocking the event loop with time.sleep().
                        killed = await _async_kill_orphan_claude_processes(
                            task_pids_before,
                            1.0,
                        )
                    else:
                        # Fallback: snapshot current PIDs and kill all of them.
                        # This is the nuclear option — only used when we have
                        # no per-task PID tracking (shouldn't happen normally).
                        logger.warning(
                            f"[WATCHDOG] Task {task_id}: no pids_before snapshot! "
                            f"Using nuclear kill (all Claude processes)."
                        )
                        current_pids = await asyncio.to_thread(_snapshot_claude_pids)
                        # FIX(C-2): Use async wrapper instead of run_in_executor
                        killed = await _async_kill_specific_pids(
                            current_pids,
                            1.0,
                        )
                    if killed:
                        logger.critical(
                            f"[WATCHDOG] Killed {killed} orphan Claude process(es) "
                            f"for stuck task {task_id}"
                        )
                    # Remove from running_tasks so we don't keep killing
                    ctx.running_tasks.pop(task_id, None)

                elif elapsed > timeout:
                    logger.warning(
                        f"[WATCHDOG] Task {task_id} ({role}) exceeded timeout: "
                        f"elapsed={elapsed:.0f}s > timeout={timeout}s, turns={turns}. "
                        f"Waiting for asyncio.wait_for to cancel it."
                    )
                elif idle > IDLE_KILL_THRESHOLD and turns > 0:
                    # Agent has been completely idle for 5+ minutes — force kill
                    # it instead of waiting for the full timeout. This prevents
                    # wasting 20+ minutes on stuck agents (observed in benchmarks).
                    logger.warning(
                        f"[WATCHDOG] Task {task_id} ({role}) FORCE KILLING: "
                        f"idle for {idle:.0f}s (kill threshold={IDLE_KILL_THRESHOLD}s), "
                        f"turns={turns}, elapsed={elapsed:.0f}s/{timeout}s. "
                        f"Killing orphan processes to unblock downstream tasks."
                    )
                    task_pids_before = info.get("pids_before", None)
                    if task_pids_before is not None:
                        killed = await _async_kill_orphan_claude_processes(
                            task_pids_before, 1.0,
                        )
                    else:
                        current_pids = await asyncio.to_thread(_snapshot_claude_pids)
                        killed = await _async_kill_specific_pids(
                            current_pids, 1.0,
                        )
                    if killed:
                        logger.warning(
                            f"[WATCHDOG] Killed {killed} process(es) for idle task {task_id}"
                        )
                    ctx.running_tasks.pop(task_id, None)

                elif idle > IDLE_THRESHOLD and turns > 0:
                    logger.warning(
                        f"[WATCHDOG] Task {task_id} ({role}) appears idle: "
                        f"no tool call for {idle:.0f}s (threshold={IDLE_THRESHOLD}s), "
                        f"turns_so_far={turns}, elapsed={elapsed:.0f}s/{timeout}s"
                    )
                elif turns == 0 and elapsed > STARTUP_IDLE_THRESHOLD:
                    # Agent registered but never made a single tool call.
                    # This can happen if the SDK is stuck connecting or the
                    # subprocess failed to start.
                    logger.warning(
                        f"[WATCHDOG] Task {task_id} ({role}) has 0 turns after "
                        f"{elapsed:.0f}s — agent may be stuck at startup. "
                        f"(timeout={timeout}s)"
                    )
        except asyncio.CancelledError:
            raise  # Let cancellation propagate
        except Exception as exc:
            # Watchdog must never crash — log and continue
            logger.error(f"[WATCHDOG] Unexpected error (will retry): {exc}", exc_info=True)


async def _run_with_semaphore(
    task: TaskInput,
    ctx: _ExecutionContext,
) -> TaskOutput:
    """Acquire the graph's bounded worker-pool slot, then run the task.

    Writer tasks additionally acquire file-level locks for their entire
    ``files_scope`` to prevent conflicts across concurrent graph executions.
    Reader tasks bypass file locking entirely.

    Args:
        task: The task to execute.
        ctx:  Execution context (owns the semaphore and all shared state).

    Returns:
        The TaskOutput produced by ``_run_single_task``.
    """
    is_writer = task.role in _WRITER_ROLES
    locked_files: list[str] = []

    async with ctx.node_semaphore:
        logger.debug(
            "[DAG] Semaphore acquired for %s (active_slots=%d/%d)",
            task.id,
            ctx.max_concurrent_tasks - ctx.node_semaphore._value,  # type: ignore[attr-defined]
            ctx.max_concurrent_tasks,
        )

        # Acquire file-level locks for writer tasks
        if is_writer and task.files_scope:
            locked_files = list(task.files_scope)
            acquired = await _file_lock_manager.acquire_files(locked_files)
            if not acquired:
                logger.warning(
                    "[DAG] Task %s: timed out acquiring file locks for %s",
                    task.id,
                    locked_files,
                )
                return TaskOutput(
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    summary="Timed out waiting for file locks",
                    issues=["Could not acquire file-level locks within timeout"],
                    failure_details=(
                        f"File lock timeout ({cfg.FILE_LOCK_TIMEOUT}s) for: {locked_files}"
                    ),
                    confidence=0.0,
                )
            logger.debug(
                "[DAG] File locks acquired for %s: %s (total_active=%d)",
                task.id,
                locked_files,
                _file_lock_manager.active_locks,
            )

        try:
            return await _run_single_task(task, ctx)
        finally:
            if locked_files:
                _file_lock_manager.release_files(locked_files)
                logger.debug(
                    "[DAG] File locks released for %s: %s",
                    task.id,
                    locked_files,
                )


# ---------------------------------------------------------------------------
# Checkpoint & resume helpers
# ---------------------------------------------------------------------------


async def _emit_checkpoint(
    ctx: _ExecutionContext,
    graph: TaskGraph,
    round_num: int,
    *,
    status: str = "running",
) -> None:
    """Persist a checkpoint to SQLite and emit a WS event to the frontend.

    Called after every completed round for crash recovery and real-time
    progress tracking.
    """
    checkpoint = DAGCheckpoint(
        project_id=graph.project_id,
        graph_json=graph.model_dump_json(),
        completed_tasks={tid: out.model_dump() for tid, out in ctx.completed.items()},
        retries=dict(ctx.retries),
        total_cost=ctx.total_cost,
        remediation_count=ctx.remediation_count,
        healing_history=ctx.healing_history,
        round_num=round_num,
        created_at=time.time(),
        status=status,
    )

    # Persist to SQLite (non-blocking)
    try:
        await _async_save_checkpoint(ctx.project_dir, checkpoint)
        logger.debug(
            f"[DAG] Checkpoint saved: round={round_num} status={status} "
            f"completed={len(ctx.completed)}/{len(graph.tasks)}"
        )
    except Exception as exc:
        logger.warning(f"[DAG] Checkpoint persistence failed (non-fatal): {exc}")

    # Emit WS event so the frontend gets real-time checkpoint progress
    if ctx.on_event is not None:
        event = {
            "type": "dag_checkpoint",
            "project_id": graph.project_id,
            "round_num": round_num,
            "status": status,
            "completed_count": sum(1 for o in ctx.completed.values() if o.is_successful()),
            "failed_count": sum(1 for o in ctx.completed.values() if not o.is_successful()),
            "total_tasks": len(graph.tasks),
            "total_cost": ctx.total_cost,
            "remediation_count": ctx.remediation_count,
            "timestamp": time.time(),
        }
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _task = asyncio.ensure_future(ctx.on_event(event))
                _background_event_tasks.add(_task)
                _task.add_done_callback(_background_event_tasks.discard)
        except Exception as ev_err:
            logger.debug(f"[DAG] Checkpoint event emission failed: {ev_err}")


def _force_retry_stuck_tasks(ctx: _ExecutionContext, graph: TaskGraph) -> int:
    """Force-retry failed tasks that haven't exhausted their retry budget.

    Returns the number of tasks re-queued for retry.  This is the last-resort
    mechanism before declaring a deadlock — it removes failed tasks from
    ctx.completed so ready_tasks() picks them up again.

    Only retries tasks whose dependencies are all successfully completed
    (i.e., the task itself failed, not a dependency).
    """
    retried = 0
    for task in graph.tasks:
        if task.id not in ctx.completed:
            continue
        output = ctx.completed[task.id]
        if output.is_successful():
            continue
        if output.is_terminal():
            continue

        # Check if all dependencies are satisfied
        deps_ok = all(
            dep in ctx.completed and ctx.completed[dep].is_successful() for dep in task.depends_on
        )
        if not deps_ok:
            continue

        # Check retry budget (allow one extra retry in deadlock recovery)
        current_retries = ctx.retries.get(task.id, 0)
        max_allowed = MAX_TASK_RETRIES + 1
        if current_retries >= max_allowed:
            continue

        # Force retry
        ctx.retries[task.id] = current_retries + 1
        ctx.total_cost -= output.cost_usd
        del ctx.completed[task.id]
        retried += 1
        logger.info(f"[DAG] Force-retry: {task.id} (attempt {ctx.retries[task.id]}/{max_allowed})")

    return retried


async def restore_graph_from_checkpoint(
    project_dir: str,
    project_id: str,
) -> tuple[TaskGraph | None, dict[str, TaskOutput], dict[str, int], int]:
    """Restore a DAG execution from the latest checkpoint.

    Returns:
        (graph, completed_tasks, retries, round_num) or (None, {}, {}, 0) if no checkpoint.
    """
    checkpoint = _load_latest_checkpoint(project_dir, project_id)
    if checkpoint is None:
        return None, {}, {}, 0

    try:
        graph = TaskGraph.model_validate_json(checkpoint.graph_json)
        completed = {
            tid: TaskOutput.model_validate(data) for tid, data in checkpoint.completed_tasks.items()
        }
        logger.info(
            f"[DAG] Restored checkpoint: project={project_id} "
            f"round={checkpoint.round_num} "
            f"completed={len(completed)}/{len(graph.tasks)} "
            f"status={checkpoint.status}"
        )
        return graph, completed, checkpoint.retries, checkpoint.round_num
    except Exception as exc:
        logger.warning(f"[DAG] Checkpoint restore failed: {exc}")
        return None, {}, {}, 0


async def _execute_graph_inner(
    ctx: _ExecutionContext,
    graph: TaskGraph,
    max_budget_usd: float,
) -> ExecutionResult:
    """Inner graph execution loop (separated for watchdog wrapping).

    Hardened for long-running executions:
    - Checkpoints state to SQLite after every round
    - Emits WS progress events on each checkpoint
    - Does not stop until ALL plan tasks are completed or explicitly failed
    - Adaptive deadlock detection with retry before giving up
    """
    round_num = 0
    _deadlock_retries = 0  # Track consecutive deadlock detections
    _MAX_DEADLOCK_RETRIES = 3  # Allow some deadlock retries before giving up
    _graph_start_time = time.monotonic()  # For DAG_PROGRESS ETA computation

    while not graph.is_complete(ctx.completed):
        round_num += 1
        if round_num > MAX_ROUNDS:
            _pending = [t.id for t in graph.tasks if t.id not in ctx.completed]
            logger.error(
                f"[DAG] Safety limit: exceeded {MAX_ROUNDS} rounds.\n"
                f"  Completed: {list(ctx.completed.keys())}\n"
                f"  Still pending: {_pending}\n"
                f"  Total cost: ${ctx.total_cost:.4f}\n"
                f"  Remediations: {ctx.remediation_count}"
            )
            # Final checkpoint with interrupted status
            await _emit_checkpoint(ctx, graph, round_num, status="max_rounds_exceeded")
            break

        completed_ids = list(ctx.completed.keys())
        pending_ids = [t.id for t in graph.tasks if t.id not in ctx.completed]
        logger.info(
            f"[DAG] === Round {round_num} === "
            f"completed={len(completed_ids)}/{len(graph.tasks)} "
            f"pending={pending_ids} "
            f"cost_so_far=${ctx.total_cost:.4f}/{max_budget_usd:.2f} "
            f"remediations={ctx.remediation_count}/{MAX_TOTAL_REMEDIATIONS}"
        )

        ready = graph.ready_tasks(ctx.completed)

        if not ready:
            if graph.has_failed(ctx.completed):
                # Try self-healing before giving up
                healed = await _try_self_heal(ctx)
                if healed:
                    _deadlock_retries = 0  # Reset — we made progress
                    continue  # New tasks were added, re-check ready

                # Before giving up, check if there are tasks with NO failed
                # dependencies that just haven't been retried yet.
                _deadlock_retries += 1
                if _deadlock_retries <= _MAX_DEADLOCK_RETRIES:
                    # Force-retry failed tasks that haven't hit their max retries
                    _force_retried = _force_retry_stuck_tasks(ctx, graph)
                    if _force_retried:
                        logger.info(
                            f"[DAG] Deadlock retry #{_deadlock_retries}: "
                            f"force-retried {_force_retried} stuck tasks"
                        )
                        continue

                _failed_tasks = [
                    f"{tid}={out.status.value}({out.failure_details[:60] if out.failure_details else 'no details'})"
                    for tid, out in ctx.completed.items()
                    if not out.is_successful()
                ]
                logger.error(
                    f"[DAG] Graph has unresolvable failures after {_deadlock_retries} "
                    f"deadlock retries and healing attempts. Stopping.\n"
                    f"  Failed tasks: {_failed_tasks}\n"
                    f"  Completed: {len(ctx.completed)}/{len(graph.tasks)}\n"
                    f"  Remediation attempts: {ctx.remediation_count}"
                )
                await _emit_checkpoint(ctx, graph, round_num, status="failed")
                break
            _all_statuses = [
                f"{t.id}={'done' if t.id in ctx.completed else 'pending'}(deps={t.depends_on})"
                for t in graph.tasks
            ]
            _deadlock_retries += 1
            if _deadlock_retries <= _MAX_DEADLOCK_RETRIES:
                logger.warning(
                    f"[DAG] No ready tasks but graph not complete (retry #{_deadlock_retries}). "
                    f"Attempting to unblock..."
                )
                _force_retried = _force_retry_stuck_tasks(ctx, graph)
                if _force_retried:
                    continue
            logger.warning(
                f"[DAG] No ready tasks but graph not complete. Deadlock after "
                f"{_deadlock_retries} retries.\n"
                f"  Task statuses: {_all_statuses}\n"
                f"  Completed IDs: {list(ctx.completed.keys())}"
            )
            await _emit_checkpoint(ctx, graph, round_num, status="deadlocked")
            break
        else:
            _deadlock_retries = 0  # Reset on successful round

        if ctx.total_cost >= max_budget_usd:
            _pending = [t.id for t in graph.tasks if t.id not in ctx.completed]
            logger.error(
                f"[DAG] Budget exhausted (${ctx.total_cost:.2f} >= ${max_budget_usd})\n"
                f"  Completed: {len(ctx.completed)}/{len(graph.tasks)}\n"
                f"  Still pending: {_pending}\n"
                f"  Cost breakdown: {[(tid, f'${out.cost_usd:.4f}') for tid, out in ctx.completed.items()]}"
            )
            await _emit_checkpoint(ctx, graph, round_num, status="budget_exhausted")
            break

        ready_info = [(t.id, t.role.value) for t in ready]
        logger.info(f"[DAG] Round {round_num}: {len(ready)} ready tasks: {ready_info}")

        # Split into parallel batches
        batches = _plan_batches(ready)
        for bi, batch in enumerate(batches):
            batch_info = [(t.id, t.role.value) for t in batch]
            logger.info(f"[DAG] Round {round_num} batch {bi + 1}/{len(batches)}: {batch_info}")

        for batch in batches:
            # asyncio.wait() (not gather) — subtasks survive individual failures.
            # Bounded by _run_with_semaphore (max_concurrent_tasks).
            subtasks = [
                asyncio.create_task(
                    _run_with_semaphore(task, ctx),
                    name=f"dag-{task.id}",
                )
                for task in batch
            ]
            logger.debug(
                "[DAG] Round %d batch: launched %d tasks (semaphore_limit=%d, queue_depth=%d)",
                round_num,
                len(subtasks),
                ctx.max_concurrent_tasks,
                max(0, len(subtasks) - ctx.max_concurrent_tasks),
            )

            cancel_hits = 0
            while not all(t.done() for t in subtasks):
                try:
                    await asyncio.wait(subtasks, return_when=asyncio.ALL_COMPLETED)
                    break
                except asyncio.CancelledError:
                    cancel_hits += 1
                    _drain_cancellations("(batch wait)")
                    still_running = sum(1 for t in subtasks if not t.done())
                    if cancel_hits <= 3 or cancel_hits % 50 == 0:
                        logger.warning(
                            f"[DAG] asyncio.wait CancelledError #{cancel_hits} "
                            f"— {still_running}/{len(subtasks)} still running, retrying"
                        )

            if cancel_hits:
                logger.info(
                    f"[DAG] Batch finished despite {cancel_hits} CancelledError interruptions"
                )

            # Drain any residual cancellations after all subtasks finished
            _drain_cancellations("(post-batch)")

            # Collect results from completed subtasks
            raw_results = []
            for subtask in subtasks:
                if subtask.cancelled():
                    raw_results.append(asyncio.CancelledError())
                elif subtask.exception() is not None:
                    raw_results.append(subtask.exception())
                else:
                    raw_results.append(subtask.result())

            # Convert exceptions to FAILED TaskOutputs.
            # Spurious CancelledErrors become node failures, not DAG cancellation.
            results: list[TaskOutput] = []
            _cancelled_count = sum(1 for r in raw_results if isinstance(r, asyncio.CancelledError))
            _all_cancelled = _cancelled_count == len(raw_results)

            for task_item, raw in zip(batch, raw_results, strict=False):
                if isinstance(raw, asyncio.CancelledError):
                    # Treat as spurious anyio leak — convert to task failure.
                    if _all_cancelled:
                        logger.warning(
                            f"[DAG] Task {task_item.id} got CancelledError (all tasks in batch cancelled "
                            f"— treating as spurious anyio leak, NOT propagating)"
                        )
                        error_output = TaskOutput(
                            task_id=task_item.id,
                            status=TaskStatus.FAILED,
                            summary="Task cancelled (spurious CancelledError — all-batch anyio leak)",
                            issues=[
                                "CancelledError during execution — all tasks in batch hit by anyio cancel-scope leak"
                            ],
                            failure_details="asyncio.CancelledError (spurious all-batch — anyio cancel-scope leak)",
                            confidence=0.0,
                        )
                        error_output.failure_category = classify_failure(error_output)
                        results.append(error_output)
                        continue
                    else:
                        # Only SOME tasks cancelled — likely spurious anyio leak.
                        # Treat as a task failure so the DAG can continue.
                        logger.warning(
                            f"[DAG] Task {task_item.id} got CancelledError but other tasks in batch succeeded. "
                            f"Treating as task failure (likely spurious anyio cancel-scope leak)."
                        )
                        error_output = TaskOutput(
                            task_id=task_item.id,
                            status=TaskStatus.FAILED,
                            summary="Task cancelled (spurious CancelledError — anyio bug)",
                            issues=[
                                "CancelledError during execution — likely anyio cancel-scope leak"
                            ],
                            failure_details="asyncio.CancelledError (spurious — not user-initiated)",
                            confidence=0.0,
                        )
                        error_output.failure_category = classify_failure(error_output)
                        results.append(error_output)
                        continue
                elif isinstance(raw, BaseException):
                    logger.error(f"[DAG] Task {task_item.id} raised exception: {raw}", exc_info=raw)
                    error_output = TaskOutput(
                        task_id=task_item.id,
                        status=TaskStatus.FAILED,
                        summary=f"Agent threw exception: {type(raw).__name__}: {str(raw)[:200]}",
                        issues=[str(raw)[:300]],
                        failure_details=str(raw)[:500],
                        confidence=0.0,
                    )
                    error_output.failure_category = classify_failure(error_output)
                    results.append(error_output)
                else:
                    results.append(raw)

            for task, output in zip(batch, results, strict=False):
                ctx.completed[task.id] = output
                ctx.total_cost += output.cost_usd

                # Successful remediation unblocks downstream tasks that
                # depend on the original failed task.
                if output.is_successful() and task.is_remediation and task.original_task_id:
                    ctx.completed[task.original_task_id] = output
                    logger.info(
                        f"[DAG] Remediation {task.id} succeeded — "
                        f"unblocking dependents of {task.original_task_id}"
                    )

                logger.info(
                    f"[DAG] Task {task.id} ({task.role.value}) -> "
                    f"{output.status.value} (${output.cost_usd:.4f}, "
                    f"confidence={output.confidence:.2f})"
                )

                if ctx.on_task_done:
                    try:
                        # Add progress info to output for frontend
                        total_tasks = len(ctx.graph.tasks)
                        done_tasks = sum(1 for t in ctx.graph.tasks if t.id in ctx.completed)
                        output.progress = f"{done_tasks}/{total_tasks}"
                        await ctx.on_task_done(task, output)
                    except Exception as exc:
                        logger.warning(f"[DAG] on_task_done callback failed: {exc}")

                # Emit DAG_PROGRESS aggregate event
                _done_count = sum(1 for t in ctx.graph.tasks if t.id in ctx.completed)
                _emit_dag_progress(
                    ctx.on_event,
                    graph.project_id,
                    _done_count,
                    len(graph.tasks),
                    _graph_start_time,
                )

                # Handle failures
                if not output.is_successful():
                    await _handle_failure(task, output, ctx)

                # Validate required artifacts
                if output.is_successful() and task.required_artifacts:
                    _check_required_artifact_types(task, output)

                # Per-task commit: one focused commit per completed task
                if output.is_successful():
                    # ── Approval Gate ──────────────────────────────────────
                    # If a commit_approval_callback is configured (e.g. in
                    # interactive mode), ask the user before committing.
                    _commit_approved = True
                    if ctx.commit_approval_callback:
                        try:
                            _approval_desc = (
                                f"Task **{task.id}** ({task.role.value}) completed.\n"
                                f"Summary: {output.summary[:200]}\n"
                                f"Files: {', '.join(output.artifacts[:5]) if output.artifacts else 'none'}\n\n"
                                f"Commit these changes?"
                            )
                            _commit_approved = await ctx.commit_approval_callback(_approval_desc)
                            if not _commit_approved:
                                logger.info(f"[DAG] Task {task.id} commit rejected by user")
                        except Exception as approval_exc:
                            logger.warning(
                                f"[DAG] Approval callback failed (auto-approving): {approval_exc}"
                            )
                            _commit_approved = True

                    if _commit_approved:
                        try:
                            committed = await commit_single_task(ctx.project_dir, output)
                            if committed:
                                logger.info(f"[DAG] Task {task.id} committed: {committed}")
                        except Exception as exc:
                            logger.warning(f"[DAG] Per-task commit failed (non-fatal): {exc}")

        # Fallback: commit anything remaining that wasn't caught by per-task commits
        _round_commit_approved = True
        if ctx.commit_approval_callback:
            try:
                _round_desc = (
                    f"Round {round_num} completed. Commit remaining changes?\n"
                    f"Tasks: {', '.join(t.id for t in ready if t.id in ctx.completed)}"
                )
                _round_commit_approved = await ctx.commit_approval_callback(_round_desc)
                if not _round_commit_approved:
                    logger.info(f"[DAG] Round {round_num} fallback commit rejected by user")
            except Exception as approval_exc:
                logger.warning(
                    f"[DAG] Round approval callback failed (auto-approving): {approval_exc}"
                )
                _round_commit_approved = True

        if _round_commit_approved:
            try:
                round_outputs = [ctx.completed[t.id] for t in ready if t.id in ctx.completed]
                committed = await executor_commit(ctx.project_dir, round_outputs, round_num)
                if committed:
                    logger.info(f"[DAG] Round {round_num} fallback commit: {committed}")
            except Exception as exc:
                logger.warning(f"[DAG] Auto-commit failed (non-fatal): {exc}")

        # ── Checkpoint: persist state after each round for crash recovery ──
        await _emit_checkpoint(ctx, graph, round_num, status="running")

    # ── Final checkpoint: mark execution as completed or failed ──
    _final_status = "completed" if graph.is_complete(ctx.completed) else "interrupted"
    await _emit_checkpoint(ctx, graph, round_num, status=_final_status)
    if _final_status == "completed":
        # Clean up old checkpoints on successful completion
        _clear_checkpoints(ctx.project_dir, graph.project_id)

    return _build_result(ctx, graph)


# ---------------------------------------------------------------------------
# Execution context (mutable state for a single graph execution)
# ---------------------------------------------------------------------------


class _ExecutionContext:
    """Mutable state for a single graph execution."""

    def __init__(
        self,
        graph: TaskGraph,
        project_dir: str,
        specialist_prompts: dict[str, str],
        sdk: Any,
        max_budget_usd: float,
        session_ids: dict[str, str],
        on_task_start: Callable | None = None,
        on_task_done: Callable | None = None,
        on_remediation: Callable | None = None,
        on_agent_stream: Callable | None = None,
        on_agent_tool_use: Callable | None = None,
        on_event: Callable | None = None,
        max_concurrent_tasks: int = 4,
        commit_approval_callback: Callable[[str], Awaitable[bool]] | None = None,
    ):
        self.commit_approval_callback = commit_approval_callback
        self.graph = graph
        self.project_dir = project_dir
        self.specialist_prompts = specialist_prompts
        self.sdk = sdk
        self.max_budget_usd = max_budget_usd
        self.session_ids = session_ids
        self.on_task_start = on_task_start
        self.on_task_done = on_task_done
        self.on_remediation = on_remediation
        self.on_agent_stream = on_agent_stream
        self.on_agent_tool_use = on_agent_tool_use

        self.completed: dict[str, TaskOutput] = {}
        self.retries: dict[str, int] = {}
        self.total_cost: float = 0.0
        self.remediation_count: int = 0
        self.healing_history: list[dict[str, str]] = []
        self.task_counter: int = len(graph.tasks)
        self.graph_lock: asyncio.Lock = asyncio.Lock()

        # ── Bounded worker-pool — limits concurrent DAG node executions ───────
        # At most max_concurrent_tasks agent tasks execute simultaneously within
        # this graph run.  This prevents resource exhaustion (memory, subprocesses,
        # API rate limits) when a round has many independent ready tasks.
        self.max_concurrent_tasks: int = max(1, max_concurrent_tasks)
        self.node_semaphore: asyncio.Semaphore = asyncio.Semaphore(self.max_concurrent_tasks)

        # Running task tracking for watchdog and observability.
        # Maps task_id -> {role, start_time, timeout, last_activity}
        self.running_tasks: dict[str, dict] = {}

        # on_event callback for forwarding events to the orchestrator / frontend
        self.on_event: Callable | None = on_event

        # ── Hivemind improvements ──
        self.artifact_registry: ArtifactRegistry = ArtifactRegistry(project_dir)
        self.dynamic_spawner: DynamicSpawner = DynamicSpawner()
        self.structured_notes: StructuredNotes = StructuredNotes(project_dir)
        self.structured_notes.init_session(graph.vision)
        self.model_overrides: dict[str, str] = {}  # task_id -> model override

        # ── Blackboard: enhanced cross-agent context layer ──
        from blackboard import Blackboard

        self.blackboard: Blackboard = Blackboard(self.structured_notes)


# ---------------------------------------------------------------------------
# Execution Result
# ---------------------------------------------------------------------------


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
        """Return True if every task in the result set succeeded."""
        return self.failure_count == 0

    def summary_text(self) -> str:
        """Return a human-readable summary of all task results."""
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


def _build_result(ctx: _ExecutionContext, graph: TaskGraph) -> ExecutionResult:
    all_outputs = list(ctx.completed.values())
    # High-level run summary
    completed_count = sum(1 for o in all_outputs if o.status.value == "completed")
    failed_count = sum(1 for o in all_outputs if o.status.value == "failed")
    roles_used = list(
        {graph.get_task(tid).role.value for tid in ctx.completed if graph.get_task(tid)}
    )
    total_input_tokens = sum(o.input_tokens for o in all_outputs)
    total_output_tokens = sum(o.output_tokens for o in all_outputs)
    total_tokens = sum(o.total_tokens for o in all_outputs)
    logger.info(
        f"[DAG] ══════ RUN COMPLETE ══════ "
        f"{completed_count}✅ {failed_count}❌ / {len(graph.tasks)} tasks | "
        f"cost=${ctx.total_cost:.4f} | tokens={total_tokens} | "
        f"roles={roles_used}"
    )
    # Save artifact manifest for downstream consumers
    try:
        ctx.artifact_registry.save_manifest()
    except Exception as exc:
        logger.warning(f"[DAG] Failed to save artifact manifest (non-fatal): {exc}")

    return ExecutionResult(
        outputs=all_outputs,
        total_cost=sum(o.cost_usd for o in all_outputs),
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_tokens=total_tokens,
        success_count=sum(1 for o in all_outputs if o.is_successful()),
        failure_count=sum(1 for o in all_outputs if not o.is_successful()),
        remediation_count=ctx.remediation_count,
        healing_history=ctx.healing_history,
    )


# ---------------------------------------------------------------------------
# Single task execution
# ---------------------------------------------------------------------------

# Number of turns reserved for the mandatory summary phase.
_SUMMARY_PHASE_TURNS = 5
# Minimum turns in work phase before we bother with a summary phase.
_MIN_WORK_TURNS_FOR_SUMMARY = 3


async def _run_single_task(
    task: TaskInput,
    ctx: _ExecutionContext,
) -> TaskOutput:
    """Run one task using Two-Phase Architecture.

    Phase 1 — WORK: Agent executes the task with full tools.
        max_turns = role_limit - _SUMMARY_PHASE_TURNS (reserves turns for summary).
    Phase 2 — SUMMARY: Agent produces ONLY the JSON output block.
        Resumes the same session, tools disabled, max_turns = _SUMMARY_PHASE_TURNS.
        Runs ALWAYS after work phase (not just as fallback).

    This guarantees the agent always gets a chance to produce JSON, even if
    it used all work turns. The summary phase costs ~$0.01-0.05.

    Fallback: If both phases fail to produce JSON, multi-signal work detection
    infers the result from the agent's text output.
    """
    role_name = task.role.value
    max_turns = _get_max_turns(role_name)
    task_timeout = _get_task_timeout(role_name, task=task)

    logger.info(
        f"[DAG] ▶ START {task.id} ({role_name}): {task.goal[:80]}{'...' if len(task.goal) > 80 else ''}"
    )
    logger.info(
        f"[DAG] _run_single_task START: {task.id} ({role_name}) "
        f"goal='{task.goal[:100]}' "
        f"max_turns={max_turns}, timeout={task_timeout}s, "
        f"context_from={task.context_from or 'none'} "
        f"depends_on={task.depends_on or 'none'} "
        f"retry_count={ctx.retries.get(task.id, 0)}"
    )

    if ctx.on_task_start:
        try:
            await ctx.on_task_start(task)
        except Exception as exc:
            logger.warning("[DAG] on_task_start callback failed for %s: %s", task.id, exc)

    # Milestone 1: preparing — context gathering begins
    _task_start_mono = time.monotonic()
    _est_total = float(task_timeout)
    _task_goal = task.goal or ""
    _emit_task_progress(
        ctx.on_event,
        ctx.graph.project_id,
        task.id,
        "preparing",
        _task_start_mono,
        _est_total,
        task_name=_task_goal,
    )

    # Gather context from upstream tasks (now with structured artifacts)
    context_outputs = {tid: ctx.completed[tid] for tid in task.context_from if tid in ctx.completed}

    # Build the prompt using the typed contract serialiser
    prompt = task_input_to_prompt(
        task,
        context_outputs,
        graph_vision=ctx.graph.vision,
        graph_epics=ctx.graph.epic_breakdown,
    )

    # Get specialist system prompt
    system_prompt = ctx.specialist_prompts.get(
        role_name,
        ctx.specialist_prompts.get("backend_developer", "You are an expert software engineer."),
    )

    # ── PROJECT BOUNDARY INJECTION ──────────────────────────────────────
    # Ensure every DAG agent receives the project boundary header,
    # preventing file leakage between projects.
    from project_context import build_project_header

    _project_boundary = build_project_header(ctx.graph.project_id, ctx.project_dir)
    if _project_boundary and _project_boundary not in system_prompt:
        system_prompt = _project_boundary + "\n\n" + system_prompt
    # ────────────────────────────────────────────────────────────────────

    # Inject relevant skills
    try:
        skill_names = select_skills_for_task(role_name, task.goal)
        if skill_names:
            system_prompt = system_prompt + build_skill_prompt(skill_names)
    except Exception as exc:
        logger.warning(f"[DAG] Task {task.id}: skill injection failed (non-fatal): {exc}")

    # ── Inject file artifact context (JIT Context) ──
    try:
        enhanced_prompt = ctx.artifact_registry.enhance_prompt(task, prompt)
        if enhanced_prompt != prompt:
            logger.info(
                f"[DAG] Task {task.id}: injected artifact context ({len(enhanced_prompt) - len(prompt)} chars)"
            )
            prompt = enhanced_prompt
    except Exception as exc:
        logger.warning(
            f"[DAG] Task {task.id}: artifact context injection failed (non-fatal): {exc}"
        )

    # ── Inject Blackboard context (enhanced shared knowledge base) ──
    try:
        bb_ctx = ctx.blackboard.build_smart_context(
            role=role_name,
            task_goal=task.goal,
            context_from=task.context_from,
        )
        if bb_ctx:
            prompt += f"\n\n{bb_ctx}"
            logger.info(f"[DAG] Task {task.id}: injected Blackboard context ({len(bb_ctx)} chars)")
    except Exception as exc:
        # Fallback to basic notes if Blackboard fails
        logger.warning(
            f"[DAG] Task {task.id}: Blackboard context failed ({exc}), falling back to basic notes"
        )
        try:
            notes_ctx = ctx.structured_notes.build_notes_context(
                role=role_name, task_goal=task.goal
            )
            if notes_ctx:
                prompt += f"\n\n{notes_ctx}"
        except Exception:
            pass  # Both layers failed — proceed without shared context

    # Resume session if available
    session_key = f"{ctx.graph.project_id}:{role_name}:{task.id}"
    session_id = ctx.session_ids.get(session_key)

    _sys_prompt_tokens = max(1, len(system_prompt) // 4)  # ~4 chars/token heuristic
    logger.info(
        f"[DAG] Task {task.id}: calling SDK "
        f"max_turns={max_turns}, timeout={task_timeout}s, max_budget=${_get_task_budget(role_name)}, "
        f"session={'resume' if session_id else 'new'}, "
        f"prompt_len={len(prompt)}, system_prompt_len={len(system_prompt)} (~{_sys_prompt_tokens:,} tokens)"
    )

    # ── Agent output file logging for real-time visibility ──
    # Each task writes its streaming output and tool calls to a log file.
    # This allows debugging stuck agents and provides an audit trail.
    log_dir = Path(ctx.project_dir) / ".hivemind" / "agent_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    agent_log_file = log_dir / f"{task.id}_{role_name}.log"
    _log_start_time = time.monotonic()

    def _write_agent_log(entry: str) -> None:
        """Append a timestamped entry to the agent's log file."""
        try:
            elapsed = time.monotonic() - _log_start_time
            with open(agent_log_file, "a", encoding="utf-8") as f:
                f.write(f"[{elapsed:7.1f}s] {entry}\n")
        except OSError as _log_err:
            logger.debug(
                "[DAG] _write_agent_log failed (non-critical, disk/permissions?): %s", _log_err
            )

    _write_agent_log(f"=== Task {task.id} ({role_name}) started ===")
    _write_agent_log(f"Goal: {task.goal[:200]}")
    _write_agent_log(f"Max turns: {max_turns}, Timeout: {task_timeout}s")
    _write_agent_log("---")

    # Snapshot Claude PIDs BEFORE this task starts — used by watchdog
    # to identify orphan processes belonging to THIS specific task.
    from sdk_client import _snapshot_claude_pids

    task_pids_before = _snapshot_claude_pids()

    # Register this task for watchdog monitoring
    ctx.running_tasks[task.id] = {
        "role": role_name,
        "start_time": time.monotonic(),
        "timeout": task_timeout,
        "last_activity": time.monotonic(),
        "turns_used": 0,
        "pids_before": task_pids_before,  # For targeted orphan killing
    }

    # ── Build streaming callbacks scoped to this task ──
    _on_stream = None
    _on_tool_use = None
    if ctx.on_agent_stream:

        async def _on_stream(text):
            try:
                _write_agent_log(f"STREAM: {text[:500]}")
                await ctx.on_agent_stream(role_name, text, task.id)
            except Exception as exc:
                logger.debug("[DAG] on_agent_stream callback error (task=%s): %s", task.id, exc)
    else:
        # Even without a stream callback, still log to file
        async def _on_stream(text):
            _write_agent_log(f"STREAM: {text[:500]}")

    if ctx.on_agent_tool_use:

        async def _on_tool_use(tool_name, tool_info="", tool_input=None):
            try:
                _write_agent_log(f"TOOL: {tool_name} | {tool_info[:200]}")
                # Update watchdog activity tracker
                if task.id in ctx.running_tasks:
                    ctx.running_tasks[task.id]["last_activity"] = time.monotonic()
                    ctx.running_tasks[task.id]["turns_used"] += 1
                # Milestone 3: writing_files — detected file mutation tool
                if tool_name in ("Write", "Edit", "NotebookEdit", "write_file", "edit_file"):
                    _emit_task_progress(
                        ctx.on_event,
                        ctx.graph.project_id,
                        task.id,
                        "writing_files",
                        _task_start_mono,
                        _est_total,
                        task_name=_task_goal,
                    )
                await ctx.on_agent_tool_use(role_name, tool_name, tool_info, task.id)
            except Exception as exc:
                logger.debug("[DAG] on_agent_tool_use callback error (task=%s): %s", task.id, exc)
    else:

        async def _on_tool_use(tool_name, tool_info="", tool_input=None):
            _write_agent_log(f"TOOL: {tool_name} | {tool_info[:200]}")
            if task.id in ctx.running_tasks:
                ctx.running_tasks[task.id]["last_activity"] = time.monotonic()
                ctx.running_tasks[task.id]["turns_used"] += 1
            if tool_name in ("Write", "Edit", "NotebookEdit", "write_file", "edit_file"):
                _emit_task_progress(
                    ctx.on_event,
                    ctx.graph.project_id,
                    task.id,
                    "writing_files",
                    _task_start_mono,
                    _est_total,
                )

    # ── Two-Phase Execution ──
    # Phase 1: WORK — agent does the actual task with full tools.
    # We reserve _SUMMARY_PHASE_TURNS turns for the mandatory summary phase.
    work_turns = max(max_turns - _SUMMARY_PHASE_TURNS, max_turns // 2)
    # Time budget: leave 90 seconds for the summary phase
    work_timeout = max(task_timeout - 90, task_timeout // 2)

    # Inject turn budget into the prompt so the agent can make smarter
    # prioritisation decisions (e.g. stop starting new files when close to limit).
    prompt += (
        f"\n\n⚠️ TURN BUDGET (Phase 1): You have {work_turns} turns for this work phase. "
        f"Every tool call (read_file, write_file, bash, grep …) consumes 1 turn. "
        f"When you have ~10 turns remaining, stop starting new work and focus on "
        f"finishing what you already began. "
        f"A mandatory Phase 2 will immediately follow — it gives you "
        f"{_SUMMARY_PHASE_TURNS} tool-free turns to produce your JSON summary, "
        f"so you do NOT need to squeeze the JSON into your last work turn."
    )

    logger.info(
        f"[DAG] Task {task.id}: PHASE 1 (WORK) — "
        f"max_turns={work_turns}/{max_turns}, timeout={work_timeout}s/{task_timeout}s"
    )

    # ── Diagnostic: snapshot git status BEFORE agent runs ──
    pre_git_status = await _git_status(ctx, task.id, "PRE-RUN")
    if pre_git_status:
        logger.info(f"[DAG] Task {task.id}: PRE-RUN git status: {pre_git_status[:300]}")

    # No asyncio.wait_for() here — isolated_query() handles its own timeouts.
    # Double-wrapping causes a race condition that leaks subprocesses.

    t0 = time.monotonic()
    # Milestone 2: agent_working — SDK call begins
    _emit_task_progress(
        ctx.on_event,
        ctx.graph.project_id,
        task.id,
        "agent_working",
        _task_start_mono,
        _est_total,
        task_name=_task_goal,
    )
    try:
        # BUG-23: isolated_query (separate thread) prevents anyio leak propagation.
        from config import get_agent_timeout as _get_role_timeout

        _role_timeout = _get_role_timeout(role_name)
        # Check for model override from Dynamic Spawner
        _model_override = ctx.model_overrides.get(task.id)
        if _model_override:
            logger.info(f"[DAG] Task {task.id}: using model override: {_model_override}")

        response = await isolated_query(
            ctx.sdk,
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=ctx.project_dir,
            session_id=session_id,
            max_turns=work_turns,
            max_budget_usd=_get_task_budget(role_name),
            on_stream=_on_stream,
            on_tool_use=_on_tool_use,
            per_message_timeout=_role_timeout,
            model=_model_override,
        )
        # Check if the SDK itself timed out (returned error response)
        if response.is_error and "timeout" in response.error_message.lower():
            elapsed = time.monotonic() - t0
            logger.warning(
                f"[DAG] Task {task.id}: WORK phase SDK timeout after {elapsed:.0f}s. "
                f"Will attempt summary phase to recover JSON."
            )
            # Treat SDK timeout like our old timeout — try summary phase
            # but keep the response for session_id extraction
    except TimeoutError:
        # Safety net: isolated_query should never raise TimeoutError directly
        # (it returns SDKResponse with is_error=True instead), but handle just in case
        elapsed = time.monotonic() - t0
        logger.warning(
            f"[DAG] Task {task.id}: Unexpected TimeoutError after {elapsed:.0f}s. "
            f"Will attempt summary phase to recover JSON."
        )
        # Work phase timed out — we still try the summary phase below
        response = None
    except asyncio.CancelledError:
        elapsed = time.monotonic() - t0
        _ = asyncio.current_task()  # acknowledged
        logger.warning(
            f"[DAG] Task {task.id} ({role_name}) got CancelledError after {elapsed:.1f}s — "
            f"draining and treating as work-phase failure."
        )
        _drain_cancellations(f"(work phase for {task.id})")
        response = None
    except CircuitOpenError as exc:
        logger.error(f"[DAG] Task {task.id} rejected by circuit breaker: {exc}", exc_info=True)
        ctx.running_tasks.pop(task.id, None)  # Cleanup before early return
        output = TaskOutput(
            task_id=task.id,
            status=TaskStatus.FAILED,
            summary=f"Circuit breaker open — SDK backend is failing ({exc.failures} consecutive failures)",
            issues=["Circuit breaker is open — SDK backend is unresponsive"],
            failure_details=str(exc),
            confidence=0.0,
        )
        output.failure_category = FailureCategory.EXTERNAL
        return output

    work_elapsed = time.monotonic() - t0

    # Process work phase response
    work_session_id: str | None = None
    work_cost = 0.0
    work_input_tokens = 0
    work_output_tokens = 0
    work_total_tokens = 0
    work_turns_used = 0
    work_text = ""
    work_had_error = False

    if response is not None:
        work_session_id = response.session_id or None
        work_cost = response.cost_usd
        work_input_tokens = response.input_tokens
        work_output_tokens = response.output_tokens
        work_total_tokens = response.total_tokens
        work_turns_used = response.num_turns
        work_text = response.text
        work_had_error = response.is_error

        # Persist session ID
        if response.session_id:
            ctx.session_ids[session_key] = response.session_id

        logger.info(
            f"[DAG] Task {task.id}: WORK phase done in {work_elapsed:.1f}s — "
            f"is_error={response.is_error} turns={work_turns_used}/{work_turns} "
            f"cost=${work_cost:.4f} text_len={len(work_text)}"
        )

        if response.is_error:
            logger.warning(
                f"[DAG] Task {task.id}: WORK phase error: {response.error_message[:200]}"
            )
            work_had_error = True
    else:
        # Timeout case — try to get session_id from stored sessions
        work_session_id = ctx.session_ids.get(session_key)
        logger.info(
            f"[DAG] Task {task.id}: WORK phase timed out, "
            f"session_id={'yes' if work_session_id else 'no'}"
        )

    # ── Diagnostic: snapshot git status AFTER agent runs ──
    post_git_status = await _git_status(ctx, task.id, "POST-RUN")
    if post_git_status != pre_git_status:
        logger.info(f"[DAG] Task {task.id}: POST-RUN git status CHANGED: {post_git_status[:500]}")
    elif not post_git_status:
        logger.warning(
            f"[DAG] Task {task.id} ({role_name}): POST-RUN git status EMPTY — "
            f"agent made NO file changes on disk"
        )

    # ── Try to parse JSON from work phase output ──
    # FIX: Even if work_had_error (e.g. timeout), the agent may have
    # produced useful text before the timeout. Try to extract JSON from
    # whatever text we got.
    output: TaskOutput | None = None
    if work_text:
        work_tool_uses = response.tool_uses if response is not None else None
        output = extract_task_output(work_text, task.id, task.role.value, tool_uses=work_tool_uses)
        output.cost_usd = work_cost
        output.input_tokens = work_input_tokens
        output.output_tokens = work_output_tokens
        output.total_tokens = work_total_tokens
        output.turns_used = work_turns_used
        logger.info(
            f"[DAG] Task {task.id}: WORK phase extract -> "
            f"status={output.status.value} confidence={output.confidence:.2f} "
            f"(work_had_error={work_had_error})"
        )

    # ── Phase 2: SUMMARY — mandatory JSON extraction ──
    # Run summary phase if:
    # 1. No JSON was found in work phase output, OR
    # 2. JSON was found but with low confidence (multi-signal inferred)
    # 3. Agent did meaningful work (turns >= _MIN_WORK_TURNS_FOR_SUMMARY)
    # FIX: Also run summary when work_had_error (timeout) — the agent may
    # have done significant work before timeout but the turn counter wasn't
    # fully updated. The summary phase is cheap (~$0.01-0.05) and can
    # recover the agent's work.
    # Check if required artifacts are present in output
    _missing_required = set()
    if task.required_artifacts and output and output.structured_artifacts:
        _produced_types = {a.type for a in output.structured_artifacts}
        _missing_required = set(task.required_artifacts) - _produced_types
    elif task.required_artifacts and (output is None or not output.structured_artifacts):
        _missing_required = set(task.required_artifacts)

    needs_summary = (
        work_session_id
        and (
            work_turns_used >= _MIN_WORK_TURNS_FOR_SUMMARY
            or work_had_error  # Timeout or other error — always try summary
        )
        and (
            output is None
            or not output.is_successful()
            or output.confidence <= 0.90  # Run summary unless very high-confidence JSON
            or bool(_missing_required)  # Always run if required artifacts are missing
        )
    )

    if needs_summary:
        # Milestone 4: summarising — entering summary phase
        _emit_task_progress(
            ctx.on_event,
            ctx.graph.project_id,
            task.id,
            "summarising",
            _task_start_mono,
            _est_total,
            task_name=_task_goal,
        )
        logger.info(
            f"[DAG] Task {task.id}: PHASE 2 (SUMMARY) — "
            f"resuming session, tools disabled, max_turns={_SUMMARY_PHASE_TURNS}"
        )

        summary_output = await _run_summary_phase(
            task=task,
            ctx=ctx,
            session_id=work_session_id,
            system_prompt=system_prompt,
            work_cost=work_cost,
            work_turns=work_turns_used,
            role_name=role_name,
            work_tool_uses=response.tool_uses if response is not None else None,
        )

        if summary_output is not None:
            # Summary phase produced valid JSON — use it
            if output is None or summary_output.confidence > output.confidence:
                output = summary_output
                logger.info(
                    f"[DAG] Task {task.id}: SUMMARY phase improved output — "
                    f"confidence={output.confidence:.2f}"
                )
            else:
                # Summary didn't improve — keep work phase output but add costs
                output.cost_usd = work_cost + (summary_output.cost_usd - work_cost)
                output.input_tokens = work_input_tokens + summary_output.input_tokens
                output.output_tokens = work_output_tokens + summary_output.output_tokens
                output.total_tokens = work_total_tokens + summary_output.total_tokens
                output.turns_used = work_turns_used + (summary_output.turns_used - work_turns_used)
    elif output is None:
        # No work output and no summary possible — create failure output
        output = TaskOutput(
            task_id=task.id,
            status=TaskStatus.FAILED,
            summary=f"Agent produced no output (work_turns={work_turns_used}, error={work_had_error})",
            issues=["No output from work phase and no session for summary phase"],
            cost_usd=work_cost,
            input_tokens=work_input_tokens,
            output_tokens=work_output_tokens,
            total_tokens=work_total_tokens,
            turns_used=work_turns_used,
            confidence=0.0,
        )

    # ── Artifact validation: verify claimed files exist on disk ──
    # Wrapped in try/finally to ensure running_tasks cleanup on any exception
    try:
        if output.is_successful() and output.artifacts:
            output = _validate_artifacts(output, ctx.project_dir)

        # ── Register artifacts for downstream JIT Context ──
        if output.is_successful():
            try:
                n_registered = ctx.artifact_registry.register(output)
                if n_registered:
                    logger.info(
                        f"[DAG] Task {task.id}: {n_registered} artifacts registered in registry"
                    )
            except Exception as exc:
                logger.warning(
                    f"[DAG] Task {task.id}: artifact registration failed (non-fatal): {exc}"
                )

        # ── Blackboard: register file ownership for conflict detection ──
        if output.is_successful() and output.artifacts:
            try:
                for artifact_path in output.artifacts:
                    conflict = ctx.blackboard.register_file_ownership(artifact_path, task.id)
                    if conflict:
                        logger.warning(
                            "[DAG] Task %s: Blackboard detected file conflict: %s",
                            task.id,
                            conflict.description,
                        )
            except Exception as exc:
                logger.warning(
                    f"[DAG] Task {task.id}: Blackboard file tracking failed (non-fatal): {exc}"
                )

        # ── Reflexion: self-critique before accepting output ──
        try:
            from reflexion import run_reflexion, should_reflect

            if should_reflect(task, output):
                logger.info(
                    "[DAG] Task %s: entering Reflexion phase (confidence=%.2f)",
                    task.id,
                    output.confidence,
                )
                output, verdict = await run_reflexion(
                    task=task,
                    output=output,
                    session_id=ctx.session_ids.get(session_key),
                    system_prompt=system_prompt,
                    project_dir=ctx.project_dir,
                    sdk=ctx.sdk,
                )
                logger.info(
                    "[DAG] Task %s: Reflexion complete — %s (cost=$%.4f)",
                    task.id,
                    verdict.summary(),
                    verdict.critique_cost_usd,
                )
                # Record reflexion outcome in structured notes
                ctx.structured_notes.add_note(
                    category=NoteCategory.CONTEXT,
                    title=f"Reflexion for {task.id}",
                    content=verdict.summary(),
                    author_role=role_name,
                    author_task_id=task.id,
                    tags=[task.id, "reflexion"],
                )
        except Exception as exc:
            logger.warning(f"[DAG] Task {task.id}: Reflexion failed (non-fatal): {exc}")

        # ── Record structured notes from task output ──
        try:
            if output.is_successful() and output.summary:
                # Include files changed alongside summary for richer context
                _files_info = ""
                if output.artifacts:
                    _files_info = f"\nFiles changed: {', '.join(output.artifacts[:10])}"
                ctx.structured_notes.add_note(
                    category=NoteCategory.CONTEXT,
                    title=f"Task {task.id} completed",
                    content=output.summary[:500] + _files_info,
                    author_role=role_name,
                    author_task_id=task.id,
                    tags=[task.id, role_name],
                )
            if output.issues:
                for issue in output.issues[:3]:  # Cap to avoid noise
                    ctx.structured_notes.add_note(
                        category=NoteCategory.GOTCHA,
                        title=f"Issue in {task.id}",
                        content=issue,
                        author_role=role_name,
                        author_task_id=task.id,
                        tags=[task.id, role_name],
                    )
        except Exception as exc:
            logger.warning(f"[DAG] Task {task.id}: notes recording failed (non-fatal): {exc}")

        # ── Detect max_turns exhaustion ──
        total_turns = max_turns  # The combined limit
        if output.turns_used >= total_turns and not output.is_successful():
            logger.warning(
                f"[DAG] Task {task.id} ({role_name}): max_turns exhausted "
                f"({output.turns_used}/{total_turns} turns, ${output.cost_usd:.4f}). "
                f"Output status={output.status.value}, confidence={output.confidence:.2f}"
            )
            # Reduce confidence significantly — agent ran out of turns
            output.confidence = max(output.confidence - 0.3, 0.1)
            if not output.failure_category:
                output.failure_category = FailureCategory.TIMEOUT
                output.failure_details = (
                    f"Agent exhausted max_turns ({output.turns_used}/{total_turns}) "
                    f"without completing. Role: {role_name}."
                )
            # If no meaningful summary was produced, mark as FAILED
            if not output.summary or output.summary.strip() == "":
                output.status = TaskStatus.FAILED
                logger.warning(
                    f"[DAG] Task {task.id}: max_turns exhausted with no summary — marking FAILED"
                )
        elif not output.is_successful() and not output.failure_category:
            output.failure_category = classify_failure(output)

        total_elapsed = time.monotonic() - t0
        log_level = logging.INFO if output.is_successful() else logging.WARNING
        logger.log(
            log_level,
            f"[DAG] Task {task.id} ({role_name}) finished in {total_elapsed:.1f}s: "
            f"status={output.status.value}, confidence={output.confidence:.2f}, "
            f"{output.turns_used}/{total_turns} turns, ${output.cost_usd:.4f}",
        )

        # ── Activity log: structured record of this task's outcome ──
        # Written to .hivemind/activity.jsonl (one JSON object per line) so the
        # orchestrator, frontend, and external tools can query agent history.
        # Also emitted via on_event so the frontend gets it in real-time.
        _emit_activity_log(
            task=task,
            output=output,
            elapsed=total_elapsed,
            post_git_status=post_git_status,
            project_dir=ctx.project_dir,
            on_event=ctx.on_event,
        )

        # Milestone 5: complete/failed — task finished
        _final_milestone = "complete" if output.is_successful() else "failed"
        _emit_task_progress(
            ctx.on_event,
            ctx.graph.project_id,
            task.id,
            _final_milestone,
            _task_start_mono,
            0.0,
            task_name=_task_goal,
        )
        # Clean up throttler state for this task
        from dashboard.events import task_progress_throttler

        task_progress_throttler.reset(f"tp:{task.id}")

        return output

    finally:
        # Deregister from watchdog — guaranteed even on unexpected exceptions
        ctx.running_tasks.pop(task.id, None)
        _write_agent_log(f"=== Task {task.id} FINISHED in {time.monotonic() - t0:.1f}s ===")


def _validate_artifacts(output: TaskOutput, project_dir: str) -> TaskOutput:
    """Verify that artifacts claimed by the agent actually exist on disk.

    Checks each file path in ``output.artifacts`` against the filesystem.
    Removes phantom files and adjusts confidence accordingly.
    Does NOT fail the task — just corrects the artifact list and logs warnings.
    """
    if not output.artifacts:
        return output

    project_path = Path(project_dir).resolve()
    verified: list[str] = []
    phantom: list[str] = []

    for artifact_path in output.artifacts:
        # Use resolve() + is_relative_to() to prevent path traversal
        found = False
        for candidate in [
            project_path / artifact_path.lstrip("/"),  # relative to project (primary)
            Path(artifact_path),  # absolute path (agent may give abs)
        ]:
            try:
                resolved = candidate.resolve()
                # Only accept paths inside the project directory
                if resolved.is_relative_to(project_path) and resolved.exists():
                    found = True
                    break
            except (ValueError, OSError) as _path_err:
                logger.debug(
                    "[DAG] Artifact path resolution skipped (%s): %s",
                    type(_path_err).__name__,
                    _path_err,
                )
                continue
        if found:
            verified.append(artifact_path)
        else:
            phantom.append(artifact_path)

    # Also flag files that exist but are outside the project boundary
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
        logger.warning(
            f"[DAG] Task {output.task_id}: artifact validation — "
            f"{len(outside)} files OUTSIDE project boundary removed: {outside[:5]}"
        )
        output.issues.append(
            f"Artifact validation: {len(outside)} files outside project boundary rejected"
        )
        output.confidence = max(output.confidence - 0.2, 0.1)

    if phantom:
        logger.warning(
            f"[DAG] Task {output.task_id}: artifact validation — "
            f"{len(phantom)} phantom files removed: {phantom[:5]}"
        )
        output.artifacts = verified
        output.issues.append(f"Artifact validation: {len(phantom)} claimed files not found on disk")
        # Reduce confidence proportionally to phantom ratio
        if len(verified) == 0 and len(phantom) > 0:
            # All artifacts are phantom — significant confidence reduction
            output.confidence = max(output.confidence - 0.3, 0.1)
        else:
            phantom_ratio = len(phantom) / (len(verified) + len(phantom))
            output.confidence = max(output.confidence - (phantom_ratio * 0.2), 0.1)
    else:
        logger.info(f"[DAG] Task {output.task_id}: all {len(verified)} artifacts verified on disk")

    return output


def _emit_activity_log(
    task: TaskInput,
    output: TaskOutput,
    elapsed: float,
    post_git_status: str,
    project_dir: str,
    on_event: Callable | None = None,
) -> None:
    """Append a structured activity log entry for a completed agent task.

    Writes one JSON object (newline-delimited) to
    ``{project_dir}/.hivemind/activity.jsonl`` so the full agent history is
    available for debugging, analytics, and orchestrator review.

    Also schedules an async event via *on_event* so the frontend receives the
    entry in real-time without waiting for a file read.

    Args:
        task:            The task that just completed.
        output:          The task's output (status, summary, etc.).
        elapsed:         Wall-clock duration of the task in seconds.
        post_git_status: The ``git status`` output captured after the agent ran.
        project_dir:     Absolute path to the project working directory.
        on_event:        Optional async callback — ``await on_event(entry)`` is
                         scheduled as a fire-and-forget asyncio task when provided.
    """
    activity_entry = {
        "timestamp": time.time(),
        "agent": task.role.value,
        "task": task.id,
        "status": "completed" if output.is_successful() else "failed",
        "summary": output.summary if output.summary else "",
        "files_changed": post_git_status[:200] if post_git_status else "",
        "duration_s": round(elapsed, 1),
    }

    # Persist to .hivemind/activity.jsonl (append-only, one JSON object per line)
    try:
        forge_dir = Path(project_dir) / ".hivemind"
        forge_dir.mkdir(parents=True, exist_ok=True)
        activity_file = forge_dir / "activity.jsonl"
        with open(activity_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(activity_entry) + "\n")
    except OSError as log_err:
        logger.warning("[DAG] _emit_activity_log: failed to write activity.jsonl: %s", log_err)

    # Broadcast via event bus so the frontend gets real-time agent activity
    if on_event is not None:
        event = {"type": "agent_activity", **activity_entry}
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _task = asyncio.ensure_future(on_event(event))
                _background_event_tasks.add(_task)
                _task.add_done_callback(_background_event_tasks.discard)
        except Exception as ev_err:
            logger.debug("[DAG] _emit_activity_log: could not schedule on_event: %s", ev_err)


async def _run_summary_phase(
    task: TaskInput,
    ctx: _ExecutionContext,
    session_id: str,
    system_prompt: str,
    work_cost: float,
    work_turns: int,
    role_name: str = "developer",
    work_tool_uses: list[str] | None = None,
) -> TaskOutput | None:
    """Phase 2 of Two-Phase Architecture: extract structured JSON from the agent.

    Resumes the same session with tools disabled and asks ONLY for the JSON
    output block. The agent has full context of what it did in Phase 1.

    This runs as a mandatory step (not just fallback), guaranteeing the agent
    always gets a dedicated chance to produce its receipt.

    Cost: ~$0.01-0.05 per call.
    Returns: TaskOutput if JSON was successfully extracted, None otherwise.
    """
    # Build role-specific structured_artifacts examples based on required_artifacts
    _artifact_examples = []
    for art_type in task.required_artifacts or []:
        _artifact_examples.append(
            "    {\n"
            f'      "type": "{art_type.value}",\n'
            f'      "title": "{art_type.value.replace("_", " ").title()}",\n'
            '      "data": {"summary": "brief description of findings/results"}\n'
            "    }"
        )
    # Always include file_manifest if not already present
    if not task.required_artifacts or ArtifactType.FILE_MANIFEST not in task.required_artifacts:
        _artifact_examples.append(
            "    {\n"
            '      "type": "file_manifest",\n'
            '      "title": "Files Modified",\n'
            '      "data": {"files": {"path/to/file.py": "what was done"}}\n'
            "    }"
        )
    _artifacts_str = (
        ",\n".join(_artifact_examples)
        if _artifact_examples
        else (
            "    {\n"
            '      "type": "file_manifest",\n'
            '      "title": "Files Modified",\n'
            '      "data": {"files": {"path/to/file.py": "description"}}\n'
            "    }"
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
        "  ]\n"
        "}\n"
        "```\n\n"
        "IMPORTANT: Output ONLY the JSON block above. No explanations, no tools."
    )

    logger.info(
        f"[DAG] Task {task.id}: SUMMARY phase — resuming session "
        f"{session_id[:12]}..., tools=disabled, max_turns={_SUMMARY_PHASE_TURNS}"
    )

    try:
        _summary_task = asyncio.create_task(
            isolated_query(
                ctx.sdk,
                prompt=summary_prompt,
                system_prompt=system_prompt,
                cwd=ctx.project_dir,
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
                    logger.warning(
                        f"[DAG] Task {task.id}: SUMMARY timed out ({cancel_hits} interruptions)"
                    )
                    _summary_task.cancel()
                    break
                if cancel_hits <= 3 or cancel_hits % 20 == 0:
                    logger.warning(
                        f"[DAG] Task {task.id}: SUMMARY CancelledError #{cancel_hits} — retrying"
                    )

        summary_response = None
        if _summary_task.done() and not _summary_task.cancelled():
            exc = _summary_task.exception()
            if exc:
                logger.warning(
                    f"[DAG] Task {task.id}: SUMMARY exception ({type(exc).__name__}: {exc})"
                )
            else:
                summary_response = _summary_task.result()
        else:
            logger.warning(
                f"[DAG] Task {task.id}: SUMMARY cancelled/timed-out ({cancel_hits} interruptions)"
            )

        if summary_response is None:
            return None
    except Exception as exc:
        logger.warning(
            f"[DAG] Task {task.id}: SUMMARY unexpected exception ({type(exc).__name__}: {exc})"
        )
        return None

    if summary_response.is_error:
        logger.warning(
            f"[DAG] Task {task.id}: SUMMARY phase error: {summary_response.error_message[:100]}"
        )
        return None

    # Parse the summary response
    # Pass tool_uses from WORK phase (summary phase has tools=[] so won't have its own)
    output = extract_task_output(
        summary_response.text, task.id, task.role.value, tool_uses=work_tool_uses
    )
    output.cost_usd = work_cost + summary_response.cost_usd
    output.input_tokens = getattr(output, "input_tokens", 0) + summary_response.input_tokens
    output.output_tokens = getattr(output, "output_tokens", 0) + summary_response.output_tokens
    output.total_tokens = getattr(output, "total_tokens", 0) + summary_response.total_tokens
    output.turns_used = work_turns + summary_response.num_turns

    logger.info(
        f"[DAG] Task {task.id}: SUMMARY phase result — "
        f"status={output.status.value} confidence={output.confidence:.2f} "
        f"summary_cost=${summary_response.cost_usd:.4f}"
    )

    if output.is_successful() and output.confidence > 0.0:
        return output

    # Summary phase didn't produce valid JSON either
    logger.info(
        f"[DAG] Task {task.id}: SUMMARY phase did not produce valid JSON. "
        f"Falling back to work phase output."
    )
    return None


# ---------------------------------------------------------------------------
# Failure handling — smart retry + self-healing
# ---------------------------------------------------------------------------


async def _handle_failure(
    task: TaskInput,
    output: TaskOutput,
    ctx: _ExecutionContext,
) -> None:
    """Handle a failed task: decide between retry, remediation, or give up.

    Uses per-subcategory retry strategies from ``contracts.get_retry_strategy``
    for fine-grained control over retry limits and backoff.
    """
    category = output.failure_category or classify_failure(output)
    strategy = get_retry_strategy(category)

    # Check if this category allows retries at all
    max_retries_for_category: int = int(strategy["max_retries"])
    remediation_allowed: bool = bool(strategy["remediation_allowed"])
    logger.info(
        f"[DAG] _handle_failure: task={task.id} category={category.value} "
        f"strategy: max_retries={max_retries_for_category} "
        f"remediation_allowed={remediation_allowed} "
        f"current_retries={ctx.retries.get(task.id, 0)} "
        f"is_terminal={output.is_terminal()}"
    )

    if max_retries_for_category == 0:
        logger.warning(f"[DAG] Task {task.id} failed with {category.value} — not retryable")
        return

    # Check if we already retried too many times (per-subcategory limit)
    retry_count = ctx.retries.get(task.id, 0)

    if retry_count < max_retries_for_category and not output.is_terminal():
        ctx.retries[task.id] = retry_count + 1
        logger.warning(
            f"[DAG] Task {task.id} failed ({category.value}), "
            f"retrying ({ctx.retries[task.id]}/{max_retries_for_category})"
        )
        # Remove from completed so ready_tasks picks it up again
        ctx.total_cost -= output.cost_usd  # Don't double-count on retry
        del ctx.completed[task.id]
        return

    # Retries exhausted — try dynamic model switch before remediation.
    # If the failure is likely model-related (e.g. the model got confused),
    # try re-running the same task with a different Claude model.
    try:
        alt_model = ctx.dynamic_spawner.get_respawn_model(task=task, output=output)
        if alt_model:
            logger.info(
                f"[DAG] Task {task.id}: Dynamic Spawner suggests model switch to {alt_model}"
            )
            # Re-queue the task with a model override
            ctx.retries[task.id] = (
                retry_count  # Don't increment — this is a model switch, not a retry
            )
            ctx.total_cost -= output.cost_usd
            del ctx.completed[task.id]
            # Store the model override for _run_single_task to pick up
            ctx.model_overrides[task.id] = alt_model
            ctx.healing_history.append(
                {
                    "task_id": task.id,
                    "action": "model_switch",
                    "from_model": "default",
                    "to_model": alt_model,
                    "reason": f"Dynamic Spawner: {category.value} failure",
                }
            )
            return
    except Exception as spawn_err:
        logger.warning(f"[DAG] Task {task.id}: Dynamic Spawner failed (non-fatal): {spawn_err}")

    # Model switch not applicable — try remediation if allowed for this category
    # NOTE: We intentionally do NOT check ctx.remediation_count here.
    # The cap is enforced atomically inside _create_remediation under
    # graph_lock to prevent a TOCTOU race where two concurrent task
    # failures both pass the check and both exceed MAX_TOTAL_REMEDIATIONS.
    if remediation_allowed:
        depth = _remediation_depth(task, ctx.graph.tasks)
        if depth < MAX_REMEDIATION_DEPTH:
            await _create_remediation(task, output, ctx)


def _remediation_depth(task: TaskInput, graph_tasks: list[TaskInput] | None = None) -> int:
    """Count how deep in the remediation chain this task is.

    Traces back through original_task_id to find the full chain depth.
    """
    if not task.is_remediation:
        return 0
    depth = 1
    if graph_tasks and task.original_task_id:
        # Trace the chain
        task_map = {t.id: t for t in graph_tasks}
        current_id = task.original_task_id
        seen: set[str] = {task.id}  # Prevent infinite loops
        while current_id in task_map and current_id not in seen:
            parent = task_map[current_id]
            seen.add(current_id)
            if parent.is_remediation:
                depth += 1
                current_id = parent.original_task_id
            else:
                break
    return depth


async def _create_remediation(
    failed_task: TaskInput,
    failed_output: TaskOutput,
    ctx: _ExecutionContext,
) -> bool:
    """Create and inject a remediation task into the graph.

    The remediation cap (MAX_TOTAL_REMEDIATIONS) is checked and the counter
    incremented atomically inside ``graph_lock`` to prevent a TOCTOU race
    where two concurrent task failures both pass an external check and both
    exceed the cap.

    Returns:
        True if a remediation task was successfully created, False otherwise.
    """
    async with ctx.graph_lock:
        # --- Atomic cap enforcement (TOCTOU-safe) ---
        if ctx.remediation_count >= MAX_TOTAL_REMEDIATIONS:
            logger.warning(
                f"[DAG] Remediation cap ({MAX_TOTAL_REMEDIATIONS}) reached — "
                f"not creating additional remediation for {failed_task.id}"
            )
            return False

        ctx.task_counter += 1
        remediation = create_remediation_task(
            failed_task=failed_task,
            failed_output=failed_output,
            task_counter=ctx.task_counter,
        )

        if remediation is None:
            logger.warning(
                f"[DAG] No remediation strategy for {failed_task.id} "
                f"({failed_output.failure_category})"
            )
            return False

        # Inject into the live graph and increment the counter — both under
        # the same lock so no other coroutine can slip past the cap check.
        ctx.graph.add_task(remediation)
        ctx.remediation_count += 1

    healing_entry = {
        "action": "remediation_created",
        "failed_task": failed_task.id,
        "failure_category": (failed_output.failure_category or FailureCategory.UNKNOWN).value,
        "remediation_task": remediation.id,
        "detail": f"Auto-created {remediation.id} ({remediation.role.value}) to fix "
        f"{failed_task.id}: {failed_output.failure_details[:100]}",
    }
    ctx.healing_history.append(healing_entry)

    logger.info(
        f"[DAG] Self-healing: created {remediation.id} ({remediation.role.value}) "
        f"to fix {failed_task.id} [{failed_output.failure_category}]"
    )

    if ctx.on_remediation:
        try:
            await ctx.on_remediation(failed_task, failed_output, remediation)
        except Exception as exc:
            logger.warning(f"[DAG] on_remediation callback failed: {exc}")

    return True


async def _try_self_heal(ctx: _ExecutionContext) -> bool:
    """Last-resort self-healing: check all failed tasks for possible remediation.

    Returns True if at least one remediation task was created.
    """
    healed = False
    for task in ctx.graph.tasks:
        if task.id not in ctx.completed:
            continue
        output = ctx.completed[task.id]
        if output.is_successful() or output.is_terminal():
            continue
        if task.is_remediation:
            continue  # Don't remediate a remediation

        # Check if we already created a remediation for this task
        existing_fix = any(
            t.is_remediation and t.original_task_id == task.id for t in ctx.graph.tasks
        )
        if existing_fix:
            continue

        created = await _create_remediation(task, output, ctx)
        if created:
            healed = True

    return healed


# ---------------------------------------------------------------------------
# Artifact validation
# ---------------------------------------------------------------------------


def _check_required_artifact_types(task: TaskInput, output: TaskOutput) -> None:
    """Warn if an agent didn't produce its required artifact *types*."""
    if not task.required_artifacts:
        return

    produced_types = {a.type for a in output.structured_artifacts}
    missing = set(task.required_artifacts) - produced_types

    if missing:
        missing_names = [m.value for m in missing]
        logger.warning(f"[DAG] Task {task.id} missing required artifacts: {missing_names}")
        output.issues.append(f"Missing required artifacts: {', '.join(missing_names)}")


# ---------------------------------------------------------------------------
# Batch planning — parallelism vs. sequential
# ---------------------------------------------------------------------------


def _plan_batches(tasks: list[TaskInput]) -> list[list[TaskInput]]:
    """
    Split a list of ready tasks into sequential batches.

    Rules:
    - Reader-only tasks can always batch together
    - Writer tasks with overlapping file scopes must be sequential
    - Writer tasks with non-overlapping scopes can batch together
    """
    if not tasks:
        return []

    readers = [t for t in tasks if t.role in _READER_ROLES]
    writers = [t for t in tasks if t.role in _WRITER_ROLES]
    others = [t for t in tasks if t.role not in _READER_ROLES and t.role not in _WRITER_ROLES]

    batches: list[list[TaskInput]] = []

    # Writers run FIRST — they produce the code that readers will verify
    if writers:
        writer_batches = _split_writers_by_conflicts(writers)
        batches.extend(writer_batches)

    # All readers + others can go in one parallel batch AFTER writers
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


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


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
            lines.append(f"{icon} {prefix}[{task.id}] {task.role.value}: {output.summary[:120]}")
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
            lines.append(f"⏭️  [{task.id}] {task.role.value}: Not executed")

    if result.healing_history:
        lines.append("\n### Self-Healing Actions")
        for h in result.healing_history:
            lines.append(f"  🔧 {h.get('detail', '')}")

    return "\n".join(lines)
