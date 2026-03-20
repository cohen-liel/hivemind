"""Tests for brain_performance.py — AgentPerformanceTracker.

Covers record, get_role_stats, get_all_stats, get_summary,
failure distribution, rolling window, and persistence.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from brain_performance import (
    AgentPerformanceTracker,
    RoleStats,
    TaskMetrics,
    _MAX_HISTORY_PER_ROLE,
    get_performance_tracker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_task(tracker: AgentPerformanceTracker, role: str = "developer",
                 status: str = "completed", quality: float = 80.0, **kw):
    """Convenience wrapper around tracker.record()."""
    defaults = {
        "task_id": f"task_{id(kw)}",
        "role": role,
        "status": status,
        "quality_score": quality,
        "confidence": 0.8,
        "total_tokens": 10_000,
        "input_tokens": 7_000,
        "output_tokens": 3_000,
        "duration_seconds": 30.0,
        "turns_used": 5,
    }
    defaults.update(kw)
    tracker.record(**defaults)


# ---------------------------------------------------------------------------
# TaskMetrics dataclass
# ---------------------------------------------------------------------------


class TestTaskMetrics:
    def test_taskmetrics_when_created_should_have_auto_timestamp(self):
        m = TaskMetrics(
            task_id="t1", role="dev", status="completed",
            quality_score=90.0, confidence=0.9, total_tokens=1000,
            input_tokens=700, output_tokens=300,
            duration_seconds=10.0, turns_used=3,
        )
        assert m.timestamp > 0
        assert m.is_remediation is False
        assert m.failure_category is None


# ---------------------------------------------------------------------------
# RoleStats
# ---------------------------------------------------------------------------


class TestRoleStats:
    def test_to_dict_when_default_should_have_all_keys(self):
        rs = RoleStats(role="pm")
        d = rs.to_dict()
        assert d["role"] == "pm"
        assert d["total_tasks"] == 0
        assert "avg_quality_score" in d
        assert "success_rate" in d
        assert "token_efficiency" in d

    def test_to_dict_when_values_should_round(self):
        rs = RoleStats(role="dev", avg_quality_score=80.1234, success_rate=0.91234)
        d = rs.to_dict()
        assert d["avg_quality_score"] == 80.1
        assert d["success_rate"] == 0.912


# ---------------------------------------------------------------------------
# AgentPerformanceTracker — recording
# ---------------------------------------------------------------------------


class TestTrackerRecord:
    def test_record_when_first_task_should_create_role_entry(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="pm", task_id="t1")
        assert "pm" in tracker._history
        assert len(tracker._history["pm"]) == 1

    def test_record_when_multiple_should_accumulate(self):
        tracker = AgentPerformanceTracker()
        for i in range(5):
            _record_task(tracker, role="dev", task_id=f"t{i}")
        assert len(tracker._history["dev"]) == 5

    def test_record_when_exceeds_max_should_trim(self):
        tracker = AgentPerformanceTracker()
        for i in range(_MAX_HISTORY_PER_ROLE + 20):
            _record_task(tracker, role="dev", task_id=f"t{i}")
        assert len(tracker._history["dev"]) == _MAX_HISTORY_PER_ROLE

    def test_record_when_remediation_should_flag(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="r1", is_remediation=True)
        assert tracker._history["dev"][0].is_remediation is True


# ---------------------------------------------------------------------------
# AgentPerformanceTracker — stats
# ---------------------------------------------------------------------------


class TestTrackerStats:
    def test_get_role_stats_when_empty_should_return_zeros(self):
        tracker = AgentPerformanceTracker()
        stats = tracker.get_role_stats("nonexistent")
        assert stats.total_tasks == 0
        assert stats.success_rate == 0.0

    def test_get_role_stats_when_all_completed_should_have_100_success(self):
        tracker = AgentPerformanceTracker()
        for i in range(3):
            _record_task(tracker, role="dev", task_id=f"t{i}", status="completed", quality=80.0)
        stats = tracker.get_role_stats("dev")
        assert stats.total_tasks == 3
        assert stats.completed == 3
        assert stats.success_rate == 1.0

    def test_get_role_stats_when_mixed_should_compute_averages(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="t1", status="completed", quality=90.0,
                     total_tokens=10000, duration_seconds=30.0, confidence=0.9)
        _record_task(tracker, role="dev", task_id="t2", status="completed", quality=70.0,
                     total_tokens=20000, duration_seconds=60.0, confidence=0.7)
        _record_task(tracker, role="dev", task_id="t3", status="failed", quality=10.0,
                     failure_category="TOOL_ERROR")
        stats = tracker.get_role_stats("dev")
        assert stats.total_tasks == 3
        assert stats.completed == 2
        assert stats.failed == 1
        assert stats.success_rate == pytest.approx(2 / 3, abs=0.01)
        assert stats.avg_quality_score == pytest.approx(80.0, abs=0.1)

    def test_get_role_stats_when_remediation_should_track_rate(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="t1")
        _record_task(tracker, role="dev", task_id="t2", is_remediation=True)
        stats = tracker.get_role_stats("dev")
        assert stats.remediation_rate == pytest.approx(0.5, abs=0.01)

    def test_token_efficiency_when_computed_should_be_quality_per_1k(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="t1", quality=80.0, total_tokens=10_000)
        stats = tracker.get_role_stats("dev")
        # 80 / (10000/1000) = 80/10 = 8.0
        assert stats.token_efficiency == pytest.approx(8.0, abs=0.1)


# ---------------------------------------------------------------------------
# AgentPerformanceTracker — all stats & summary
# ---------------------------------------------------------------------------


class TestTrackerSummary:
    def test_get_all_stats_when_multi_role_should_cover_all(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="pm", task_id="t1")
        _record_task(tracker, role="dev", task_id="t2")
        all_stats = tracker.get_all_stats()
        assert "pm" in all_stats
        assert "dev" in all_stats

    def test_get_summary_when_tasks_exist_should_have_correct_shape(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="t1")
        _record_task(tracker, role="dev", task_id="t2", status="failed")
        summary = tracker.get_summary()
        assert summary["total_tasks_tracked"] == 2
        assert summary["total_completed"] == 1
        assert summary["total_failed"] == 1
        assert "dev" in summary["roles"]

    def test_get_summary_when_empty_should_return_zeroes(self):
        tracker = AgentPerformanceTracker()
        summary = tracker.get_summary()
        assert summary["total_tasks_tracked"] == 0
        assert summary["overall_success_rate"] == 0.0


# ---------------------------------------------------------------------------
# Failure distribution
# ---------------------------------------------------------------------------


class TestFailureDistribution:
    def test_distribution_when_failures_should_count_categories(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="t1", status="failed",
                     failure_category="TOOL_ERROR")
        _record_task(tracker, role="dev", task_id="t2", status="failed",
                     failure_category="TOOL_ERROR")
        _record_task(tracker, role="dev", task_id="t3", status="failed",
                     failure_category="TIMEOUT")
        dist = tracker.get_failure_distribution()
        assert dist["TOOL_ERROR"] == 2
        assert dist["TIMEOUT"] == 1

    def test_distribution_when_filtered_by_role_should_scope(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="t1", status="failed",
                     failure_category="TOOL_ERROR")
        _record_task(tracker, role="pm", task_id="t2", status="failed",
                     failure_category="TIMEOUT")
        dist = tracker.get_failure_distribution(role="dev")
        assert "TOOL_ERROR" in dist
        assert "TIMEOUT" not in dist

    def test_distribution_when_no_failures_should_be_empty(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="t1")
        dist = tracker.get_failure_distribution()
        assert dist == {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_to_disk_when_valid_dir_should_create_file(self):
        tracker = AgentPerformanceTracker()
        _record_task(tracker, role="dev", task_id="t1")
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker.save_to_disk(tmpdir)
            path = os.path.join(tmpdir, ".hivemind", "agent_performance.json")
            assert os.path.exists(path)
            data = json.loads(open(path).read())
            assert data["total_tasks_tracked"] == 1

    def test_save_to_disk_when_invalid_dir_should_not_raise(self):
        tracker = AgentPerformanceTracker()
        tracker.save_to_disk("/nonexistent/path/that/doesnt/exist")  # Should not raise

    def test_load_from_disk_when_no_file_should_not_raise(self):
        tracker = AgentPerformanceTracker()
        tracker.load_from_disk("/nonexistent/path")  # Should not raise


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_performance_tracker_should_return_same_instance(self):
        t1 = get_performance_tracker()
        t2 = get_performance_tracker()
        assert t1 is t2
