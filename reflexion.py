"""Reflexion Engine — self-critique layer for agent outputs.

Before an agent's output is accepted, the Reflexion Engine asks a lightweight
LLM call to critique the result against the original task requirements.  If
the critique identifies concrete issues, the agent gets one chance to fix them
in a "reflection turn" — reusing the same session so it has full context.

Research basis:
    Shinn et al. (2023) "Reflexion: Language Agents with Verbal Reinforcement
    Learning" — showed that adding a self-reflection step improves HumanEval
    pass rates from 80% to 91% and ALFWorld success from 75% to 97%.

Token cost:
    ~1,000–2,000 tokens per reflection (critique prompt + response).
    This is far cheaper than a full remediation cycle (~50,000+ tokens).

Integration:
    Called from ``dag_executor._run_single_task`` after Phase 2 (SUMMARY)
    but before the output is committed.  Only triggers when:
    1. REFLEXION_ENABLED is True (config flag)
    2. The task succeeded (no point reflecting on failures)
    3. Confidence is below REFLEXION_CONFIDENCE_THRESHOLD
    4. The task is not itself a remediation (avoid infinite loops)

Architecture:
    - Uses ``isolated_query`` with tools=[] (no tool use, just reasoning)
    - Reuses the agent's existing session for full context
    - Critique is structured: returns a JSON verdict with issues list
    - If issues found, a single "fix turn" is given to the agent
    - The fix turn reuses the same session with tools enabled
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import config as cfg
from contracts import TaskInput, TaskOutput

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
REFLEXION_ENABLED: bool = cfg._get("REFLEXION_ENABLED", "false", str).lower() == "true"
REFLEXION_CONFIDENCE_THRESHOLD: float = cfg._get("REFLEXION_CONFIDENCE_THRESHOLD", "0.95", float)
REFLEXION_MAX_FIX_TURNS: int = cfg._get("REFLEXION_MAX_FIX_TURNS", "10", int)
REFLEXION_CRITIQUE_BUDGET: float = cfg._get("REFLEXION_CRITIQUE_BUDGET", "2.0", float)


@dataclass
class ReflexionVerdict:
    """Result of the self-critique phase."""

    should_fix: bool
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence_adjustment: float = 0.0
    critique_cost_usd: float = 0.0
    critique_text: str = ""

    def summary(self) -> str:
        if not self.should_fix:
            return "Reflexion: output looks good, no fixes needed."
        return (
            f"Reflexion: found {len(self.issues)} issue(s). "
            f"Suggestions: {'; '.join(self.suggestions[:3])}"
        )


def should_reflect(task: TaskInput, output: TaskOutput) -> bool:
    """Determine whether a task output should go through Reflexion.

    Returns True if all conditions are met:
    1. Reflexion is enabled globally
    2. The task succeeded
    3. Confidence is below the threshold (high-confidence outputs skip)
    4. The task is not a remediation task (avoid reflection loops)
    """
    if not REFLEXION_ENABLED:
        return False
    if not output.is_successful():
        return False
    if output.confidence >= REFLEXION_CONFIDENCE_THRESHOLD:
        logger.debug(
            "[Reflexion] Skipping %s — confidence %.2f > threshold %.2f",
            task.id,
            output.confidence,
            REFLEXION_CONFIDENCE_THRESHOLD,
        )
        return False
    if task.is_remediation:
        logger.debug("[Reflexion] Skipping %s — remediation task", task.id)
        return False
    return True


def build_critique_prompt(task: TaskInput, output: TaskOutput) -> str:
    """Build the self-critique prompt for the Reflexion phase.

    The prompt asks the agent to evaluate its own work against the
    original acceptance criteria and identify concrete issues.
    """
    criteria_text = "\n".join(f"  - {c}" for c in (task.acceptance_criteria or []))
    if not criteria_text:
        criteria_text = "  - (No explicit criteria — use professional judgment)"

    artifacts_text = ", ".join(output.artifacts[:10]) if output.artifacts else "(none listed)"
    issues_text = "\n".join(f"  - {i}" for i in output.issues) if output.issues else "  (none)"

    return (
        "## SELF-REFLECTION PHASE\n\n"
        "You just completed a task. Before your work is accepted, critically "
        "evaluate what you did. Be honest — finding issues now is MUCH cheaper "
        "than a full remediation cycle later.\n\n"
        f"**Original Goal:** {task.goal}\n\n"
        f"**Acceptance Criteria:**\n{criteria_text}\n\n"
        f"**Your Summary:** {output.summary}\n\n"
        f"**Files Changed:** {artifacts_text}\n\n"
        f"**Known Issues:** \n{issues_text}\n\n"
        "Now answer these questions:\n"
        "1. Did you fully meet ALL acceptance criteria?\n"
        "2. Are there any edge cases you missed?\n"
        "3. Did you leave any TODO/FIXME/placeholder code?\n"
        "4. Are there any obvious bugs or type errors?\n"
        "5. Did you follow the project conventions visible in existing code?\n"
        "6. SCOPE CHECK: Did you ONLY change files required by the task goal? "
        "If you modified unrelated files (cleanup, refactoring, removing imports "
        "in files not in your task), that is an issue.\n"
        "7. CODE QUALITY: Are any files you changed over 500 lines? "
        "Did you duplicate logic that already exists elsewhere? "
        "Did you write boilerplate that could be a simple helper?\n\n"
        "Respond with ONLY this JSON (no markdown fences, no explanation):\n"
        "{\n"
        '  "verdict": "pass" or "needs_fix",\n'
        '  "issues": ["list of concrete issues found"],\n'
        '  "suggestions": ["specific fix for each issue"],\n'
        '  "confidence_adjustment": 0.0\n'
        "}\n\n"
        'If everything looks good, use "verdict": "pass" with empty lists.\n'
        'If you found real issues, use "verdict": "needs_fix" and list them.\n'
        "Do NOT invent problems — only flag genuine issues."
    )


def build_fix_prompt(verdict: ReflexionVerdict) -> str:
    """Build the prompt for the fix turn after a failing critique.

    The agent gets one chance to address the issues found during
    self-reflection, with full tool access.
    """
    issues_text = "\n".join(f"  {i + 1}. {issue}" for i, issue in enumerate(verdict.issues))
    suggestions_text = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(verdict.suggestions))

    return (
        "## REFLEXION FIX PHASE\n\n"
        "Your self-reflection found issues that need fixing. "
        "Address them now — this is your last chance before the output "
        "is committed.\n\n"
        f"**Issues Found:**\n{issues_text}\n\n"
        f"**Suggested Fixes:**\n{suggestions_text}\n\n"
        "Fix these issues using the available tools. Focus on the most "
        "critical issues first. When done, produce your updated JSON "
        "output block as before."
    )


def parse_critique_response(text: str) -> ReflexionVerdict:
    """Parse the LLM's critique response into a ReflexionVerdict.

    Handles both clean JSON and JSON embedded in markdown fences.
    Falls back to a "pass" verdict if parsing fails (fail-safe).
    """
    # Strip markdown fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last fence lines
        json_lines = []
        in_fence = False
        for line in lines:
            if line.strip().startswith("```") and not in_fence:
                in_fence = True
                continue
            if line.strip() == "```" and in_fence:
                break
            if in_fence:
                json_lines.append(line)
        cleaned = "\n".join(json_lines)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("[Reflexion] Failed to parse critique response, defaulting to pass")
                return ReflexionVerdict(should_fix=False, critique_text=text[:500])
        else:
            logger.warning("[Reflexion] No JSON found in critique response, defaulting to pass")
            return ReflexionVerdict(should_fix=False, critique_text=text[:500])

    verdict_str = data.get("verdict", "pass").lower().strip()
    issues = data.get("issues", [])
    suggestions = data.get("suggestions", [])
    confidence_adj = float(data.get("confidence_adjustment", 0.0))

    # Only flag for fix if there are actual concrete issues
    should_fix = verdict_str == "needs_fix" and len(issues) > 0

    return ReflexionVerdict(
        should_fix=should_fix,
        issues=issues if isinstance(issues, list) else [str(issues)],
        suggestions=suggestions if isinstance(suggestions, list) else [str(suggestions)],
        confidence_adjustment=confidence_adj,
        critique_text=text[:500],
    )


async def run_reflexion(
    task: TaskInput,
    output: TaskOutput,
    session_id: str | None,
    system_prompt: str,
    project_dir: str,
    sdk: object,
    on_stream: Callable | None = None,
) -> tuple[TaskOutput, ReflexionVerdict]:
    """Execute the full Reflexion cycle: critique + optional fix.

    Args:
        task: The original task input.
        output: The agent's current output (post Phase 2).
        session_id: The agent's session ID for context continuity.
        system_prompt: The agent's system prompt.
        project_dir: Working directory.
        sdk: The SDK client instance.

    Returns:
        Tuple of (possibly improved output, verdict with details).
    """
    from isolated_query import isolated_query

    t0 = time.monotonic()

    # ── Step 1: Self-Critique ──
    critique_prompt = build_critique_prompt(task, output)

    logger.info(
        "[Reflexion] Task %s: starting self-critique (session=%s)",
        task.id,
        "resume" if session_id else "new",
    )

    try:
        critique_response = await isolated_query(
            sdk,
            prompt=critique_prompt,
            system_prompt=system_prompt,
            cwd=project_dir,
            session_id=session_id,
            max_turns=3,  # Critique needs minimal turns
            max_budget_usd=REFLEXION_CRITIQUE_BUDGET,
            tools=[],  # No tools — pure reasoning
            max_retries=0,
            on_stream=on_stream,
        )
    except Exception as exc:
        logger.warning(
            "[Reflexion] Task %s: critique call failed (%s), skipping reflexion",
            task.id,
            exc,
        )
        return output, ReflexionVerdict(should_fix=False, critique_text=f"Error: {exc}")

    if critique_response.is_error:
        logger.warning(
            "[Reflexion] Task %s: critique returned error: %s",
            task.id,
            critique_response.error_message[:200],
        )
        return output, ReflexionVerdict(
            should_fix=False,
            critique_cost_usd=critique_response.cost_usd,
            critique_text=f"Error: {critique_response.error_message[:200]}",
        )

    verdict = parse_critique_response(critique_response.text)
    verdict.critique_cost_usd = critique_response.cost_usd

    critique_elapsed = time.monotonic() - t0
    logger.info(
        "[Reflexion] Task %s: critique done in %.1fs — verdict=%s, issues=%d, cost=$%.4f",
        task.id,
        critique_elapsed,
        "needs_fix" if verdict.should_fix else "pass",
        len(verdict.issues),
        verdict.critique_cost_usd,
    )

    # ── Step 2: If critique passed, boost confidence and return ──
    if not verdict.should_fix:
        # Reflexion passed — boost confidence slightly
        output.confidence = min(output.confidence + 0.05, 1.0)
        output.cost_usd += verdict.critique_cost_usd
        return output, verdict

    # ── Step 3: Fix Turn — agent addresses the issues ──
    fix_prompt = build_fix_prompt(verdict)
    fix_session = critique_response.session_id or session_id

    logger.info(
        "[Reflexion] Task %s: starting fix turn (%d issues, max_turns=%d)",
        task.id,
        len(verdict.issues),
        REFLEXION_MAX_FIX_TURNS,
    )

    try:
        fix_response = await isolated_query(
            sdk,
            prompt=fix_prompt,
            system_prompt=system_prompt,
            cwd=project_dir,
            session_id=fix_session,
            max_turns=REFLEXION_MAX_FIX_TURNS,
            max_budget_usd=REFLEXION_CRITIQUE_BUDGET * 2,
            max_retries=0,
            on_stream=on_stream,
        )
    except Exception as exc:
        logger.warning(
            "[Reflexion] Task %s: fix turn failed (%s), keeping original output",
            task.id,
            exc,
        )
        output.cost_usd += verdict.critique_cost_usd
        output.issues.extend(verdict.issues)
        return output, verdict

    fix_elapsed = time.monotonic() - t0 - critique_elapsed

    if fix_response.is_error:
        logger.warning(
            "[Reflexion] Task %s: fix turn returned error: %s",
            task.id,
            fix_response.error_message[:200],
        )
        output.cost_usd += verdict.critique_cost_usd + fix_response.cost_usd
        output.issues.extend(verdict.issues)
        return output, verdict

    # ── Step 4: Extract improved output from fix response ──
    from contracts import extract_task_output

    fix_output = extract_task_output(
        fix_response.text,
        task.id,
        task.role,
        tool_uses=fix_response.tool_uses if fix_response else None,
    )

    total_cost = output.cost_usd + verdict.critique_cost_usd + fix_response.cost_usd
    total_turns = output.turns_used + 3 + fix_response.num_turns  # 3 for critique

    logger.info(
        "[Reflexion] Task %s: fix turn done in %.1fs — "
        "new_status=%s, new_confidence=%.2f, fix_cost=$%.4f",
        task.id,
        fix_elapsed,
        fix_output.status.value,
        fix_output.confidence,
        fix_response.cost_usd,
    )

    # Use the fix output if it's better than the original
    if fix_output.is_successful() and fix_output.confidence >= output.confidence:
        fix_output.cost_usd = total_cost
        fix_output.turns_used = total_turns
        # Merge artifacts — fix may have changed additional files
        all_artifacts = list(set((output.artifacts or []) + (fix_output.artifacts or [])))
        fix_output.artifacts = all_artifacts
        logger.info(
            "[Reflexion] Task %s: using improved output (confidence %.2f -> %.2f)",
            task.id,
            output.confidence,
            fix_output.confidence,
        )
        return fix_output, verdict

    # Fix didn't improve things — keep original but add cost and issues
    output.cost_usd = total_cost
    output.turns_used = total_turns
    output.issues.extend([f"[Reflexion] {issue}" for issue in verdict.issues])
    logger.info(
        "[Reflexion] Task %s: fix did not improve output, keeping original",
        task.id,
    )
    return output, verdict
