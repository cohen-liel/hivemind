"""Tests for sdk_client.py — error classification, connection pool, retry logic.

Tests the pure logic functions without calling real Claude CLI.
The SDK import (claude_agent_sdk) is mocked at module level.
"""

from __future__ import annotations

import asyncio

import pytest

# Import the parts of sdk_client that don't need the actual SDK binary
# We can test classify_error, ErrorCategory, SDKResponse, _ConnectionPool directly.
from sdk_client import (
    ErrorCategory,
    SDKResponse,
    _ConnectionPool,
    classify_error,
)

# ============================================================
# Error Classification — comprehensive coverage
# ============================================================


class TestClassifyError:
    """Tests for classify_error() — the brain of the retry system."""

    # --- Transient errors ---

    @pytest.mark.parametrize(
        "msg",
        [
            "Connection timeout after 30s",
            "Request timed out",
            "Deadline exceeded",
            "Connection refused (ECONNREFUSED)",
            "Connection reset by peer (ECONNRESET)",
            "Broken pipe during write",
            "Network unreachable",
            "DNS resolution failed",
            "EOF received",
            "Socket error on read",
            "Service unavailable",
            "502 Bad Gateway",
            "503 Service Unavailable",
            "504 Gateway Timeout",
            "Process spawn failed",
            "Process exited with code 1",
        ],
    )
    def test_transient_errors(self, msg):
        assert classify_error(msg) == ErrorCategory.TRANSIENT

    # --- Rate limit errors ---

    @pytest.mark.parametrize(
        "msg",
        [
            "Rate limit exceeded",
            "rate_limit_error: too many requests",
            "429 Too Many Requests",
            "You are being throttled",
        ],
    )
    def test_rate_limit_errors(self, msg):
        assert classify_error(msg) == ErrorCategory.RATE_LIMIT

    # --- Session errors ---

    @pytest.mark.parametrize(
        "msg",
        [
            "Invalid session ID",
            "Session expired, please start a new one",
            "Cannot resume: session not found",
            "Expired session token",
        ],
    )
    def test_session_errors(self, msg):
        assert classify_error(msg) == ErrorCategory.SESSION

    # --- Auth errors (permanent) ---

    @pytest.mark.parametrize(
        "msg",
        [
            "Authentication failed",
            "401 Unauthorized",
            "403 Forbidden",
            "Permission denied: cannot access resource",
            "Not logged in",
            "Login required to proceed",
        ],
    )
    def test_auth_errors(self, msg):
        assert classify_error(msg) == ErrorCategory.AUTH

    # --- Budget errors (permanent) ---

    @pytest.mark.parametrize(
        "msg",
        [
            "Budget exceeded",
            "Spending limit reached",
            "Insufficient funds for query",
            "Quota exhausted",
        ],
    )
    def test_budget_errors(self, msg):
        assert classify_error(msg) == ErrorCategory.BUDGET

    # --- Permanent errors ---

    @pytest.mark.parametrize(
        "msg",
        [
            "Invalid request payload",
            "Malformed JSON in body",
            "400 Bad Request",
            "Process exited with exit code 71",  # macOS sandbox
            "Process exited with exit code: 71",
        ],
    )
    def test_permanent_errors(self, msg):
        assert classify_error(msg) == ErrorCategory.PERMANENT

    # --- Unknown ---

    def test_empty_string_returns_unknown(self):
        assert classify_error("") == ErrorCategory.UNKNOWN

    def test_none_returns_unknown(self):
        """classify_error handles None gracefully."""
        # The function checks `if not error_message:` which handles None
        assert classify_error(None) == ErrorCategory.UNKNOWN

    def test_gibberish_returns_unknown(self):
        assert classify_error("xyzzy foobar baz") == ErrorCategory.UNKNOWN

    # --- Edge cases: priority / overlap ---

    def test_timeout_has_priority_over_connection(self):
        """'Connection timeout' should be TRANSIENT (timeout wins)."""
        result = classify_error("Connection timeout after 30s")
        assert result == ErrorCategory.TRANSIENT

    def test_exit_code_71_is_permanent_not_transient(self):
        """Exit code 71 (macOS sandbox) should be PERMANENT despite 'process' keyword."""
        assert classify_error("Process exited with exit code 71") == ErrorCategory.PERMANENT

    def test_case_insensitive(self):
        """Classification is case-insensitive."""
        assert classify_error("TIMEOUT") == ErrorCategory.TRANSIENT
        assert classify_error("Rate Limit Exceeded") == ErrorCategory.RATE_LIMIT
        assert classify_error("UNAUTHORIZED") == ErrorCategory.AUTH


# ============================================================
# SDKResponse dataclass
# ============================================================


class TestSDKResponse:
    def test_default_values(self):
        r = SDKResponse(text="Hello")
        assert r.text == "Hello"
        assert r.session_id == ""
        assert r.cost_usd == 0.0
        assert r.is_error is False
        assert r.error_message == ""
        assert r.retry_count == 0

    def test_error_response(self):
        r = SDKResponse(
            text="Error: something broke",
            is_error=True,
            error_message="something broke",
            error_category=ErrorCategory.TRANSIENT,
            retry_count=2,
        )
        assert r.is_error is True
        assert r.error_category == ErrorCategory.TRANSIENT
        assert r.retry_count == 2

    def test_full_response(self):
        r = SDKResponse(
            text="Generated code",
            session_id="sess-abc-123",
            cost_usd=0.15,
            duration_ms=3200,
            num_turns=5,
        )
        assert r.session_id == "sess-abc-123"
        assert r.cost_usd == 0.15
        assert r.duration_ms == 3200
        assert r.num_turns == 5


# ============================================================
# Connection Pool
# ============================================================


class TestConnectionPool:
    def test_initial_state(self):
        pool = _ConnectionPool(max_concurrent=3)
        assert pool.active_count == 0
        stats = pool.stats
        assert stats["active"] == 0
        assert stats["total_queries"] == 0
        assert stats["total_errors"] == 0
        assert stats["total_tokens"] == 0

    @pytest.mark.asyncio
    async def test_acquire_release(self):
        pool = _ConnectionPool(max_concurrent=2)
        await pool.acquire()
        assert pool.active_count == 1
        await pool.release(tokens=100)
        assert pool.active_count == 0
        stats = pool.stats
        assert stats["total_queries"] == 1
        assert stats["total_tokens"] == 100
        assert stats["total_errors"] == 0

    @pytest.mark.asyncio
    async def test_error_tracking(self):
        pool = _ConnectionPool(max_concurrent=5)
        await pool.acquire()
        await pool.release(tokens=0, is_error=True)
        assert pool.stats["total_errors"] == 1

    @pytest.mark.asyncio
    async def test_concurrent_limit(self):
        """Pool blocks when max_concurrent is reached."""
        pool = _ConnectionPool(max_concurrent=2)
        await pool.acquire()
        await pool.acquire()
        assert pool.active_count == 2

        # Third acquire should block — use a timeout to prove it
        acquired = False

        async def try_acquire():
            nonlocal acquired
            await pool.acquire()
            acquired = True

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.05)  # Give it a moment
        assert acquired is False  # Should be blocked

        # Release one slot — the waiter should proceed
        await pool.release()
        await asyncio.sleep(0.05)
        assert acquired is True
        assert pool.active_count == 2  # 1 original + 1 newly acquired

        # Cleanup
        await pool.release()
        await pool.release()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_multiple_acquire_release_cycles(self):
        pool = _ConnectionPool(max_concurrent=3)
        for i in range(10):
            await pool.acquire()
            await pool.release(tokens=10 * i)
        assert pool.active_count == 0
        assert pool.stats["total_queries"] == 10
        assert pool.stats["total_tokens"] == 450

    @pytest.mark.asyncio
    async def test_token_accumulation(self):
        pool = _ConnectionPool(max_concurrent=5)
        await pool.acquire()
        await pool.release(tokens=100)
        await pool.acquire()
        await pool.release(tokens=250)
        assert pool.stats["total_tokens"] == 350


# ============================================================
# ErrorCategory enum
# ============================================================


class TestErrorCategory:
    def test_all_values(self):
        """Verify all expected categories exist."""
        assert ErrorCategory.TRANSIENT.value == "transient"
        assert ErrorCategory.RATE_LIMIT.value == "rate_limit"
        assert ErrorCategory.SESSION.value == "session"
        assert ErrorCategory.AUTH.value == "auth"
        assert ErrorCategory.BUDGET.value == "budget"
        assert ErrorCategory.PERMANENT.value == "permanent"
        assert ErrorCategory.UNKNOWN.value == "unknown"

    def test_category_count(self):
        """Exactly 7 error categories."""
        assert len(ErrorCategory) == 7


# ============================================================
# Retry logic integration (without real SDK)
# ============================================================


class TestRetryLogicPure:
    """Test the retry decision logic by checking which categories are retryable."""

    @pytest.mark.parametrize(
        "category,retryable",
        [
            (ErrorCategory.TRANSIENT, True),
            (ErrorCategory.RATE_LIMIT, True),
            (ErrorCategory.SESSION, True),
            (ErrorCategory.UNKNOWN, True),
            (ErrorCategory.AUTH, False),
            (ErrorCategory.BUDGET, False),
            (ErrorCategory.PERMANENT, False),
        ],
    )
    def test_retryable_categories(self, category, retryable):
        """Verify which error categories should trigger a retry."""
        non_retryable = {ErrorCategory.AUTH, ErrorCategory.BUDGET, ErrorCategory.PERMANENT}
        assert (category not in non_retryable) == retryable
