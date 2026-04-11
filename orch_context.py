"""Context management for the orchestrator.

Extracted from orchestrator.py to reduce file size.
All functions operate on an OrchestratorManager instance passed as `mgr`.
This module handles:
  - Accumulating shared context from agent responses
  - Priority-based context trimming (12K token budget)
  - Building smart context summaries for sub-agents
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from config import MAX_SHARED_CONTEXT_ENTRIES, SUBPROCESS_SHORT_TIMEOUT

if TYPE_CHECKING:
    from sdk_client import SDKResponse

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────
# Maximum token budget for shared_context
CONTEXT_TOKEN_BUDGET = 12_000

# Priority levels (higher = more important, never trimmed first)
PRIORITY_CRITICAL = 3  # FAILED/ERROR/BLOCKED — always kept
PRIORITY_HIGH = 2  # NEEDS_FOLLOWUP, warnings
PRIORITY_RECENT = 2  # Last 3 rounds — always kept
PRIORITY_INFO = 0  # Informational/success — trimmed first


# ── Token estimation ──────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses a simple heuristic: ~4 characters per token (conservative).
    This avoids importing tiktoken while staying within ±15% accuracy
    for English text typical in agent context.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def classify_context_priority(entry: str) -> int:
    """Classify a shared_context entry by priority level.

    Returns:
        3 (CRITICAL) — FAILED, ERROR, BLOCKED entries (never trim)
        2 (HIGH)     — NEEDS_FOLLOWUP, warnings, recent rounds
        0 (INFO)     — Success/informational entries (trim first)

    Delegates to the unified classifier in blackboard.classify_complexity().
    """
    from blackboard import classify_complexity

    result = classify_complexity(context_entry=entry)
    return result.context_priority


def compress_context_entry(entry: str) -> str:
    """Compress a context entry to its essential information.

    Keeps: role/status header, status line, issues, file changes.
    Truncates: raw output, verbose descriptions.
    Falls back to the first 300 chars of the entry if nothing matches,
    so we never silently return an empty string.
    """
    if not entry:
        return entry
    lines = entry.split("\n")
    essential = []
    for line in lines:
        ls = line.strip()
        if ls.startswith(("[", "Status:", "Files changed:", "Issues:", "Commands:")):
            essential.append(line[:200])
        elif ls.startswith("Output:"):
            essential.append(line[:120])
        elif ls.startswith("Test results:"):
            essential.append(line[:150])
        elif ls.startswith("Diff summary:"):
            essential.append(line[:120])
        elif len(essential) < 4:
            essential.append(line[:150])
    # Fallback: if compression produced nothing, return first 300 chars
    if not essential:
        return entry[:300]
    return "\n".join(essential)


# ── Core context operations ───────────────────────────────────────────


async def accumulate_context(mgr, agent_role: str, task: str, response: SDKResponse):
    """Build rich shared context from an agent's response.

    Called under lock. Creates structured context entries that help
    other agents and the orchestrator understand what was done.
    Uses git diff --stat for reliable file tracking instead of regex.
    """
    text = response.text

    # Use git for reliable file change detection instead of fragile regex
    files_changed_git = []
    git_diff_snippet = ""
    try:

        async def _git_acc(*args: str) -> str:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=mgr.project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SUBPROCESS_SHORT_TIMEOUT)
            return stdout.decode("utf-8", errors="replace")

        diff_names = await _git_acc("diff", "--name-only", "HEAD")
        if diff_names.strip():
            files_changed_git = [f.strip() for f in diff_names.strip().split("\n") if f.strip()]
        untracked = await _git_acc("ls-files", "--others", "--exclude-standard")
        if untracked.strip():
            files_changed_git.extend(
                [f"(new) {f.strip()}" for f in untracked.strip().split("\n") if f.strip()]
            )
        if files_changed_git:
            stat_out = await _git_acc("diff", "--stat", "HEAD")
            if stat_out.strip():
                git_diff_snippet = stat_out.strip()[:300]
    except Exception as _exc:
        logger.debug("[Orchestrator] non-fatal exception suppressed: %s", _exc)

    # Fallback: parse text for file operations if git didn't find anything
    files_from_text = []
    commands_run = []
    test_results = []
    for line in text.split("\n"):
        lower = line.lower().strip()
        if any(
            w in lower for w in ("created file", "wrote to", "writing:", "✏️ writing", "created:")
        ):
            for token in line.split():
                if "/" in token or ("." in token and len(token) > 3):
                    cleaned = token.strip("`\"',;:()[]")
                    if cleaned and not cleaned.startswith("http"):
                        files_from_text.append(cleaned)
        elif any(w in lower for w in ("edited", "modified", "updated", "🔧 editing")):
            for token in line.split():
                if "/" in token or ("." in token and len(token) > 3):
                    cleaned = token.strip("`\"',;:()[]")
                    if cleaned and not cleaned.startswith("http"):
                        files_from_text.append(cleaned)
        elif any(w in lower for w in ("running:", "💻 running", "executed:", "$ ")):
            cmd = line.strip()[:80]
            if cmd:
                commands_run.append(cmd)
        elif any(
            w in lower
            for w in (
                "test passed",
                "tests passed",
                "all tests",
                "test failed",
                "tests failed",
                "assertion",
            )
        ):
            test_results.append(line.strip()[:120])

    # Build structured context entry
    ctx_parts = [f"[{agent_role}] Round {mgr._current_loop} | Task: {task[:200]}"]
    if response.is_error:
        ctx_parts.append(f"  Status: FAILED — {response.error_message[:200]}")
    else:
        ctx_parts.append(
            f"  Status: SUCCESS ({response.num_turns} turns, ${response.cost_usd:.4f})"
        )

    files_info = files_changed_git if files_changed_git else files_from_text
    if files_info:
        ctx_parts.append(f"  Files changed: {', '.join(files_info[:12])}")
    if git_diff_snippet:
        ctx_parts.append(f"  Diff summary: {git_diff_snippet[:200]}")
    if commands_run:
        ctx_parts.append(f"  Commands: {'; '.join(commands_run[:5])}")
    if test_results:
        ctx_parts.append(f"  Test results: {'; '.join(test_results[:3])}")

    # Include key output summary
    summary = ""
    status_line = ""
    issues = ""
    for marker in ["## SUMMARY", "## Summary", "### Summary", "## Result", "### Changes"]:
        idx = text.find(marker)
        if idx >= 0:
            end = text.find("\n## ", idx + len(marker))
            summary = text[idx : end if end > idx else idx + 400].strip()
            break
    for sm in ["## STATUS", "## Status"]:
        idx = text.find(sm)
        if idx >= 0:
            for line in text[idx : idx + 200].strip().split("\n")[1:]:
                if line.strip():
                    status_line = line.strip()[:150]
                    break
            break
    for im in ["## ISSUES FOUND", "## Issues Found"]:
        idx = text.find(im)
        if idx >= 0:
            lines = [
                l.strip()
                for l in text[idx : idx + 400].strip().split("\n")[1:]
                if l.strip() and l.strip() not in ("(or: none)", "none")
            ]
            if lines:
                issues = "; ".join(lines[:3])
            break
    if not summary:
        summary = text[:400].strip()
    if summary:
        ctx_parts.append(f"  Output: {summary[:400]}")
    if status_line:
        ctx_parts.append(f"  Status: {status_line}")
    if issues:
        ctx_parts.append(f"  Issues: {issues}")

    mgr.shared_context.append("\n".join(ctx_parts))
    trim_context_by_priority(mgr)


def trim_context_by_priority(mgr) -> None:
    """Trim shared_context using priority-based strategy.

    Priority rules (in order):
    1. Task ledger summary — always kept (injected separately)
    2. Last 3 rounds — always kept regardless of priority
    3. CRITICAL/FAILED items — always kept
    4. INFORMATIONAL items — trimmed first (oldest first)
    """
    if not mgr.shared_context:
        return

    # Absolute cap — hard limit regardless of priority to prevent unbounded growth
    if len(mgr.shared_context) > MAX_SHARED_CONTEXT_ENTRIES:
        mgr.shared_context = mgr.shared_context[-MAX_SHARED_CONTEXT_ENTRIES:]

    total_tokens = sum(estimate_tokens(e) for e in mgr.shared_context)
    if total_tokens <= CONTEXT_TOKEN_BUDGET and len(mgr.shared_context) <= 30:
        return

    tagged: list[tuple[int, int, int, int, str]] = []
    num_entries = len(mgr.shared_context)
    for i, entry in enumerate(mgr.shared_context):
        priority = classify_context_priority(entry)
        round_num = 0
        if "Round " in entry:
            try:
                round_part = entry.split("Round ")[1].split()[0].strip("|").strip()
                round_num = int(round_part)
            except (IndexError, ValueError):
                pass
        if i >= num_entries - 3:
            priority = max(priority, PRIORITY_RECENT)
        tokens = estimate_tokens(entry)
        tagged.append((i, priority, round_num, tokens, entry))

    removable = sorted(
        [
            (i, p, r, t, e)
            for i, p, r, t, e in tagged
            if p < PRIORITY_CRITICAL and i < num_entries - 3
        ],
        key=lambda x: (x[1], x[2]),
    )

    kept_set: set[int] = set(range(num_entries))

    for idx, _pri, _rnd, tok, entry in removable:
        if total_tokens <= CONTEXT_TOKEN_BUDGET:
            break
        compressed = compress_context_entry(entry)
        compressed_tokens = estimate_tokens(compressed)
        savings = tok - compressed_tokens
        if savings > 50:
            mgr.shared_context[idx] = compressed
            total_tokens -= savings
        else:
            kept_set.discard(idx)
            total_tokens -= tok

    if total_tokens > CONTEXT_TOKEN_BUDGET:
        for idx, pri, _rnd, _tok, _orig_entry in tagged:
            if idx not in kept_set:
                continue
            if total_tokens <= CONTEXT_TOKEN_BUDGET:
                break
            if pri >= PRIORITY_CRITICAL and idx < num_entries - 3:
                # Read the CURRENT content (may already be compressed from the
                # first pass above) so we don't double-compress stale data.
                current_entry = mgr.shared_context[idx]
                compressed = compress_context_entry(current_entry)
                compressed_tokens = estimate_tokens(compressed)
                current_tokens = estimate_tokens(current_entry)
                if compressed_tokens < current_tokens:
                    mgr.shared_context[idx] = compressed
                    total_tokens -= current_tokens - compressed_tokens

    mgr.shared_context = [mgr.shared_context[i] for i in sorted(kept_set)]


def get_context_for_agent(mgr, agent_role: str) -> str:
    """Build a smart context summary for a sub-agent.

    Uses priority-based selection with 12K token budget enforcement:
    1. Task ledger summary — always included (from manifest)
    2. Last 3 rounds — always included
    3. CRITICAL/FAILED items — always included
    4. Agent's own history — included when space permits
    5. INFORMATIONAL items — included last, trimmed first
    """
    if not mgr.shared_context:
        return ""

    critical_entries = []
    followup_entries = []
    own_history = []
    recent_entries = []
    info_entries = []

    num_entries = len(mgr.shared_context)
    for i, ctx in enumerate(mgr.shared_context):
        is_recent = i >= num_entries - 3
        priority = classify_context_priority(ctx)

        if is_recent:
            recent_entries.append(ctx)
        elif priority >= PRIORITY_CRITICAL:
            critical_entries.append(ctx)
        elif "NEEDS_FOLLOWUP" in ctx:
            followup_entries.append(ctx)
        elif f"[{agent_role}]" in ctx:
            own_history.append(ctx)
        else:
            info_entries.append(ctx)

    manifest = mgr._read_project_manifest()
    manifest_tokens = estimate_tokens(manifest) if manifest else 0
    available_tokens = CONTEXT_TOKEN_BUDGET - manifest_tokens

    selected: list[str] = []
    tokens_used = 0

    def _add_entries(entries: list[str], max_count: int | None = None) -> None:
        nonlocal tokens_used
        to_add = entries[-max_count:] if max_count else entries
        for entry in to_add:
            compressed = compress_context_entry(entry)
            entry_tokens = estimate_tokens(compressed)
            if tokens_used + entry_tokens <= available_tokens:
                selected.append(compressed)
                tokens_used += entry_tokens
            elif tokens_used < available_tokens:
                lines = compressed.split("\n")
                mini = "\n".join(lines[:2])
                mini_tokens = estimate_tokens(mini)
                if tokens_used + mini_tokens <= available_tokens:
                    selected.append(mini)
                    tokens_used += mini_tokens

    _add_entries(recent_entries)
    _add_entries(critical_entries)
    _add_entries(followup_entries, max_count=3)
    _add_entries(own_history, max_count=3)
    remaining_info = sorted(info_entries, key=lambda e: e, reverse=True)
    _add_entries(remaining_info, max_count=max(0, 8 - len(selected)))

    if not selected:
        return ""

    sections: list[str] = []
    if manifest:
        sections.append(
            "─── PROJECT MANIFEST (team's persistent memory) ───\n"
            + manifest
            + "\n───────────────────────────────────────────────────"
        )
    if selected:
        sections.append("Context from previous rounds:\n" + "\n---\n".join(selected))

    return "\n\n".join(sections) if sections else ""
