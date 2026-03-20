"""Tests for brain_quality.py — QualityScorer.

Covers compute_quality_score with various inputs:
confidence, test reports, review findings, artifact completeness,
efficiency scoring, and hard penalties.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from brain_quality import (
    QualityBreakdown,
    _ROLE_TOKEN_BASELINES,
    _WEIGHTS,
    _compute_artifact_score,
    _compute_efficiency_score,
    _compute_review_score,
    _compute_test_score,
    compute_quality_score,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_output(**kwargs) -> SimpleNamespace:
    """Create a mock TaskOutput with sensible defaults."""
    defaults = {
        "confidence": 0.8,
        "status": "completed",
        "structured_artifacts": [],
        "artifacts": [],
        "issues": [],
        "blockers": [],
        "total_tokens": 0,
        "summary": "",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_input(**kwargs) -> SimpleNamespace:
    defaults = {
        "required_artifacts": [],
        "role": "backend_developer",
        "files_scope": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_artifact(art_type: str, data: dict | None = None):
    return SimpleNamespace(type=art_type, data=data or {})


# ---------------------------------------------------------------------------
# QualityBreakdown dataclass
# ---------------------------------------------------------------------------


class TestQualityBreakdown:
    def test_breakdown_when_default_should_have_zero_fields(self):
        b = QualityBreakdown()
        assert b.composite == 0.0
        assert b.penalties == []

    def test_breakdown_when_frozen_should_be_immutable(self):
        b = QualityBreakdown(composite=50.0)
        with pytest.raises(AttributeError):
            b.composite = 99.0  # type: ignore


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------


class TestWeights:
    def test_weights_when_summed_should_equal_one(self):
        total = sum(_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Confidence dimension
# ---------------------------------------------------------------------------


class TestConfidence:
    def test_score_when_default_confidence_should_get_penalty_and_40(self):
        out = _make_output(confidence=0.5)
        score, bd = compute_quality_score(out)
        assert bd.confidence_score == 40.0
        assert "default_confidence_not_set" in bd.penalties

    def test_score_when_high_confidence_should_scale_to_100(self):
        out = _make_output(confidence=1.0)
        _, bd = compute_quality_score(out)
        assert bd.confidence_score == 100.0

    def test_score_when_zero_confidence_should_scale_to_0(self):
        out = _make_output(confidence=0.0)
        _, bd = compute_quality_score(out)
        assert bd.confidence_score == 0.0

    def test_score_when_missing_confidence_should_use_default(self):
        out = SimpleNamespace(status="completed", structured_artifacts=[], artifacts=[],
                              issues=[], blockers=[], total_tokens=0, summary="")
        _, bd = compute_quality_score(out)
        assert bd.confidence_score == 40.0


# ---------------------------------------------------------------------------
# Test score dimension
# ---------------------------------------------------------------------------


class TestTestScore:
    def test_score_when_no_test_report_should_return_70(self):
        penalties: list[str] = []
        out = _make_output()
        s = _compute_test_score(out, penalties)
        assert s == 70.0

    def test_score_when_all_tests_pass_should_return_100(self):
        art = _make_artifact("test_report", {"total": 10, "passed": 10})
        out = _make_output(structured_artifacts=[art])
        penalties: list[str] = []
        s = _compute_test_score(out, penalties)
        assert s == 100.0

    def test_score_when_half_tests_pass_should_return_50(self):
        art = _make_artifact("test_report", {"total": 10, "passed": 5})
        out = _make_output(structured_artifacts=[art])
        penalties: list[str] = []
        s = _compute_test_score(out, penalties)
        assert s == 50.0

    def test_score_when_zero_total_should_return_50_with_penalty(self):
        art = _make_artifact("test_report", {"total": 0, "passed": 0})
        out = _make_output(structured_artifacts=[art])
        penalties: list[str] = []
        s = _compute_test_score(out, penalties)
        assert s == 50.0
        assert "test_report_empty" in penalties


# ---------------------------------------------------------------------------
# Review score dimension
# ---------------------------------------------------------------------------


class TestReviewScore:
    def test_score_when_no_issues_should_return_100(self):
        out = _make_output(issues=[], blockers=[])
        penalties: list[str] = []
        s = _compute_review_score(out, penalties)
        assert s == 100.0

    def test_score_when_2_issues_should_return_80(self):
        out = _make_output(issues=["a", "b"], blockers=[])
        penalties: list[str] = []
        s = _compute_review_score(out, penalties)
        assert s == 80.0

    def test_score_when_4_issues_should_return_60(self):
        out = _make_output(issues=["a", "b", "c", "d"], blockers=[])
        penalties: list[str] = []
        s = _compute_review_score(out, penalties)
        assert s == 60.0

    def test_score_when_blockers_present_should_cap_at_30(self):
        out = _make_output(issues=[], blockers=["critical"])
        penalties: list[str] = []
        s = _compute_review_score(out, penalties)
        assert s == 20.0  # 30 - 1*10
        assert "blockers_1" in penalties

    def test_score_when_many_blockers_should_floor_at_0(self):
        out = _make_output(issues=[], blockers=["a", "b", "c", "d"])
        penalties: list[str] = []
        s = _compute_review_score(out, penalties)
        assert s == 0.0

    def test_score_when_review_report_has_findings_should_count(self):
        review_art = _make_artifact("review_report", {"findings": ["f1", "f2", "f3"]})
        out = _make_output(issues=[], blockers=[], structured_artifacts=[review_art])
        penalties: list[str] = []
        s = _compute_review_score(out, penalties)
        assert s == 60.0  # 3 review issues => 3 total => <=5 => 60

    def test_score_when_high_issue_count_should_apply_penalty(self):
        out = _make_output(issues=list(range(8)), blockers=[])
        penalties: list[str] = []
        s = _compute_review_score(out, penalties)
        assert "high_issue_count_8" in penalties


# ---------------------------------------------------------------------------
# Artifact completeness dimension
# ---------------------------------------------------------------------------


class TestArtifactScore:
    def test_score_when_no_input_should_return_70(self):
        out = _make_output()
        penalties: list[str] = []
        s = _compute_artifact_score(out, None, penalties)
        assert s == 70.0

    def test_score_when_no_required_artifacts_should_return_100(self):
        inp = _make_input(required_artifacts=[])
        out = _make_output()
        penalties: list[str] = []
        s = _compute_artifact_score(out, inp, penalties)
        assert s == 100.0

    def test_score_when_all_required_produced_should_return_100(self):
        inp = _make_input(required_artifacts=["test_report"])
        art = _make_artifact("test_report", {"total": 5, "passed": 5})
        out = _make_output(structured_artifacts=[art])
        penalties: list[str] = []
        s = _compute_artifact_score(out, inp, penalties)
        assert s == 100.0

    def test_score_when_missing_required_should_penalise(self):
        inp = _make_input(required_artifacts=["test_report", "api_contract"])
        art = _make_artifact("test_report", {})
        out = _make_output(structured_artifacts=[art])
        penalties: list[str] = []
        s = _compute_artifact_score(out, inp, penalties)
        assert s == 50.0  # 1/2 matched
        assert any("missing_artifacts" in p for p in penalties)

    def test_score_when_no_required_produced_should_return_0(self):
        inp = _make_input(required_artifacts=["test_report"])
        out = _make_output(structured_artifacts=[])
        penalties: list[str] = []
        s = _compute_artifact_score(out, inp, penalties)
        assert s == 0.0


# ---------------------------------------------------------------------------
# Efficiency score dimension
# ---------------------------------------------------------------------------


class TestEfficiencyScore:
    def test_score_when_no_tokens_should_return_70(self):
        out = _make_output(total_tokens=0)
        penalties: list[str] = []
        s = _compute_efficiency_score(out, None, penalties)
        assert s == 70.0

    def test_score_when_very_efficient_should_return_100(self):
        # backend_developer baseline = 30_000; 10_000 => ratio 0.33 < 0.5
        inp = _make_input(role="backend_developer")
        out = _make_output(total_tokens=10_000)
        penalties: list[str] = []
        s = _compute_efficiency_score(out, inp, penalties)
        assert s == 100.0

    def test_score_when_at_baseline_should_return_around_80(self):
        inp = _make_input(role="backend_developer")
        out = _make_output(total_tokens=30_000)
        penalties: list[str] = []
        s = _compute_efficiency_score(out, inp, penalties)
        assert 79.0 <= s <= 81.0  # ratio=1.0 => 80 - (1.0-0.5)*20 = 80

    def test_score_when_excessive_should_penalise(self):
        inp = _make_input(role="pm")  # baseline 8000
        out = _make_output(total_tokens=24_000)  # 3x => ratio=3.0
        penalties: list[str] = []
        s = _compute_efficiency_score(out, inp, penalties)
        assert "token_overuse_3.0x" in penalties
        assert s < 50.0

    def test_score_when_unknown_role_should_use_default_baseline(self):
        inp = _make_input(role="unknown_role")
        out = _make_output(total_tokens=12_500)  # half of default 25000
        penalties: list[str] = []
        s = _compute_efficiency_score(out, inp, penalties)
        assert s == 100.0


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------


class TestCompositeScore:
    def test_score_when_all_perfect_should_be_near_100(self):
        art = _make_artifact("test_report", {"total": 10, "passed": 10})
        out = _make_output(confidence=1.0, structured_artifacts=[art],
                           total_tokens=5000)
        inp = _make_input(required_artifacts=["test_report"], role="backend_developer")
        score, bd = compute_quality_score(out, inp)
        assert score >= 90.0

    def test_score_when_failed_status_should_cap_at_15(self):
        out = _make_output(confidence=1.0, status="failed")
        score, bd = compute_quality_score(out)
        assert score <= 15.0
        assert "task_failed" in bd.penalties

    def test_score_when_failed_enum_status_should_cap_at_15(self):
        status_enum = MagicMock()
        status_enum.value = "failed"
        out = _make_output(confidence=1.0, status=status_enum)
        score, bd = compute_quality_score(out)
        assert score <= 15.0

    def test_score_should_be_clamped_0_100(self):
        out = _make_output(confidence=0.0)
        score, _ = compute_quality_score(out)
        assert 0.0 <= score <= 100.0

    def test_score_should_be_rounded_to_one_decimal(self):
        out = _make_output(confidence=0.73)
        score, _ = compute_quality_score(out)
        assert score == round(score, 1)
