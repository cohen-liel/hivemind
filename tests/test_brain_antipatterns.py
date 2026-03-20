"""Tests for brain_antipatterns.py — AntiPatternCatalog.

Covers pattern registration, detection for each built-in AP,
scan aggregation, match history, top patterns, and summary.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from brain_antipatterns import (
    AntiPattern,
    AntiPatternCatalog,
    AntiPatternMatch,
    Severity,
    _detect_missing_artifacts,
    _detect_scope_creep,
    _detect_summary_mismatch,
    _status_is,
    get_anti_pattern_catalog,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_output(**kwargs) -> SimpleNamespace:
    defaults = {
        "confidence": 0.8,
        "status": "completed",
        "structured_artifacts": [],
        "artifacts": [],
        "issues": [],
        "blockers": [],
        "total_tokens": 5000,
        "output_tokens": 1000,
        "turns_used": 5,
        "summary": "",
        "followups": [],
        "task_id": "test_task",
        "failure_details": "",
        "failure_category": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_input(**kwargs) -> SimpleNamespace:
    defaults = {
        "required_artifacts": [],
        "role": "backend_developer",
        "files_scope": [],
        "id": "task_001",
        "is_remediation": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_artifact(art_type: str, data: dict | None = None):
    return SimpleNamespace(type=art_type, data=data or {})


# ---------------------------------------------------------------------------
# Severity enum
# ---------------------------------------------------------------------------


class TestSeverity:
    def test_severity_values(self):
        assert Severity.LOW.value == "low"
        assert Severity.CRITICAL.value == "critical"

    def test_severity_is_str_enum(self):
        assert isinstance(Severity.HIGH, str)


# ---------------------------------------------------------------------------
# _status_is helper
# ---------------------------------------------------------------------------


class TestStatusIs:
    def test_status_is_when_string_match_should_return_true(self):
        out = _make_output(status="completed")
        assert _status_is(out, "completed") is True

    def test_status_is_when_enum_match_should_return_true(self):
        status_enum = MagicMock()
        status_enum.value = "failed"
        out = _make_output(status=status_enum)
        assert _status_is(out, "failed") is True

    def test_status_is_when_no_status_should_return_false(self):
        out = SimpleNamespace()
        assert _status_is(out, "completed") is False

    def test_status_is_when_mismatch_should_return_false(self):
        out = _make_output(status="completed")
        assert _status_is(out, "failed") is False


# ---------------------------------------------------------------------------
# AntiPattern.check()
# ---------------------------------------------------------------------------


class TestAntiPatternCheck:
    def test_check_when_matches_should_increment_counter(self):
        p = AntiPattern(
            id="TEST-001", name="Test", description="desc",
            severity=Severity.LOW, category="test",
            remediation="fix it",
            detector=lambda out, _: True,
        )
        assert p.match_count == 0
        result = p.check(_make_output())
        assert result is True
        assert p.match_count == 1
        assert p.last_matched is not None

    def test_check_when_no_match_should_not_increment(self):
        p = AntiPattern(
            id="TEST-002", name="Test", description="desc",
            severity=Severity.LOW, category="test",
            remediation="fix it",
            detector=lambda out, _: False,
        )
        result = p.check(_make_output())
        assert result is False
        assert p.match_count == 0

    def test_check_when_detector_throws_should_return_false(self):
        p = AntiPattern(
            id="TEST-003", name="Test", description="desc",
            severity=Severity.LOW, category="test",
            remediation="fix it",
            detector=lambda out, _: 1 / 0,  # Raises
        )
        assert p.check(_make_output()) is False

    def test_to_dict_should_have_all_fields(self):
        p = AntiPattern(
            id="AP-999", name="Name", description="Desc",
            severity=Severity.HIGH, category="cat",
            remediation="rem",
            detector=lambda o, i: False,
        )
        d = p.to_dict()
        assert d["id"] == "AP-999"
        assert d["severity"] == "high"
        assert d["match_count"] == 0


# ---------------------------------------------------------------------------
# Built-in pattern detectors (AP-001 through AP-015)
# ---------------------------------------------------------------------------


class TestBuiltinDetectors:
    def test_ap001_when_default_confidence_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(confidence=0.5)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-001" in ids

    def test_ap001_when_explicit_confidence_should_not_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(confidence=0.9)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-001" not in ids

    def test_ap002_when_completed_no_artifacts_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="completed", artifacts=[])
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-002" in ids

    def test_ap002_when_has_artifacts_should_not_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="completed", artifacts=["file.py"])
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-002" not in ids

    def test_ap003_when_token_explosion_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(total_tokens=70_000)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-003" in ids

    def test_ap003_when_normal_tokens_should_not_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(total_tokens=20_000)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-003" not in ids

    def test_ap005_when_silent_failure_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="failed", failure_details="", failure_category=None)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-005" in ids

    def test_ap005_when_failure_with_details_should_not_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="failed", failure_details="timeout occurred",
                           failure_category="TIMEOUT")
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-005" not in ids

    def test_ap009_when_zero_turns_completed_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="completed", turns_used=0)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-009" in ids

    def test_ap009_when_normal_turns_should_not_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="completed", turns_used=10)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-009" not in ids

    def test_ap010_when_completed_with_blockers_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="completed", blockers=["something"])
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-010" in ids

    def test_ap012_when_low_confidence_completion_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="completed", confidence=0.2)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-012" in ids

    def test_ap013_when_excessive_issues_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(issues=list(range(12)))
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-013" in ids

    def test_ap014_when_failed_with_many_followups_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(status="failed", followups=["a", "b", "c"])
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-014" in ids

    def test_ap015_when_high_output_tokens_should_match(self):
        cat = AntiPatternCatalog()
        out = _make_output(output_tokens=25_000)
        matches = cat.scan(out)
        ids = [m.pattern_id for m in matches]
        assert "AP-015" in ids


# ---------------------------------------------------------------------------
# Detector helpers
# ---------------------------------------------------------------------------


class TestDetectorHelpers:
    def test_detect_scope_creep_when_exceeds_should_return_true(self):
        # scope=2 files, artifacts=7 => 7 > 2*1.5=3 and 7-2=5 >= 3
        inp = _make_input(files_scope=["a.py", "b.py"])
        out = _make_output(artifacts=["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"])
        assert _detect_scope_creep(out, inp) is True

    def test_detect_scope_creep_when_within_limit_should_return_false(self):
        inp = _make_input(files_scope=["a.py", "b.py", "c.py"])
        out = _make_output(artifacts=["a.py", "b.py", "c.py", "d.py"])
        assert _detect_scope_creep(out, inp) is False

    def test_detect_scope_creep_when_no_input_should_return_false(self):
        out = _make_output(artifacts=["a.py"])
        assert _detect_scope_creep(out, None) is False

    def test_detect_missing_artifacts_when_missing_should_return_true(self):
        inp = _make_input(required_artifacts=["test_report"])
        out = _make_output(status="completed", structured_artifacts=[])
        assert _detect_missing_artifacts(out, inp) is True

    def test_detect_missing_artifacts_when_all_present_should_return_false(self):
        inp = _make_input(required_artifacts=["test_report"])
        art = _make_artifact("test_report", {})
        out = _make_output(status="completed", structured_artifacts=[art])
        assert _detect_missing_artifacts(out, inp) is False

    def test_detect_missing_artifacts_when_not_completed_should_return_false(self):
        inp = _make_input(required_artifacts=["test_report"])
        out = _make_output(status="failed", structured_artifacts=[])
        assert _detect_missing_artifacts(out, inp) is False

    def test_detect_summary_mismatch_when_many_unmatched_should_return_true(self):
        out = _make_output(
            summary="Modified foo.py, bar.py, baz.py, qux.py, config.yaml and utils.js",
            artifacts=["main.py"],
        )
        assert _detect_summary_mismatch(out, None) is True

    def test_detect_summary_mismatch_when_no_summary_should_return_false(self):
        out = _make_output(summary="", artifacts=["a.py"])
        assert _detect_summary_mismatch(out, None) is False


# ---------------------------------------------------------------------------
# Catalog operations
# ---------------------------------------------------------------------------


class TestCatalogOperations:
    def test_register_when_custom_pattern_should_be_findable(self):
        cat = AntiPatternCatalog()
        custom = AntiPattern(
            id="CUSTOM-001", name="Custom", description="Custom detector",
            severity=Severity.LOW, category="custom",
            remediation="fix",
            detector=lambda o, i: True,
        )
        cat.register(custom)
        assert cat.get_pattern("CUSTOM-001") is not None

    def test_list_patterns_when_builtins_should_have_15(self):
        cat = AntiPatternCatalog()
        patterns = cat.list_patterns()
        assert len(patterns) == 15

    def test_get_pattern_when_nonexistent_should_return_none(self):
        cat = AntiPatternCatalog()
        assert cat.get_pattern("DOES-NOT-EXIST") is None

    def test_scan_when_matches_should_record_history(self):
        cat = AntiPatternCatalog()
        out = _make_output(confidence=0.5)
        cat.scan(out)
        history = cat.get_match_history()
        assert len(history) > 0
        assert any(m["pattern_id"] == "AP-001" for m in history)

    def test_get_match_history_when_role_filter_should_scope(self):
        cat = AntiPatternCatalog()
        out = _make_output(confidence=0.5, task_id="t1")
        inp = _make_input(role="pm")
        cat.scan(out, inp)
        history = cat.get_match_history(role="pm")
        assert all(m["role"] == "pm" for m in history)
        other = cat.get_match_history(role="nonexistent_role")
        assert len(other) == 0

    def test_get_top_patterns_when_matches_should_sort_by_count(self):
        cat = AntiPatternCatalog()
        # Trigger AP-001 multiple times
        for _ in range(5):
            cat.scan(_make_output(confidence=0.5))
        top = cat.get_top_patterns(3)
        assert len(top) <= 3
        assert top[0]["match_count"] >= top[-1]["match_count"]

    def test_get_summary_should_have_correct_shape(self):
        cat = AntiPatternCatalog()
        cat.scan(_make_output(confidence=0.5))
        s = cat.get_summary()
        assert "total_patterns" in s
        assert "total_matches" in s
        assert "matches_by_severity" in s
        assert "top_patterns" in s
        assert "recent_matches" in s

    def test_match_history_when_exceeds_max_should_trim(self):
        cat = AntiPatternCatalog()
        cat._max_history = 5
        for i in range(10):
            cat.scan(_make_output(confidence=0.5, task_id=f"t{i}"))
        assert len(cat._match_history) <= 5


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_anti_pattern_catalog_should_return_same_instance(self):
        c1 = get_anti_pattern_catalog()
        c2 = get_anti_pattern_catalog()
        assert c1 is c2
