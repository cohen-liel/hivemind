"""Tests for WatchdogTestRunner — subprocess execution, parsing, scheduling, events.

task_id: task_007
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.health import SuiteResult, TestFailure, TestRunResult
from src.watchdog.test_runner import WatchdogTestRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc_mock(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock subprocess with given stdout/stderr/returncode."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    proc.returncode = returncode
    return proc


# ===========================================================================
# 1. Constructor & properties
# ===========================================================================


class TestWatchdogInit:
    def test_default_properties(self):
        runner = WatchdogTestRunner()
        assert runner.is_running is False
        assert runner.scheduler_active is False
        assert runner.last_run is None
        assert runner.recent_runs == []
        assert runner.interval == 1800

    def test_custom_interval(self):
        runner = WatchdogTestRunner(interval=60, max_history=5, suite_timeout=30)
        assert runner.interval == 60
        assert runner._max_history == 5
        assert runner._suite_timeout == 30


# ===========================================================================
# 2. pytest output parser
# ===========================================================================


class TestParsePytestOutput:
    def setup_method(self):
        self.runner = WatchdogTestRunner()

    def test_parse_pytest_when_all_passed_should_return_passed(self):
        stdout = "===== 42 passed in 5.67s ====="
        result = self.runner._parse_pytest_output(stdout, "", 0, 5.67)
        assert result.suite == "backend"
        assert result.status == "passed"
        assert result.passed == 42
        assert result.failed == 0
        assert result.total == 42

    def test_parse_pytest_when_mixed_results_should_count_correctly(self):
        stdout = "===== 10 passed, 3 failed, 2 skipped in 8.10s ====="
        result = self.runner._parse_pytest_output(stdout, "", 1, 8.10)
        assert result.passed == 10
        assert result.failed == 3
        assert result.skipped == 2
        assert result.total == 15
        assert result.status == "failed"

    def test_parse_pytest_when_failures_present_should_extract_names(self):
        stdout = (
            "FAILED tests/test_foo.py::test_bar - AssertionError\n"
            "FAILED tests/test_baz.py::test_qux\n"
            "===== 5 passed, 2 failed in 3.00s ====="
        )
        result = self.runner._parse_pytest_output(stdout, "", 1, 3.0)
        assert len(result.failures) == 2
        assert result.failures[0].test_name == "tests/test_foo.py::test_bar"
        assert "AssertionError" in result.failures[0].message
        assert result.failures[1].test_name == "tests/test_baz.py::test_qux"

    def test_parse_pytest_when_no_summary_and_nonzero_exit_should_return_error(self):
        result = self.runner._parse_pytest_output("", "ImportError: no module", 2, 0.5)
        assert result.status == "error"
        assert "ImportError" in (result.error_message or "")

    def test_parse_pytest_when_short_format_should_parse(self):
        stdout = "42 passed, 1 warning in 5.67s"
        result = self.runner._parse_pytest_output(stdout, "", 0, 5.67)
        assert result.passed == 42
        assert result.status == "passed"

    def test_parse_pytest_when_errors_present_should_mark_failed(self):
        stdout = "===== 5 passed, 1 errors in 2.00s ====="
        result = self.runner._parse_pytest_output(stdout, "", 1, 2.0)
        assert result.errors == 1
        assert result.status == "failed"

    def test_parse_pytest_when_deselected_should_count_as_skipped(self):
        stdout = "===== 5 passed, 3 deselected in 1.00s ====="
        result = self.runner._parse_pytest_output(stdout, "", 0, 1.0)
        assert result.skipped == 3
        assert result.total == 8


# ===========================================================================
# 3. vitest output parser
# ===========================================================================


class TestParseVitestOutput:
    def setup_method(self):
        self.runner = WatchdogTestRunner()

    def test_parse_vitest_json_when_all_passed_should_return_passed(self):
        json_out = json.dumps({
            "testResults": [{
                "assertionResults": [
                    {"status": "passed", "fullName": "test_a"},
                    {"status": "passed", "fullName": "test_b"},
                ]
            }]
        })
        result = self.runner._parse_vitest_output(json_out, "", 0, 1.0)
        assert result.suite == "frontend"
        assert result.status == "passed"
        assert result.passed == 2
        assert result.total == 2

    def test_parse_vitest_json_when_failures_should_extract_details(self):
        json_out = json.dumps({
            "testResults": [{
                "assertionResults": [
                    {"status": "passed", "fullName": "ok_test"},
                    {"status": "failed", "fullName": "bad_test", "failureMessages": ["expected 1 got 2"]},
                ]
            }]
        })
        result = self.runner._parse_vitest_output(json_out, "", 1, 2.0)
        assert result.status == "failed"
        assert result.failed == 1
        assert len(result.failures) == 1
        assert result.failures[0].test_name == "bad_test"
        assert "expected 1 got 2" in result.failures[0].message

    def test_parse_vitest_json_when_skipped_should_count(self):
        json_out = json.dumps({
            "testResults": [{
                "assertionResults": [
                    {"status": "passed", "fullName": "a"},
                    {"status": "pending", "fullName": "b"},
                    {"status": "todo", "fullName": "c"},
                ]
            }]
        })
        result = self.runner._parse_vitest_output(json_out, "", 0, 0.5)
        assert result.skipped == 2
        assert result.passed == 1

    def test_parse_vitest_when_no_json_should_fallback_to_text(self):
        text = "Tests  5 passed, 2 failed"
        result = self.runner._parse_vitest_output(text, "", 1, 3.0)
        assert result.passed == 5
        assert result.failed == 2
        assert result.status == "failed"

    def test_parse_vitest_when_empty_output_and_error_should_return_error(self):
        result = self.runner._parse_vitest_output("", "crash!", 1, 0.1)
        assert result.status == "error"
        assert "crash!" in (result.error_message or "")

    def test_parse_vitest_json_with_preceding_console_output(self):
        stdout = "some console log\n" + json.dumps({
            "testResults": [{
                "assertionResults": [
                    {"status": "passed", "fullName": "x"},
                ]
            }]
        })
        result = self.runner._parse_vitest_output(stdout, "", 0, 1.0)
        assert result.passed == 1
        assert result.status == "passed"

    def test_parse_vitest_fallback_text_when_separate_pass_fail_lines(self):
        text = "3 pass\n1 fail"
        result = self.runner._parse_vitest_output(text, "", 1, 2.0)
        assert result.passed == 3
        assert result.failed == 1


# ===========================================================================
# 4. run_backend_tests — subprocess execution
# ===========================================================================


class TestRunBackendTests:
    def setup_method(self):
        self.runner = WatchdogTestRunner(suite_timeout=10)

    @pytest.mark.asyncio
    async def test_run_backend_when_pytest_passes_should_return_passed(self):
        proc = _make_proc_mock("===== 10 passed in 1.00s =====", "", 0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch("asyncio.wait_for", return_value=await proc.communicate()):
                result = await self.runner.run_backend_tests()
        assert result.suite == "backend"
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_run_backend_when_timeout_should_return_error(self):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = AsyncMock()
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                result = await self.runner.run_backend_tests()
        assert result.status == "error"
        assert "Timed out" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_run_backend_when_pytest_not_found_should_return_error(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await self.runner.run_backend_tests()
        assert result.status == "error"
        assert "not found" in (result.error_message or "").lower()


# ===========================================================================
# 5. run_frontend_tests — subprocess execution
# ===========================================================================


class TestRunFrontendTests:
    def setup_method(self):
        self.runner = WatchdogTestRunner(suite_timeout=10)

    @pytest.mark.asyncio
    async def test_run_frontend_when_no_frontend_dir_should_return_error(self):
        with patch("os.path.isdir", return_value=False):
            result = await self.runner.run_frontend_tests()
        assert result.status == "error"
        assert "not found" in (result.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_run_frontend_when_timeout_should_return_error(self):
        with patch("os.path.isdir", return_value=True):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_exec.return_value = AsyncMock()
                with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                    result = await self.runner.run_frontend_tests()
        assert result.status == "error"
        assert "Timed out" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_run_frontend_when_npx_not_found_should_return_error(self):
        with patch("os.path.isdir", return_value=True):
            with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
                result = await self.runner.run_frontend_tests()
        assert result.status == "error"
        assert "npx" in (result.error_message or "").lower()


# ===========================================================================
# 6. run_all — integration of both suites + event emission
# ===========================================================================


class TestRunAll:
    def setup_method(self):
        self.runner = WatchdogTestRunner()

    @pytest.mark.asyncio
    async def test_run_all_when_both_pass_should_return_passed(self):
        backend = SuiteResult(suite="backend", status="passed", total=5, passed=5)
        frontend = SuiteResult(suite="frontend", status="passed", total=3, passed=3)

        with patch.object(self.runner, "run_backend_tests", return_value=backend):
            with patch.object(self.runner, "run_frontend_tests", return_value=frontend):
                with patch.object(self.runner, "_emit_report", new_callable=AsyncMock):
                    result = await self.runner.run_all(trigger="manual")

        assert result.status == "passed"
        assert result.trigger == "manual"
        assert len(result.suites) == 2
        assert "8/8 passed" in result.summary

    @pytest.mark.asyncio
    async def test_run_all_when_one_fails_should_return_failed(self):
        backend = SuiteResult(suite="backend", status="failed", total=5, passed=3, failed=2)
        frontend = SuiteResult(suite="frontend", status="passed", total=3, passed=3)

        with patch.object(self.runner, "run_backend_tests", return_value=backend):
            with patch.object(self.runner, "run_frontend_tests", return_value=frontend):
                with patch.object(self.runner, "_emit_report", new_callable=AsyncMock):
                    result = await self.runner.run_all()

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_run_all_when_suite_raises_should_return_error(self):
        backend = SuiteResult(suite="backend", status="passed", total=5, passed=5)

        with patch.object(self.runner, "run_backend_tests", return_value=backend):
            with patch.object(self.runner, "run_frontend_tests", side_effect=RuntimeError("boom")):
                with patch.object(self.runner, "_emit_report", new_callable=AsyncMock):
                    result = await self.runner.run_all()

        assert result.status == "error"
        error_suites = [s for s in result.suites if s.status == "error"]
        assert len(error_suites) == 1

    @pytest.mark.asyncio
    async def test_run_all_should_store_result_in_history(self):
        backend = SuiteResult(suite="backend", status="passed", total=1, passed=1)
        frontend = SuiteResult(suite="frontend", status="passed", total=1, passed=1)

        with patch.object(self.runner, "run_backend_tests", return_value=backend):
            with patch.object(self.runner, "run_frontend_tests", return_value=frontend):
                with patch.object(self.runner, "_emit_report", new_callable=AsyncMock):
                    result = await self.runner.run_all()

        assert self.runner.last_run is result
        assert len(self.runner.recent_runs) == 1

    @pytest.mark.asyncio
    async def test_run_all_should_emit_watchdog_report(self):
        backend = SuiteResult(suite="backend", status="passed", total=1, passed=1)
        frontend = SuiteResult(suite="frontend", status="passed", total=1, passed=1)

        emit_mock = AsyncMock()
        with patch.object(self.runner, "run_backend_tests", return_value=backend):
            with patch.object(self.runner, "run_frontend_tests", return_value=frontend):
                with patch.object(self.runner, "_emit_report", emit_mock):
                    await self.runner.run_all()

        emit_mock.assert_called_once()
        emitted = emit_mock.call_args[0][0]
        assert isinstance(emitted, TestRunResult)

    @pytest.mark.asyncio
    async def test_run_all_should_clear_is_running_after_completion(self):
        backend = SuiteResult(suite="backend", status="passed", total=1, passed=1)
        frontend = SuiteResult(suite="frontend", status="passed", total=1, passed=1)

        with patch.object(self.runner, "run_backend_tests", return_value=backend):
            with patch.object(self.runner, "run_frontend_tests", return_value=frontend):
                with patch.object(self.runner, "_emit_report", new_callable=AsyncMock):
                    await self.runner.run_all()

        assert self.runner.is_running is False

    @pytest.mark.asyncio
    async def test_run_all_should_clear_is_running_on_exception(self):
        with patch.object(self.runner, "run_backend_tests", side_effect=Exception("fail")):
            with patch.object(self.runner, "run_frontend_tests", side_effect=Exception("fail")):
                with patch.object(self.runner, "_emit_report", new_callable=AsyncMock):
                    await self.runner.run_all()

        assert self.runner.is_running is False


# ===========================================================================
# 7. Concurrent run prevention
# ===========================================================================


class TestConcurrentRunPrevention:
    @pytest.mark.asyncio
    async def test_run_all_when_already_running_should_raise(self):
        runner = WatchdogTestRunner()
        # Simulate already running
        runner._is_running = True
        with pytest.raises(RuntimeError, match="already in progress"):
            await runner.run_all()


# ===========================================================================
# 8. History management
# ===========================================================================


class TestHistoryManagement:
    @pytest.mark.asyncio
    async def test_history_should_respect_max_history(self):
        runner = WatchdogTestRunner(max_history=3)
        backend = SuiteResult(suite="backend", status="passed", total=1, passed=1)
        frontend = SuiteResult(suite="frontend", status="passed", total=1, passed=1)

        for _ in range(5):
            with patch.object(runner, "run_backend_tests", return_value=backend):
                with patch.object(runner, "run_frontend_tests", return_value=frontend):
                    with patch.object(runner, "_emit_report", new_callable=AsyncMock):
                        await runner.run_all()

        assert len(runner.recent_runs) == 3

    def test_last_run_when_no_history_should_return_none(self):
        runner = WatchdogTestRunner()
        assert runner.last_run is None

    def test_recent_runs_when_no_history_should_return_empty_list(self):
        runner = WatchdogTestRunner()
        assert runner.recent_runs == []


# ===========================================================================
# 9. Scheduler lifecycle
# ===========================================================================


class TestSchedulerLifecycle:
    @pytest.mark.asyncio
    async def test_start_scheduler_should_activate(self):
        runner = WatchdogTestRunner(interval=3600)
        await runner.start_scheduler()
        try:
            assert runner.scheduler_active is True
            assert runner._scheduler_task is not None
            assert not runner._scheduler_task.done()
        finally:
            await runner.stop_scheduler()

    @pytest.mark.asyncio
    async def test_stop_scheduler_should_deactivate(self):
        runner = WatchdogTestRunner(interval=3600)
        await runner.start_scheduler()
        await runner.stop_scheduler()
        assert runner.scheduler_active is False
        assert runner._scheduler_task is None

    @pytest.mark.asyncio
    async def test_start_scheduler_when_already_active_should_noop(self):
        runner = WatchdogTestRunner(interval=3600)
        await runner.start_scheduler()
        task1 = runner._scheduler_task
        await runner.start_scheduler()  # should be a no-op
        assert runner._scheduler_task is task1
        await runner.stop_scheduler()

    @pytest.mark.asyncio
    async def test_stop_scheduler_when_not_active_should_noop(self):
        runner = WatchdogTestRunner()
        # Should not raise
        await runner.stop_scheduler()
        assert runner.scheduler_active is False

    @pytest.mark.asyncio
    async def test_scheduler_loop_cancellation_should_not_leak(self):
        runner = WatchdogTestRunner(interval=3600)
        await runner.start_scheduler()
        task = runner._scheduler_task
        await runner.stop_scheduler()
        assert task.done()


# ===========================================================================
# 10. EventBus emission
# ===========================================================================


class TestEmitReport:
    @pytest.mark.asyncio
    async def test_emit_report_should_publish_correct_payload(self):
        runner = WatchdogTestRunner()
        result = TestRunResult(
            run_id="abc123",
            status="passed",
            trigger="manual",
            started_at="2026-01-01T00:00:00Z",
            finished_at="2026-01-01T00:01:00Z",
            duration_seconds=60.0,
            summary="all good",
            suites=[SuiteResult(suite="backend", status="passed", total=5, passed=5)],
        )

        mock_bus = AsyncMock()
        with patch("dashboard.events.event_bus", mock_bus):
            await runner._emit_report(result)

        mock_bus.publish.assert_called_once()
        payload = mock_bus.publish.call_args[0][0]
        assert payload["type"] == "watchdog_report"
        assert payload["run_id"] == "abc123"
        assert payload["status"] == "passed"
        assert len(payload["suites"]) == 1

    @pytest.mark.asyncio
    async def test_emit_report_when_eventbus_fails_should_not_raise(self):
        runner = WatchdogTestRunner()
        result = TestRunResult(
            run_id="x", status="passed", trigger="manual",
            started_at="2026-01-01T00:00:00Z",
        )
        mock_bus = AsyncMock()
        mock_bus.publish.side_effect = Exception("bus down")
        with patch("dashboard.events.event_bus", mock_bus):
            # Should NOT raise
            await runner._emit_report(result)
