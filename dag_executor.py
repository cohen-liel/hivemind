"""
DAG Executor — The Orchestrator's new brain.

Replaces the regex-based delegate-parsing loop with a clean DAG execution engine:
- Reads TaskInput objects (typed contracts)
- Runs independent tasks in PARALLEL, conflicting tasks SEQUENTIALLY
- Reads TaskOutput.status — NO text parsing
- Auto-commits after each round (only the executor touches git)
- Handles retries, failures, and blocked paths gracefully
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Awaitable

import state
from contracts import (
    AgentRole,
    TaskGraph,
    TaskInput,
    TaskOutput,
    TaskStatus,
    extract_task_output,
    task_input_to_prompt,
)
from git_discipline import executor_commit

logger = logging.getLogger(__name__)

# Max times a single task can be retried on failure
MAX_TASK_RETRIES = 2

# Roles that write/modify files — must run sequentially when file scopes overlap
_WRITER_ROLES = {
    AgentRole.FRONTEND_DEVELOPER,
    AgentRole.BACKEND_DEVELOPER,
    AgentRole.DATABASE_EXPERT,
    AgentRole.DEVOPS,
    # Legacy
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
    # Legacy
    AgentRole.TESTER,
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
    max_budget_usd: float = 50.0,
    session_id_store: dict[str, str] | None = None,
) -> list[TaskOutput]:
    """
    Execute a TaskGraph to completion.

    Args:
        graph: The PM's execution plan
        project_dir: Working directory for all agents
        specialist_prompts: dict[role_value -> system_prompt]
        sdk_client: ClaudeSDKManager instance (defaults to state.sdk_client)
        on_task_start: Optional async callback fired when a task begins
        on_task_done: Optional async callback fired when a task finishes
        max_budget_usd: Hard budget cap across the entire graph
        session_id_store: Mutable dict to persist agent session IDs for resume

    Returns:
        List of TaskOutput for all executed tasks (in completion order)
    """
    sdk = sdk_client or state.sdk_client
    if sdk is None:
        raise RuntimeError("SDK client not initialized")

    completed: dict[str, TaskOutput] = {}   # task_id -> TaskOutput
    retries: dict[str, int] = {}             # task_id -> retry count
    total_cost = 0.0
    session_ids = session_id_store or {}

    logger.info(
        f"[DAG] Starting graph execution: project={graph.project_id} "
        f"tasks={len(graph.tasks)} budget=${max_budget_usd}"
    )

    round_num = 0
    while not graph.is_complete(completed):
        round_num += 1
        ready = graph.ready_tasks(completed)

        if not ready:
            # No tasks ready but graph not complete → deadlock or all failed
            if graph.has_failed(completed):
                logger.error("[DAG] Graph has unresolvable failures. Stopping.")
                break
            logger.warning("[DAG] No ready tasks but graph not complete. Deadlock?")
            break

        if total_cost >= max_budget_usd:
            logger.error(f"[DAG] Budget exhausted (${total_cost:.2f} >= ${max_budget_usd})")
            break

        logger.info(f"[DAG] Round {round_num}: {len(ready)} ready tasks")

        # Split into parallel batches (no file conflicts) vs sequential
        batches = _plan_batches(ready)

        for batch in batches:
            # Fire all tasks in this batch concurrently
            coros = [
                _run_single_task(
                    task=task,
                    graph=graph,
                    completed=completed,
                    project_dir=project_dir,
                    specialist_prompts=specialist_prompts,
                    sdk=sdk,
                    session_ids=session_ids,
                    on_start=on_task_start,
                )
                for task in batch
            ]
            results: list[TaskOutput] = await asyncio.gather(*coros, return_exceptions=False)

            for task, output in zip(batch, results):
                completed[task.id] = output
                total_cost += output.cost_usd
                logger.info(
                    f"[DAG] Task {task.id} ({task.role.value}) → "
                    f"{output.status.value} (${output.cost_usd:.4f}, "
                    f"confidence={output.confidence:.2f})"
                )
                if on_task_done:
                    try:
                        await on_task_done(task, output)
                    except Exception:
                        pass

                # Handle failures: retry if budget allows
                if not output.is_successful() and not output.is_terminal():
                    retries[task.id] = retries.get(task.id, 0) + 1
                    if retries[task.id] <= MAX_TASK_RETRIES:
                        logger.warning(
                            f"[DAG] Task {task.id} failed ({output.status.value}), "
                            f"retrying ({retries[task.id]}/{MAX_TASK_RETRIES})"
                        )
                        del completed[task.id]  # Remove to make it eligible again

        # Auto-commit after each round (only executor commits)
        try:
            round_outputs = [completed[t.id] for t in ready if t.id in completed]
            committed = await executor_commit(project_dir, round_outputs, round_num)
            if committed:
                logger.info(f"[DAG] Round {round_num} committed: {committed}")
        except Exception as exc:
            logger.warning(f"[DAG] Auto-commit failed (non-fatal): {exc}")

    all_outputs = list(completed.values())
    total_cost_final = sum(o.cost_usd for o in all_outputs)
    success_count = sum(1 for o in all_outputs if o.is_successful())

    logger.info(
        f"[DAG] Graph complete: {success_count}/{len(all_outputs)} succeeded, "
        f"total cost=${total_cost_final:.4f}"
    )
    return all_outputs


# ---------------------------------------------------------------------------
# Single task execution
# ---------------------------------------------------------------------------

async def _run_single_task(
    task: TaskInput,
    graph: TaskGraph,
    completed: dict[str, TaskOutput],
    project_dir: str,
    specialist_prompts: dict[str, str],
    sdk,
    session_ids: dict[str, str],
    on_start: Callable | None,
) -> TaskOutput:
    """Run one task: build prompt → call specialist → parse TaskOutput."""
    if on_start:
        try:
            await on_start(task)
        except Exception:
            pass

    # Gather context from upstream tasks
    context_outputs = {
        tid: completed[tid]
        for tid in task.context_from
        if tid in completed
    }

    # Build the prompt using the typed contract serialiser
    prompt = task_input_to_prompt(task, context_outputs)

    # Get specialist system prompt
    system_prompt = specialist_prompts.get(
        task.role.value,
        specialist_prompts.get("developer", "You are an expert software engineer.")
    )

    # Resume session if we have one for this role
    session_key = f"{graph.project_id}:{task.role.value}"
    session_id = session_ids.get(session_key)

    t0 = time.monotonic()
    response = await sdk.query_with_retry(
        prompt=prompt,
        system_prompt=system_prompt,
        cwd=project_dir,
        session_id=session_id,
        max_turns=30,
        max_budget_usd=15.0,
    )
    elapsed = time.monotonic() - t0

    # Persist session ID for resume
    if response.session_id:
        session_ids[session_key] = response.session_id

    if response.is_error:
        return TaskOutput(
            task_id=task.id,
            status=TaskStatus.FAILED,
            summary=f"Agent error: {response.error_message}",
            issues=[response.error_message],
            cost_usd=response.cost_usd,
            turns_used=response.num_turns,
            confidence=0.0,
        )

    output = extract_task_output(response.text, task.id)
    output.cost_usd = response.cost_usd
    output.turns_used = response.num_turns

    logger.debug(
        f"[DAG] Task {task.id} finished in {elapsed:.1f}s "
        f"({response.num_turns} turns, ${response.cost_usd:.4f})"
    )
    return output


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

    # All readers + others can go in one parallel batch
    parallel_batch = readers + others
    if parallel_batch:
        batches.append(parallel_batch)

    # Writers: group by file conflicts
    if writers:
        writer_batches = _split_writers_by_conflicts(writers)
        batches.extend(writer_batches)

    return batches


def _split_writers_by_conflicts(writers: list[TaskInput]) -> list[list[TaskInput]]:
    """
    Group writer tasks into sequential batches to avoid file conflicts.
    Tasks with no known file_scope (empty) are treated as potentially conflicting.
    """
    batches: list[list[TaskInput]] = []
    claimed_files: set[str] = set()
    current_batch: list[TaskInput] = []

    for task in writers:
        if not task.files_scope:
            # Unknown scope → flush current batch, run alone
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                claimed_files = set()
            batches.append([task])
            continue

        scope = set(task.files_scope)
        if scope & claimed_files:
            # Conflict: flush and start new batch
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
# Summary helpers (for the orchestrator to surface to frontend)
# ---------------------------------------------------------------------------

def build_execution_summary(graph: TaskGraph, outputs: list[TaskOutput]) -> str:
    """Build a human-readable summary of the graph execution."""
    output_map = {o.task_id: o for o in outputs}
    lines = [
        f"## Execution Summary — {graph.vision}",
        f"Tasks: {len(outputs)}/{len(graph.tasks)} executed",
        "",
    ]
    total_cost = 0.0
    for task in graph.tasks:
        output = output_map.get(task.id)
        if output:
            icon = "✅" if output.is_successful() else "❌" if output.status == TaskStatus.FAILED else "⚠️"
            lines.append(
                f"{icon} [{task.id}] {task.role.value}: {output.summary[:120]}"
            )
            if output.artifacts:
                lines.append(f"   Files: {', '.join(output.artifacts[:5])}")
            if output.issues:
                lines.append(f"   Issues: {'; '.join(output.issues[:2])}")
            total_cost += output.cost_usd
        else:
            lines.append(f"⏭️  [{task.id}] {task.role.value}: Not executed")

    lines.append(f"\nTotal cost: ${total_cost:.4f}")
    return "\n".join(lines)
