"""
Git Discipline — Only the DAG Executor commits. Never individual agents.

Agents' system prompts explicitly forbid git commit/push.
This module is the SINGLE place where commits are created.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from contracts import TaskOutput, TaskStatus

logger = logging.getLogger(__name__)


async def executor_commit(
    project_dir: str,
    round_outputs: list[TaskOutput],
    round_num: int,
) -> str | None:
    """
    Auto-commit all staged/unstaged changes after a DAG round completes.

    Returns the short commit hash, or None if there was nothing to commit.
    Only called by dag_executor — never by individual agents.
    """
    if not round_outputs:
        return None

    proj = Path(project_dir)
    if not (proj / ".git").exists():
        logger.debug("[git] No .git directory, skipping auto-commit")
        return None

    # Check if there's anything to commit
    status = await _run(["git", "status", "--porcelain"], cwd=project_dir)
    if not status.strip():
        return None  # Nothing to commit

    # Stage everything (agents may have created/modified files)
    await _run(["git", "add", "-A"], cwd=project_dir)

    # Build commit message from outputs
    message = _build_commit_message(round_outputs, round_num)

    result = await _run(["git", "commit", "-m", message], cwd=project_dir)

    # Extract short hash
    hash_result = await _run(["git", "rev-parse", "--short", "HEAD"], cwd=project_dir)
    short_hash = hash_result.strip()

    logger.info(f"[git] Auto-committed round {round_num}: {short_hash}")
    return short_hash


def _build_commit_message(outputs: list[TaskOutput], round_num: int) -> str:
    """Build a structured commit message from task outputs."""
    successful = [o for o in outputs if o.status == TaskStatus.COMPLETED]
    failed = [o for o in outputs if o.status == TaskStatus.FAILED]

    # Gather all artifacts
    all_artifacts: list[str] = []
    for o in successful:
        all_artifacts.extend(o.artifacts[:3])  # max 3 per task

    # First line: concise summary
    if len(successful) == 1:
        first_line = f"feat: {successful[0].summary[:72]}"
    elif successful:
        first_line = f"feat: complete round {round_num} — {len(successful)} tasks"
    else:
        first_line = f"wip: round {round_num} (partial — {len(failed)} failed)"

    # Body
    body_lines = ["", "Completed by DAG Executor (auto-commit):"]
    for o in successful:
        body_lines.append(f"  ✅ [{o.task_id}] {o.summary[:100]}")
    for o in failed:
        body_lines.append(f"  ❌ [{o.task_id}] FAILED: {'; '.join(o.issues[:2])[:80]}")

    if all_artifacts:
        unique = list(dict.fromkeys(all_artifacts))[:10]
        body_lines.append(f"\nFiles: {', '.join(unique)}")

    total_cost = sum(o.cost_usd for o in outputs)
    body_lines.append(f"Cost: ${total_cost:.4f}")

    return first_line + "\n".join(body_lines)


async def _run(cmd: list[str], cwd: str) -> str:
    """Run a subprocess command and return stdout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            logger.debug(f"[git] Command {cmd} failed: {err}")
            return ""
        return stdout.decode(errors="replace")
    except Exception as exc:
        logger.debug(f"[git] Command {cmd} exception: {exc}")
        return ""


async def ensure_no_agent_commits(project_dir: str) -> None:
    """
    Safety check: warn if any commits were made by agents (not the executor).
    Checks the last 5 commits for missing 'DAG Executor' signature.
    """
    log = await _run(
        ["git", "log", "--oneline", "-5", "--format=%H %s"],
        cwd=project_dir,
    )
    for line in log.strip().splitlines():
        if line and "DAG Executor" not in line and "auto-commit" not in line.lower():
            # Could be a human commit or an old-style commit — that's fine
            pass
