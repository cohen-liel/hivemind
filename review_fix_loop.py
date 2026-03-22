"""Review-Fix Loop — DAG-level backtracking for code quality.

When a code_reviewer (or security_auditor) completes and reports issues in
its output, this module creates a targeted fix task for the original developer
and optionally re-runs the reviewer to verify the fix.

This implements the missing "backtracking" capability in the DAG:
    Developer → Reviewer (finds issues) → Developer Fix → Re-Review

Without this, reviewer issues go into output.issues but nobody acts on them.
The DAG is forward-only: once a task completes, it's done.

Integration:
    Called from ``dag_executor._execute_graph_inner`` after a reviewer/auditor
    task completes successfully. Injects fix tasks into the live graph.

Design:
    - Only triggers for reviewer/auditor roles with concrete issues
    - Creates a fix task that depends on the reviewer output
    - Optionally creates a re-review task after the fix
    - Max 2 review-fix cycles per original task (configurable)
    - Uses the existing graph.add_task() mechanism (same as remediation)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import config as cfg
from contracts import (
    AgentRole,
    ArtifactType,
    TaskInput,
    TaskOutput,
    TaskStatus,
)

if TYPE_CHECKING:
    from contracts import TaskGraph

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
REVIEW_FIX_ENABLED: bool = cfg._get("REVIEW_FIX_ENABLED", "true", str).lower() == "true"
REVIEW_FIX_MAX_CYCLES: int = cfg._get("REVIEW_FIX_MAX_CYCLES", "2", int)
REVIEW_FIX_MIN_ISSUES: int = cfg._get("REVIEW_FIX_MIN_ISSUES", "1", int)
REVIEW_FIX_RE_REVIEW: bool = cfg._get("REVIEW_FIX_RE_REVIEW", "true", str).lower() == "true"

# Roles that can trigger a review-fix loop
_REVIEWER_ROLES = {
    AgentRole.REVIEWER,
    AgentRole.SECURITY_AUDITOR,
    AgentRole.UX_CRITIC,
}

# Roles that should receive fix tasks
_DEVELOPER_ROLES = {
    AgentRole.FRONTEND_DEVELOPER,
    AgentRole.BACKEND_DEVELOPER,
    AgentRole.DATABASE_EXPERT,
    AgentRole.DEVELOPER,
    AgentRole.PYTHON_BACKEND,
    AgentRole.TYPESCRIPT_ARCHITECT,
}


def _extract_review_issues(output: TaskOutput) -> list[str]:
    """Extract actionable issues from a reviewer's output.

    Looks at output.issues, output.summary, and structured_artifacts
    for concrete problems that need fixing.
    """
    issues = []

    # Direct issues list
    if output.issues:
        for issue in output.issues:
            # Filter out meta-issues (reflexion notes, validation notes)
            if issue.startswith("[Reflexion") or issue.startswith("Artifact validation"):
                continue
            issues.append(issue)

    # Check structured artifacts for review/security reports
    for artifact in output.structured_artifacts:
        if artifact.type in (ArtifactType.REVIEW_REPORT, ArtifactType.SECURITY_REPORT):
            data = artifact.data or {}
            # Common patterns in review reports
            for key in ("issues", "findings", "vulnerabilities", "problems"):
                if key in data and isinstance(data[key], list):
                    issues.extend(str(item) for item in data[key])

    # Deduplicate while preserving order
    seen = set()
    unique_issues = []
    for issue in issues:
        normalized = issue.strip().lower()
        if normalized not in seen and len(normalized) > 5:
            seen.add(normalized)
            unique_issues.append(issue.strip())

    return unique_issues


def _find_original_developer_task(
    reviewer_task: TaskInput,
    graph_tasks: list[TaskInput],
    completed: dict[str, TaskOutput],
) -> TaskInput | None:
    """Find the developer task that the reviewer was reviewing.

    Traces back through context_from dependencies to find the most recent
    developer task in the reviewer's dependency chain.
    """
    # Check direct dependencies first
    for dep_id in reviewer_task.context_from or []:
        for task in graph_tasks:
            if task.id == dep_id and task.role in _DEVELOPER_ROLES:
                return task

    # Check depends_on
    for dep_id in reviewer_task.depends_on or []:
        for task in graph_tasks:
            if task.id == dep_id and task.role in _DEVELOPER_ROLES:
                return task

    # Broader search: find any developer task that produced files
    # mentioned in the reviewer's context
    if reviewer_task.files_scope:
        for task in graph_tasks:
            if task.role not in _DEVELOPER_ROLES:
                continue
            if task.id not in completed:
                continue
            dev_output = completed[task.id]
            if not dev_output.is_successful():
                continue
            # Check if developer's artifacts overlap with reviewer's scope
            dev_files = set(dev_output.artifacts or [])
            review_scope = set(reviewer_task.files_scope or [])
            if dev_files & review_scope:
                return task

    return None


def _count_existing_fix_cycles(
    original_task_id: str,
    graph_tasks: list[TaskInput],
) -> int:
    """Count how many review-fix cycles already exist for a task."""
    count = 0
    for task in graph_tasks:
        if (
            hasattr(task, "review_fix_for")
            and getattr(task, "review_fix_for", None) == original_task_id
        ):
            count += 1
    return count


def should_trigger_review_fix(
    reviewer_task: TaskInput,
    reviewer_output: TaskOutput,
    graph_tasks: list[TaskInput],
    completed: dict[str, TaskOutput],
) -> bool:
    """Determine if a completed reviewer task should trigger a fix cycle.

    Returns True if:
    1. Review-fix loop is enabled
    2. The task is a reviewer/auditor role
    3. The output has actionable issues
    4. We haven't exceeded max cycles for the original task
    5. There's an identifiable developer task to fix
    """
    if not REVIEW_FIX_ENABLED:
        return False

    if reviewer_task.role not in _REVIEWER_ROLES:
        return False

    if not reviewer_output.is_successful():
        return False

    issues = _extract_review_issues(reviewer_output)
    if len(issues) < REVIEW_FIX_MIN_ISSUES:
        return False

    # Find the original developer task
    dev_task = _find_original_developer_task(reviewer_task, graph_tasks, completed)
    if dev_task is None:
        logger.debug(
            "[ReviewFix] No developer task found for reviewer %s",
            reviewer_task.id,
        )
        return False

    # Check cycle count
    cycles = _count_existing_fix_cycles(dev_task.id, graph_tasks)
    if cycles >= REVIEW_FIX_MAX_CYCLES:
        logger.info(
            "[ReviewFix] Max cycles (%d) reached for %s, skipping",
            REVIEW_FIX_MAX_CYCLES,
            dev_task.id,
        )
        return False

    return True


def create_fix_task(
    reviewer_task: TaskInput,
    reviewer_output: TaskOutput,
    dev_task: TaskInput,
    dev_output: TaskOutput,
    task_counter: int,
) -> TaskInput:
    """Create a fix task for the developer based on reviewer feedback.

    The fix task:
    - Has the same role as the original developer
    - Depends on the reviewer task (gets reviewer feedback as context)
    - Has a focused goal: fix the specific issues found
    - Gets the original task's files_scope
    """
    issues = _extract_review_issues(reviewer_output)
    issues_text = "\n".join(f"  {i + 1}. {issue}" for i, issue in enumerate(issues[:10]))

    fix_goal = (
        f"FIX REVIEW ISSUES from {reviewer_task.id} ({reviewer_task.role.value}):\n"
        f"Original task: {dev_task.goal[:200]}\n\n"
        f"The reviewer found these issues in your code:\n{issues_text}\n\n"
        f"Fix ALL listed issues. Do NOT introduce new features or refactor "
        f"unrelated code. Focus ONLY on the issues above."
    )

    fix_task = TaskInput(
        id=f"fix_{dev_task.id}_r{task_counter}",
        role=dev_task.role,
        goal=fix_goal,
        depends_on=[reviewer_task.id],
        context_from=[reviewer_task.id, dev_task.id],
        files_scope=dev_task.files_scope,
        acceptance_criteria=[
            f"Fix issue: {issue[:100]}" for issue in issues[:5]
        ] + ["Do not break existing functionality"],
    )

    # Mark this as a review-fix task for cycle counting
    fix_task.review_fix_for = dev_task.id  # type: ignore[attr-defined]
    fix_task.is_remediation = False  # Not a remediation — it's a quality improvement

    return fix_task


def create_re_review_task(
    fix_task: TaskInput,
    original_reviewer: TaskInput,
    task_counter: int,
) -> TaskInput:
    """Create a re-review task to verify the fix.

    The re-review:
    - Has the same role as the original reviewer
    - Depends on the fix task
    - Has a focused goal: verify the specific fixes
    """
    re_review = TaskInput(
        id=f"rereview_{original_reviewer.id}_r{task_counter}",
        role=original_reviewer.role,
        goal=(
            f"RE-REVIEW: Verify that the fixes in {fix_task.id} properly address "
            f"the issues found in the original review ({original_reviewer.id}). "
            f"Focus ONLY on whether the reported issues are resolved. "
            f"Do NOT expand scope to find new issues."
        ),
        depends_on=[fix_task.id],
        context_from=[fix_task.id, original_reviewer.id],
        files_scope=original_reviewer.files_scope,
        acceptance_criteria=[
            "Verify all previously reported issues are fixed",
            "Confirm no regressions were introduced",
        ],
    )

    re_review.review_fix_for = fix_task.review_fix_for  # type: ignore[attr-defined]

    return re_review


async def inject_review_fix_tasks(
    reviewer_task: TaskInput,
    reviewer_output: TaskOutput,
    ctx: object,  # _ExecutionContext — avoid circular import
) -> bool:
    """Inject fix + optional re-review tasks into the live graph.

    Called from dag_executor after a reviewer task completes.

    Returns True if tasks were injected.
    """
    if not REVIEW_FIX_ENABLED:
        return False

    graph = ctx.graph  # type: ignore[attr-defined]
    completed = ctx.completed  # type: ignore[attr-defined]

    if not should_trigger_review_fix(
        reviewer_task, reviewer_output, graph.tasks, completed
    ):
        return False

    dev_task = _find_original_developer_task(
        reviewer_task, graph.tasks, completed
    )
    if dev_task is None:
        return False

    dev_output = completed.get(dev_task.id)
    if dev_output is None:
        return False

    issues = _extract_review_issues(reviewer_output)
    logger.info(
        "[ReviewFix] Triggering fix cycle: %s found %d issues in %s's work",
        reviewer_task.id,
        len(issues),
        dev_task.id,
    )

    async with ctx.graph_lock:  # type: ignore[attr-defined]
        ctx.task_counter += 1  # type: ignore[attr-defined]

        # Create fix task
        fix_task = create_fix_task(
            reviewer_task=reviewer_task,
            reviewer_output=reviewer_output,
            dev_task=dev_task,
            dev_output=dev_output,
            task_counter=ctx.task_counter,  # type: ignore[attr-defined]
        )
        graph.add_task(fix_task)

        logger.info(
            "[ReviewFix] Created fix task %s (%s) for %s",
            fix_task.id,
            fix_task.role.value,
            dev_task.id,
        )

        # Optionally create re-review task
        if REVIEW_FIX_RE_REVIEW:
            ctx.task_counter += 1  # type: ignore[attr-defined]
            re_review = create_re_review_task(
                fix_task=fix_task,
                original_reviewer=reviewer_task,
                task_counter=ctx.task_counter,  # type: ignore[attr-defined]
            )
            graph.add_task(re_review)

            logger.info(
                "[ReviewFix] Created re-review task %s (%s)",
                re_review.id,
                re_review.role.value,
            )

    # Record in healing history
    ctx.healing_history.append({  # type: ignore[attr-defined]
        "action": "review_fix_loop",
        "reviewer_task": reviewer_task.id,
        "developer_task": dev_task.id,
        "fix_task": fix_task.id,
        "issues_count": len(issues),
        "detail": (
            f"Review-fix loop: {reviewer_task.id} found {len(issues)} issues "
            f"in {dev_task.id} → created {fix_task.id}"
        ),
    })

    return True
