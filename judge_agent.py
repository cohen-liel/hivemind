"""Judge Agent — post-execution quality evaluation for DAG results.

After the DAG executor completes all tasks, this module evaluates the
overall quality of the work.  It uses a structured rubric with 6 dimensions
and returns a pass/fail verdict with actionable feedback.

This replaces/extends the legacy ``auto_evaluate`` in ``orch_review.py``
which only runs on the legacy (non-DAG) path.

Integration point:
    orchestrator._run_dag_session — after DAG execution completes,
    call ``evaluate_dag_results(...)`` to get a quality assessment.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from contracts import TaskGraph, TaskOutput, TaskStatus

logger = logging.getLogger(__name__)


# ── Evaluation Dimensions ────────────────────────────────────────────────────

class Dimension(str, Enum):
    CORRECTNESS = "correctness"
    COMPLETENESS = "completeness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    STYLE = "style"


DIMENSION_WEIGHTS: dict[Dimension, float] = {
    Dimension.CORRECTNESS: 0.30,
    Dimension.COMPLETENESS: 0.25,
    Dimension.SECURITY: 0.15,
    Dimension.PERFORMANCE: 0.10,
    Dimension.MAINTAINABILITY: 0.10,
    Dimension.STYLE: 0.10,
}

# Minimum weighted score to pass (0-10 scale)
DEFAULT_PASS_THRESHOLD = 6.0


@dataclass
class DimensionScore:
    """Score for a single evaluation dimension."""
    dimension: Dimension
    score: float  # 0-10
    reasoning: str
    suggestions: list[str] = field(default_factory=list)


@dataclass
class JudgeVerdict:
    """Complete evaluation result."""
    passed: bool
    weighted_score: float
    dimension_scores: list[DimensionScore]
    overall_feedback: str
    critical_issues: list[str]
    task_results_summary: str


@dataclass
class JudgeAgent:
    """Evaluates DAG execution results using a structured rubric.

    The judge calls Claude via isolated_query with a carefully crafted
    prompt that asks for scores on each dimension.
    """

    pass_threshold: float = DEFAULT_PASS_THRESHOLD
    history: list[JudgeVerdict] = field(default_factory=list)

    async def evaluate_dag_results(
        self,
        graph: TaskGraph,
        results: dict[str, TaskOutput],
        project_dir: str,
        goal: str = "",
        sdk=None,
    ) -> JudgeVerdict:
        """Evaluate the quality of all completed tasks.

        Args:
            graph: The task graph that was executed
            results: task_id -> TaskOutput mapping
            project_dir: Project directory for SDK call
            goal: The original user goal
            sdk: ClaudeSDKManager instance (passed to isolated_query)

        Returns:
            JudgeVerdict with pass/fail and detailed scores
        """
        # Build a summary of what was done
        task_summary = self._build_task_summary(graph, results)

        # Build the evaluation prompt
        prompt = self._build_eval_prompt(goal, task_summary)

        # Call Claude for evaluation
        from isolated_query import isolated_query
        import state

        _sdk = sdk or state.sdk_client

        response = await isolated_query(
            _sdk,
            prompt=prompt,
            system_prompt=(
                "You are a senior code reviewer and quality judge. "
                "Evaluate the work objectively using the provided rubric. "
                "Be strict but fair."
            ),
            cwd=project_dir,
            max_turns=3,
            max_budget_usd=1.0,
        )

        response_text = response.text if response else ""

        # Parse the response
        verdict = self._parse_evaluation(response_text, task_summary)
        self.history.append(verdict)

        logger.info(
            "[JudgeAgent] verdict: %s (score=%.1f, threshold=%.1f)",
            "PASS" if verdict.passed else "FAIL",
            verdict.weighted_score,
            self.pass_threshold,
        )
        return verdict

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of all evaluations."""
        total = len(self.history)
        passed = sum(1 for v in self.history if v.passed)
        avg_score = (
            sum(v.weighted_score for v in self.history) / total
            if total > 0 else 0.0
        )
        return {
            "total_evaluations": total,
            "passed": passed,
            "failed": total - passed,
            "average_score": round(avg_score, 2),
        }

    # ── Internal ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_task_summary(
        graph: TaskGraph, results: dict[str, TaskOutput]
    ) -> str:
        """Build a text summary of all task results."""
        lines = []
        for task in graph.tasks:
            output = results.get(task.id)
            if output is None:
                lines.append(f"- [{task.id}] {task.role.value}: NOT EXECUTED")
                continue
            status = "COMPLETED" if output.status == TaskStatus.COMPLETED else "FAILED"
            lines.append(
                f"- [{task.id}] {task.role.value}: {status}\n"
                f"  Summary: {output.summary[:200]}\n"
                f"  Files: {', '.join(output.files_modified[:5]) if output.files_modified else 'none'}"
            )
            if output.issues:
                lines.append(f"  Issues: {'; '.join(output.issues[:3])}")
        return "\n".join(lines)

    def _build_eval_prompt(self, goal: str, task_summary: str) -> str:
        """Build the evaluation prompt with rubric."""
        dimensions_text = "\n".join(
            f"- {d.value} (weight {w:.0%})"
            for d, w in DIMENSION_WEIGHTS.items()
        )

        return (
            f"Evaluate the following work against the original goal.\n\n"
            f"## Original Goal\n{goal}\n\n"
            f"## Task Results\n{task_summary}\n\n"
            f"## Evaluation Rubric\nScore each dimension 0-10:\n{dimensions_text}\n\n"
            f"## Response Format\n"
            f"For each dimension, respond exactly like this:\n"
            f"DIMENSION: <name>\n"
            f"SCORE: <0-10>\n"
            f"REASONING: <why>\n"
            f"SUGGESTIONS: <comma-separated improvements>\n\n"
            f"After all dimensions, add:\n"
            f"OVERALL: <overall feedback>\n"
            f"CRITICAL: <comma-separated critical issues, or NONE>"
        )

    def _parse_evaluation(self, text: str, task_summary: str) -> JudgeVerdict:
        """Parse the judge response into a JudgeVerdict."""
        scores: list[DimensionScore] = []

        for dim in Dimension:
            score = self._extract_dimension_score(text, dim)
            scores.append(score)

        # Calculate weighted score
        weighted = sum(
            s.score * DIMENSION_WEIGHTS.get(s.dimension, 0.1)
            for s in scores
        )

        # Extract overall feedback
        overall = ""
        m = re.search(r"OVERALL:\s*(.+?)(?=CRITICAL:|$)", text, re.DOTALL | re.IGNORECASE)
        if m:
            overall = m.group(1).strip()

        # Extract critical issues
        critical: list[str] = []
        m = re.search(r"CRITICAL:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            if raw.lower() != "none":
                critical = [c.strip() for c in raw.split(",") if c.strip()]

        passed = weighted >= self.pass_threshold

        return JudgeVerdict(
            passed=passed,
            weighted_score=round(weighted, 2),
            dimension_scores=scores,
            overall_feedback=overall,
            critical_issues=critical,
            task_results_summary=task_summary,
        )

    @staticmethod
    def _extract_dimension_score(text: str, dim: Dimension) -> DimensionScore:
        """Extract score for a specific dimension from the response."""
        # Try to find the dimension block
        pattern = (
            rf"DIMENSION:\s*{re.escape(dim.value)}\s*\n"
            rf"SCORE:\s*(\d+(?:\.\d+)?)\s*\n"
            rf"REASONING:\s*(.+?)\n"
            rf"SUGGESTIONS:\s*(.+?)(?=\nDIMENSION:|\nOVERALL:|$)"
        )
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)

        if m:
            score = min(10.0, max(0.0, float(m.group(1))))
            reasoning = m.group(2).strip()
            suggestions_raw = m.group(3).strip()
            suggestions = [s.strip() for s in suggestions_raw.split(",") if s.strip()]
            return DimensionScore(
                dimension=dim,
                score=score,
                reasoning=reasoning,
                suggestions=suggestions,
            )

        # Fallback: try to find just a score mention
        m = re.search(rf"{dim.value}.*?(\d+(?:\.\d+)?)/10", text, re.IGNORECASE)
        if m:
            score = min(10.0, max(0.0, float(m.group(1))))
            return DimensionScore(
                dimension=dim, score=score, reasoning="Extracted from text", suggestions=[]
            )

        # Default: neutral score
        return DimensionScore(
            dimension=dim, score=5.0, reasoning="Could not parse score", suggestions=[]
        )
