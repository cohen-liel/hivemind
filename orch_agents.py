"""Agent execution: query routing, sub-agent scheduling, and file conflict detection.

Extracted from orchestrator.py to reduce file size.
All functions operate on an OrchestratorManager instance passed as ``mgr``.
This module handles:
  - _query_agent: routing queries to the correct agent with event-loop isolation
  - _run_sub_agents: smart scheduling (sequential writers, parallel readers)
  - _record_response: recording agent output in conversation log
  - _get_available_skills_summary: building skill info for the orchestrator
  - File-touch extraction and conflict detection
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator import Delegation, OrchestratorManager

import orch_context
import orch_review
from config import (
    AGENT_CANCEL_POLL_DELAY,
    AGENT_EMOJI,
    AGENT_RETRY_DELAY,
    AGENT_TIMEOUT_SECONDS,
    ASYNC_WAIT_TIMEOUT,
    MAX_BUDGET_USD,
    MAX_CANCEL_WAIT_RETRIES,
    MAX_ORCHESTRATOR_LOOPS,
    MAX_TURNS_PER_CYCLE,
    ORCHESTRATOR_SYSTEM_PROMPT,
    SDK_MAX_BUDGET_PER_QUERY,
    SDK_MAX_TURNS_PER_QUERY,
    SOLO_AGENT_PROMPT,
    get_agent_timeout,
    get_specialist_prompt,
)
from isolated_query import isolated_query
from project_context import build_project_header
from sdk_client import SDKResponse

logger = logging.getLogger(__name__)


# ── _query_agent ─────────────────────────────────────────────────────────


async def query_agent(
    mgr: OrchestratorManager,
    agent_role: str,
    prompt: str,
    skill_names: list[str] | None = None,
) -> SDKResponse:
    """Route a query to the appropriate agent with event-loop isolation.

    Sub-agents run via ``isolated_query()`` (separate thread + event loop)
    so that anyio's cancel-scope cleanup cannot leak CancelledError into
    the main event loop and poison sibling tasks.

    The orchestrator itself runs directly via ``query_with_retry()``
    because it always runs alone (no parallel siblings to poison).
    """
    from config import get_agent_config
    from skills_registry import build_skill_prompt, select_skills_for_task

    allowed_tools: list[str] | None = None
    tools: list[str] | None = None

    if agent_role == "orchestrator" and mgr.multi_agent:
        system_prompt = ORCHESTRATOR_SYSTEM_PROMPT
        skill_content = get_available_skills_summary()
        if skill_content:
            system_prompt += skill_content
        _orch_cfg = get_agent_config("orchestrator")
        max_turns = _orch_cfg.turns
        max_budget = _orch_cfg.budget
        permission_mode = "bypassPermissions"
        allowed_tools = [
            "Read",
            "Glob",
            "Grep",
            "LS",
            "Bash(git log*)",
            "Bash(git diff*)",
            "Bash(git status*)",
            "Bash(cat *)",
            "Bash(head *)",
            "Bash(tail *)",
            "Bash(wc *)",
            "Bash(find *)",
            "Bash(pytest*)",
            "Bash(python*-m*pytest*)",
            "Bash(npm test*)",
            "Bash(npx jest*)",
        ]
        logger.info(
            f"[{mgr.project_id}] Querying orchestrator (coordinator mode, read-only tools, max_turns={max_turns})"
        )
    elif agent_role == "orchestrator" and not mgr.multi_agent:
        system_prompt = SOLO_AGENT_PROMPT
        max_turns = SDK_MAX_TURNS_PER_QUERY
        max_budget = SDK_MAX_BUDGET_PER_QUERY
        permission_mode = "bypassPermissions"
        logger.info(f"[{mgr.project_id}] Querying orchestrator (solo mode, full tools)")
    else:
        system_prompt = get_specialist_prompt(agent_role, mode=mgr.mode)
        task_hint = prompt[:1000]
        auto_skills = select_skills_for_task(agent_role, task_hint, max_skills=2)
        all_skills = list(dict.fromkeys(list(skill_names or []) + auto_skills))
        if all_skills:
            skill_suffix = build_skill_prompt(list(dict.fromkeys(all_skills)))
            if skill_suffix:
                system_prompt += skill_suffix
        _sub_cfg = get_agent_config(agent_role)
        max_turns = _sub_cfg.turns
        max_budget = _sub_cfg.budget
        permission_mode = "bypassPermissions"
        logger.info(
            f"[{mgr.project_id}] Querying sub-agent '{agent_role}' (max_turns={max_turns}, budget=${max_budget}, skills={all_skills or 'none'})"
        )

    # Prepend project boundary + context
    _boundary_header = build_project_header(mgr.project_name, mgr.project_dir)
    system_prompt = _boundary_header + system_prompt

    # Log system prompt token usage for observability
    _prompt_tokens = max(1, len(system_prompt) // 4)  # ~4 chars/token heuristic
    logger.info(
        f"[{mgr.project_id}] System prompt for '{agent_role}': "
        f"~{_prompt_tokens:,} tokens ({len(system_prompt):,} chars)"
    )

    # Try to resume session
    session_id = await mgr.session_mgr.get_session(mgr.user_id, mgr.project_id, agent_role)

    # Stream callback
    async def on_stream(text: str):
        """Handle a streaming text chunk from the Claude SDK."""
        emoji = AGENT_EMOJI.get(agent_role, "\U0001f527")
        await mgr._notify(f"{emoji} *{agent_role}*\n{text[-500:]}")
        if agent_role in mgr.agent_states:
            mgr.agent_states[agent_role]["last_activity_at"] = time.time()
            mgr.agent_states[agent_role]["last_activity_type"] = "stream"
            mgr._silence_alerted.discard(agent_role)

    # Tool use callback
    async def on_tool_use(tool_name: str, tool_info: str, tool_input: dict):
        """Handle a tool-use event from the Claude SDK."""
        mgr.current_tool = tool_info
        if agent_role in mgr.agent_states:
            mgr.agent_states[agent_role]["current_tool"] = tool_info
            count = mgr.agent_states[agent_role].get("tool_count", 0) + 1
            mgr.agent_states[agent_role]["tool_count"] = count
            mgr.agent_states[agent_role]["last_activity_at"] = time.time()
            mgr.agent_states[agent_role]["last_activity_type"] = "tool_use"
            mgr._silence_alerted.discard(agent_role)
        await mgr._emit_event(
            "tool_use",
            agent=agent_role,
            tool_name=tool_name,
            description=tool_info,
            input=tool_input,
            timestamp=time.time(),
        )
        await mgr._emit_event(
            "agent_update",
            agent=agent_role,
            text=tool_info,
            timestamp=time.time(),
        )

    # Event-loop isolation decision
    use_isolation = agent_role != "orchestrator"

    if use_isolation:
        role_timeout = get_agent_timeout(agent_role)
        response = await isolated_query(
            mgr.sdk,
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=mgr.project_dir,
            session_id=session_id,
            max_turns=max_turns,
            max_budget_usd=max_budget,
            permission_mode=permission_mode,
            on_stream=on_stream,
            on_tool_use=on_tool_use,
            allowed_tools=allowed_tools,
            tools=tools,
            per_message_timeout=role_timeout,
        )
    else:
        response = await mgr.sdk.query_with_retry(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=mgr.project_dir,
            session_id=session_id,
            max_turns=max_turns,
            max_budget_usd=max_budget,
            permission_mode=permission_mode,
            on_stream=on_stream,
            on_tool_use=on_tool_use,
            allowed_tools=allowed_tools,
            tools=tools,
        )

    # Save session for future resume
    if response.session_id and not response.is_error:
        await mgr.session_mgr.save_session(
            mgr.user_id,
            mgr.project_id,
            agent_role,
            response.session_id,
            response.cost_usd,
            response.num_turns,
        )
    elif response.is_error and session_id:
        error_lower = response.error_message.lower()
        if "session" in error_lower or "resume" in error_lower:
            await mgr.session_mgr.invalidate_session(mgr.user_id, mgr.project_id, agent_role)
    return response


# ── _record_response ─────────────────────────────────────────────────────


def record_response(mgr: OrchestratorManager, agent_name: str, role: str, response: SDKResponse):
    """Record an agent response in the conversation log and update costs."""
    from orchestrator import Message

    mgr.total_cost_usd += response.cost_usd
    mgr._agents_used.add(agent_name)
    mgr.conversation_log.append(
        Message(
            agent_name=agent_name,
            role=role,
            content=response.text,
            cost_usd=response.cost_usd,
        )
    )
    mgr._create_background_task(
        mgr.session_mgr.add_message(
            mgr.project_id,
            agent_name,
            role,
            response.text,
            response.cost_usd,
        )
    )


# ── _get_available_skills_summary ────────────────────────────────────────


def get_available_skills_summary() -> str:
    """Build a summary of available skills for the orchestrator to reference."""
    from skills_registry import SKILL_AGENT_MAP, list_skills

    skills = list_skills()
    if not skills:
        return ""
    lines = ["AVAILABLE SKILLS \u2014 you can request these via the 'skills' field in delegation:"]
    for skill_name in skills:
        mapped_agent = SKILL_AGENT_MAP.get(skill_name, "developer")
        lines.append(f"  - {skill_name} (best suited for: {mapped_agent})")
    lines.append(
        "\nTo use a skill, add a 'skills' array to your delegation JSON:\n"
        "<delegate>\n"
        '{"agent": "developer", "task": "...", "skills": ["frontend-design"]}\n'
        "</delegate>"
    )
    return "\n".join(lines)


# ── _run_sub_agents ──────────────────────────────────────────────────────


async def run_sub_agents(
    mgr: OrchestratorManager,
    delegations: list[Delegation],
) -> dict[str, list[SDKResponse]]:
    """Execute sub-agent tasks with smart scheduling.

    Code-modifying agents (developer, devops) run SEQUENTIALLY to avoid
    conflicting file changes (the Cognition/Devin insight). Read-only
    agents (reviewer, tester, researcher) run in PARALLEL after writers finish.

    Failed agents are automatically retried once with extra context.
    """
    from config import get_all_role_names

    by_role: dict[str, list] = {}
    results: dict[str, list[SDKResponse]] = {}
    for d in delegations:
        all_known = get_all_role_names(include_legacy=True)
        if d.agent not in all_known:
            logger.warning(f"Unknown sub-agent role: {d.agent}, skipping")
            results.setdefault("\u26a0 Invalid Role", []).append(
                SDKResponse(
                    text=(
                        f"Delegation to unknown role '{d.agent}' was skipped.\n"
                        f"Valid roles are: {', '.join(sorted(all_known))}.\n"
                        f"Task was: {d.task[:200]}"
                    ),
                    is_error=True,
                    error_message=f"Unknown agent role: {d.agent}",
                )
            )
            continue
        by_role.setdefault(d.agent, []).append(d)

    lock = asyncio.Lock()
    files_touched: dict[str, set[str]] = {}

    async def run_role(agent_role: str, role_delegations: list):
        """Run all delegations for a single role (sequentially)."""
        for delegation in role_delegations:
            if mgr._stop_event.is_set():
                break

            _limit_msg: str | None = None
            async with lock:
                if mgr.turn_count >= MAX_TURNS_PER_CYCLE:
                    _limit_msg = (
                        f"\u23f0 Turn limit reached ({MAX_TURNS_PER_CYCLE}) \u2014 "
                        f"skipping remaining sub-agents.\n"
                        f"Use /resume to continue."
                    )
                elif mgr.total_cost_usd >= mgr._effective_budget:
                    _limit_msg = (
                        f"\U0001f4b0 Budget limit reached (${mgr.total_cost_usd:.4f} / ${mgr._effective_budget:.2f}) \u2014 "
                        f"skipping remaining sub-agents.\n"
                        f"Use /resume to continue."
                    )
                else:
                    mgr.turn_count += 1
            if _limit_msg:
                await mgr._notify(_limit_msg)
                return

            _emoji = AGENT_EMOJI.get(agent_role, "\U0001f527")
            await mgr._notify(f"{_emoji} *{agent_role}* is working on:\n_{delegation.task[:200]}_")

            mgr.current_agent = agent_role
            mgr.agent_states[agent_role] = {
                "state": "working",
                "task": delegation.task[:300],
                "last_activity_at": time.time(),
                "last_activity_type": "started",
            }
            mgr._silence_alerted.discard(agent_role)
            await mgr._emit_event(
                "agent_started",
                agent=agent_role,
                task=delegation.task[:300],
            )
            agent_start = time.monotonic()

            # Build sub-agent prompt
            sub_prompt = (
                f"Project: {mgr.project_name}\n"
                f"Working directory: {mgr.project_dir}\n\n"
                f"Task: {delegation.task}"
            )
            if delegation.context:
                sub_prompt += f"\n\nContext: {delegation.context}"

            async with lock:
                agent_context = orch_context.get_context_for_agent(mgr, agent_role)
                if agent_context:
                    sub_prompt += f"\n\n{agent_context}"

            workspace = await asyncio.to_thread(mgr._get_workspace_context)
            if workspace:
                sub_prompt += f"\n\n{workspace}"

            async with lock:
                conflicts = detect_file_conflicts(files_touched)
            if conflicts:
                conflict_lines = [
                    f"  {f}: touched by {', '.join(agents)}" for f, agents in conflicts.items()
                ]
                sub_prompt += (
                    "\n\n\u26a0\ufe0f FILE CONFLICT WARNING: The following files were already "
                    "modified by another agent this session. Read the CURRENT version "
                    "of these files before making any changes:\n" + "\n".join(conflict_lines)
                )

            # Sub-agent heartbeat
            async def _sub_heartbeat(role=agent_role, start=agent_start):
                _last_real_tool = ""
                _last_tool_time = time.monotonic()
                _tool_count = 0
                while True:
                    await asyncio.sleep(AGENT_RETRY_DELAY)
                    elapsed = int(time.monotonic() - start)
                    state_info = mgr.agent_states.get(role, {})
                    real_tool = state_info.get("current_tool", "")
                    tool_count = state_info.get("tool_count", 0)

                    if real_tool and (real_tool != _last_real_tool or tool_count != _tool_count):
                        _last_real_tool = real_tool
                        _last_tool_time = time.monotonic()
                        _tool_count = tool_count
                        status = f"{real_tool} ({elapsed}s)"
                    else:
                        stale_secs = int(time.monotonic() - _last_tool_time)
                        if _last_real_tool and stale_secs < 15:
                            status = f"{_last_real_tool} ({elapsed}s)"
                        elif _last_real_tool:
                            status = f"waiting for response... ({elapsed}s)"
                        elif elapsed < 10:
                            status = f"starting up... ({elapsed}s)"
                        else:
                            status = f"waiting for Claude response... ({elapsed}s)"

                    state_info.update(
                        {
                            "state": "working",
                            "current_tool": status,
                        }
                    )
                    mgr.agent_states[role] = state_info
                    await mgr._emit_event(
                        "agent_update",
                        agent=role,
                        text=f"{AGENT_EMOJI.get(role, chr(0x1F527))} {status}",
                        summary=f"{AGENT_EMOJI.get(role, chr(0x1F527))} {role}: {status}",
                    )

            _hb_task = asyncio.create_task(_sub_heartbeat())

            try:
                response = await query_agent(
                    mgr, agent_role, sub_prompt, skill_names=delegation.skills
                )
            finally:
                _hb_task.cancel()
                try:
                    await _hb_task
                except asyncio.CancelledError:
                    pass

            # Auto-retry once on failure
            if response.is_error and not mgr._stop_event.is_set():
                error_msg = response.error_message
                logger.warning(
                    f"[{mgr.project_id}] Agent '{agent_role}' failed: {error_msg}. "
                    f"Retrying with enriched context..."
                )
                await mgr._notify(
                    f"\U0001f504 *{agent_role}* failed, retrying with more context..."
                )
                await mgr._emit_event(
                    "agent_started",
                    agent=agent_role,
                    task=f"[RETRY] {delegation.task[:250]}",
                )
                await mgr.session_mgr.invalidate_session(mgr.user_id, mgr.project_id, agent_role)
                workspace_now = await asyncio.to_thread(mgr._get_workspace_context)
                error_lower = error_msg.lower()

                if "permission" in error_lower or "eperm" in error_lower:
                    hint = "Check file permissions. Try reading the file first to confirm it exists and is accessible."
                elif (
                    "not found" in error_lower
                    or "no such file" in error_lower
                    or "enoent" in error_lower
                ):
                    hint = "The file or path does not exist. List the directory first (ls) to see what's actually there."
                elif "syntax" in error_lower or "parse" in error_lower or "invalid" in error_lower:
                    hint = "There is a syntax or parsing error. Read the file carefully before editing. Check line numbers in the error."
                elif "timeout" in error_lower or "timed out" in error_lower:
                    hint = "The operation timed out. Try a simpler/faster approach, or break it into smaller steps."
                elif "import" in error_lower or "module" in error_lower or "package" in error_lower:
                    hint = "A dependency is missing. Check requirements.txt or package.json. Try pip install or npm install first."
                elif "connection" in error_lower or "network" in error_lower:
                    hint = "Network or connection issue. Check if the service is running. Try a local alternative."
                else:
                    hint = "Try a completely different approach. The previous method did not work."

                retry_prompt = (
                    f"[RETRY \u2014 previous attempt failed]\n"
                    f"Error: {error_msg}\n\n"
                    f"Diagnosis: {hint}\n\n"
                    f"Before retrying:\n"
                    f"1. Read the error message carefully \u2014 understand WHY it failed\n"
                    f"2. Check your assumptions (file exists? correct path? right syntax?)\n"
                    f"3. Try the simplest possible fix first\n\n"
                    f"Original task: {delegation.task}\n"
                )
                if delegation.context:
                    retry_prompt += f"\nContext: {delegation.context}\n"
                if workspace_now:
                    retry_prompt += f"\n{workspace_now}\n"
                response = await query_agent(
                    mgr, agent_role, retry_prompt, skill_names=delegation.skills
                )

            async with lock:
                record_response(mgr, agent_role, agent_role.capitalize(), response)
                results.setdefault(agent_role, []).append(response)
                await orch_context.accumulate_context(mgr, agent_role, delegation.task, response)
                touched = extract_touched_files(response.text)
                files_touched.setdefault(agent_role, set()).update(touched)

            # Emit agent_finished event
            agent_duration = time.monotonic() - agent_start
            prev_state = mgr.agent_states.get(agent_role, {})
            prev_state.update(
                {
                    "state": "error" if response.is_error else "done",
                    "task": delegation.task[:300],
                    "cost": response.cost_usd,
                    "turns": response.num_turns,
                    "duration": agent_duration,
                }
            )
            mgr.agent_states[agent_role] = prev_state
            _failure_reason = ""
            if response.is_error:
                _failure_reason = (
                    response.error_message or response.text[:200] or "Unknown error"
                ).strip()
            await mgr._emit_event(
                "agent_finished",
                agent=agent_role,
                cost=response.cost_usd,
                turns=response.num_turns,
                duration=round(agent_duration, 1),
                is_error=response.is_error,
                failure_reason=_failure_reason,
            )

            summary = response.text[:2500]
            if len(response.text) > 2500:
                summary += "\n... (truncated)"

            if "tool use" in summary.lower() and "no text output" in summary.lower():
                changed = await orch_review.detect_file_changes(mgr)
                if changed:
                    summary += f"\n\nFiles changed:\n{changed}"

            try:
                await mgr.session_mgr.record_agent_performance(
                    project_id=mgr.project_id,
                    agent_role=agent_role,
                    status="error" if response.is_error else "success",
                    duration_seconds=agent_duration,
                    cost_usd=response.cost_usd,
                    turns_used=response.num_turns,
                    task_description=delegation.task[:500],
                    error_message=response.error_message[:500] if response.is_error else "",
                    round_number=mgr._current_loop,
                )
            except Exception as perf_err:
                logger.debug(f"[{mgr.project_id}] Failed to record agent perf: {perf_err}")

            status_icon = "\u2705" if not response.is_error else "\u26a0\ufe0f"
            emoji = AGENT_EMOJI.get(agent_role, "\U0001f527")
            dur_str = f" ({response.duration_ms // 1000}s)" if response.duration_ms > 0 else ""
            await mgr._send_result(
                f"{status_icon}{emoji} *{agent_role}* finished{dur_str}\n"
                f"\U0001f4b0 ${response.cost_usd:.4f} | Turns: {response.num_turns}\n\n"
                f"{summary}"
            )

    # ═══ SMART SCHEDULING ═══
    writer_roles = {r: d for r, d in by_role.items() if r in mgr._WRITER_ROLES}
    reader_roles = {r: d for r, d in by_role.items() if r in mgr._READER_ROLES}
    for r, d in by_role.items():
        if r not in mgr._WRITER_ROLES and r not in mgr._READER_ROLES:
            reader_roles[r] = d

    async def _heartbeat():
        elapsed = 0
        while True:
            await asyncio.sleep(AGENT_CANCEL_POLL_DELAY)
            elapsed += 8
            working_details = []
            for name, info in list(mgr.agent_states.items()):
                if info.get("state") != "working":
                    continue
                _hb_emoji = AGENT_EMOJI.get(name, "\U0001f527")
                detail = f"{_hb_emoji} {name}"
                tool = info.get("current_tool")
                task = info.get("task", "")
                if tool:
                    detail += f" \u2192 {tool}"
                elif task:
                    detail += f": {task[:80]}"
                else:
                    detail += f" (running {elapsed}s)"
                working_details.append(detail)

            if working_details:
                for name, info in list(mgr.agent_states.items()):
                    if info.get("state") == "working":
                        tool = info.get("current_tool", "")
                        task = info.get("task", "")
                        status_text = (
                            tool
                            if tool
                            else (f"Working on: {task[:100]}" if task else f"Running ({elapsed}s)")
                        )
                        await mgr._emit_event(
                            "agent_update",
                            agent=name,
                            text=status_text,
                            summary=f"{AGENT_EMOJI.get(name, chr(0x1F527))} {name}: {status_text}",
                            timestamp=time.time(),
                        )
                await mgr._emit_event(
                    "loop_progress",
                    loop=mgr._current_loop,
                    max_loops=MAX_ORCHESTRATOR_LOOPS,
                    turn=mgr.turn_count,
                    max_turns=MAX_TURNS_PER_CYCLE,
                    cost=mgr.total_cost_usd,
                    max_budget=MAX_BUDGET_USD,
                )

    async def _isolated_run_role(role, dels):
        try:
            await run_role(role, dels)
        except asyncio.CancelledError:
            if mgr._stop_event.is_set():
                raise
            logger.warning(
                f"[{mgr.project_id}] Agent '{role}' was cancelled (not by stop). Treating as error."
            )
            async with lock:
                results.setdefault(role, []).append(
                    SDKResponse(
                        text=f"Agent '{role}' was cancelled unexpectedly.",
                        is_error=True,
                        error_message="Cancelled unexpectedly",
                    )
                )
        except RuntimeError as e:
            if "cancel scope" in str(e):
                logger.warning(
                    f"[{mgr.project_id}] Agent '{role}' hit anyio "
                    f"cancel scope bug (contained by isolation): {e}"
                )
            else:
                raise

    async def _run_roles_parallel(roles_dict: dict[str, list]):
        if not roles_dict:
            return
        if len(roles_dict) == 1:
            role, dels = next(iter(roles_dict.items()))
            await _isolated_run_role(role, dels)
            return

        _role_tasks: dict[str, asyncio.Task] = {}
        for role, dels in roles_dict.items():
            task = asyncio.create_task(
                _isolated_run_role(role, dels),
                name=f"agent-{role}",
            )
            _role_tasks[role] = task

        _max_role_timeout = (
            max(get_agent_timeout(r) for r in roles_dict.keys())
            if roles_dict
            else AGENT_TIMEOUT_SECONDS
        )
        _wait_timeout = _max_role_timeout + 120
        remaining = set(_role_tasks.values())
        still_pending = set()
        _wait_cancel_retries = 0
        # Use MAX_CANCEL_WAIT_RETRIES from config
        while remaining:
            try:
                _done, still_pending = await asyncio.wait(
                    remaining,
                    return_when=asyncio.ALL_COMPLETED,
                    timeout=_wait_timeout,
                )
                remaining = set()
            except asyncio.CancelledError:
                if mgr._stop_event.is_set():
                    raise
                _drained = mgr._drain_cancellations()
                remaining = {t for t in remaining if not t.done()}
                if not remaining:
                    break
                _wait_cancel_retries += 1
                if _wait_cancel_retries > MAX_CANCEL_WAIT_RETRIES:
                    logger.error(
                        f"[{mgr.project_id}] Too many CancelledErrors in asyncio.wait "
                        f"({_wait_cancel_retries}) \u2014 force-cancelling {len(remaining)} remaining agent(s)"
                    )
                    for t in remaining:
                        t.cancel()
                    try:
                        await asyncio.wait(remaining, timeout=ASYNC_WAIT_TIMEOUT)
                    except asyncio.CancelledError:
                        raise
                    except Exception as _wait_err:
                        logger.debug(
                            "[%s] asyncio.wait during force-cancel raised unexpectedly: %s",
                            mgr.project_id,
                            _wait_err,
                        )
                    remaining = set()
                    break
                if _wait_cancel_retries <= 3:
                    logger.warning(
                        f"[{mgr.project_id}] Spurious CancelledError in asyncio.wait \u2014 "
                        f"{len(remaining)} agent(s) still running, re-waiting... "
                        f"(attempt {_wait_cancel_retries}/{MAX_CANCEL_WAIT_RETRIES})"
                    )
                await asyncio.sleep(min(0.1 * (2**_wait_cancel_retries), 5.0))
                continue

        if still_pending:
            logger.warning(
                f"[{mgr.project_id}] {len(still_pending)} agent task(s) timed out "
                f"after {_wait_timeout}s \u2014 cancelling"
            )
            for t in still_pending:
                t.cancel()
            await asyncio.wait(still_pending, timeout=ASYNC_WAIT_TIMEOUT)

        for role_name, task in _role_tasks.items():
            if task.cancelled():
                logger.warning(f"[{mgr.project_id}] Agent role '{role_name}' was cancelled")
                async with lock:
                    results.setdefault(role_name, []).append(
                        SDKResponse(
                            text=f"Agent '{role_name}' was cancelled.",
                            is_error=True,
                            error_message="Task cancelled",
                        )
                    )
            elif task.exception() is not None:
                exc = task.exception()
                logger.error(
                    f"[{mgr.project_id}] Agent role '{role_name}' raised exception: {exc}",
                    exc_info=exc,
                )
                await mgr._send_result(
                    f"\u26a0\ufe0f *{role_name}* crashed unexpectedly: {exc}\n"
                    f"The orchestrator will be notified to handle this."
                )
                async with lock:
                    results.setdefault(role_name, []).append(
                        SDKResponse(
                            text=f"Agent crashed with exception: {exc}",
                            is_error=True,
                            error_message=str(exc),
                        )
                    )

    heartbeat_task = asyncio.create_task(_heartbeat())
    try:
        # Phase 1: Writers SEQUENTIALLY
        if writer_roles:
            writer_count = sum(len(d) for d in writer_roles.values())
            await mgr._notify(
                f"\U0001f4dd Running {writer_count} code-modifying task(s) sequentially "
                f"({', '.join(writer_roles.keys())})..."
            )
            for role, dels in writer_roles.items():
                if mgr._stop_event.is_set():
                    break
                await _isolated_run_role(role, dels)

        # Phase 2: Readers IN PARALLEL
        if reader_roles and not mgr._stop_event.is_set():
            reader_count = sum(len(d) for d in reader_roles.values())
            await mgr._notify(
                f"\U0001f50d Running {reader_count} verification task(s) in parallel "
                f"({', '.join(reader_roles.keys())})..."
            )
            await _run_roles_parallel(reader_roles)

    except asyncio.CancelledError:
        if mgr._stop_event.is_set():
            raise
        else:
            _drained = mgr._drain_cancellations()
            logger.warning(
                f"[{mgr.project_id}] _run_sub_agents got SPURIOUS CancelledError "
                f"(anyio cancel-scope leak). Drained {_drained} cancellations. Continuing..."
            )

    finally:
        heartbeat_task.cancel()
        try:
            await asyncio.wait_for(heartbeat_task, timeout=ASYNC_WAIT_TIMEOUT)
        except (TimeoutError, asyncio.CancelledError):
            pass

    mgr.current_agent = None
    mgr.current_tool = None

    # Detect file conflicts between parallel agents
    if len(files_touched) > 1:
        conflicts = detect_file_conflicts(files_touched)
        if conflicts:
            conflict_msg = "\u26a0\ufe0f FILE CONFLICTS DETECTED:\n"
            for file_path, agents in conflicts.items():
                conflict_msg += f"  \u2022 {file_path} \u2014 modified by: {', '.join(agents)}\n"
            conflict_msg += "\nThe orchestrator will be informed to resolve these conflicts."
            await mgr._notify(conflict_msg)
            logger.warning(f"[{mgr.project_id}] File conflicts: {conflicts}")
            results.setdefault("\u26a0 File Conflicts", []).append(
                SDKResponse(
                    text=conflict_msg,
                    is_error=True,
                    error_message=f"File conflicts in: {', '.join(conflicts.keys())}",
                )
            )

    return results


# ── File touch extraction & conflict detection ───────────────────────────


def extract_touched_files(text: str) -> set[str]:
    """Extract file paths that an agent likely modified from its output."""
    touched = set()
    for line in text.split("\n"):
        lower = line.lower().strip()
        if any(
            w in lower
            for w in (
                "writing:",
                "editing:",
                "created:",
                "modified:",
                "wrote to",
                "\u270f\ufe0f",
                "\U0001f527 editing",
                "updated file",
                "created file",
            )
        ):
            for token in line.split():
                cleaned = token.strip("`\"',;:()[]{}")
                if ("/" in cleaned or "." in cleaned) and len(cleaned) > 3:
                    if not cleaned.startswith("http") and not cleaned.startswith("//"):
                        touched.add(cleaned)
    return touched


def detect_file_conflicts(files_touched: dict[str, set[str]]) -> dict[str, list[str]]:
    """Detect files modified by multiple agents (potential conflicts)."""
    file_to_agents: dict[str, list[str]] = {}
    for agent, files in files_touched.items():
        for f in files:
            file_to_agents.setdefault(f, []).append(agent)
    return {f: agents for f, agents in file_to_agents.items() if len(agents) > 1}
