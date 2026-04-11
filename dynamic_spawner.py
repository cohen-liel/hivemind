"""Dynamic Spawner — model-level fallback for failed agents.

When an agent task fails, the standard remediation flow retries with the
same model.  This module adds a **model cascade**: if the original model
fails, try a more capable (or different) Claude model before giving up.

This leverages the ``model`` parameter in ``ClaudeAgentOptions`` documented
in the Claude Agent SDK README.

Integration point:
    dag_executor._handle_failure — call ``maybe_respawn(...)`` before
    falling back to the existing remediation/retry logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from contracts import (
    FailureCategory,
    TaskInput,
    TaskOutput,
    TaskStatus,
    classify_failure,
)

logger = logging.getLogger(__name__)


# ── Model Cascade ────────────────────────────────────────────────────────────

# Ordered from cheapest/fastest to most capable.
# The spawner tries the next model in the cascade when the current one fails.
DEFAULT_MODEL_CASCADE: list[str] = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
]

# Failure categories where switching models is likely to help.
# For example, TIMEOUT may benefit from a faster model, BUILD_ERROR from
# a smarter one.  PERMISSION and DEPENDENCY_MISSING are infrastructure
# issues — switching models won't help.
_MODEL_SWITCH_ELIGIBLE: set[FailureCategory] = {
    FailureCategory.BUILD_ERROR,
    FailureCategory.TEST_FAILURE,
    FailureCategory.API_MISMATCH,
    FailureCategory.TIMEOUT,
    FailureCategory.UNCLEAR_GOAL,
}


@dataclass
class SpawnAttempt:
    """Record of a model-switch attempt."""

    task_id: str
    original_model: str | None
    new_model: str
    reason: str
    succeeded: bool | None = None  # filled after execution


@dataclass
class DynamicSpawner:
    """Manages model-level fallback for failed tasks.

    Tracks which models have been tried per task so we don't repeat.
    """

    model_cascade: list[str] = field(default_factory=lambda: list(DEFAULT_MODEL_CASCADE))
    # task_id -> set of models already tried
    _tried: dict[str, set[str]] = field(default_factory=dict)
    # History of all spawn attempts
    history: list[SpawnAttempt] = field(default_factory=list)

    def should_respawn(
        self,
        task: TaskInput,
        output: TaskOutput,
        current_model: str | None = None,
    ) -> bool:
        """Decide whether a model switch is worth trying.

        Returns True if:
        1. The failure category is eligible for model switching.
        2. There is at least one untried model in the cascade.
        """
        if output.status != TaskStatus.FAILED:
            return False

        category = self._classify_failure(output)
        if category not in _MODEL_SWITCH_ELIGIBLE:
            logger.debug(
                "[DynamicSpawner] task %s failed with %s — not eligible for model switch",
                task.id,
                category,
            )
            return False

        next_model = self._next_model(task.id, current_model)
        if next_model is None:
            logger.debug(
                "[DynamicSpawner] task %s — all models exhausted",
                task.id,
            )
            return False

        return True

    def get_respawn_model(
        self,
        task: TaskInput,
        output: TaskOutput,
        current_model: str | None = None,
    ) -> str | None:
        """Return the next model to try, or None if exhausted.

        Also records the attempt in history and marks the model as tried.
        """
        if not self.should_respawn(task, output, current_model):
            return None

        next_model = self._next_model(task.id, current_model)
        if next_model is None:
            return None

        # Record
        self._tried.setdefault(task.id, set())
        if current_model:
            self._tried[task.id].add(current_model)
        self._tried[task.id].add(next_model)

        reason = self._build_reason(output)
        attempt = SpawnAttempt(
            task_id=task.id,
            original_model=current_model,
            new_model=next_model,
            reason=reason,
        )
        self.history.append(attempt)

        logger.info(
            "[DynamicSpawner] task %s: switching from %s -> %s (reason: %s)",
            task.id,
            current_model or "default",
            next_model,
            reason,
        )
        return next_model

    def record_result(self, task_id: str, model: str, succeeded: bool) -> None:
        """Update the last attempt for this task with the result."""
        for attempt in reversed(self.history):
            if attempt.task_id == task_id and attempt.new_model == model:
                attempt.succeeded = succeeded
                break

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of all spawn attempts."""
        total = len(self.history)
        succeeded = sum(1 for a in self.history if a.succeeded is True)
        failed = sum(1 for a in self.history if a.succeeded is False)
        return {
            "total_attempts": total,
            "succeeded": succeeded,
            "failed": failed,
            "pending": total - succeeded - failed,
            "attempts": [
                {
                    "task_id": a.task_id,
                    "from": a.original_model,
                    "to": a.new_model,
                    "reason": a.reason,
                    "succeeded": a.succeeded,
                }
                for a in self.history
            ],
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _next_model(self, task_id: str, current_model: str | None) -> str | None:
        """Find the next untried model in the cascade."""
        tried = self._tried.get(task_id, set())
        if current_model:
            tried = tried | {current_model}

        for model in self.model_cascade:
            if model not in tried:
                return model
        return None

    @staticmethod
    def _classify_failure(output: TaskOutput) -> FailureCategory:
        """Classify failure using the canonical contracts.classify_failure."""
        return classify_failure(output)

    @staticmethod
    def _build_reason(output: TaskOutput) -> str:
        """Build a human-readable reason for the model switch."""
        if output.issues:
            return f"Failed: {output.issues[0][:100]}"
        return f"Failed: {output.summary[:100]}"
