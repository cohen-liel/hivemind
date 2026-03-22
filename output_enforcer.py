"""Output Enforcer — structured output validation and self-verification.

Ensures every agent output conforms to the expected JSON schema before
acceptance. When output is malformed or missing required fields, the
enforcer either auto-repairs it or requests a re-generation.

This addresses a core quality issue: agents sometimes produce free-text
output instead of the required JSON block, or produce JSON with missing
fields (e.g., empty artifacts list when files were clearly modified).

Verification levels:
    1. SCHEMA  — JSON parses, required fields present, types correct
    2. SEMANTIC — summary is non-empty, artifacts list matches actual files
    3. SELF-CHECK — agent verifies its own output against acceptance criteria

Integration:
    Called from ``dag_executor._run_single_task`` in the Phase 2 (SUMMARY)
    extraction step. Wraps the existing ``extract_task_output`` function
    with validation and auto-repair.

Design:
    - Uses Pydantic validation from TaskOutput model
    - Cross-references artifacts against actual filesystem
    - Detects common LLM output failures (empty summary, wrong status)
    - Auto-repairs trivial issues (e.g., "completed" vs "COMPLETED")
    - Flags serious issues for re-extraction
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import config as cfg
from contracts import TaskInput, TaskOutput, TaskStatus

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
OUTPUT_ENFORCER_ENABLED: bool = cfg._get("OUTPUT_ENFORCER_ENABLED", "true", str).lower() == "true"
OUTPUT_ENFORCER_VERIFY_FILES: bool = cfg._get("OUTPUT_ENFORCER_VERIFY_FILES", "true", str).lower() == "true"
OUTPUT_ENFORCER_MIN_SUMMARY_LEN: int = cfg._get("OUTPUT_ENFORCER_MIN_SUMMARY_LEN", "20", int)


@dataclass
class EnforcerResult:
    """Result of output validation and enforcement."""

    valid: bool = True
    auto_repaired: bool = False
    repairs: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.valid and not self.auto_repaired:
            return "Output validation passed."
        if self.valid and self.auto_repaired:
            return f"Output auto-repaired: {'; '.join(self.repairs)}"
        return f"Output validation failed: {'; '.join(self.issues)}"


def validate_and_repair(
    output: TaskOutput,
    task: TaskInput,
    project_dir: str,
) -> tuple[TaskOutput, EnforcerResult]:
    """Validate a TaskOutput and auto-repair trivial issues.

    Args:
        output: The extracted TaskOutput from agent response.
        task: The original TaskInput for context.
        project_dir: Working directory for file verification.

    Returns:
        Tuple of (possibly repaired output, validation result).
    """
    if not OUTPUT_ENFORCER_ENABLED:
        return output, EnforcerResult()

    result = EnforcerResult()

    # ── 1. Status normalization ──
    if output.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.NEEDS_FOLLOWUP):
        # Agent returned weird status — normalize
        if output.summary and len(output.summary) > 20:
            output.status = TaskStatus.COMPLETED
            result.auto_repaired = True
            result.repairs.append(f"Status normalized from '{output.status}' to 'completed'")

    # ── 2. Summary validation ──
    if not output.summary or len(output.summary.strip()) < OUTPUT_ENFORCER_MIN_SUMMARY_LEN:
        result.issues.append(
            f"Summary too short ({len(output.summary) if output.summary else 0} chars, "
            f"minimum {OUTPUT_ENFORCER_MIN_SUMMARY_LEN})"
        )
        # Try to auto-repair from task goal
        if not output.summary:
            output.summary = f"Completed task: {task.goal[:200]}"
            result.auto_repaired = True
            result.repairs.append("Generated summary from task goal")

    # ── 3. Confidence validation ──
    if output.confidence == 0.5 and output.is_successful():
        # Default confidence was never set by agent — this is suspicious
        # Check if there are artifacts (agent actually did work)
        if output.artifacts:
            output.confidence = 0.7  # Reasonable default for completed work
            result.auto_repaired = True
            result.repairs.append("Adjusted default confidence 0.5 → 0.7 (has artifacts)")

    if output.confidence > 0.95 and output.issues:
        # Agent claims high confidence but reported issues — contradictory
        output.confidence = min(0.85, output.confidence)
        result.auto_repaired = True
        result.repairs.append(
            f"Reduced confidence {output.confidence:.2f} → 0.85 "
            f"(contradicts {len(output.issues)} reported issues)"
        )

    # ── 4. Artifact file verification ──
    if OUTPUT_ENFORCER_VERIFY_FILES and output.artifacts and project_dir:
        verified_artifacts = []
        missing_artifacts = []

        for artifact_path in output.artifacts:
            full_path = (
                Path(project_dir) / artifact_path
                if not Path(artifact_path).is_absolute()
                else Path(artifact_path)
            )
            if full_path.exists():
                verified_artifacts.append(artifact_path)
            else:
                missing_artifacts.append(artifact_path)

        if missing_artifacts:
            result.auto_repaired = True
            result.repairs.append(
                f"Removed {len(missing_artifacts)} non-existent artifacts: "
                f"{', '.join(missing_artifacts[:5])}"
            )
            output.artifacts = verified_artifacts

            # If ALL artifacts are missing, something went very wrong
            if not verified_artifacts and missing_artifacts:
                result.issues.append(
                    f"All {len(missing_artifacts)} claimed artifacts are missing from disk"
                )

    # ── 5. Detect files changed but not listed in artifacts ──
    if OUTPUT_ENFORCER_VERIFY_FILES and output.is_successful() and project_dir:
        try:
            import subprocess

            git_result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                cwd=project_dir,
                timeout=10,
            )
            if git_result.returncode == 0:
                changed_files = [
                    f.strip() for f in git_result.stdout.strip().split("\n") if f.strip()
                ]
                # Also check untracked files
                untracked_result = subprocess.run(
                    ["git", "ls-files", "--others", "--exclude-standard"],
                    capture_output=True,
                    text=True,
                    cwd=project_dir,
                    timeout=10,
                )
                if untracked_result.returncode == 0:
                    changed_files.extend(
                        f.strip()
                        for f in untracked_result.stdout.strip().split("\n")
                        if f.strip()
                    )

                # Find files that were changed but not listed
                listed = set(output.artifacts or [])
                unlisted = [f for f in changed_files if f not in listed and f]

                if unlisted and len(unlisted) <= 20:
                    # Auto-add reasonable number of unlisted files
                    output.artifacts = list(set((output.artifacts or []) + unlisted))
                    result.auto_repaired = True
                    result.repairs.append(
                        f"Added {len(unlisted)} unlisted changed files to artifacts"
                    )
        except Exception:
            pass  # Git check is best-effort

    # ── 6. Task ID consistency ──
    if output.task_id != task.id:
        output.task_id = task.id
        result.auto_repaired = True
        result.repairs.append(f"Fixed task_id mismatch: '{output.task_id}' → '{task.id}'")

    # ── Final verdict ──
    result.valid = len(result.issues) == 0

    if result.auto_repaired:
        logger.info(
            "[OutputEnforcer] Task %s: auto-repaired %d issues: %s",
            task.id,
            len(result.repairs),
            "; ".join(result.repairs),
        )

    if not result.valid:
        logger.warning(
            "[OutputEnforcer] Task %s: validation failed with %d issues: %s",
            task.id,
            len(result.issues),
            "; ".join(result.issues),
        )

    return output, result


def build_self_verification_prompt(task: TaskInput, output: TaskOutput) -> str:
    """Build a prompt for agent self-verification of its output.

    This is a lightweight check that asks the agent to verify its own
    output against the acceptance criteria. Used as a final gate before
    acceptance.
    """
    criteria_text = "\n".join(
        f"  {i + 1}. {c}" for i, c in enumerate(task.acceptance_criteria or [])
    )
    if not criteria_text:
        criteria_text = "  (No explicit criteria)"

    artifacts_text = ", ".join(output.artifacts[:10]) if output.artifacts else "(none)"

    return (
        "## SELF-VERIFICATION CHECK\n\n"
        "Before your output is accepted, verify:\n\n"
        f"**Task Goal:** {task.goal}\n\n"
        f"**Acceptance Criteria:**\n{criteria_text}\n\n"
        f"**Your Artifacts:** {artifacts_text}\n\n"
        f"**Your Summary:** {output.summary}\n\n"
        "For each acceptance criterion, answer YES or NO:\n"
        "- Did you meet it? If NO, what's missing?\n\n"
        "If any criterion is NOT met, fix it now using the available tools.\n"
        "If all criteria are met, respond with: VERIFIED"
    )
