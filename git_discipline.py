"""
Git Discipline — Only the DAG Executor commits. Never individual agents.

Agents' system prompts explicitly forbid git commit/push.
This module is the SINGLE place where commits are created.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from config import SUBPROCESS_MEDIUM_TIMEOUT
from contracts import TaskOutput, TaskStatus

logger = logging.getLogger(__name__)

# Patterns that must never be staged by the auto-committer.
# These protect secrets, certificates, credential files, and Hivemind-internal
# metadata from accidentally being committed to project repositories.
_SENSITIVE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*secret*",
    "*credential*",
    "*credentials*",
    "*password*",
    "*.aws/credentials",
    "*.ssh/id_*",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    # Hivemind agent metadata — must never enter project commits
    ".hivemind/*",
    ".hivemind/**",
    "hivemind_*.log",
    "hivemind_*.tmp",
    "me_file*",
    "*.hivemind.json",
    # Agent-generated reports/reviews — work products, not source code
    "*REVIEW*",
    "*_REPORT*",
    "*_report*",
    "reviews/*",
    "reviews/**",
    "REVIEW_*.md",
    "*.review.md",
    # Orchestration metadata — plans, tasks, notes
    "*.plan.md",
    "notes.json",
    ".notes.json",
    "NOTES.md",
    "task_*.json",
    "plans/*",
    "plans/**",
    "tasks/*",
    "tasks/**",
)


def _is_sensitive(filepath: str) -> bool:
    """Return True if *filepath* matches any known sensitive file pattern."""
    from fnmatch import fnmatch

    name = Path(filepath).name
    # Block anything inside .hivemind/, plans/, or tasks/ directories entirely
    normalized = filepath.replace("\\", "/")
    if normalized.startswith(".hivemind/") or normalized == ".hivemind":
        return True
    if normalized.startswith("plans/") or normalized.startswith("tasks/"):
        return True
    # Match against both the full relative path and just the filename
    return any(fnmatch(filepath, pat) or fnmatch(name, pat) for pat in _SENSITIVE_PATTERNS)


_git_locks: dict[str, asyncio.Lock] = {}


def _git_lock(project_dir: str) -> asyncio.Lock:
    """Get or create a per-project git lock to prevent concurrent commits."""
    if project_dir not in _git_locks:
        _git_locks[project_dir] = asyncio.Lock()
    return _git_locks[project_dir]


async def commit_single_task(
    project_dir: str,
    output: TaskOutput,
    task_goal: str = "",
    task_role: str = "",
) -> str | None:
    """
    Auto-commit changes after a single task completes.

    Returns the short commit hash, or None if there was nothing to commit.
    Uses a per-project lock to prevent concurrent git operations.

    When the task output lists specific artifacts, ONLY those files are
    staged — preventing unrelated changes from leaking into the commit.
    """
    if not output or not output.is_successful():
        return None

    # Collect the file paths this task claims to have changed
    scoped_files = [f for f in (output.artifacts or []) if not _is_sensitive(f)] or None

    async with _git_lock(project_dir):
        return await _do_commit(
            project_dir,
            [output],
            task_id=output.task_id,
            scoped_files=scoped_files,
            task_goal=task_goal,
            task_role=task_role,
        )


async def executor_commit(
    project_dir: str,
    round_outputs: list[TaskOutput],
    round_num: int,
) -> str | None:
    """
    Fallback: commit any remaining unstaged changes after a DAG round.

    In normal flow, commit_single_task handles per-task commits.
    This catches anything that slipped through.
    """
    if not round_outputs:
        return None

    async with _git_lock(project_dir):
        return await _do_commit(project_dir, round_outputs, round_num=round_num)


async def _do_commit(
    project_dir: str,
    outputs: list[TaskOutput],
    task_id: str = "",
    round_num: int = 0,
    scoped_files: list[str] | None = None,
    task_goal: str = "",
    task_role: str = "",
) -> str | None:
    """Internal: perform the actual git add + commit. Caller must hold the lock.

    Args:
        scoped_files: When provided, ONLY these files are staged instead of
            ``git add -u``. This keeps single-task commits focused on the
            files the agent actually changed, preventing unrelated
            modifications from leaking in.
    """

    proj = Path(project_dir)
    if not (proj / ".git").exists():
        logger.debug("[git] No .git directory, skipping auto-commit")
        return None

    # Check if there's anything to commit
    status = await _run(["git", "status", "--porcelain"], cwd=project_dir)
    if not status.strip():
        return None  # Nothing to commit

    if scoped_files:
        # Scoped commit: only stage files this task actually changed
        await _stage_scoped_files(project_dir, scoped_files)
    else:
        # Broad commit (round fallback): stage everything safely
        await _stage_files_safely(project_dir)

    # After selective staging, check again — we might have excluded everything
    staged = await _run(["git", "diff", "--cached", "--name-only"], cwd=project_dir)
    if not staged.strip():
        logger.debug("[git] All changes were sensitive files — nothing to commit")
        return None

    # Build commit message from actual staged files + task context
    staged_files = [f.strip() for f in staged.strip().splitlines() if f.strip()]

    # Pre-commit verification: check Python files compile
    import py_compile as _pyc

    _syntax_warnings: list[str] = []
    for sf in staged_files:
        if sf.endswith(".py"):
            full_path = str(proj / sf)
            try:
                _pyc.compile(full_path, doraise=True)
            except _pyc.PyCompileError as e:
                _syntax_warnings.append(f"{sf}: {e}")
                logger.warning("[git] Syntax error in staged file %s: %s", sf, e)

    message = _build_commit_message(
        outputs,
        round_num,
        task_id,
        staged_files=staged_files,
        task_goal=task_goal,
        task_role=task_role,
        syntax_warnings=_syntax_warnings,
    )

    await _run(["git", "commit", "-m", message], cwd=project_dir)

    # Extract short hash
    hash_result = await _run(["git", "rev-parse", "--short", "HEAD"], cwd=project_dir)
    short_hash = hash_result.strip()

    label = f"task {task_id}" if task_id else f"round {round_num}"
    logger.info(f"[git] Auto-committed {label}: {short_hash}")
    return short_hash


async def _stage_scoped_files(project_dir: str, scoped_files: list[str]) -> None:
    """Stage only the files the task claims to have changed.

    Each file is validated against _SENSITIVE_PATTERNS before staging.
    Files that don't exist or have no changes are silently skipped by git.
    """
    staged = 0
    for filepath in scoped_files:
        if _is_sensitive(filepath):
            logger.warning("[git] Skipping sensitive scoped file: %s", filepath)
            continue
        result = await _run(["git", "add", "--", filepath], cwd=project_dir)
        if result is not None:
            staged += 1
    if staged:
        logger.debug("[git] Scoped staging: %d/%d files staged", staged, len(scoped_files))


async def _stage_files_safely(project_dir: str) -> None:
    """Stage project changes while excluding known-sensitive file patterns.

    Strategy:
    1. ``git add -u`` — stages modifications and deletions of already-tracked
       files.  Tracked files were deliberately added previously and are already
       in the repository, so re-staging their changes is safe.
    2. Enumerate untracked files (``??`` in ``git status --porcelain``) and add
       each one individually only if it does NOT match _SENSITIVE_PATTERNS.
       This prevents an agent-created ``.env`` or ``*.key`` from sneaking into
       a commit.
    """
    # Step 1: stage tracked changes (modifications + deletions)
    await _run(["git", "add", "-u"], cwd=project_dir)

    # Step 1b: unstage any tracked metadata files that shouldn't be committed
    await _unstage_metadata_files(project_dir)

    # Step 2: enumerate untracked files and add safe ones
    raw = await _run(
        ["git", "status", "--porcelain", "-z"],
        cwd=project_dir,
    )
    entries = [e.strip() for e in raw.split("\0") if e.strip()]
    skipped: list[str] = []
    for entry in entries:
        if not entry.startswith("?? "):
            continue  # Already tracked/staged by step 1
        filepath = entry[3:]  # Strip the "?? " prefix
        if _is_sensitive(filepath):
            skipped.append(filepath)
            logger.warning("[git] Skipping sensitive file from auto-commit: %s", filepath)
        else:
            await _run(["git", "add", "--", filepath], cwd=project_dir)

    if skipped:
        logger.warning(
            "[git] %d sensitive file(s) excluded from auto-commit: %s",
            len(skipped),
            skipped,
        )


async def _unstage_metadata_files(project_dir: str) -> None:
    """Unstage any tracked metadata/sensitive files from the index.

    After ``git add -u`` stages all tracked modifications, this function
    checks the staged diff for files matching _SENSITIVE_PATTERNS and removes
    them from the index (``git rm --cached``) so they won't be committed.
    The files remain on disk — only the git index entry is removed.
    """
    staged = await _run(["git", "diff", "--cached", "--name-only"], cwd=project_dir)
    if not staged.strip():
        return
    unstaged: list[str] = []
    for filepath in staged.strip().splitlines():
        filepath = filepath.strip()
        if filepath and _is_sensitive(filepath):
            await _run(["git", "reset", "HEAD", "--", filepath], cwd=project_dir)
            unstaged.append(filepath)
            logger.info("[git] Unstaged tracked metadata file: %s", filepath)
    if unstaged:
        logger.info(
            "[git] %d tracked metadata file(s) removed from staging: %s",
            len(unstaged),
            unstaged,
        )


def _infer_commit_type(staged_files: list[str], task_role: str, summary: str) -> str:
    """Infer conventional commit type from changed files and context."""
    lower_summary = summary.lower()
    names = [Path(f).name.lower() for f in staged_files]
    dirs = {Path(f).parts[0].lower() for f in staged_files if len(Path(f).parts) > 1}

    if (
        any(
            n.startswith("test_") or n.startswith("test.") or "_test." in n or ".test." in n
            for n in names
        )
        or "tests" in dirs
        or task_role == "test_engineer"
    ):
        return "test"
    if (
        any(n in ("dockerfile", "docker-compose.yml", ".github", "ci.yml") for n in names)
        or task_role == "devops"
    ):
        return "ci"
    if any(n.endswith(".md") for n in names) and all(n.endswith(".md") for n in names):
        return "docs"
    if "fix" in lower_summary or "bug" in lower_summary or "repair" in lower_summary:
        return "fix"
    if "refactor" in lower_summary:
        return "refactor"
    if "style" in lower_summary or task_role == "ux_critic":
        return "style"
    if "security" in lower_summary or task_role == "security_auditor":
        return "security"
    if "review" in lower_summary or task_role == "reviewer":
        return "chore"
    return "feat"


def _infer_scope(staged_files: list[str]) -> str:
    """Infer a short scope from the staged files (e.g. 'auth', 'api', 'ui')."""
    if not staged_files:
        return ""
    dirs = []
    for f in staged_files:
        parts = Path(f).parts
        if len(parts) > 1:
            dirs.append(parts[0])
        else:
            dirs.append(Path(f).stem)

    # If all files share a common directory, use it as scope
    if dirs and len(set(dirs)) == 1:
        scope = dirs[0]
        # Shorten common patterns
        if scope in ("src", "lib", "app"):
            # Use subdirectory if available
            subdirs = [Path(f).parts[1] for f in staged_files if len(Path(f).parts) > 2]
            if subdirs and len(set(subdirs)) == 1:
                scope = subdirs[0]
        return scope[:20]

    # Multiple directories — pick the most common
    if dirs:
        from collections import Counter

        most_common = Counter(dirs).most_common(1)[0][0]
        return most_common[:20]

    return ""


def _summarize_first_line(task_goal: str, summary: str, max_len: int = 60) -> str:
    """Pick the best short description for the first line of the commit."""
    # Prefer task_goal — it's what was asked, which is more descriptive
    # than the agent's summary which tends to be generic
    text = task_goal or summary or "update code"

    # Clean up: remove markdown, leading emojis, "Task:" prefixes
    text = text.strip().lstrip("*#- ")
    # Lowercase first char for conventional commit style
    if text and text[0].isupper() and not text[:2].isupper():
        text = text[0].lower() + text[1:]

    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0]

    return text


def _build_commit_message(
    outputs: list[TaskOutput],
    round_num: int = 0,
    task_id: str = "",
    staged_files: list[str] | None = None,
    task_goal: str = "",
    task_role: str = "",
    syntax_warnings: list[str] | None = None,
) -> str:
    """Build a structured commit message from task outputs and actual staged files."""
    successful = [o for o in outputs if o.status == TaskStatus.COMPLETED]
    failed = [o for o in outputs if o.status == TaskStatus.FAILED]
    files = staged_files or []

    # Single-task commit: clean conventional commit
    if task_id and len(successful) == 1:
        o = successful[0]
        commit_type = _infer_commit_type(files, task_role, o.summary)
        scope = _infer_scope(files)
        desc = _summarize_first_line(task_goal, o.summary)
        scope_part = f"({scope})" if scope else ""
        first_line = f"{commit_type}{scope_part}: {desc}"

        body_lines = []
        if o.summary and task_goal and o.summary != task_goal:
            body_lines.append(f"\n{o.summary[:200]}")
        if files:
            body_lines.append(f"\nFiles ({len(files)}):")
            for f in files[:10]:
                body_lines.append(f"  - {f}")
            if len(files) > 10:
                body_lines.append(f"  ... and {len(files) - 10} more")
        body_lines.append(
            f"\nTask: {o.task_id} [{task_role}]" if task_role else f"\nTask: {o.task_id}"
        )
        if syntax_warnings:
            body_lines.append(f"\n⚠️ SYNTAX WARNINGS ({len(syntax_warnings)}):")
            for w in syntax_warnings[:5]:
                body_lines.append(f"  - {w[:120]}")
        return first_line + "\n" + "\n".join(body_lines)

    # Multi-task round commit
    if len(successful) == 1:
        o = successful[0]
        commit_type = _infer_commit_type(files, task_role, o.summary)
        desc = _summarize_first_line(task_goal, o.summary)
        first_line = f"{commit_type}: {desc}"
    elif successful:
        first_line = f"feat: complete {len(successful)} tasks (round {round_num})"
    else:
        first_line = f"wip: round {round_num} (partial — {len(failed)} failed)"

    body_lines: list[str] = []
    for o in successful:
        body_lines.append(f"  - [{o.task_id}] {o.summary[:100]}")
    for o in failed:
        body_lines.append(f"  - [{o.task_id}] FAILED: {'; '.join(o.issues[:2])[:80]}")

    if files:
        body_lines.append(f"\nFiles ({len(files)}):")
        for f in files[:15]:
            body_lines.append(f"  - {f}")
        if len(files) > 15:
            body_lines.append(f"  ... and {len(files) - 15} more")

    if syntax_warnings:
        body_lines.append(f"\n⚠️ SYNTAX WARNINGS ({len(syntax_warnings)}):")
        for w in syntax_warnings[:5]:
            body_lines.append(f"  - {w[:120]}")

    return first_line + "\n" + "\n".join(body_lines)


async def _run(cmd: list[str], cwd: str) -> str:
    """Run a subprocess command and return stdout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=SUBPROCESS_MEDIUM_TIMEOUT
        )
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
