"""Reflexion Engine — iterative self-critique layer for agent outputs.

Before an agent's output is accepted, the Reflexion Engine runs an iterative
critique-fix loop: the agent critiques its own work, fixes issues, then
re-critiques the fix — repeating until the output passes or the iteration
budget is exhausted.

Research basis:
    Shinn et al. (2023) "Reflexion: Language Agents with Verbal Reinforcement
    Learning" — showed that adding a self-reflection step improves HumanEval
    pass rates from 80% to 91% and ALFWorld success from 75% to 97%.

    Multi-pass extension: Madaan et al. (2023) "Self-Refine: Iterative
    Refinement with Self-Feedback" — showed that 2-3 iterations of
    self-feedback consistently outperform single-pass reflection across
    code generation, math reasoning, and dialogue tasks.

Token cost:
    ~1,000–2,000 tokens per reflection iteration (critique prompt + response).
    With max 3 iterations: ~3,000–6,000 tokens total.
    Still far cheaper than a full remediation cycle (~50,000+ tokens).

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
    - If issues found, a fix turn is given to the agent with full tools
    - After fix, a RE-CRITIQUE validates the fix (multi-pass)
    - Loop continues until: pass, max iterations reached, or no improvement
    - Each iteration tracks cost and issues for full audit trail
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import config as cfg
from contracts import TaskInput, TaskOutput

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
REFLEXION_ENABLED: bool = cfg._get("REFLEXION_ENABLED", "true", str).lower() == "true"
REFLEXION_CONFIDENCE_THRESHOLD: float = cfg._get("REFLEXION_CONFIDENCE_THRESHOLD", "0.95", float)
REFLEXION_MAX_FIX_TURNS: int = cfg._get("REFLEXION_MAX_FIX_TURNS", "10", int)
REFLEXION_CRITIQUE_BUDGET: float = cfg._get("REFLEXION_CRITIQUE_BUDGET", "2.0", float)
REFLEXION_MAX_ITERATIONS: int = cfg._get("REFLEXION_MAX_ITERATIONS", "3", int)


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


@dataclass
class ReflexionTrace:
    """Full audit trail of the multi-pass reflexion loop."""

    iterations: int = 0
    total_critique_cost: float = 0.0
    total_fix_cost: float = 0.0
    verdicts: list[ReflexionVerdict] = field(default_factory=list)
    issues_found_per_iteration: list[int] = field(default_factory=list)
    final_verdict: ReflexionVerdict | None = None
    converged: bool = False  # True if loop ended with a "pass" verdict

    def summary(self) -> str:
        status = "converged (pass)" if self.converged else f"stopped after {self.iterations} iterations"
        total_issues = sum(self.issues_found_per_iteration)
        return (
            f"Reflexion: {self.iterations} iteration(s), {status}, "
            f"{total_issues} total issues found, "
            f"cost=${self.total_critique_cost + self.total_fix_cost:.4f}"
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


def build_critique_prompt(task: TaskInput, output: TaskOutput, iteration: int = 1) -> str:
    """Build the self-critique prompt for the Reflexion phase.

    The prompt asks the agent to evaluate its own work against the
    original acceptance criteria and identify concrete issues.

    Args:
        task: The original task input.
        output: The agent's current output.
        iteration: Which iteration of the critique loop (1-based).
    """
    criteria_text = "\n".join(f"  - {c}" for c in (task.acceptance_criteria or []))
    if not criteria_text:
        criteria_text = "  - (No explicit criteria — use professional judgment)"

    artifacts_text = ", ".join(output.artifacts[:10]) if output.artifacts else "(none listed)"
    issues_text = "\n".join(f"  - {i}" for i in output.issues) if output.issues else "  (none)"

    iteration_context = ""
    if iteration > 1:
        iteration_context = (
            f"\n**⚠️ This is iteration {iteration} of self-reflection.**\n"
            f"You already attempted to fix issues in the previous iteration. "
            f"Focus ONLY on issues that remain UNFIXED. If your previous fix "
            f"resolved the issues, respond with verdict: pass.\n"
            f"Do NOT re-report issues that have already been fixed.\n"
        )

    return (
        "## SELF-REFLECTION PHASE\n\n"
        "You just completed a task. Before your work is accepted, critically "
        "evaluate what you did. Be honest — finding issues now is MUCH cheaper "
        "than a full remediation cycle later.\n\n"
        f"{iteration_context}"
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


def build_fix_prompt(verdict: ReflexionVerdict, iteration: int = 1) -> str:
    """Build the prompt for the fix turn after a failing critique.

    Args:
        verdict: The critique verdict with issues and suggestions.
        iteration: Which iteration of the fix loop (1-based).
    """
    issues_text = "\n".join(f"  {i + 1}. {issue}" for i, issue in enumerate(verdict.issues))
    suggestions_text = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(verdict.suggestions))

    urgency = ""
    if iteration > 1:
        urgency = (
            f"\n**⚠️ This is fix attempt #{iteration}.** Previous fixes did not fully "
            f"resolve all issues. Focus on the REMAINING issues listed below. "
            f"Be thorough — this may be your last chance.\n"
        )

    return (
        "## REFLEXION FIX PHASE\n\n"
        "Your self-reflection found issues that need fixing. "
        "Address them now.\n\n"
        f"{urgency}"
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
) -> tuple[TaskOutput, ReflexionVerdict]:
    """Execute the iterative multi-pass Reflexion cycle.

    Runs up to REFLEXION_MAX_ITERATIONS of: critique → fix → re-critique.
    Stops early if critique passes or fix doesn't improve output.

    Args:
        task: The original task input.
        output: The agent's current output (post Phase 2).
        session_id: The agent's session ID for context continuity.
        system_prompt: The agent's system prompt.
        project_dir: Working directory.
        sdk: The SDK client instance.

    Returns:
        Tuple of (possibly improved output, final verdict with details).
    """
    from isolated_query import isolated_query

    trace = ReflexionTrace()
    current_output = output
    current_session = session_id
    accumulated_cost = 0.0
    accumulated_turns = 0

    t0 = time.monotonic()

    for iteration in range(1, REFLEXION_MAX_ITERATIONS + 1):
        trace.iterations = iteration
        iter_t0 = time.monotonic()

        # ── Step 1: Self-Critique ──
        critique_prompt = build_critique_prompt(task, current_output, iteration=iteration)

        logger.info(
            "[Reflexion] Task %s: iteration %d/%d — starting critique (session=%s)",
            task.id,
            iteration,
            REFLEXION_MAX_ITERATIONS,
            "resume" if current_session else "new",
        )

        try:
            critique_response = await isolated_query(
                sdk,
                prompt=critique_prompt,
                system_prompt=system_prompt,
                cwd=project_dir,
                session_id=current_session,
                max_turns=3,  # Critique needs minimal turns
                max_budget_usd=REFLEXION_CRITIQUE_BUDGET,
                tools=[],  # No tools — pure reasoning
                max_retries=0,
            )
        except Exception as exc:
            logger.warning(
                "[Reflexion] Task %s: iteration %d critique failed (%s), stopping",
                task.id,
                iteration,
                exc,
            )
            break

        if critique_response.is_error:
            logger.warning(
                "[Reflexion] Task %s: iteration %d critique error: %s",
                task.id,
                iteration,
                critique_response.error_message[:200],
            )
            accumulated_cost += critique_response.cost_usd
            break

        verdict = parse_critique_response(critique_response.text)
        verdict.critique_cost_usd = critique_response.cost_usd
        accumulated_cost += critique_response.cost_usd
        accumulated_turns += 3

        trace.verdicts.append(verdict)
        trace.total_critique_cost += critique_response.cost_usd
        trace.issues_found_per_iteration.append(len(verdict.issues))

        # Update session for continuity
        if critique_response.session_id:
            current_session = critique_response.session_id

        critique_elapsed = time.monotonic() - iter_t0
        logger.info(
            "[Reflexion] Task %s: iteration %d critique done in %.1fs — "
            "verdict=%s, issues=%d, cost=$%.4f",
            task.id,
            iteration,
            critique_elapsed,
            "needs_fix" if verdict.should_fix else "pass",
            len(verdict.issues),
            verdict.critique_cost_usd,
        )

        # ── If critique passed, we're done ──
        if not verdict.should_fix:
            trace.converged = True
            trace.final_verdict = verdict
            # Boost confidence — passed self-critique
            confidence_boost = 0.05 * iteration  # More iterations passed = more confidence
            current_output.confidence = min(current_output.confidence + confidence_boost, 1.0)
            logger.info(
                "[Reflexion] Task %s: PASSED at iteration %d (confidence boosted by +%.2f)",
                task.id,
                iteration,
                confidence_boost,
            )
            break

        # ── Step 2: Fix Turn ──
        fix_prompt = build_fix_prompt(verdict, iteration=iteration)
        fix_session = critique_response.session_id or current_session

        logger.info(
            "[Reflexion] Task %s: iteration %d fix turn (%d issues, max_turns=%d)",
            task.id,
            iteration,
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
            )
        except Exception as exc:
            logger.warning(
                "[Reflexion] Task %s: iteration %d fix failed (%s), stopping",
                task.id,
                iteration,
                exc,
            )
            current_output.issues.extend(verdict.issues)
            trace.final_verdict = verdict
            break

        fix_elapsed = time.monotonic() - iter_t0 - critique_elapsed
        accumulated_cost += fix_response.cost_usd
        accumulated_turns += fix_response.num_turns
        trace.total_fix_cost += fix_response.cost_usd

        # Update session for next iteration
        if fix_response.session_id:
            current_session = fix_response.session_id

        if fix_response.is_error:
            logger.warning(
                "[Reflexion] Task %s: iteration %d fix error: %s",
                task.id,
                iteration,
                fix_response.error_message[:200],
            )
            current_output.issues.extend(verdict.issues)
            trace.final_verdict = verdict
            break

        # ── Step 3: Extract improved output ──
        from contracts import extract_task_output

        fix_output = extract_task_output(
            fix_response.text,
            task.id,
            task.role.value,
            tool_uses=fix_response.tool_uses if fix_response else None,
        )

        logger.info(
            "[Reflexion] Task %s: iteration %d fix done in %.1fs — "
            "new_status=%s, new_confidence=%.2f, fix_cost=$%.4f",
            task.id,
            iteration,
            fix_elapsed,
            fix_output.status.value,
            fix_output.confidence,
            fix_response.cost_usd,
        )

        # Use the fix output if it's better
        if fix_output.is_successful() and fix_output.confidence >= current_output.confidence:
            # Merge artifacts
            all_artifacts = list(set(
                (current_output.artifacts or []) + (fix_output.artifacts or [])
            ))
            fix_output.artifacts = all_artifacts
            current_output = fix_output
            logger.info(
                "[Reflexion] Task %s: iteration %d improved output (confidence %.2f -> %.2f)",
                task.id,
                iteration,
                output.confidence,
                fix_output.confidence,
            )
        else:
            # Fix didn't improve — stop iterating, diminishing returns
            logger.info(
                "[Reflexion] Task %s: iteration %d fix did not improve, stopping loop",
                task.id,
                iteration,
            )
            current_output.issues.extend([f"[Reflexion iter {iteration}] {i}" for i in verdict.issues])
            trace.final_verdict = verdict
            break

        trace.final_verdict = verdict

    # ── Finalize: accumulate costs and turns ──
    total_elapsed = time.monotonic() - t0
    current_output.cost_usd = output.cost_usd + accumulated_cost
    current_output.turns_used = output.turns_used + accumulated_turns

    # If we never set a final verdict (e.g., first critique failed), create one
    if trace.final_verdict is None:
        trace.final_verdict = ReflexionVerdict(should_fix=False, critique_text="No critique completed")

    logger.info(
        "[Reflexion] Task %s: %s — total %.1fs, %d iterations, cost=$%.4f",
        task.id,
        trace.summary(),
        total_elapsed,
        trace.iterations,
        accumulated_cost,
    )

    return current_output, trace.final_verdict
