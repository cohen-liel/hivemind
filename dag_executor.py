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
from contracts import (
    AgentRole,
    ArtifactContractError,
    ArtifactType,
    FailureCategory,
    TaskGraph,
    TaskInput,
    TaskOutput,
    TaskStatus,
    classify_failure,
    create_remediation_task,
    extract_task_output,
    get_retry_strategy,
    task_input_to_prompt,
    validate_artifact_contracts,
)
from git_discipline import commit_single_task, executor_commit
from isolated_query import isolated_query
from sdk_client import CircuitOpenError
from skills_registry import build_skill_prompt, select_skills_for_task

logger = logging.getLogger(__name__)

# Prevent GC of fire-and-forget event broadcast tasks
_background_event_tasks: set[asyncio.Task] = set()

# --- Configuration — pulled from centralised config.py (H-3 fix) ---
# These were previously hardcoded magic numbers; they are now tuneable via env vars.
MAX_TASK_RETRIES: int = cfg.MAX_TASK_RETRIES  # Direct retries per task
MAX_REMEDIATION_DEPTH: int = (
    cfg.MAX_REMEDIATION_DEPTH
)  # Max chain of fix_xxx tasks before giving up
MAX_TOTAL_REMEDIATIONS: int = (
    cfg.MAX_TOTAL_REMEDIATIONS
)  # Max total remediation tasks per graph execution
MAX_ROUNDS: int = cfg.MAX_DAG_ROUNDS  # Safety limit on execution rounds

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


def _get_task_timeout(role: str) -> int:
    """Return the wall-clock timeout (seconds) for a given agent role.

    Uses the centralized AGENT_REGISTRY from config.py.
    """
    from config import get_agent_timeout

    return get_agent_timeout(role)


def _get_task_budget(role: str) -> float:
    """Return the per-task budget (USD) for a given agent role.

    Uses the centralized AGENT_REGISTRY from config.py.
    """
    from config import get_agent_budget

    return get_agent_budget(role)


# Roles that write/modify files — must run sequentially when file scopes overlap
_WRITER_ROLES = {
    AgentRole.FRONTEND_DEVELOPER,
    AgentRole.BACKEND_DEVELOPER,
    AgentRole.DATABASE_EXPERT,
    AgentRole.DEVOPS,
    AgentRole.TYPESCRIPT_ARCHITECT,
    AgentRole.PYTHON_BACKEND,
    AgentRole.DEVELOPER,
}

# Roles that are read-only / analysis only — always safe to run in parallel
_READER_ROLES = {
    AgentRole.RESEARCHER,
    AgentRole.REVIEWER,
    AgentRole.SECURITY_AUDITOR,
    AgentRole.UX_CRITIC,
    AgentRole.TEST_ENGINEER,
    AgentRole.TESTER,
    AgentRole.MEMORY,
}

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

    IDLE_THRESHOLD = 120  # seconds without a tool call
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

    This wrapper ensures that at most ``ctx.max_concurrent_tasks`` DAG nodes
    run simultaneously within a single graph execution.  When all slots are
    occupied the coroutine blocks (via the semaphore) until a slot is freed by
    a finishing peer — there is no busy-polling or explicit sleep.

    Args:
        task: The task to execute.
        ctx:  Execution context (owns the semaphore and all shared state).

    Returns:
        The TaskOutput produced by ``_run_single_task``.
    """
    async with ctx.node_semaphore:
        logger.debug(
            "[DAG] Semaphore acquired for %s (active_slots=%d/%d)",
            task.id,
            ctx.max_concurrent_tasks - ctx.node_semaphore._value,  # type: ignore[attr-defined]
            ctx.max_concurrent_tasks,
        )
        return await _run_single_task(task, ctx)


async def _execute_graph_inner(
    ctx: _ExecutionContext,
    graph: TaskGraph,
    max_budget_usd: float,
) -> ExecutionResult:
    """Inner graph execution loop (separated for watchdog wrapping)."""
    round_num = 0
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
                    continue  # New tasks were added, re-check ready
                _failed_tasks = [
                    f"{tid}={out.status.value}({out.failure_details[:60] if out.failure_details else 'no details'})"
                    for tid, out in ctx.completed.items()
                    if not out.is_successful()
                ]
                logger.error(
                    f"[DAG] Graph has unresolvable failures after healing attempts. Stopping.\n"
                    f"  Failed tasks: {_failed_tasks}\n"
                    f"  Completed: {len(ctx.completed)}/{len(graph.tasks)}\n"
                    f"  Remediation attempts: {ctx.remediation_count}"
                )
                break
            _all_statuses = [
                f"{t.id}={'done' if t.id in ctx.completed else 'pending'}(deps={t.depends_on})"
                for t in graph.tasks
            ]
            logger.warning(
                f"[DAG] No ready tasks but graph not complete. Deadlock?\n"
                f"  Task statuses: {_all_statuses}\n"
                f"  Completed IDs: {list(ctx.completed.keys())}"
            )
            break

        if ctx.total_cost >= max_budget_usd:
            _pending = [t.id for t in graph.tasks if t.id not in ctx.completed]
            logger.error(
                f"[DAG] Budget exhausted (${ctx.total_cost:.2f} >= ${max_budget_usd})\n"
                f"  Completed: {len(ctx.completed)}/{len(graph.tasks)}\n"
                f"  Still pending: {_pending}\n"
                f"  Cost breakdown: {[(tid, f'${out.cost_usd:.4f}') for tid, out in ctx.completed.items()]}"
            )
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

                # Handle failures
                if not output.is_successful():
                    await _handle_failure(task, output, ctx)

                # Validate required artifacts
                if output.is_successful() and task.required_artifacts:
                    _check_required_artifact_types(task, output)

                # Per-task commit: one focused commit per completed task
                if output.is_successful():
                    try:
                        committed = await commit_single_task(ctx.project_dir, output)
                        if committed:
                            logger.info(f"[DAG] Task {task.id} committed: {committed}")
                    except Exception as exc:
                        logger.warning(f"[DAG] Per-task commit failed (non-fatal): {exc}")

        # Fallback: commit anything remaining that wasn't caught by per-task commits
        try:
            round_outputs = [ctx.completed[t.id] for t in ready if t.id in ctx.completed]
            committed = await executor_commit(ctx.project_dir, round_outputs, round_num)
            if committed:
                logger.info(f"[DAG] Round {round_num} fallback commit: {committed}")
        except Exception as exc:
            logger.warning(f"[DAG] Auto-commit failed (non-fatal): {exc}")

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
    ):
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


# ---------------------------------------------------------------------------
# Execution Result
# ---------------------------------------------------------------------------


class ExecutionResult:
    """Result of a graph execution, including healing history."""

    def __init__(
        self,
        outputs: list[TaskOutput],
        total_cost: float,
        success_count: int,
        failure_count: int,
        remediation_count: int,
        healing_history: list[dict[str, str]],
    ):
        self.outputs = outputs
        self.total_cost = total_cost
        self.success_count = success_count
        self.failure_count = failure_count
        self.remediation_count = remediation_count
        self.healing_history = healing_history

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
            f"Total cost: ${self.total_cost:.4f}",
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
    logger.info(
        f"[DAG] ══════ RUN COMPLETE ══════ "
        f"{completed_count}✅ {failed_count}❌ / {len(graph.tasks)} tasks | "
        f"cost=${ctx.total_cost:.4f} | "
        f"roles={roles_used}"
    )
    return ExecutionResult(
        outputs=all_outputs,
        total_cost=sum(o.cost_usd for o in all_outputs),
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
    task_timeout = _get_task_timeout(role_name)

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
                await ctx.on_agent_tool_use(role_name, tool_name, tool_info, task.id)
            except Exception as exc:
                logger.debug("[DAG] on_agent_tool_use callback error (task=%s): %s", task.id, exc)
    else:

        async def _on_tool_use(tool_name, tool_info="", tool_input=None):
            _write_agent_log(f"TOOL: {tool_name} | {tool_info[:200]}")
            if task.id in ctx.running_tasks:
                ctx.running_tasks[task.id]["last_activity"] = time.monotonic()
                ctx.running_tasks[task.id]["turns_used"] += 1

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
    try:
        # BUG-23: isolated_query (separate thread) prevents anyio leak propagation.
        from config import get_agent_timeout as _get_role_timeout

        _role_timeout = _get_role_timeout(role_name)
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
    work_turns_used = 0
    work_text = ""
    work_had_error = False

    if response is not None:
        work_session_id = response.session_id or None
        work_cost = response.cost_usd
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
                output.turns_used = work_turns_used + (summary_output.turns_used - work_turns_used)
    elif output is None:
        # No work output and no summary possible — create failure output
        output = TaskOutput(
            task_id=task.id,
            status=TaskStatus.FAILED,
            summary=f"Agent produced no output (work_turns={work_turns_used}, error={work_had_error})",
            issues=["No output from work phase and no session for summary phase"],
            cost_usd=work_cost,
            turns_used=work_turns_used,
            confidence=0.0,
        )

    # ── Artifact validation: verify claimed files exist on disk ──
    # Wrapped in try/finally to ensure running_tasks cleanup on any exception
    try:
        if output.is_successful() and output.artifacts:
            output = _validate_artifacts(output, ctx.project_dir)

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

    # Retries exhausted — try remediation if allowed for this category
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
    lines = [
        f"## Execution Summary — {graph.vision}",
        f"Tasks: {result.success_count + result.failure_count}/{len(graph.tasks)} executed "
        f"({result.success_count} succeeded, {result.failure_count} failed)",
        f"Self-healing: {result.remediation_count} remediation tasks created",
        f"Total cost: ${result.total_cost:.4f}",
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
