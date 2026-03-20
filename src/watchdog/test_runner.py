"""WatchdogTestRunner — runs backend (pytest) and frontend (vitest) test suites
as async subprocesses, collects structured results, and emits events via EventBus.

Usage::

    from src.watchdog.test_runner import watchdog_runner
    await watchdog_runner.start_scheduler()   # periodic runs
    result = await watchdog_runner.run_all()   # on-demand
    await watchdog_runner.stop_scheduler()     # graceful shutdown
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from src.api.health import SuiteResult, TestFailure, TestRunResult

logger = logging.getLogger("watchdog.test_runner")

# ---------------------------------------------------------------------------
# Configuration (env-driven with sane defaults)
# ---------------------------------------------------------------------------
WATCHDOG_INTERVAL_SECONDS = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "1800"))
WATCHDOG_MAX_HISTORY = int(os.getenv("WATCHDOG_MAX_HISTORY", "10"))
WATCHDOG_SUITE_TIMEOUT = int(os.getenv("WATCHDOG_SUITE_TIMEOUT", "300"))  # 5 min


class WatchdogTestRunner:
    """Runs pytest and vitest suites, stores results, emits EventBus events."""

    def __init__(
        self,
        interval: int = WATCHDOG_INTERVAL_SECONDS,
        max_history: int = WATCHDOG_MAX_HISTORY,
        suite_timeout: int = WATCHDOG_SUITE_TIMEOUT,
    ) -> None:
        self._interval = interval
        self._max_history = max_history
        self._suite_timeout = suite_timeout

        self._history: deque[TestRunResult] = deque(maxlen=max_history)
        self._is_running = False
        self._scheduler_task: asyncio.Task | None = None
        self._scheduler_active = False
        self._lock = asyncio.Lock()

        # Project root — two levels up from src/watchdog/
        self._project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def scheduler_active(self) -> bool:
        return self._scheduler_active

    @property
    def interval(self) -> int:
        return self._interval

    @property
    def last_run(self) -> TestRunResult | None:
        return self._history[-1] if self._history else None

    @property
    def recent_runs(self) -> list[TestRunResult]:
        return list(self._history)

    async def run_all(self, trigger: str = "manual") -> TestRunResult:
        """Run both backend and frontend suites. Non-reentrant."""
        async with self._lock:
            if self._is_running:
                raise RuntimeError("A test run is already in progress")
            self._is_running = True

        run_id = uuid.uuid4().hex[:12]
        started = datetime.now(timezone.utc)
        t0 = time.monotonic()

        try:
            backend, frontend = await asyncio.gather(
                self.run_backend_tests(),
                self.run_frontend_tests(),
                return_exceptions=True,
            )

            suites: list[SuiteResult] = []
            for res in (backend, frontend):
                if isinstance(res, BaseException):
                    suite_name = "backend" if res is backend else "frontend"
                    suites.append(SuiteResult(
                        suite=suite_name,
                        status="error",
                        error_message=str(res),
                    ))
                else:
                    suites.append(res)

            duration = round(time.monotonic() - t0, 2)
            overall = "passed" if all(s.status == "passed" for s in suites) else "failed"
            if any(s.status == "error" for s in suites):
                overall = "error"

            total_pass = sum(s.passed for s in suites)
            total_fail = sum(s.failed for s in suites)
            total_tests = sum(s.total for s in suites)
            summary = f"{total_pass}/{total_tests} passed, {total_fail} failed ({duration}s)"

            result = TestRunResult(
                run_id=run_id,
                status=overall,
                trigger=trigger,
                started_at=started.isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=duration,
                suites=suites,
                summary=summary,
            )

            self._history.append(result)
            await self._emit_report(result)
            return result

        finally:
            self._is_running = False

    async def run_backend_tests(self) -> SuiteResult:
        """Execute pytest and parse results."""
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-m", "pytest",
                "--tb=short", "--no-header", "-q",
                "--override-ini=addopts=",
                cwd=self._project_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self._suite_timeout
            )
        except asyncio.TimeoutError:
            logger.error("Backend test suite timed out after %ds", self._suite_timeout)
            return SuiteResult(
                suite="backend",
                status="error",
                duration_seconds=round(time.monotonic() - t0, 2),
                error_message=f"Timed out after {self._suite_timeout}s",
            )
        except FileNotFoundError:
            return SuiteResult(
                suite="backend",
                status="error",
                duration_seconds=round(time.monotonic() - t0, 2),
                error_message="pytest not found in PATH",
            )

        duration = round(time.monotonic() - t0, 2)
        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        return self._parse_pytest_output(stdout, stderr, proc.returncode or 0, duration)

    async def run_frontend_tests(self) -> SuiteResult:
        """Execute vitest and parse results."""
        frontend_dir = os.path.join(self._project_root, "frontend")
        if not os.path.isdir(frontend_dir):
            return SuiteResult(
                suite="frontend",
                status="error",
                error_message="frontend/ directory not found",
            )

        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "vitest", "run", "--reporter=json",
                cwd=frontend_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "CI": "true"},
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self._suite_timeout
            )
        except asyncio.TimeoutError:
            logger.error("Frontend test suite timed out after %ds", self._suite_timeout)
            return SuiteResult(
                suite="frontend",
                status="error",
                duration_seconds=round(time.monotonic() - t0, 2),
                error_message=f"Timed out after {self._suite_timeout}s",
            )
        except FileNotFoundError:
            return SuiteResult(
                suite="frontend",
                status="error",
                duration_seconds=round(time.monotonic() - t0, 2),
                error_message="npx not found in PATH",
            )

        duration = round(time.monotonic() - t0, 2)
        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        return self._parse_vitest_output(stdout, stderr, proc.returncode or 0, duration)

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    async def start_scheduler(self) -> None:
        """Start periodic test execution loop."""
        if self._scheduler_active:
            logger.warning("Scheduler already active")
            return
        self._scheduler_active = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("Watchdog scheduler started (interval=%ds)", self._interval)

    async def stop_scheduler(self) -> None:
        """Stop periodic test execution."""
        self._scheduler_active = False
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        self._scheduler_task = None
        logger.info("Watchdog scheduler stopped")

    async def _scheduler_loop(self) -> None:
        """Internal loop — runs test suites at fixed intervals."""
        while self._scheduler_active:
            try:
                await asyncio.sleep(self._interval)
                if not self._scheduler_active:
                    break
                logger.info("Scheduled watchdog test run starting")
                await self.run_all(trigger="scheduled")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Scheduled test run failed", exc_info=True)

    # ------------------------------------------------------------------
    # EventBus integration
    # ------------------------------------------------------------------

    async def _emit_report(self, result: TestRunResult) -> None:
        """Publish a watchdog_report event via EventBus."""
        try:
            from dashboard.events import event_bus

            await event_bus.publish({
                "type": "watchdog_report",
                "run_id": result.run_id,
                "status": result.status,
                "trigger": result.trigger,
                "summary": result.summary,
                "duration_seconds": result.duration_seconds,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "suites": [s.model_dump() for s in result.suites],
                "timestamp": time.time(),
            })
        except Exception:
            logger.error("Failed to emit watchdog_report event", exc_info=True)

    # ------------------------------------------------------------------
    # Output parsers
    # ------------------------------------------------------------------

    def _parse_pytest_output(
        self, stdout: str, stderr: str, returncode: int, duration: float
    ) -> SuiteResult:
        """Parse pytest -q output into a SuiteResult."""
        failures: list[TestFailure] = []
        total = passed = failed = skipped = errors = 0

        # pytest summary line: "5 passed, 2 failed, 1 skipped in 3.45s"
        summary_match = re.search(
            r"=*\s*([\d\w\s,]+)\s+in\s+[\d.]+s\s*=*",
            stdout,
        )
        if not summary_match:
            # Try the short-format line: "42 passed, 1 warning in 5.67s"
            summary_match = re.search(
                r"([\d]+\s+passed.*?)\s+in\s+[\d.]+s",
                stdout,
            )

        if summary_match:
            summary_text = summary_match.group(1)
            for count_match in re.finditer(r"(\d+)\s+(\w+)", summary_text):
                count = int(count_match.group(1))
                label = count_match.group(2).lower()
                if label == "passed":
                    passed = count
                elif label == "failed":
                    failed = count
                elif label == "skipped" or label == "deselected":
                    skipped += count
                elif label in ("error", "errors"):
                    errors = count
            total = passed + failed + skipped + errors

        # Extract individual failure names from FAILED lines
        for fail_match in re.finditer(r"FAILED\s+(.+?)(?:\s+-\s+(.+))?$", stdout, re.MULTILINE):
            failures.append(TestFailure(
                test_name=fail_match.group(1).strip(),
                message=(fail_match.group(2) or "").strip(),
            ))

        # If no summary found but returncode != 0, mark as error
        if total == 0 and returncode != 0:
            combined = (stdout + "\n" + stderr).strip()
            error_msg = combined[-500:] if len(combined) > 500 else combined
            return SuiteResult(
                suite="backend",
                status="error",
                duration_seconds=duration,
                error_message=error_msg or f"pytest exited with code {returncode}",
            )

        status = "passed" if failed == 0 and errors == 0 and returncode == 0 else "failed"

        return SuiteResult(
            suite="backend",
            status=status,
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            failures=failures,
            duration_seconds=duration,
        )

    def _parse_vitest_output(
        self, stdout: str, stderr: str, returncode: int, duration: float
    ) -> SuiteResult:
        """Parse vitest --reporter=json output into a SuiteResult."""
        failures: list[TestFailure] = []

        # vitest JSON reporter outputs a JSON object; extract it
        json_data = None
        # The JSON may be preceded by console output, find the JSON block
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("{"):
                try:
                    json_data = json.loads(stripped)
                    break
                except json.JSONDecodeError:
                    continue

        # Try parsing the full stdout as JSON (vitest sometimes outputs single blob)
        if json_data is None:
            try:
                json_data = json.loads(stdout)
            except json.JSONDecodeError:
                pass

        if json_data and "testResults" in json_data:
            total = passed = failed = skipped = 0
            for test_file in json_data["testResults"]:
                for assertion in test_file.get("assertionResults", []):
                    total += 1
                    st = assertion.get("status", "")
                    if st == "passed":
                        passed += 1
                    elif st == "failed":
                        failed += 1
                        failures.append(TestFailure(
                            test_name=assertion.get("fullName", assertion.get("title", "unknown")),
                            message="\n".join(assertion.get("failureMessages", []))[:500],
                        ))
                    elif st in ("skipped", "pending", "todo"):
                        skipped += 1

            status = "passed" if failed == 0 and returncode == 0 else "failed"
            return SuiteResult(
                suite="frontend",
                status=status,
                total=total,
                passed=passed,
                failed=failed,
                skipped=skipped,
                failures=failures,
                duration_seconds=duration,
            )

        # Fallback: parse vitest text output
        total = passed = failed = skipped = 0

        for count_match in re.finditer(
            r"Tests\s+(\d+)\s+passed.*?(\d+)\s+failed", stdout + stderr
        ):
            passed = int(count_match.group(1))
            failed = int(count_match.group(2))
            total = passed + failed

        if total == 0:
            pass_match = re.search(r"(\d+)\s+pass", stdout + stderr, re.IGNORECASE)
            fail_match = re.search(r"(\d+)\s+fail", stdout + stderr, re.IGNORECASE)
            if pass_match:
                passed = int(pass_match.group(1))
            if fail_match:
                failed = int(fail_match.group(1))
            total = passed + failed

        if total == 0 and returncode != 0:
            combined = (stdout + "\n" + stderr).strip()
            return SuiteResult(
                suite="frontend",
                status="error",
                duration_seconds=duration,
                error_message=(combined[-500:] if len(combined) > 500 else combined)
                or f"vitest exited with code {returncode}",
            )

        status = "passed" if failed == 0 and returncode == 0 else "failed"
        return SuiteResult(
            suite="frontend",
            status=status,
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            failures=failures,
            duration_seconds=duration,
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
watchdog_runner = WatchdogTestRunner()
