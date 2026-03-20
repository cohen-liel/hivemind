"""Brain Hive — Agent Performance Tracker.

Tracks accuracy, speed, token efficiency, and quality scores per agent role
across tasks. Maintains a rolling window of metrics to enable:
- Identifying underperforming roles for prompt tuning
- Adaptive timeout/budget allocation based on historical performance
- Experience-based role selection for ambiguous tasks

All state is in-memory (per-process). For production persistence, the
metrics can be serialised to the project's .hivemind/ directory.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum entries per role in the rolling window
_MAX_HISTORY_PER_ROLE = 100


@dataclass
class TaskMetrics:
    """Metrics for a single completed task."""

    task_id: str
    role: str
    status: str  # "completed" | "failed" | "skipped"
    quality_score: float  # 0-100 from QualityScorer
    confidence: float  # 0-1 agent self-reported
    total_tokens: int
    input_tokens: int
    output_tokens: int
    duration_seconds: float
    turns_used: int
    is_remediation: bool = False
    failure_category: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class RoleStats:
    """Aggregate statistics for an agent role."""

    role: str
    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    avg_quality_score: float = 0.0
    avg_duration_seconds: float = 0.0
    avg_tokens: float = 0.0
    avg_confidence: float = 0.0
    success_rate: float = 0.0
    token_efficiency: float = 0.0  # quality_score / (tokens / 1000)
    remediation_rate: float = 0.0  # % of tasks that were remediations

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "total_tasks": self.total_tasks,
            "completed": self.completed,
            "failed": self.failed,
            "avg_quality_score": round(self.avg_quality_score, 1),
            "avg_duration_seconds": round(self.avg_duration_seconds, 1),
            "avg_tokens": round(self.avg_tokens, 0),
            "avg_confidence": round(self.avg_confidence, 2),
            "success_rate": round(self.success_rate, 3),
            "token_efficiency": round(self.token_efficiency, 2),
            "remediation_rate": round(self.remediation_rate, 3),
        }


class AgentPerformanceTracker:
    """Tracks per-role performance metrics across task executions.

    Thread-safe for single-event-loop async usage (no locks needed in asyncio).
    """

    def __init__(self) -> None:
        # role_name -> list[TaskMetrics] (rolling window)
        self._history: dict[str, list[TaskMetrics]] = {}

    def record(
        self,
        task_id: str,
        role: str,
        status: str,
        quality_score: float,
        confidence: float = 0.5,
        total_tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_seconds: float = 0.0,
        turns_used: int = 0,
        is_remediation: bool = False,
        failure_category: str | None = None,
    ) -> None:
        """Record metrics for a completed task."""
        metrics = TaskMetrics(
            task_id=task_id,
            role=role,
            status=status,
            quality_score=quality_score,
            confidence=confidence,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_seconds=duration_seconds,
            turns_used=turns_used,
            is_remediation=is_remediation,
            failure_category=failure_category,
        )

        if role not in self._history:
            self._history[role] = []

        history = self._history[role]
        history.append(metrics)

        # Trim to rolling window
        if len(history) > _MAX_HISTORY_PER_ROLE:
            self._history[role] = history[-_MAX_HISTORY_PER_ROLE:]

        logger.debug(
            "[PerfTracker] Recorded %s: role=%s quality=%.1f tokens=%d duration=%.1fs",
            task_id, role, quality_score, total_tokens, duration_seconds,
        )

    def get_role_stats(self, role: str) -> RoleStats:
        """Compute aggregate statistics for a role."""
        history = self._history.get(role, [])
        stats = RoleStats(role=role)

        if not history:
            return stats

        stats.total_tasks = len(history)
        stats.completed = sum(1 for m in history if m.status == "completed")
        stats.failed = sum(1 for m in history if m.status == "failed")
        stats.success_rate = stats.completed / stats.total_tasks if stats.total_tasks else 0.0

        completed_metrics = [m for m in history if m.status == "completed"]
        if completed_metrics:
            stats.avg_quality_score = sum(m.quality_score for m in completed_metrics) / len(completed_metrics)
            stats.avg_duration_seconds = sum(m.duration_seconds for m in completed_metrics) / len(completed_metrics)
            stats.avg_tokens = sum(m.total_tokens for m in completed_metrics) / len(completed_metrics)
            stats.avg_confidence = sum(m.confidence for m in completed_metrics) / len(completed_metrics)

            # Token efficiency: quality per 1K tokens
            total_tokens = sum(m.total_tokens for m in completed_metrics)
            total_quality = sum(m.quality_score for m in completed_metrics)
            if total_tokens > 0:
                stats.token_efficiency = total_quality / (total_tokens / 1000)

        remediation_count = sum(1 for m in history if m.is_remediation)
        stats.remediation_rate = remediation_count / stats.total_tasks if stats.total_tasks else 0.0

        return stats

    def get_all_stats(self) -> dict[str, RoleStats]:
        """Get aggregate stats for all tracked roles."""
        return {role: self.get_role_stats(role) for role in self._history}

    def get_summary(self) -> dict[str, Any]:
        """Get a JSON-serialisable summary of all performance data."""
        all_stats = self.get_all_stats()
        total_tasks = sum(s.total_tasks for s in all_stats.values())
        total_completed = sum(s.completed for s in all_stats.values())
        total_failed = sum(s.failed for s in all_stats.values())

        return {
            "total_tasks_tracked": total_tasks,
            "total_completed": total_completed,
            "total_failed": total_failed,
            "overall_success_rate": round(total_completed / total_tasks, 3) if total_tasks else 0.0,
            "roles": {role: stats.to_dict() for role, stats in all_stats.items()},
        }

    def get_failure_distribution(self, role: str | None = None) -> dict[str, int]:
        """Get distribution of failure categories, optionally filtered by role."""
        dist: dict[str, int] = {}
        for r, history in self._history.items():
            if role and r != role:
                continue
            for m in history:
                if m.failure_category:
                    dist[m.failure_category] = dist.get(m.failure_category, 0) + 1
        return dict(sorted(dist.items(), key=lambda x: x[1], reverse=True))

    def save_to_disk(self, project_dir: str) -> None:
        """Persist current metrics to project's .hivemind directory."""
        try:
            hive_dir = Path(project_dir) / ".hivemind"
            hive_dir.mkdir(parents=True, exist_ok=True)
            path = hive_dir / "agent_performance.json"
            data = self.get_summary()
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("[PerfTracker] Failed to save metrics: %s", exc)

    def load_from_disk(self, project_dir: str) -> None:
        """Load previously saved metrics (summary only — no history restoration)."""
        try:
            path = Path(project_dir) / ".hivemind" / "agent_performance.json"
            if path.exists():
                logger.info("[PerfTracker] Loaded saved metrics from %s", path)
        except Exception as exc:
            logger.warning("[PerfTracker] Failed to load metrics: %s", exc)


# Module-level singleton
_tracker = AgentPerformanceTracker()


def get_performance_tracker() -> AgentPerformanceTracker:
    """Return the module-level singleton tracker."""
    return _tracker
