"""Brain Hive — Quality Scorer.

Computes a 0-100 quality score per task output combining:
- Agent self-reported confidence (weighted)
- Test pass rate (from test_report artifacts)
- Review findings severity (from review_report artifacts)
- Artifact completeness (required vs produced)
- Token efficiency (lower is better, compared to role baseline)

The composite score replaces naive self-reported confidence as the
authoritative quality signal for downstream decisions (retry, remediation,
experience ledger entries).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityBreakdown:
    """Detailed breakdown of how the quality score was computed."""

    confidence_score: float = 0.0
    test_score: float = 0.0
    review_score: float = 0.0
    artifact_score: float = 0.0
    efficiency_score: float = 0.0
    composite: float = 0.0
    penalties: list[str] = field(default_factory=list)


# Default weights — sum to 1.0
_WEIGHTS = {
    "confidence": 0.15,
    "test": 0.30,
    "review": 0.20,
    "artifact": 0.20,
    "efficiency": 0.15,
}

# Baseline token budgets per role (used for efficiency scoring)
_ROLE_TOKEN_BASELINES: dict[str, int] = {
    "pm": 8_000,
    "frontend_developer": 30_000,
    "backend_developer": 30_000,
    "database_expert": 20_000,
    "reviewer": 15_000,
    "test_engineer": 25_000,
    "security_auditor": 15_000,
    "researcher": 20_000,
    "devops": 20_000,
    "memory": 10_000,
}

_DEFAULT_BASELINE = 25_000


def compute_quality_score(
    task_output: Any,
    task_input: Any | None = None,
) -> tuple[float, QualityBreakdown]:
    """Compute a 0-100 quality score for a completed task output.

    Args:
        task_output: A TaskOutput instance (from contracts.py).
        task_input: Optional TaskInput for artifact completeness check.

    Returns:
        (score, breakdown) where score is 0-100 float.
    """
    penalties: list[str] = []

    # 1. Confidence score (0-100) — agent's self-assessment, but discounted
    confidence_raw = getattr(task_output, "confidence", 0.5)
    # Penalise default confidence (0.5) — agents that don't set it get less credit
    if confidence_raw == 0.5:
        confidence_score = 40.0
        penalties.append("default_confidence_not_set")
    else:
        confidence_score = confidence_raw * 100.0

    # 2. Test score (0-100) — from test_report artifacts
    test_score = _compute_test_score(task_output, penalties)

    # 3. Review score (0-100) — inversely proportional to issues found
    review_score = _compute_review_score(task_output, penalties)

    # 4. Artifact completeness (0-100)
    artifact_score = _compute_artifact_score(task_output, task_input, penalties)

    # 5. Efficiency score (0-100) — token usage vs baseline
    efficiency_score = _compute_efficiency_score(task_output, task_input, penalties)

    # Composite weighted score
    composite = (
        _WEIGHTS["confidence"] * confidence_score
        + _WEIGHTS["test"] * test_score
        + _WEIGHTS["review"] * review_score
        + _WEIGHTS["artifact"] * artifact_score
        + _WEIGHTS["efficiency"] * efficiency_score
    )

    # Hard penalties for critical failures
    status_val = getattr(task_output, "status", None)
    if status_val is not None:
        status_str = status_val.value if hasattr(status_val, "value") else str(status_val)
        if status_str == "failed":
            composite = min(composite, 15.0)
            penalties.append("task_failed")

    # Clamp
    composite = max(0.0, min(100.0, round(composite, 1)))

    breakdown = QualityBreakdown(
        confidence_score=round(confidence_score, 1),
        test_score=round(test_score, 1),
        review_score=round(review_score, 1),
        artifact_score=round(artifact_score, 1),
        efficiency_score=round(efficiency_score, 1),
        composite=composite,
        penalties=penalties,
    )

    return composite, breakdown


def _compute_test_score(task_output: Any, penalties: list[str]) -> float:
    """Extract test pass rate from test_report artifacts."""
    structured_artifacts = getattr(task_output, "structured_artifacts", [])
    for art in structured_artifacts:
        art_type = getattr(art, "type", None)
        type_val = art_type.value if hasattr(art_type, "value") else str(art_type)
        if type_val == "test_report":
            data = getattr(art, "data", {})
            total = data.get("total", 0)
            passed = data.get("passed", 0)
            if total > 0:
                return (passed / total) * 100.0
            penalties.append("test_report_empty")
            return 50.0

    # No test report — neutral score (don't penalise non-test tasks)
    return 70.0


def _compute_review_score(task_output: Any, penalties: list[str]) -> float:
    """Score based on issues found — fewer issues = higher score."""
    issues = getattr(task_output, "issues", [])
    blockers = getattr(task_output, "blockers", [])

    # Also check review_report artifacts
    review_issues = 0
    structured_artifacts = getattr(task_output, "structured_artifacts", [])
    for art in structured_artifacts:
        art_type = getattr(art, "type", None)
        type_val = art_type.value if hasattr(art_type, "value") else str(art_type)
        if type_val == "review_report":
            data = getattr(art, "data", {})
            review_issues += len(data.get("findings", []))

    total_issues = len(issues) + review_issues
    blocker_count = len(blockers)

    if blocker_count > 0:
        penalties.append(f"blockers_{blocker_count}")
        return max(0.0, 30.0 - blocker_count * 10.0)

    if total_issues == 0:
        return 100.0
    if total_issues <= 2:
        return 80.0
    if total_issues <= 5:
        return 60.0
    penalties.append(f"high_issue_count_{total_issues}")
    return max(20.0, 60.0 - (total_issues - 5) * 5.0)


def _compute_artifact_score(
    task_output: Any,
    task_input: Any | None,
    penalties: list[str],
) -> float:
    """Score based on artifact completeness — did the agent produce what was required?"""
    if task_input is None:
        return 70.0

    required = getattr(task_input, "required_artifacts", [])
    if not required:
        # No artifacts required — full marks
        return 100.0

    produced_types = set()
    for art in getattr(task_output, "structured_artifacts", []):
        art_type = getattr(art, "type", None)
        type_val = art_type.value if hasattr(art_type, "value") else str(art_type)
        produced_types.add(type_val)

    required_types = set()
    for r in required:
        r_val = r.value if hasattr(r, "value") else str(r)
        required_types.add(r_val)

    if not required_types:
        return 100.0

    matched = required_types & produced_types
    ratio = len(matched) / len(required_types)
    score = ratio * 100.0

    missing = required_types - produced_types
    if missing:
        penalties.append(f"missing_artifacts:{','.join(sorted(missing))}")

    return score


def _compute_efficiency_score(
    task_output: Any,
    task_input: Any | None,
    penalties: list[str],
) -> float:
    """Score based on token efficiency relative to role baseline."""
    total_tokens = getattr(task_output, "total_tokens", 0)
    if total_tokens == 0:
        return 70.0  # No data — neutral

    role_name = ""
    if task_input is not None:
        role = getattr(task_input, "role", None)
        role_name = role.value if hasattr(role, "value") else str(role) if role else ""

    baseline = _ROLE_TOKEN_BASELINES.get(role_name, _DEFAULT_BASELINE)
    ratio = total_tokens / baseline

    if ratio <= 0.5:
        return 100.0  # Very efficient
    if ratio <= 1.0:
        return 90.0 - (ratio - 0.5) * 20.0  # 90 → 80
    if ratio <= 2.0:
        return 80.0 - (ratio - 1.0) * 30.0  # 80 → 50
    # Excessive token usage
    penalties.append(f"token_overuse_{ratio:.1f}x")
    return max(10.0, 50.0 - (ratio - 2.0) * 10.0)
