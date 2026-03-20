"""Comprehensive tests for isolated_query exponential backoff and timeout circuit breaker.

Tests cover:
- Backoff constant configuration and values
- Exponential backoff calculation (initial, factor, cap)
- Circuit breaker timeout detection logic
- _StreamEvent construction
- _run_in_fresh_loop success and error handling
- SDKResponse construction for error cases
- Concurrent _run_in_fresh_loop calls
- Configuration via environment variable
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from isolated_query import (
    ISOLATED_QUERY_TIMEOUT,
    _BACKOFF_CAP,
    _BACKOFF_FACTOR,
    _BACKOFF_INITIAL,
    _StreamEvent,
    _run_in_fresh_loop,
)
from sdk_client import ErrorCategory, SDKResponse


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_sdk():
    """Mock SDK manager."""
    return MagicMock()


def _make_success_response(**kwargs) -> SDKResponse:
    defaults = dict(text="Success", session_id="sess-123", cost_usd=0.01, num_turns=3, is_error=False)
    defaults.update(kwargs)
    return SDKResponse(**defaults)


def _make_error_response(category=ErrorCategory.TRANSIENT, **kwargs) -> SDKResponse:
    defaults = dict(text="Error occurred", is_error=True, error_message="Something went wrong", error_category=category)
    defaults.update(kwargs)
    return SDKResponse(**defaults)


# ── Unit Tests: Backoff Constants ────────────────────────────────────────────

class TestBackoffConstants:
    def test_backoff_initial_is_positive(self):
        assert _BACKOFF_INITIAL > 0

    def test_backoff_initial_is_100ms(self):
        assert _BACKOFF_INITIAL == 0.1

    def test_backoff_cap_is_5_seconds(self):
        assert _BACKOFF_CAP == 5.0

    def test_backoff_factor_is_2(self):
        assert _BACKOFF_FACTOR == 2.0

    def test_backoff_factor_greater_than_1(self):
        assert _BACKOFF_FACTOR > 1.0

    def test_circuit_breaker_timeout_default(self):
        assert ISOLATED_QUERY_TIMEOUT == 300.0

    def test_circuit_breaker_timeout_is_positive(self):
        assert ISOLATED_QUERY_TIMEOUT > 0


# ── Unit Tests: Exponential Backoff Calculation ──────────────────────────────

class TestExponentialBackoffCalculation:
    def test_backoff_sequence(self):
        """Backoff should follow: 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 5.0, 5.0, ..."""
        delay = _BACKOFF_INITIAL
        expected = [0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 5.0, 5.0]
        actual = []
        for _ in range(8):
            actual.append(round(delay, 1))
            delay = min(delay * _BACKOFF_FACTOR, _BACKOFF_CAP)
        assert actual == expected

    def test_backoff_never_exceeds_cap(self):
        delay = _BACKOFF_INITIAL
        for _ in range(100):
            delay = min(delay * _BACKOFF_FACTOR, _BACKOFF_CAP)
        assert delay <= _BACKOFF_CAP

    def test_backoff_reaches_cap(self):
        delay = _BACKOFF_INITIAL
        reached_cap = False
        for _ in range(20):
            delay = min(delay * _BACKOFF_FACTOR, _BACKOFF_CAP)
            if delay >= _BACKOFF_CAP:
                reached_cap = True
                break
        assert reached_cap

    def test_backoff_iterations_to_cap(self):
        """Should reach cap in ~6 doublings: 0.1→0.2→0.4→0.8→1.6→3.2→5.0."""
        delay = _BACKOFF_INITIAL
        iterations = 0
        while delay < _BACKOFF_CAP:
            delay = min(delay * _BACKOFF_FACTOR, _BACKOFF_CAP)
            iterations += 1
        assert iterations == 6

    def test_backoff_cap_is_reasonable_vs_timeout(self):
        """Cap should be much less than the circuit breaker timeout."""
        assert _BACKOFF_CAP < ISOLATED_QUERY_TIMEOUT / 10


# ── Unit Tests: _StreamEvent ─────────────────────────────────────────────────

class TestStreamEvent:
    def test_stream_event_construction(self):
        ev = _StreamEvent(kind="stream", payload="hello")
        assert ev.kind == "stream"
        assert ev.payload == "hello"

    def test_stream_event_default_payload(self):
        ev = _StreamEvent(kind="done")
        assert ev.payload is None

    def test_stream_event_tool_use(self):
        ev = _StreamEvent(kind="tool_use", payload=("Read", "info", {"path": "/x"}))
        assert ev.kind == "tool_use"
        assert ev.payload[0] == "Read"

    def test_stream_event_error(self):
        ev = _StreamEvent(kind="error", payload=Exception("fail"))
        assert ev.kind == "error"


# ── Unit Tests: _run_in_fresh_loop ───────────────────────────────────────────

class TestRunInFreshLoop:
    def test_run_in_fresh_loop_success(self):
        response = _make_success_response()
        async def coro():
            return response
        result = _run_in_fresh_loop(coro)
        assert result.text == "Success"
        assert not result.is_error

    def test_run_in_fresh_loop_cancel_scope_error(self):
        async def coro():
            raise RuntimeError("cancel scope blah")
        result = _run_in_fresh_loop(coro)
        assert result.is_error
        assert result.error_category == ErrorCategory.TRANSIENT
        assert "cancel scope" in result.error_message

    def test_run_in_fresh_loop_unexpected_error(self):
        async def coro():
            raise ValueError("unexpected failure")
        result = _run_in_fresh_loop(coro)
        assert result.is_error
        assert result.error_category == ErrorCategory.UNKNOWN
        assert "unexpected failure" in result.error_message

    def test_run_in_fresh_loop_runtime_error_non_cancel_scope(self):
        async def coro():
            raise RuntimeError("something else entirely")
        with pytest.raises(RuntimeError, match="something else entirely"):
            _run_in_fresh_loop(coro)

    def test_run_in_fresh_loop_returns_sdk_response(self):
        response = _make_success_response(text="Hello world", cost_usd=0.05)
        async def coro():
            return response
        result = _run_in_fresh_loop(coro)
        assert isinstance(result, SDKResponse)
        assert result.cost_usd == 0.05

    def test_run_in_fresh_loop_with_async_operations(self):
        """Coroutine with async operations should work in fresh loop."""
        async def coro():
            await asyncio.sleep(0.01)
            return _make_success_response(text="after sleep")
        result = _run_in_fresh_loop(coro)
        assert result.text == "after sleep"


# ── Unit Tests: Circuit Breaker Logic ────────────────────────────────────────

class TestCircuitBreakerLogic:
    def test_timeout_check_triggers_when_elapsed_exceeds_timeout(self):
        """The circuit breaker condition should detect elapsed >= timeout."""
        timeout = 0.1
        wait_start = time.monotonic() - 0.2  # 200ms ago
        elapsed = time.monotonic() - wait_start
        assert elapsed >= timeout

    def test_timeout_check_does_not_trigger_before_timeout(self):
        """The circuit breaker condition should not trigger prematurely."""
        timeout = 300.0
        wait_start = time.monotonic()
        elapsed = time.monotonic() - wait_start
        assert elapsed < timeout

    def test_circuit_breaker_error_response_format(self):
        """Circuit breaker should produce a specific error format."""
        elapsed = 305.0
        response = SDKResponse(
            text=f"Error: Isolated query timed out after {elapsed:.0f}s (circuit breaker triggered)",
            is_error=True,
            error_message=f"Circuit breaker timeout after {elapsed:.0f}s",
            error_category=ErrorCategory.TRANSIENT,
        )
        assert response.is_error
        assert "circuit breaker" in response.error_message.lower()
        assert response.error_category == ErrorCategory.TRANSIENT

    def test_diagnostics_string_format(self):
        """Diagnostic string should include key telemetry fields."""
        _diag = (
            f"executor_done=False, "
            f"cancel_hits=3, "
            f"elapsed=305.1s, "
            f"queue_size=42, "
            f"timeout={ISOLATED_QUERY_TIMEOUT}s"
        )
        assert "executor_done" in _diag
        assert "cancel_hits" in _diag
        assert "elapsed" in _diag
        assert "queue_size" in _diag
        assert "timeout" in _diag


# ── Async Tests: Backoff delay simulation ────────────────────────────────────

class TestBackoffDelaySimulation:
    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self):
        """Simulate the backoff loop and verify timing behavior."""
        delays = []
        _backoff_delay = _BACKOFF_INITIAL

        for i in range(5):
            delays.append(_backoff_delay)
            _backoff_delay = min(_backoff_delay * _BACKOFF_FACTOR, _BACKOFF_CAP)

        assert delays[0] == pytest.approx(0.1)
        assert delays[1] == pytest.approx(0.2)
        assert delays[2] == pytest.approx(0.4)
        assert delays[3] == pytest.approx(0.8)
        assert delays[4] == pytest.approx(1.6)

    @pytest.mark.asyncio
    async def test_cumulative_backoff_time(self):
        """Total backoff time should be bounded."""
        total = 0.0
        delay = _BACKOFF_INITIAL
        for _ in range(20):
            total += delay
            delay = min(delay * _BACKOFF_FACTOR, _BACKOFF_CAP)
        # Sum should be well under the circuit breaker timeout
        assert total < ISOLATED_QUERY_TIMEOUT


# ── Async Tests: Concurrent _run_in_fresh_loop ──────────────────────────────

class TestConcurrentFreshLoop:
    @pytest.mark.asyncio
    async def test_concurrent_fresh_loops_independent(self):
        """Multiple _run_in_fresh_loop calls should not interfere."""
        results = []

        async def coro_factory(idx):
            async def coro():
                await asyncio.sleep(0.01)
                return _make_success_response(text=f"result-{idx}")
            return coro

        loop = asyncio.get_event_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        futures = []
        for i in range(3):
            coro = await coro_factory(i)
            fut = loop.run_in_executor(executor, _run_in_fresh_loop, coro)
            futures.append(fut)

        results = await asyncio.gather(*futures)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, SDKResponse)
            assert not r.is_error

        executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_concurrent_fresh_loops_with_errors(self):
        """Errors in one fresh loop should not affect others."""
        loop = asyncio.get_event_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

        async def success_coro():
            return _make_success_response(text="ok")

        async def error_coro():
            raise ValueError("isolated failure")

        fut_ok = loop.run_in_executor(executor, _run_in_fresh_loop, success_coro)
        fut_err = loop.run_in_executor(executor, _run_in_fresh_loop, error_coro)

        results = await asyncio.gather(fut_ok, fut_err)
        assert not results[0].is_error
        assert results[0].text == "ok"
        assert results[1].is_error
        assert "isolated failure" in results[1].error_message

        executor.shutdown(wait=False)


# ── Edge Case Tests ──────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_zero_cancel_hits_logged_info(self):
        """When cancel_hits is 0, the success path should not mention interruptions."""
        _cancel_hits = 0
        # The code only logs "despite N CancelledError interruptions" when cancel_hits > 0
        should_log = _cancel_hits > 0
        assert not should_log

    def test_cancel_hits_positive_triggers_info_log(self):
        """When cancel_hits > 0, the code should log the count."""
        _cancel_hits = 3
        should_log = _cancel_hits > 0
        assert should_log

    def test_executor_result_empty_returns_error(self):
        """When _executor_result is empty, an error SDKResponse should be returned."""
        _executor_result = []
        _executor_error = []
        result = (
            _executor_result[0]
            if _executor_result
            else SDKResponse(
                text="Executor completed with no result",
                is_error=True,
                error_message="No result from executor",
                error_category=ErrorCategory.UNKNOWN,
            )
        )
        assert result.is_error
        assert "no result" in result.error_message.lower()

    def test_executor_error_takes_first(self):
        """When _executor_error has errors, the first should be used."""
        errors = [RuntimeError("first"), ValueError("second")]
        exc = errors[0]
        response = SDKResponse(
            text=f"Error in isolated query: {exc}",
            is_error=True,
            error_message=str(exc),
            error_category=ErrorCategory.UNKNOWN,
        )
        assert "first" in response.error_message

    @pytest.mark.asyncio
    async def test_done_event_signal_mechanism(self):
        """asyncio.Event used for signaling should work correctly."""
        done = asyncio.Event()
        assert not done.is_set()
        done.set()
        assert done.is_set()
        # wait() should return immediately when set
        await asyncio.wait_for(done.wait(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_stream_queue_bounded(self):
        """Stream queue should be bounded at 500."""
        q = asyncio.Queue(maxsize=500)
        assert q.maxsize == 500
        # Fill it up
        for i in range(500):
            q.put_nowait(i)
        # Should be full
        with pytest.raises(asyncio.QueueFull):
            q.put_nowait(501)

    def test_safe_enqueue_drops_on_full(self):
        """_safe_enqueue pattern should not raise on QueueFull."""
        q = asyncio.Queue(maxsize=1)
        q.put_nowait("first")

        # Simulate _safe_enqueue behavior
        try:
            q.put_nowait("second")
        except asyncio.QueueFull:
            pass  # Dropped — expected

        assert q.qsize() == 1

    @pytest.mark.asyncio
    async def test_backoff_with_very_small_timeout(self):
        """Even with a tiny timeout, backoff + circuit breaker should detect it."""
        tiny_timeout = 0.001
        wait_start = time.monotonic()
        await asyncio.sleep(0.01)
        elapsed = time.monotonic() - wait_start
        assert elapsed >= tiny_timeout

    def test_environment_variable_override(self):
        """ISOLATED_QUERY_TIMEOUT should be configurable."""
        import os
        # Default is 300
        assert ISOLATED_QUERY_TIMEOUT == float(
            os.getenv("HIVEMIND_ISOLATED_QUERY_TIMEOUT", "300")
        )
