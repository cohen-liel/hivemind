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
import logging
import time
from pathlib import Path
from typing import Callable, Awaitable, Any

import state
from contracts import (
    AgentRole,
    Artifact,
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
from git_discipline import executor_commit
from sdk_client import CircuitOpenError
from skills_registry import build_skill_prompt, select_skills_for_task
import config as cfg

logger = logging.getLogger(__name__)

# --- Configuration ---
MAX_TASK_RETRIES = 2          # Direct retries per task
MAX_REMEDIATION_DEPTH = 2     # Max chain of fix_xxx tasks before giving up
MAX_TOTAL_REMEDIATIONS = 5    # Max total remediation tasks per graph execution
MAX_ROUNDS = 50               # Safety limit on execution rounds

# Per-role max_turns — the REAL limit is the wall-clock timeout.
# max_turns is a safety net, not the primary constraint.
# Writer roles need many turns (each file read/write = 1 turn).
# Reader roles need fewer turns but still generous.
_ROLE_MAX_TURNS: dict[str, int] = {
    # Writers — complex multi-file tasks
    "developer": 200,
    "frontend_developer": 200,
    "backend_developer": 200,
    "database_expert": 150,
    "devops": 150,
    "typescript_architect": 200,
    "python_backend": 200,
    # Readers / analysts — research + report
    "researcher": 75,
    "reviewer": 50,
    "security_auditor": 50,
    "test_engineer": 100,
    "tester": 100,
    "ux_critic": 40,
    "memory": 30,
}
_DEFAULT_MAX_TURNS = 100  # Fallback for unlisted roles

# Per-role wall-clock timeout (seconds) — the PRIMARY limit.
# Sourced from config.py AGENT_TIMEOUT_MAP, with generous overrides.
_ROLE_TIMEOUT_SECONDS: dict[str, int] = {
    "developer": 900,           # 15 min — complex builds
    "frontend_developer": 900,
    "backend_developer": 900,
    "database_expert": 600,     # 10 min
    "devops": 600,
    "researcher": 600,          # 10 min — web searches are slow
    "reviewer": 300,            # 5 min
    "security_auditor": 300,
    "test_engineer": 600,
    "tester": 600,
    "ux_critic": 240,
    "memory": 180,
}
_DEFAULT_TIMEOUT_SECONDS = 600  # 10 min fallback


def _get_max_turns(role: str) -> int:
    """Return the max_turns limit for a given agent role."""
    return _ROLE_MAX_TURNS.get(role, _DEFAULT_MAX_TURNS)


def _get_task_timeout(role: str) -> int:
    """Return the wall-clock timeout (seconds) for a given agent role."""
    return _ROLE_TIMEOUT_SECONDS.get(role, _DEFAULT_TIMEOUT_SECONDS)

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
    max_budget_usd: float = 50.0,
    session_id_store: dict[str, str] | None = None,
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
        max_budget_usd: Hard budget cap across the entire graph
        session_id_store: Mutable dict to persist agent session IDs for resume

    Returns:
        ExecutionResult with all outputs, stats, and healing history
    """
    sdk = sdk_client or state.sdk_client
    if sdk is None:
        raise RuntimeError("SDK client not initialized")

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
    )

    logger.info(
        f"[DAG] Starting graph execution: project={graph.project_id} "
        f"tasks={len(graph.tasks)} budget=${max_budget_usd}"
    )

    round_num = 0
    while not graph.is_complete(ctx.completed):
        round_num += 1
        if round_num > MAX_ROUNDS:
            logger.error(f"[DAG] Safety limit: exceeded {MAX_ROUNDS} rounds. Completed: {list(ctx.completed.keys())}")
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
                logger.error("[DAG] Graph has unresolvable failures after healing attempts. Stopping.")
                break
            logger.warning("[DAG] No ready tasks but graph not complete. Deadlock?")
            break

        if ctx.total_cost >= max_budget_usd:
            logger.error(f"[DAG] Budget exhausted (${ctx.total_cost:.2f} >= ${max_budget_usd})")
            break

        ready_info = [(t.id, t.role.value) for t in ready]
        logger.info(f"[DAG] Round {round_num}: {len(ready)} ready tasks: {ready_info}")

        # Split into parallel batches
        batches = _plan_batches(ready)
        for bi, batch in enumerate(batches):
            batch_info = [(t.id, t.role.value) for t in batch]
            logger.info(f"[DAG] Round {round_num} batch {bi+1}/{len(batches)}: {batch_info}")

        for batch in batches:
            coros = [
                _run_single_task(task, ctx)
                for task in batch
            ]
            raw_results = await asyncio.gather(
                *coros, return_exceptions=True
            )

            # Convert exceptions to FAILED TaskOutputs
            # FIX(task_001): Check for CancelledError before the generic
            # BaseException handler so task cancellations are re-raised
            # (propagating cancellation) rather than misclassified as failures.
            results: list[TaskOutput] = []
            for task_item, raw in zip(batch, raw_results):
                if isinstance(raw, asyncio.CancelledError):
                    # Propagate cancellation — the entire graph execution
                    # should stop, not record this as a task failure.
                    logger.info(f"[DAG] Task {task_item.id} cancelled — propagating")
                    raise raw
                elif isinstance(raw, BaseException):
                    logger.error(f"[DAG] Task {task_item.id} raised exception: {raw}")
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

            for task, output in zip(batch, results):
                ctx.completed[task.id] = output
                ctx.total_cost += output.cost_usd

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
                    _validate_artifacts(task, output)

        # Auto-commit after each round
        try:
            round_outputs = [ctx.completed[t.id] for t in ready if t.id in ctx.completed]
            committed = await executor_commit(project_dir, round_outputs, round_num)
            if committed:
                logger.info(f"[DAG] Round {round_num} committed: {committed}")
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
        return self.failure_count == 0

    def summary_text(self) -> str:
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

async def _run_single_task(
    task: TaskInput,
    ctx: _ExecutionContext,
) -> TaskOutput:
    """Run one task: build prompt -> call specialist -> parse TaskOutput.

    Flow:
    1. Build prompt from task + upstream context
    2. Call SDK with per-role max_turns and wall-clock timeout
    3. Parse JSON output from agent response
    4. If no JSON found but agent did work → attempt JSON recovery query
    5. Return TaskOutput (success or classified failure)
    """
    role_name = task.role.value
    max_turns = _get_max_turns(role_name)
    task_timeout = _get_task_timeout(role_name)

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
        except Exception:
            pass

    # Gather context from upstream tasks (now with structured artifacts)
    context_outputs = {
        tid: ctx.completed[tid]
        for tid in task.context_from
        if tid in ctx.completed
    }

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
        ctx.specialist_prompts.get("backend_developer", "You are an expert software engineer.")
    )

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

    logger.info(
        f"[DAG] Task {task.id}: calling SDK "
        f"max_turns={max_turns}, timeout={task_timeout}s, max_budget=$15.0, "
        f"session={'resume' if session_id else 'new'}, "
        f"prompt_len={len(prompt)}, system_prompt_len={len(system_prompt)}"
    )

    # ── Build streaming callbacks scoped to this task ──
    _on_stream = None
    _on_tool_use = None
    if ctx.on_agent_stream:
        async def _on_stream(text):
            try:
                await ctx.on_agent_stream(role_name, text, task.id)
            except Exception:
                pass
    if ctx.on_agent_tool_use:
        async def _on_tool_use(tool_name, tool_info="", tool_input=None):
            try:
                await ctx.on_agent_tool_use(role_name, tool_name, tool_info, task.id)
            except Exception:
                pass

    # ── Execute the main SDK query ──
    t0 = time.monotonic()
    try:
        response = await asyncio.wait_for(
            ctx.sdk.query_with_retry(
                prompt=prompt,
                system_prompt=system_prompt,
                cwd=ctx.project_dir,
                session_id=session_id,
                max_turns=max_turns,
                max_budget_usd=15.0,
                on_stream=_on_stream,
                on_tool_use=_on_tool_use,
            ),
            timeout=task_timeout,
        )
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        logger.error(f"[DAG] Task {task.id} timed out after {elapsed:.0f}s (limit={task_timeout}s)")
        output = TaskOutput(
            task_id=task.id,
            status=TaskStatus.FAILED,
            summary=f"Task timed out after {elapsed:.0f} seconds (limit: {task_timeout}s)",
            issues=[f"Wall-clock timeout ({task_timeout}s) for role {role_name}"],
            failure_details=f"Task exceeded {task_timeout}s wall-clock timeout",
            confidence=0.0,
        )
        output.failure_category = FailureCategory.TIMEOUT
        return output
    except asyncio.CancelledError:
        logger.info(f"[DAG] Task {task.id} was cancelled")
        raise
    except CircuitOpenError as exc:
        logger.error(f"[DAG] Task {task.id} rejected by circuit breaker: {exc}")
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
    elapsed = time.monotonic() - t0

    # Persist session ID for potential recovery query
    if response.session_id:
        ctx.session_ids[session_key] = response.session_id

    logger.info(
        f"[DAG] Task {task.id}: SDK returned in {elapsed:.1f}s "
        f"is_error={response.is_error} turns={response.num_turns}/{max_turns} "
        f"cost=${response.cost_usd:.4f} text_len={len(response.text)} "
        f"session_id={'yes' if response.session_id else 'no'}"
    )

    if response.is_error:
        logger.warning(
            f"[DAG] Task {task.id}: SDK error: {response.error_message[:200]} "
            f"category={response.error_category}"
        )
        output = TaskOutput(
            task_id=task.id,
            status=TaskStatus.FAILED,
            summary=f"Agent error: {response.error_message}",
            issues=[response.error_message],
            cost_usd=response.cost_usd,
            turns_used=response.num_turns,
            confidence=0.0,
            failure_details=response.error_message,
        )
        output.failure_category = classify_failure(output)
        return output

    # ── Parse output ──
    output = extract_task_output(response.text, task.id)
    output.cost_usd = response.cost_usd
    output.turns_used = response.num_turns
    logger.info(
        f"[DAG] Task {task.id}: extract_task_output -> "
        f"status={output.status.value} confidence={output.confidence:.2f} "
        f"summary='{output.summary[:80]}' "
        f"artifacts={len(output.artifacts)} issues={len(output.issues)}"
    )

    # ── JSON Recovery: if no JSON was found but multi-signal detection says ──
    # the agent DID work, send a lightweight follow-up query to the same session
    # asking ONLY for the JSON summary. This costs ~$0.01 and saves the $2+ task.
    needs_recovery = (
        not output.is_successful()
        and response.session_id
        and output.confidence == 0.0
        and response.num_turns >= 5  # Agent did at least some work
        and len(response.text) >= 200  # Non-trivial output
    )
    # Also try recovery if multi-signal scored it as completed but with low confidence
    if not needs_recovery and output.is_successful() and output.confidence <= 0.7:
        # Already marked completed by multi-signal, but let's try to get proper JSON
        needs_recovery = bool(response.session_id)

    if needs_recovery and response.session_id:
        output = await _attempt_json_recovery(
            task=task,
            ctx=ctx,
            session_id=response.session_id,
            system_prompt=system_prompt,
            original_output=output,
            original_cost=response.cost_usd,
            original_turns=response.num_turns,
        )

    # ── Detect max_turns exhaustion ──
    if response.num_turns >= max_turns and not output.is_successful():
        logger.warning(
            f"[DAG] Task {task.id} ({role_name}): max_turns exhausted "
            f"({response.num_turns}/{max_turns} turns, ${response.cost_usd:.4f}). "
            f"Output status={output.status.value}, confidence={output.confidence:.2f}"
        )
        if not output.failure_category:
            output.failure_category = FailureCategory.TIMEOUT
            output.failure_details = (
                f"Agent exhausted max_turns ({response.num_turns}/{max_turns}) "
                f"without completing. Role: {role_name}."
            )
    elif not output.is_successful() and not output.failure_category:
        output.failure_category = classify_failure(output)

    log_level = logging.INFO if output.is_successful() else logging.WARNING
    logger.log(
        log_level,
        f"[DAG] Task {task.id} ({role_name}) finished in {elapsed:.1f}s: "
        f"status={output.status.value}, confidence={output.confidence:.2f}, "
        f"{response.num_turns}/{max_turns} turns, ${output.cost_usd:.4f}"
    )
    return output


async def _attempt_json_recovery(
    task: TaskInput,
    ctx: _ExecutionContext,
    session_id: str,
    system_prompt: str,
    original_output: TaskOutput,
    original_cost: float,
    original_turns: int,
) -> TaskOutput:
    """Send a lightweight follow-up query to extract JSON from an agent that did work.

    The agent already completed its task but didn't produce the required JSON
    summary. We resume the same session with a short prompt asking ONLY for
    the JSON block, with tools disabled (max_turns=5, no tools).

    Cost: ~$0.01-0.05. Saves: the entire $2-5 task from being wasted.
    """
    recovery_prompt = (
        "You have completed your work. Now produce ONLY the required JSON output block.\n"
        "Do NOT do any more work. Do NOT use any tools. Just output the JSON summary "
        "of what you already did.\n\n"
        "```json\n"
        "{\n"
        f'  "task_id": "{task.id}",\n'
        '  "status": "completed",\n'
        '  "summary": "what you did in 2-3 sentences",\n'
        '  "artifacts": ["list/of/files.py"],\n'
        '  "issues": [],\n'
        '  "confidence": 0.95\n'
        "}\n"
        "```"
    )

    logger.info(
        f"[DAG] Task {task.id}: attempting JSON recovery via session resume "
        f"(session={session_id[:12]}..., original_turns={original_turns})"
    )

    try:
        recovery_response = await asyncio.wait_for(
            ctx.sdk.query_with_retry(
                prompt=recovery_prompt,
                system_prompt=system_prompt,
                cwd=ctx.project_dir,
                session_id=session_id,
                max_turns=5,
                max_budget_usd=1.0,
                tools=[],  # Disable ALL tools — just produce text
                max_retries=0,  # Don't retry the recovery itself
            ),
            timeout=60,  # 1 minute max for recovery
        )
    except Exception as exc:
        logger.warning(
            f"[DAG] Task {task.id}: JSON recovery failed ({type(exc).__name__}: {exc}). "
            f"Keeping original output."
        )
        return original_output

    if recovery_response.is_error:
        logger.warning(
            f"[DAG] Task {task.id}: JSON recovery returned error: "
            f"{recovery_response.error_message[:100]}. Keeping original output."
        )
        return original_output

    # Try to parse the recovery response
    recovered = extract_task_output(recovery_response.text, task.id)
    total_cost = original_cost + recovery_response.cost_usd
    total_turns = original_turns + recovery_response.num_turns

    if recovered.is_successful() and recovered.confidence > original_output.confidence:
        recovered.cost_usd = total_cost
        recovered.turns_used = total_turns
        logger.info(
            f"[DAG] Task {task.id}: JSON recovery SUCCESS! "
            f"confidence={recovered.confidence:.2f} (was {original_output.confidence:.2f}), "
            f"recovery_cost=${recovery_response.cost_usd:.4f}"
        )
        return recovered

    # Recovery didn't improve things — keep original (which may be
    # a multi-signal COMPLETED with lower confidence)
    logger.info(
        f"[DAG] Task {task.id}: JSON recovery did not improve output "
        f"(recovered={recovered.status.value}/{recovered.confidence:.2f}, "
        f"original={original_output.status.value}/{original_output.confidence:.2f}). "
        f"Keeping original."
    )
    # Still add the recovery cost
    original_output.cost_usd = total_cost
    original_output.turns_used = total_turns
    return original_output


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
        logger.warning(
            f"[DAG] Task {task.id} failed with {category.value} — not retryable"
        )
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
        del ctx.completed[task.id]
        return

    # Retries exhausted — try remediation if allowed for this category
    if remediation_allowed and ctx.remediation_count < MAX_TOTAL_REMEDIATIONS:
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
) -> None:
    """Create and inject a remediation task into the graph."""
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
        return

    # Inject into the live graph
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
            t.is_remediation and t.original_task_id == task.id
            for t in ctx.graph.tasks
        )
        if existing_fix:
            continue

        if ctx.remediation_count < MAX_TOTAL_REMEDIATIONS:
            await _create_remediation(task, output, ctx)
            healed = True

    return healed


# ---------------------------------------------------------------------------
# Artifact validation
# ---------------------------------------------------------------------------

def _validate_artifacts(task: TaskInput, output: TaskOutput) -> None:
    """Warn if an agent didn't produce its required artifacts."""
    if not task.required_artifacts:
        return

    produced_types = {a.type for a in output.structured_artifacts}
    missing = set(task.required_artifacts) - produced_types

    if missing:
        missing_names = [m.value for m in missing]
        logger.warning(
            f"[DAG] Task {task.id} missing required artifacts: {missing_names}"
        )
        output.issues.append(
            f"Missing required artifacts: {', '.join(missing_names)}"
        )


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
            lines.append(
                f"{icon} {prefix}[{task.id}] {task.role.value}: {output.summary[:120]}"
            )
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
