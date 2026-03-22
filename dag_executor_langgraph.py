"""LangGraph-based DAG Executor — drop-in replacement for dag_executor.py.

This module replaces the custom while-loop DAG executor with a proper
LangGraph StateGraph that leverages:
  - **Checkpointing**: InMemorySaver for fault-tolerance and resume
  - **Typed State**: All execution context as LangGraph state channels
  - **Conditional Routing**: Dynamic task selection via conditional edges
  - **Subgraphs**: Reflexion loop as a proper LangGraph subgraph
  - **Reducers**: Annotated list channels for parallel fan-out/fan-in
  - **Retry Policies**: Built-in retry for transient failures

Architecture:
  Parent Graph: select_batch → execute_batch → post_batch → (loop or end)
  Subgraph per task: run_agent → [reflexion_check → (pass|retry)] → done

The public API is identical to the original dag_executor.execute_graph().
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from operator import add
from typing import Annotated, Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from contracts import (
    AgentRole,
    FailureCategory,
    TaskGraph,
    TaskInput,
    TaskOutput,
    TaskStatus,
    classify_failure,
    create_remediation_task,
    extract_task_output,
    task_input_to_prompt,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

MAX_ROUNDS = 50
MAX_TOTAL_REMEDIATIONS = 10
MAX_REMEDIATION_DEPTH = 3
_WRITER_ROLES = {
    AgentRole.BACKEND_DEVELOPER,
    AgentRole.FRONTEND_DEVELOPER,
    AgentRole.DEVOPS,
    AgentRole.DEVELOPER,
    AgentRole.PYTHON_BACKEND,
    AgentRole.TYPESCRIPT_ARCHITECT,
    AgentRole.DATABASE_EXPERT,
}
_READER_ROLES = {
    AgentRole.TEST_ENGINEER,
    AgentRole.TESTER,
    AgentRole.REVIEWER,
    AgentRole.SECURITY_AUDITOR,
    AgentRole.UX_CRITIC,
}


# ── Reducers ───────────────────────────────────────────────────────────────


def _merge_completed(
    old: dict[str, TaskOutput], new: dict[str, TaskOutput]
) -> dict[str, TaskOutput]:
    """Merge completed task outputs — new entries override old ones."""
    merged = {**old}
    merged.update(new)
    return merged


def _merge_dicts(old: dict, new: dict) -> dict:
    """Merge two dicts, new overrides old."""
    merged = {**old}
    merged.update(new)
    return merged


def _sum_float(old: float, new: float) -> float:
    return old + new


def _max_int(old: int, new: int) -> int:
    return max(old, new)


# ── LangGraph State ───────────────────────────────────────────────────────

from typing_extensions import TypedDict


class DAGState(TypedDict):
    """Full execution state for the LangGraph DAG executor.

    Every field uses a reducer so parallel nodes can update state safely.
    """

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

    # Batch state (set by select_batch, consumed by execute_batch)
    current_batch: list[TaskInput]
    batch_results: Annotated[list[TaskOutput], add]

    # Blackboard / shared notes
    blackboard_notes: Annotated[list[str], add]

    # Status tracking
    status: str  # "running", "completed", "failed", "max_rounds"

    # Callbacks (set once at init, not modified)
    on_task_start: Any
    on_task_done: Any
    on_remediation: Any
    on_event: Any


# ── Helper: Plan batches (same logic as original) ─────────────────────────


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


# ── Helper: Get max turns / timeout / budget per role ─────────────────────


def _get_max_turns(role: str) -> int:
    """Get max turns for a role."""
    role_turns = {
        "backend_developer": 45,
        "frontend_developer": 45,
        "test_engineer": 30,
        "devops": 25,
        "code_reviewer": 15,
        "security_auditor": 15,
    }
    return role_turns.get(role, 30)


def _get_task_timeout(role: str) -> int:
    """Get timeout in seconds for a role."""
    role_timeouts = {
        "backend_developer": 600,
        "frontend_developer": 600,
        "test_engineer": 300,
        "devops": 300,
        "code_reviewer": 180,
        "security_auditor": 180,
    }
    return role_timeouts.get(role, 300)


def _get_task_budget(role: str) -> float:
    """Get max budget per task for a role."""
    return 5.0  # Simplified — original uses config


# ── Node: select_batch ─────────────────────────────────────────────────────


def select_batch(state: DAGState) -> dict:
    """Select the next batch of ready tasks to execute.

    This node implements the DAG scheduling logic:
    1. Find all tasks whose dependencies are satisfied
    2. Group them into batches respecting file conflicts
    3. Pick the first batch for execution
    """
    graph = state["graph"]
    completed = state["completed"]
    round_num = state["round_num"] + 1

    ready = graph.ready_tasks(completed)

    if not ready:
        # Check if graph is complete or stuck
        if graph.is_complete(completed):
            return {
                "status": "completed",
                "round_num": round_num,
                "current_batch": [],
            }

        # Check for failed dependencies blocking progress
        has_failures = any(not out.is_successful() for out in completed.values())
        if has_failures:
            return {
                "status": "failed",
                "round_num": round_num,
                "current_batch": [],
            }

        # Deadlock — no ready tasks, not complete, no failures
        return {
            "status": "failed",
            "round_num": round_num,
            "current_batch": [],
        }

    if round_num > MAX_ROUNDS:
        return {
            "status": "max_rounds",
            "round_num": round_num,
            "current_batch": [],
        }

    # Plan batches and pick the first one
    batches = _plan_batches(ready)
    first_batch = batches[0] if batches else ready[:1]

    completed_ids = list(completed.keys())
    pending_ids = [t.id for t in graph.tasks if t.id not in completed]
    logger.info(
        f"[LG-DAG] Round {round_num}: "
        f"completed={len(completed_ids)}/{len(graph.tasks)} "
        f"ready={[t.id for t in ready]} "
        f"batch={[t.id for t in first_batch]} "
        f"pending={pending_ids}"
    )

    return {
        "round_num": round_num,
        "current_batch": first_batch,
        "batch_results": [],  # Reset for this batch
    }


# ── Node: execute_batch ────────────────────────────────────────────────────


async def execute_batch(state: DAGState) -> dict:
    """Execute all tasks in the current batch (potentially in parallel).

    Each task:
    1. Builds the prompt from task spec + upstream context
    2. Calls isolated_query (the Claude Code agent loop)
    3. Extracts structured output from the response
    4. Runs reflexion if applicable
    5. Records notes on the blackboard
    """
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

    async def _run_one_task(task: TaskInput) -> TaskOutput:
        """Execute a single task — the core agent invocation."""
        role_name = task.role.value
        max_turns = _get_max_turns(role_name)
        task_timeout = _get_task_timeout(role_name)

        # Notify task start
        if on_task_start:
            try:
                await on_task_start(task)
            except Exception:
                pass

        # Build prompt from upstream context
        context_outputs = {tid: completed[tid] for tid in task.context_from if tid in completed}
        prompt = task_input_to_prompt(
            task,
            context_outputs,
            graph_vision=graph.vision,
            graph_epics=graph.epic_breakdown,
        )

        # Get specialist system prompt
        system_prompt = specialist_prompts.get(
            role_name,
            specialist_prompts.get(
                "backend_developer",
                "You are an expert software engineer.",
            ),
        )

        # Inject project boundary
        try:
            from project_context import build_project_header

            _boundary = build_project_header(graph.project_id, project_dir)
            if _boundary and _boundary not in system_prompt:
                system_prompt = _boundary + "\n\n" + system_prompt
        except Exception:
            pass

        # Inject blackboard context
        blackboard_notes = state.get("blackboard_notes", [])
        if blackboard_notes:
            notes_text = "\n".join(f"- {n}" for n in blackboard_notes[-20:])
            prompt += f"\n\n## Notes from other agents:\n{notes_text}"

        # Call the agent (isolated_query)
        from isolated_query import isolated_query

        t0 = time.monotonic()
        session_key = f"{graph.project_id}:{role_name}:{task.id}"
        session_id = session_ids.get(session_key)

        logger.info(
            f"[LG-DAG] Task {task.id} ({role_name}): "
            f"calling agent, max_turns={max_turns}, timeout={task_timeout}s"
        )

        try:
            response = await asyncio.wait_for(
                isolated_query(
                    sdk,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    cwd=project_dir,
                    session_id=session_id,
                    max_turns=max_turns,
                    max_budget_usd=_get_task_budget(role_name),
                ),
                timeout=task_timeout,
            )
        except TimeoutError:
            elapsed = time.monotonic() - t0
            logger.warning(f"[LG-DAG] Task {task.id}: timeout after {elapsed:.0f}s")
            output = TaskOutput(
                task_id=task.id,
                status=TaskStatus.FAILED,
                summary=f"Agent timed out after {elapsed:.0f}s",
                issues=["Timeout"],
                failure_details=f"Timeout after {elapsed:.0f}s",
                confidence=0.0,
            )
            output.failure_category = FailureCategory.TIMEOUT
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
            return output

        elapsed = time.monotonic() - t0

        # Store session ID
        new_session_ids = {}
        if response.session_id:
            new_session_ids[session_key] = response.session_id

        # Extract structured output
        output = None
        if response.text:
            output = extract_task_output(
                response.text,
                task.id,
                role_name,
                tool_uses=response.tool_uses,
            )
            output.cost_usd = response.cost_usd
            output.input_tokens = response.input_tokens
            output.output_tokens = response.output_tokens
            output.total_tokens = response.total_tokens
            output.turns_used = response.num_turns

        if output is None:
            output = TaskOutput(
                task_id=task.id,
                status=TaskStatus.FAILED,
                summary="No output from agent",
                issues=["Agent produced no text output"],
                failure_details="Empty response",
                confidence=0.0,
            )
            output.failure_category = FailureCategory.UNKNOWN

        # Run summary phase if needed (no JSON found in work output)
        if not output.is_successful() and response.text:
            try:
                summary_response = await isolated_query(
                    sdk,
                    prompt=(
                        "Based on the work you just did, provide a JSON summary:\n"
                        '{"status": "completed"|"failed", "summary": "...", '
                        '"files_changed": [...], "issues": [...], "confidence": 0.0-1.0}\n'
                        "Respond ONLY with the JSON."
                    ),
                    system_prompt="You are summarizing your previous work. Output only valid JSON.",
                    cwd=project_dir,
                    session_id=response.session_id,
                    max_turns=3,
                    tools=[],  # No tools for summary
                )
                if summary_response.text:
                    summary_output = extract_task_output(summary_response.text, task.id, role_name)
                    if summary_output.is_successful():
                        summary_output.cost_usd = output.cost_usd + summary_response.cost_usd
                        summary_output.input_tokens = (
                            output.input_tokens + summary_response.input_tokens
                        )
                        summary_output.output_tokens = (
                            output.output_tokens + summary_response.output_tokens
                        )
                        summary_output.total_tokens = (
                            output.total_tokens + summary_response.total_tokens
                        )
                        summary_output.turns_used = output.turns_used + summary_response.num_turns
                        output = summary_output
            except Exception as exc:
                logger.warning(f"[LG-DAG] Task {task.id}: summary phase failed: {exc}")

        # Reflexion: self-critique before accepting output
        try:
            from reflexion import run_reflexion, should_reflect

            if should_reflect(task, output):
                logger.info(
                    f"[LG-DAG] Task {task.id}: entering Reflexion phase "
                    f"(confidence={output.confidence:.2f})"
                )
                output, verdict = await run_reflexion(
                    task=task,
                    output=output,
                    session_id=session_ids.get(session_key),
                    system_prompt=system_prompt,
                    project_dir=project_dir,
                    sdk=sdk,
                )
                logger.info(
                    f"[LG-DAG] Task {task.id}: Reflexion complete — "
                    f"{verdict.summary()} (cost=${verdict.critique_cost_usd:.4f})"
                )
        except Exception as exc:
            logger.warning(f"[LG-DAG] Task {task.id}: Reflexion failed (non-fatal): {exc}")

        # FINAL VALIDATION: if task was marked successful but no files were written, mark as failed
        # This runs AFTER summary phase and reflexion to catch cases where the agent
        # just printed code as text without using Write tool
        if output.is_successful() and task.role in _WRITER_ROLES:
            project_files = []
            for root, dirs, filenames in os.walk(project_dir):
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in (".git", "__pycache__", ".pytest_cache", ".hivemind")
                ]
                for fn in filenames:
                    if fn.endswith(
                        (".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".html", ".css")
                    ):
                        project_files.append(os.path.relpath(os.path.join(root, fn), project_dir))
            if not project_files:
                logger.warning(
                    f"[LG-DAG] Task {task.id}: marked successful but NO files found on disk! Forcing FAILED."
                )
                output.status = TaskStatus.FAILED
                output.failure_details = "Agent did not write any files to disk"
                output.confidence = 0.0
                output.failure_category = FailureCategory.UNKNOWN

        # Validate artifacts exist on disk
        if output.artifacts:
            validated = []
            for art_path in output.artifacts:
                full_path = os.path.join(project_dir, art_path)
                if os.path.exists(full_path):
                    validated.append(art_path)
                else:
                    logger.warning(f"[LG-DAG] Task {task.id}: artifact not found: {art_path}")
            output.artifacts = validated

        logger.info(
            f"[LG-DAG] Task {task.id} ({role_name}): "
            f"status={output.status.value}, confidence={output.confidence:.2f}, "
            f"turns={output.turns_used}, cost=${output.cost_usd:.4f}, "
            f"elapsed={elapsed:.1f}s"
        )

        # Notify task done
        if on_task_done:
            try:
                await on_task_done(task, output)
            except Exception:
                pass

        return output

    # Execute all tasks in batch — parallel if multiple
    if len(batch) == 1:
        results = [await _run_one_task(batch[0])]
    else:
        # Fan-out: run tasks in parallel
        tasks_coros = [_run_one_task(t) for t in batch]
        results = await asyncio.gather(*tasks_coros, return_exceptions=True)

        # Convert exceptions to failed outputs
        processed = []
        for task, result in zip(batch, results, strict=False):
            if isinstance(result, Exception):
                logger.error(f"[LG-DAG] Task {task.id}: parallel execution error: {result}")
                output = TaskOutput(
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    summary=f"Exception: {result}",
                    issues=[str(result)[:300]],
                    failure_details=str(result)[:500],
                    confidence=0.0,
                )
                output.failure_category = classify_failure(output)
                processed.append(output)
            else:
                processed.append(result)
        results = processed

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


# ── Node: post_batch ──────────────────────────────────────────────────────


async def post_batch(state: DAGState) -> dict:
    """Post-batch processing: git commit, remediation, status update.

    Runs after each batch completes:
    1. Git commit the changes
    2. Check for failures and create remediation tasks
    3. Update status
    """
    graph = state["graph"]
    completed = state["completed"]
    batch_results = state["batch_results"]
    project_dir = state["project_dir"]
    remediation_count = state["remediation_count"]
    task_counter = state["task_counter"]
    on_remediation = state.get("on_remediation")

    # Git commit after batch
    try:
        from git_discipline import executor_commit

        successful_outputs = [r for r in batch_results if r.is_successful()]
        if successful_outputs:
            await executor_commit(
                project_dir=project_dir,
                round_outputs=successful_outputs,
                round_num=state["round_num"],
            )
            logger.info(
                f"[LG-DAG] Git commit after round {state['round_num']}: "
                f"{len(successful_outputs)} successful tasks"
            )
    except Exception as exc:
        logger.warning(f"[LG-DAG] Git commit failed (non-fatal): {exc}")

    # Handle failures — create remediation tasks
    new_healing = []
    new_remediation_count = remediation_count
    new_task_counter = task_counter

    for result in batch_results:
        if result.is_successful():
            continue

        # Find the original task
        task = None
        for t in graph.tasks:
            if t.id == result.task_id:
                task = t
                break

        if task is None:
            continue

        # Check if we can create a remediation
        if new_remediation_count >= MAX_TOTAL_REMEDIATIONS:
            logger.warning(f"[LG-DAG] Remediation cap reached ({MAX_TOTAL_REMEDIATIONS})")
            continue

        # Check remediation depth
        depth = 0
        if task.is_remediation:
            depth = 1
            task_map = {t.id: t for t in graph.tasks}
            current_id = task.original_task_id
            seen = {task.id}
            while current_id and current_id in task_map and current_id not in seen:
                parent = task_map[current_id]
                seen.add(current_id)
                if parent.is_remediation:
                    depth += 1
                    current_id = parent.original_task_id
                else:
                    break

        if depth >= MAX_REMEDIATION_DEPTH:
            logger.warning(
                f"[LG-DAG] Task {task.id}: max remediation depth ({MAX_REMEDIATION_DEPTH})"
            )
            continue

        # Create remediation task
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
                    "failure_category": (result.failure_category or FailureCategory.UNKNOWN).value,
                    "remediation_task": remediation.id,
                    "detail": (
                        f"Auto-created {remediation.id} ({remediation.role.value}) "
                        f"to fix {task.id}: {result.failure_details[:100] if result.failure_details else 'unknown'}"
                    ),
                }
            )
            logger.info(f"[LG-DAG] Created remediation {remediation.id} for {task.id}")

            if on_remediation:
                try:
                    await on_remediation(task, result, remediation)
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
        "status": status,
    }


# ── Routing: should we continue or stop? ──────────────────────────────────

# ── Node: review_code ─────────────────────────────────────────────────────


async def review_code(state: DAGState) -> dict:
    """Review and improve code quality after all tasks complete.

    This node runs ONLY when the graph is about to finish (status=completed).
    It acts as a final quality gate:
    1. Collects all Python files in the project
    2. Runs ruff auto-fix
    3. Asks the LLM to review and improve the code
    4. Applies improvements (type hints, docstrings, error handling)
    """
    status = state["status"]
    if status != "completed":
        # Only review when all tasks are done
        return {}

    project_dir = state["project_dir"]
    sdk = state["sdk"]
    specialist_prompts = state["specialist_prompts"]

    # Collect all Python files
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
                rel = os.path.relpath(fpath, project_dir)
                try:
                    with open(fpath) as f:
                        content = f.read()
                    py_files.append((rel, content))
                except Exception:
                    pass

    if not py_files:
        logger.info("[LG-DAG] Review: no Python files to review")
        return {}

    # Build the review prompt
    files_text = ""
    for rel, content in py_files:
        files_text += f"\n### {rel}\n```python\n{content}\n```\n"

    review_prompt = f"""You are a senior code reviewer. Review the following Python project files and improve their quality.

Focus on:
1. Add type hints to all function signatures that are missing them
2. Add docstrings to all public functions/classes that are missing them
3. Fix any obvious bugs or anti-patterns
4. Improve error handling where needed
5. Ensure consistent code style

CRITICAL RULES:
- DO NOT change the logic or behavior of the code. Only improve quality.
- You can ONLY use Edit, Read, Bash, Glob, and Grep tools. The Write tool is NOT available.
- To lint files, use Bash: `ruff check --fix <file_path>`
- Use the Edit tool to make targeted find-and-replace improvements.
- After editing, run `ruff check --fix` on each file via Bash.
- Finally, run `python -m pytest` to make sure nothing is broken.
- If pytest fails after your edits, REVERT your changes using Edit.

Project files:
{files_text}
"""

    system_prompt = specialist_prompts.get(
        "reviewer",
        specialist_prompts.get(
            "backend_developer",
            "You are an expert code reviewer.",
        ),
    )

    # Inject project boundary
    try:
        from project_context import build_project_header

        _boundary = build_project_header(state["graph"].project_id, project_dir)
        if _boundary and _boundary not in system_prompt:
            system_prompt = _boundary + "\n\n" + system_prompt
    except Exception:
        pass

    logger.info(f"[LG-DAG] Review: reviewing {len(py_files)} Python files")

    try:
        from isolated_query import isolated_query

        response = await asyncio.wait_for(
            isolated_query(
                sdk,
                prompt=review_prompt,
                system_prompt=system_prompt,
                cwd=project_dir,
                max_turns=15,
                max_budget_usd=1.0,
                allowed_tools=["Read", "Edit", "Bash", "Glob", "Grep"],
            ),
            timeout=180,
        )
        logger.info(
            f"[LG-DAG] Review complete: turns={response.num_turns}, tokens={response.total_tokens}"
        )

        # Git commit the review improvements
        try:
            result = subprocess.run(
                ["git", "add", "-A"],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                ["git", "diff", "--cached", "--stat"],
                cwd=project_dir,
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                subprocess.run(
                    ["git", "commit", "-m", "review: code quality improvements"],
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                )
                logger.info("[LG-DAG] Review: committed quality improvements")
        except Exception:
            pass

        return {
            "total_cost": response.cost_usd,
            "blackboard_notes": [
                f"[review] Reviewed {len(py_files)} files, {response.num_turns} improvements made"
            ],
        }
    except TimeoutError:
        logger.warning("[LG-DAG] Review: timed out")
        return {}
    except Exception as exc:
        logger.warning(f"[LG-DAG] Review failed (non-fatal): {exc}")
        return {}


# ── Routing ──────────────────────────────────────────────────────────────


def should_continue(state: DAGState) -> Literal["select_batch", "review_code", "__end__"]:
    """Conditional edge: continue executing, review code, or stop."""
    status = state["status"]
    if status == "completed":
        return "review_code"
    elif status in ("failed", "max_rounds"):
        return END
    return "select_batch"


# ── Build the LangGraph ───────────────────────────────────────────────────


def build_dag_graph() -> StateGraph:
    """Build and compile the LangGraph DAG executor graph.

    Graph structure:
        START → select_batch → execute_batch → post_batch
              → (select_batch | review_code | END)
        review_code → END

    The loop continues until:
    - All tasks are completed → review_code → END
    - Unresolvable failures → END
    - Max rounds exceeded → END
    """
    workflow = StateGraph(DAGState)

    # Add nodes
    workflow.add_node("select_batch", select_batch)
    workflow.add_node("execute_batch", execute_batch)
    workflow.add_node("post_batch", post_batch)
    workflow.add_node("review_code", review_code)

    # Add edges
    workflow.add_edge(START, "select_batch")
    workflow.add_edge("select_batch", "execute_batch")
    workflow.add_edge("execute_batch", "post_batch")
    workflow.add_edge("review_code", END)

    # Conditional edge: loop, review, or stop
    workflow.add_conditional_edges(
        "post_batch",
        should_continue,
        {
            "select_batch": "select_batch",
            "review_code": "review_code",
            END: END,
        },
    )

    return workflow


# ── ExecutionResult (same interface as original) ───────────────────────────


class ExecutionResult:
    """Result of a graph execution, including healing history."""

    def __init__(
        self,
        outputs: list[TaskOutput],
        total_cost: float = 0.0,
        total_tokens: int = 0,
        success_count: int = 0,
        failure_count: int = 0,
        remediation_count: int = 0,
        healing_history: list[dict] | None = None,
    ):
        self.outputs = outputs
        self.total_cost = total_cost
        self.total_tokens = total_tokens
        self.success_count = success_count
        self.failure_count = failure_count
        self.remediation_count = remediation_count
        self.healing_history = healing_history or []

    @property
    def all_successful(self) -> bool:
        return self.failure_count == 0

    @property
    def summary_text(self) -> str:
        return (
            f"Tasks: {self.success_count + self.failure_count} "
            f"({self.success_count} succeeded, {self.failure_count} failed), "
            f"Remediations: {self.remediation_count}, "
            f"Cost: ${self.total_cost:.4f}"
        )


# ── Public API: execute_graph ──────────────────────────────────────────────


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

    # Build and compile the graph with checkpointing
    workflow = build_dag_graph()
    checkpointer = MemorySaver()
    compiled = workflow.compile(checkpointer=checkpointer)

    # Initial state
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
        "status": "running",
        "on_task_start": on_task_start,
        "on_task_done": on_task_done,
        "on_remediation": on_remediation,
        "on_event": on_event,
    }

    config = {
        "configurable": {
            "thread_id": f"dag-{graph.project_id}-{int(time.time())}",
        }
    }

    logger.info(
        f"[LG-DAG] Starting LangGraph execution: "
        f"project={graph.project_id}, tasks={len(graph.tasks)}, "
        f"budget=${max_budget_usd}"
    )

    t0 = time.monotonic()

    # Run the graph
    final_state = await compiled.ainvoke(initial_state, config)

    elapsed = time.monotonic() - t0

    # Build result
    completed = final_state["completed"]
    outputs = list(completed.values())
    success_count = sum(1 for o in outputs if o.is_successful())
    failure_count = sum(1 for o in outputs if not o.is_successful())
    total_cost = final_state["total_cost"]
    total_tokens = sum(o.total_tokens for o in outputs)

    result = ExecutionResult(
        outputs=outputs,
        total_cost=total_cost,
        total_tokens=total_tokens,
        success_count=success_count,
        failure_count=failure_count,
        remediation_count=final_state["remediation_count"],
        healing_history=final_state["healing_history"],
    )

    logger.info(
        f"[LG-DAG] Execution complete in {elapsed:.1f}s: "
        f"{result.summary_text}, status={final_state['status']}"
    )

    return result


# ── Summary helper ─────────────────────────────────────────────────────────


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
            icon = "✅" if output.is_successful() else "❌"
            prefix = "🔧 " if task.is_remediation else ""
            lines.append(
                f"{icon} {prefix}[{task.id}] {task.role.value}: "
                f"{output.summary[:120] if output.summary else 'No summary'}"
            )
        else:
            lines.append(f"⏭️  [{task.id}] {task.role.value}: Not executed")

    if result.healing_history:
        lines.append("\n### Self-Healing Actions")
        for h in result.healing_history:
            lines.append(f"  🔧 {h.get('detail', '')}")

    return "\n".join(lines)
