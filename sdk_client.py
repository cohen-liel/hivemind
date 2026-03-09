from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

# Unset CLAUDECODE so the SDK can spawn claude subprocesses
# (otherwise it refuses with "cannot launch inside another Claude Code session")
os.environ.pop("CLAUDECODE", None)

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from config import AGENT_TIMEOUT_SECONDS, SDK_MAX_RETRIES, CLAUDE_CLI_PATH

logger = logging.getLogger(__name__)

# Use the Claude CLI binary. Resolution order:
#   1. CLAUDE_CLI_PATH env var / config.py (explicit path or just "claude")
#   2. Native macOS binary at /usr/local/bin/claude_code/claude (avoids sandbox-exec)
#   3. shutil.which("claude") — finds it on $PATH (works in Docker + Linux)
#   4. Fallback to /usr/local/bin/claude
import shutil as _shutil

_NATIVE_BINARY = "/usr/local/bin/claude_code/claude"

if CLAUDE_CLI_PATH != "claude":
    # Explicitly configured — use as-is
    SYSTEM_CLI_PATH = CLAUDE_CLI_PATH
    logger.info(f"Using configured Claude CLI: {SYSTEM_CLI_PATH}")
elif os.path.isfile(_NATIVE_BINARY) and os.access(_NATIVE_BINARY, os.X_OK):
    # macOS native binary (avoids sandbox-exec issues)
    SYSTEM_CLI_PATH = _NATIVE_BINARY
    logger.info(f"Using native Claude binary: {_NATIVE_BINARY}")
else:
    # Find on PATH (Docker, Linux, standard installs)
    SYSTEM_CLI_PATH = _shutil.which("claude") or "/usr/local/bin/claude"
    logger.info(f"Using Claude CLI from PATH: {SYSTEM_CLI_PATH}")




# ============================================================
# Error Classification
# ============================================================

class ErrorCategory(Enum):
    """Classification of SDK errors for retry/handling decisions."""
    TRANSIENT = "transient"      # Network blips, timeouts — safe to retry
    RATE_LIMIT = "rate_limit"    # API rate limit — retry with backoff
    SESSION = "session"          # Stale/invalid session — retry without session
    AUTH = "auth"                # API key / auth — permanent, don't retry
    BUDGET = "budget"            # Budget exhausted — permanent, don't retry
    PERMANENT = "permanent"      # Other permanent errors — don't retry
    UNKNOWN = "unknown"          # Unclassified — retry once cautiously


def classify_error(error_message: str) -> ErrorCategory:
    """Classify an error message to determine retry strategy.

    Returns an ErrorCategory that guides the retry logic:
    - TRANSIENT: safe to retry immediately or with short delay
    - RATE_LIMIT: retry with exponential backoff
    - SESSION: retry without session_id
    - AUTH/BUDGET/PERMANENT: don't retry
    """
    if not error_message:
        return ErrorCategory.UNKNOWN

    lower = error_message.lower()

    # Timeout errors — transient
    if any(kw in lower for kw in ("timeout", "timed out", "deadline exceeded")):
        return ErrorCategory.TRANSIENT

    # Connection errors — transient
    if any(kw in lower for kw in (
        "connection", "connect", "network", "dns", "econnrefused",
        "econnreset", "broken pipe", "eof", "socket", "unavailable",
        "502", "503", "504",
    )):
        return ErrorCategory.TRANSIENT

    # Rate limiting
    if any(kw in lower for kw in ("rate limit", "rate_limit", "429", "too many requests", "throttl")):
        return ErrorCategory.RATE_LIMIT

    # Session/resume errors
    if any(kw in lower for kw in ("session", "resume", "invalid session", "expired session")):
        return ErrorCategory.SESSION

    # Authentication errors — permanent
    if any(kw in lower for kw in (
        "authentication", "unauthorized", "401", "403", "forbidden",
        "permission denied", "not logged in", "login required",
    )):
        return ErrorCategory.AUTH

    # Budget errors — permanent
    if any(kw in lower for kw in ("budget", "spending limit", "insufficient funds", "quota")):
        return ErrorCategory.BUDGET

    # Process spawn errors — transient (CLI binary might be busy)
    if any(kw in lower for kw in ("process", "spawn", "enoent", "exited with")):
        # Exit code 71 = macOS sandbox restriction — permanent, not transient
        if "exit code 71" in lower or "exit code: 71" in lower:
            return ErrorCategory.PERMANENT
        return ErrorCategory.TRANSIENT

    # Content/validation errors — permanent
    if any(kw in lower for kw in ("invalid", "malformed", "bad request", "400")):
        return ErrorCategory.PERMANENT

    return ErrorCategory.UNKNOWN


# ============================================================
# SDK Response
# ============================================================

@dataclass
class SDKResponse:
    text: str
    session_id: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    is_error: bool = False
    error_message: str = ""
    error_category: ErrorCategory = ErrorCategory.UNKNOWN
    retry_count: int = 0  # How many retries were needed


# ============================================================
# Connection Pool / Concurrency Limiter
# ============================================================

class _ConnectionPool:
    """Limits concurrent SDK queries to prevent resource exhaustion.

    The Claude CLI spawns a subprocess per query. Too many concurrent
    queries can overwhelm the system (file descriptors, memory, CPU).
    This semaphore-based pool ensures we stay within safe limits.
    """

    def __init__(self, max_concurrent: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_count = 0
        self._total_queries = 0
        self._total_errors = 0
        self._total_cost = 0.0
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def stats(self) -> dict:
        return {
            "active": self._active_count,
            "total_queries": self._total_queries,
            "total_errors": self._total_errors,
            "total_cost": self._total_cost,
        }

    async def acquire(self):
        await self._semaphore.acquire()
        async with self._lock:
            self._active_count += 1
            self._total_queries += 1

    async def release(self, cost: float = 0.0, is_error: bool = False):
        async with self._lock:
            self._active_count -= 1
            self._total_cost += cost
            if is_error:
                self._total_errors += 1
        self._semaphore.release()


# ============================================================
# Circuit Breaker
# ============================================================

class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and rejecting requests.

    Callers should catch this to avoid wasting resources on a
    known-failing SDK backend.
    """

    def __init__(self, open_since: float, failures: int, recovery_in: float) -> None:
        self.open_since: float = open_since
        self.failures: int = failures
        self.recovery_in: float = recovery_in
        super().__init__(
            f"Circuit breaker OPEN (since {open_since:.1f}s ago, "
            f"{failures} consecutive failures, "
            f"recovery probe in {recovery_in:.1f}s)"
        )


class CircuitState(str, Enum):
    """States of the circuit breaker."""
    CLOSED    = "closed"       # Normal — all requests pass through
    OPEN      = "open"         # Tripped — all requests rejected
    HALF_OPEN = "half_open"    # Probing — one request allowed to test recovery


class CircuitBreaker:
    """Async-safe, thread-safe circuit breaker for SDK calls.

    Opens after ``failure_threshold`` consecutive failures within
    ``failure_window_seconds``.  Once open, all calls are rejected with
    ``CircuitOpenError`` until ``recovery_timeout_seconds`` elapses.
    After that, the circuit enters *half-open* and allows one probe
    request.  A successful probe closes the circuit; a failed probe
    re-opens it.

    Thread safety is achieved via a ``threading.Lock`` guarding state
    mutations (safe because the critical section is tiny — no I/O).
    The asyncio-facing methods use ``asyncio.to_thread`` for the lock
    acquisition so the event loop is never blocked.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        failure_window_seconds: float = 60.0,
        recovery_timeout_seconds: float = 30.0,
    ) -> None:
        self._failure_threshold: int = failure_threshold
        self._failure_window: float = failure_window_seconds
        self._recovery_timeout: float = recovery_timeout_seconds

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_timestamps: list[float] = []
        self._last_failure_time: float = 0.0
        self._lock: threading.Lock = threading.Lock()
        self._half_open_in_flight: bool = False

    # ---- Public properties ----

    @property
    def state(self) -> CircuitState:
        """Current circuit state (read without lock — safe for enum reads)."""
        return self._state

    @property
    def stats(self) -> dict[str, object]:
        """Snapshot of circuit breaker statistics."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - self._failure_window
            recent = [t for t in self._failure_timestamps if t > cutoff]
            return {
                "state": self._state.value,
                "recent_failures": len(recent),
                "failure_threshold": self._failure_threshold,
                "last_failure_ago": (
                    round(now - self._last_failure_time, 1)
                    if self._last_failure_time > 0
                    else None
                ),
            }

    # ---- Core operations (sync internals, async wrappers) ----

    def _record_success_sync(self) -> None:
        """Record a successful call — resets the circuit to CLOSED."""
        with self._lock:
            self._failure_timestamps.clear()
            self._state = CircuitState.CLOSED
            self._half_open_in_flight = False

    def _record_failure_sync(self) -> None:
        """Record a failed call — may trip the circuit to OPEN."""
        with self._lock:
            now = time.monotonic()
            self._failure_timestamps.append(now)
            self._last_failure_time = now

            # Prune timestamps outside the window
            cutoff = now - self._failure_window
            self._failure_timestamps = [
                t for t in self._failure_timestamps if t > cutoff
            ]

            if len(self._failure_timestamps) >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._half_open_in_flight = False
                logger.warning(
                    f"[CircuitBreaker] OPENED: {len(self._failure_timestamps)} "
                    f"failures in {self._failure_window}s window"
                )

    def _check_can_execute_sync(self) -> bool:
        """Check if a request may proceed.  Returns True if allowed.

        Side effects:
        - Transitions OPEN → HALF_OPEN after recovery timeout.
        - Sets ``_half_open_in_flight`` to prevent multiple probes.
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self._recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_in_flight = True
                    logger.info(
                        "[CircuitBreaker] HALF-OPEN: allowing one probe request"
                    )
                    return True
                return False

            # HALF_OPEN — allow only one concurrent probe
            if self._state == CircuitState.HALF_OPEN:
                if not self._half_open_in_flight:
                    self._half_open_in_flight = True
                    return True
                return False

            return False  # pragma: no cover

    # ---- Async wrappers (safe for the event loop) ----

    async def record_success(self) -> None:
        """Async wrapper — record a successful call."""
        await asyncio.to_thread(self._record_success_sync)

    async def record_failure(self) -> None:
        """Async wrapper — record a failed call."""
        await asyncio.to_thread(self._record_failure_sync)

    async def check_can_execute(self) -> bool:
        """Async wrapper — check if a request may proceed."""
        return await asyncio.to_thread(self._check_can_execute_sync)

    async def raise_if_open(self) -> None:
        """Raise ``CircuitOpenError`` if the circuit is currently open.

        Call this before sending a request to fail fast.
        """
        can = await self.check_can_execute()
        if not can:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_failure_time
                recovery_in = max(0.0, self._recovery_timeout - elapsed)
            raise CircuitOpenError(
                open_since=elapsed,
                failures=len(self._failure_timestamps),
                recovery_in=recovery_in,
            )


# Module-level pool (shared across all ClaudeSDKManager instances)
_pool = _ConnectionPool(max_concurrent=5)

# Module-level circuit breaker (shared across all ClaudeSDKManager instances)
_circuit_breaker = CircuitBreaker(
    failure_threshold=3,
    failure_window_seconds=60.0,
    recovery_timeout_seconds=30.0,
)


# ============================================================
# Claude SDK Manager
# ============================================================

class ClaudeSDKManager:
    """Wrapper over claude-agent-sdk's query() with connection pooling,
    error classification, structured retry logic, and request/response logging.
    """

    def __init__(self):
        self._pool = _pool  # Share the module-level pool
        self._circuit_breaker = _circuit_breaker  # Share the module-level circuit breaker
        # Keep strong references to generators until they're explicitly closed.
        # This prevents Python's GC from collecting them in a random asyncio task,
        # which triggers the anyio cancel-scope RuntimeError.
        self._active_generators: set = set()

    @property
    def pool_stats(self) -> dict:
        """Return current connection pool and circuit breaker statistics."""
        stats = self._pool.stats
        stats["circuit_breaker"] = self._circuit_breaker.stats
        return stats

    async def query(
        self,
        prompt: str,
        system_prompt: str,
        cwd: str,
        session_id: str | None = None,
        max_turns: int = 10,
        max_budget_usd: float = 2.0,
        permission_mode: str | None = "bypassPermissions",
        on_stream=None,
        on_tool_use=None,
        allowed_tools: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> SDKResponse:
        """Send a query to Claude Agent SDK with connection pooling.

        Args:
            prompt: The user/agent prompt.
            system_prompt: System prompt for the agent role.
            cwd: Working directory for the agent.
            session_id: If set, resumes a previous session.
            max_turns: Max agentic turns.
            max_budget_usd: Max budget for this query.
            permission_mode: Permission mode — "bypassPermissions" for full
                access, None for default (no auto-tool-approval).
            on_stream: Optional async callback for real-time text updates.
            on_tool_use: Optional async callback for tool use events.
            tools: Base tool set — [] disables ALL tools (passes --tools ""),
                None uses default tools. Different from allowed_tools.

        Returns:
            SDKResponse with text, session_id, cost, error classification, etc.
        """
        # Build options
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=max_turns,
            max_budget_usd=max_budget_usd,
            cwd=cwd,
            cli_path=SYSTEM_CLI_PATH,
            include_partial_messages=on_stream is not None,
        )

        if permission_mode:
            options.permission_mode = permission_mode
        if allowed_tools is not None:
            options.allowed_tools = allowed_tools
        if tools is not None:
            options.tools = tools
        if session_id:
            options.resume = session_id

        # Request logging
        request_id = f"req_{int(time.monotonic() * 1000) % 100000}"
        prompt_preview = prompt[:100].replace('\n', ' ')
        logger.info(
            f"[{request_id}] SDK query START: "
            f"max_turns={max_turns}, budget=${max_budget_usd}, "
            f"session={'resume:' + session_id[:12] + '...' if session_id else 'new'}, "
            f"tools={'disabled' if tools is not None and len(tools) == 0 else 'default'}, "
            f"pool_active={self._pool.active_count}, "
            f"prompt=\"{prompt_preview}...\""
        )
        query_start = time.monotonic()

        # Acquire connection pool slot
        try:
            await asyncio.wait_for(self._pool.acquire(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.error(f"[{request_id}] Connection pool exhausted — waited 60s")
            return SDKResponse(
                text="Error: Connection pool exhausted. Too many concurrent queries.",
                session_id=session_id or "",
                is_error=True,
                error_message="Connection pool exhausted (60s timeout)",
                error_category=ErrorCategory.TRANSIENT,
            )

        try:
            # Run the stream consumption with timeout
            result = await asyncio.wait_for(
                self._consume_stream(prompt, options, on_stream, on_tool_use, request_id),
                timeout=AGENT_TIMEOUT_SECONDS,
            )

            # Response logging
            elapsed = time.monotonic() - query_start
            if result.is_error:
                result.error_category = classify_error(result.error_message)
                logger.warning(
                    f"[{request_id}] SDK query ERROR ({elapsed:.1f}s): "
                    f"category={result.error_category.value}, "
                    f"msg=\"{result.error_message[:150]}\", "
                    f"cost=${result.cost_usd:.4f}, turns={result.num_turns}"
                )
            else:
                logger.info(
                    f"[{request_id}] SDK query OK ({elapsed:.1f}s): "
                    f"text_len={len(result.text)}, cost=${result.cost_usd:.4f}, "
                    f"turns={result.num_turns}, session={result.session_id[:12] + '...' if result.session_id else 'none'}"
                )

            return result

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - query_start
            logger.warning(
                f"[{request_id}] SDK query TIMEOUT after {elapsed:.1f}s "
                f"(limit={AGENT_TIMEOUT_SECONDS}s)"
            )
            return SDKResponse(
                text=f"Error: Agent timed out after {AGENT_TIMEOUT_SECONDS} seconds",
                session_id=session_id or "",
                is_error=True,
                error_message=f"Timeout after {AGENT_TIMEOUT_SECONDS}s",
                error_category=ErrorCategory.TRANSIENT,
            )
        except asyncio.CancelledError:
            logger.info(f"[{request_id}] SDK query CANCELLED")
            # The anyio cancel-scope bug in the SDK can cause a CancelledError
            # even when nothing requested cancellation.  Instead of propagating,
            # return a safe error response and let the caller decide to retry.
            #
            # NOTE: The old code tried `raise` then `except RuntimeError` which
            # is logically impossible — re-raised CancelledError can never be
            # caught as RuntimeError.
            return SDKResponse(
                text="Agent was cancelled (anyio cancel scope leak).",
                session_id=session_id or "",
                is_error=True,
                error_message="CancelledError (likely anyio cancel scope leak)",
                error_category=ErrorCategory.TRANSIENT,
            )
        except Exception as e:
            elapsed = time.monotonic() - query_start
            category = classify_error(str(e))
            logger.error(
                f"[{request_id}] SDK query EXCEPTION ({elapsed:.1f}s): "
                f"category={category.value}, error={e}",
                exc_info=True,
            )
            return SDKResponse(
                text=f"Error: {e}",
                session_id=session_id or "",
                is_error=True,
                error_message=str(e),
                error_category=category,
            )
        finally:
            # Always release the pool slot
            cost = 0.0
            is_err = True
            try:
                # result may not exist if we hit an exception before assignment
                cost = result.cost_usd  # type: ignore[possibly-undefined]
                is_err = result.is_error  # type: ignore[possibly-undefined]
            except (NameError, UnboundLocalError):
                pass
            await self._pool.release(cost=cost, is_error=is_err)

    async def _consume_stream(
        self,
        prompt: str,
        options: ClaudeAgentOptions,
        on_stream=None,
        on_tool_use=None,
        request_id: str = "",
    ) -> SDKResponse:
        """Consume the SDK async stream and return the final SDKResponse.

        With include_partial_messages=True, we get intermediate AssistantMessage
        objects showing tool use and partial text in real time.
        """
        text_parts: list[str] = []
        result_session_id = ""
        cost_usd = 0.0
        duration_ms = 0
        num_turns = 0
        last_seen_text = ""  # Track to avoid duplicate stream callbacks
        message_count = 0
        tool_uses: list[str] = []  # Track tools used for logging

        # Explicitly manage the async generator lifecycle to prevent the anyio
        # cancel-scope bug. If we let the generator escape and get GC'd in a
        # different asyncio task, its cleanup (aclose → anyio TaskGroup.__aexit__)
        # raises RuntimeError("Attempted to exit cancel scope in a different task").
        # By explicitly calling aclose() in the SAME task, we control the cleanup.
        gen = query(prompt=prompt, options=options).__aiter__()
        # Hold a strong reference so GC can't collect it in a different task
        self._active_generators.add(gen)
        try:
            while True:
                try:
                    message = await gen.__anext__()
                except StopAsyncIteration:
                    break
                message_count += 1

                if isinstance(message, AssistantMessage):
                    # Extract content from this message (may be partial or complete)
                    turn_text = ""
                    tool_info = ""
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            turn_text += block.text
                        elif isinstance(block, ToolUseBlock):
                            # Show which tool the agent is using
                            tool_name = block.name
                            tool_input = block.input if block.input else {}
                            # Format tool use for display
                            if tool_name in ("Read", "read_file"):
                                path = tool_input.get("file_path") or tool_input.get("path", "")
                                tool_info = f"📄 Reading: {path}"
                            elif tool_name in ("Write", "write_file", "create_file"):
                                path = tool_input.get("file_path") or tool_input.get("path", "")
                                tool_info = f"✏️ Writing: {path}"
                            elif tool_name in ("Edit", "edit_file"):
                                path = tool_input.get("file_path") or tool_input.get("path", "")
                                tool_info = f"🔧 Editing: {path}"
                            elif tool_name in ("Bash", "execute_bash", "bash"):
                                cmd = str(tool_input.get("command", ""))[:100]
                                tool_info = f"💻 Running: `{cmd}`"
                            elif tool_name in ("Glob", "glob", "ListFiles"):
                                pattern = tool_input.get("pattern", "")
                                tool_info = f"🔍 Searching: {pattern}"
                            elif tool_name in ("Grep", "grep", "SearchFiles"):
                                pattern = tool_input.get("pattern", "")
                                tool_info = f"🔎 Grep: {pattern}"
                            else:
                                tool_info = f"🔧 {tool_name}"

                            tool_uses.append(tool_name)

                            # Fire on_tool_use callback with structured data
                            if on_tool_use:
                                try:
                                    # Truncate tool_input for the callback
                                    truncated_input = {}
                                    for k, v in (tool_input or {}).items():
                                        if isinstance(v, str) and len(v) > 200:
                                            truncated_input[k] = v[:200] + "..."
                                        else:
                                            truncated_input[k] = v
                                    await on_tool_use(tool_name, tool_info, truncated_input)
                                except Exception as e:
                                    logger.error(f"[{request_id}] on_tool_use callback error: {e}")

                    # Call stream callback with meaningful updates
                    if on_stream and (turn_text != last_seen_text or tool_info):
                        try:
                            update = ""
                            if tool_info:
                                update = tool_info
                            if turn_text and turn_text != last_seen_text:
                                # Show last 300 chars of new text
                                new_text = turn_text[len(last_seen_text):]
                                preview = new_text[-300:] if len(new_text) > 300 else new_text
                                if update:
                                    update += f"\n\n{preview}"
                                else:
                                    update = preview
                            if update:
                                await on_stream(update)
                        except Exception as e:
                            logger.error(f"[{request_id}] Stream callback error: {e}")
                        last_seen_text = turn_text

                    # Collect final text — each turn may extend or replace the previous
                    if turn_text:
                        if text_parts and turn_text.startswith(text_parts[-1]):
                            # This turn's text extends the last one (streaming partial)
                            text_parts[-1] = turn_text
                        elif not text_parts or turn_text != text_parts[-1]:
                            # New distinct turn text
                            text_parts.append(turn_text)

                elif isinstance(message, ResultMessage):
                    result_session_id = message.session_id or ""
                    cost_usd = message.total_cost_usd or 0.0
                    duration_ms = message.duration_ms or 0
                    num_turns = message.num_turns or 0

                    # ResultMessage may also carry a result text
                    if message.result and message.result not in text_parts:
                        text_parts.append(message.result)

                    combined = "\n\n".join(text_parts).strip()

                    # If agent did work via tools but produced no text output,
                    # provide a meaningful fallback so the orchestrator knows work was done.
                    if not combined and not message.is_error:
                        tools_summary = ", ".join(set(tool_uses)) if tool_uses else "unknown"
                        combined = (
                            f"✅ Task completed via tool use ({num_turns} turn(s), "
                            f"tools: {tools_summary}). "
                            "No text output — work was done directly. "
                            "Verify results in the workspace files."
                        )

                    # Log tool usage summary
                    if tool_uses:
                        logger.debug(
                            f"[{request_id}] Tools used ({len(tool_uses)}): "
                            f"{', '.join(tool_uses[:20])}"
                        )

                    return SDKResponse(
                        text=combined,
                        session_id=result_session_id,
                        cost_usd=cost_usd,
                        duration_ms=duration_ms,
                        num_turns=num_turns,
                        is_error=message.is_error,
                        error_message="" if not message.is_error else (message.result or "Unknown error"),
                        error_category=classify_error(message.result or "") if message.is_error else ErrorCategory.UNKNOWN,
                    )

                else:
                    # Unknown message type — log and skip
                    logger.debug(
                        f"[{request_id}] Unknown message type: {type(message).__name__}"
                    )

        except asyncio.CancelledError:
            raise  # Propagate cancellation
        except RuntimeError as e:
            if "cancel scope" in str(e):
                # anyio cancel-scope leak — suppress and return partial results
                logger.warning(
                    f"[{request_id}] anyio cancel scope error after {message_count} messages (suppressed)"
                )
                combined = "\n\n".join(text_parts).strip()
                return SDKResponse(
                    text=combined or "Agent interrupted (anyio cleanup error).",
                    session_id=result_session_id,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    num_turns=num_turns,
                    is_error=True,
                    error_message="anyio cancel scope error",
                    error_category=ErrorCategory.TRANSIENT,
                )
            raise
        except Exception as e:
            # Stream processing error (not a timeout — that's caught in query())
            logger.error(
                f"[{request_id}] Stream processing error after {message_count} messages: {e}",
                exc_info=True,
            )
            combined = "\n\n".join(text_parts).strip()
            return SDKResponse(
                text=combined or f"Error during stream processing: {e}",
                session_id=result_session_id,
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                num_turns=num_turns,
                is_error=True,
                error_message=f"Stream error: {e}",
                error_category=classify_error(str(e)),
            )
        finally:
            # Explicitly close the async generator in THIS task to prevent
            # Python's GC from closing it in a different task (which triggers
            # the anyio cancel-scope RuntimeError that cascades and kills siblings).
            try:
                await gen.aclose()
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug(f"[{request_id}] Suppressed anyio cancel scope during generator close")
                else:
                    logger.warning(f"[{request_id}] RuntimeError during generator close: {e}")
            except Exception:
                pass  # Generator cleanup errors are non-critical
            finally:
                # Release the strong reference now that we've explicitly closed it
                self._active_generators.discard(gen)

        # Stream ended without ResultMessage — treat as success if we got any text,
        # otherwise flag as a real error (SDK failed to complete the stream).
        combined = "\n\n".join(text_parts).strip()
        if combined:
            logger.warning(
                f"[{request_id}] Stream ended without ResultMessage but got {len(combined)} chars of text "
                f"({message_count} messages). Treating as partial success."
            )
        else:
            logger.error(
                f"[{request_id}] Stream ended without ResultMessage and no text "
                f"({message_count} messages). This is an SDK-level error."
            )
        return SDKResponse(
            text=combined or "⚠️ Agent produced no text output (stream ended unexpectedly).",
            session_id=result_session_id,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            num_turns=num_turns,
            is_error=not bool(combined),  # Only error if we got nothing
            error_category=ErrorCategory.TRANSIENT,
        )

    async def query_with_retry(
        self,
        prompt: str,
        system_prompt: str,
        cwd: str,
        session_id: str | None = None,
        max_turns: int = 10,
        max_budget_usd: float = 2.0,
        max_retries: int = SDK_MAX_RETRIES,
        permission_mode: str | None = "bypassPermissions",
        on_stream=None,
        on_tool_use=None,
        allowed_tools: list[str] | None = None,
        tools: list[str] | None = None,
    ) -> SDKResponse:
        """Query with automatic retry based on error classification.

        Includes circuit breaker protection: if the SDK has failed
        ``failure_threshold`` consecutive times within the failure window,
        all calls are rejected with ``CircuitOpenError`` until the recovery
        timeout elapses.

        Retry strategy (exponential backoff):
        - TRANSIENT: retry with 1s, 2s, 4s backoff
        - RATE_LIMIT: retry with 5s, 15s, 30s backoff
        - SESSION: invalidate session_id, retry fresh (no backoff)
        - AUTH/BUDGET/PERMANENT: don't retry at all
        - UNKNOWN: retry once with 2s backoff
        """
        # --- Circuit breaker gate ---
        await self._circuit_breaker.raise_if_open()

        last_response: SDKResponse | None = None
        current_session = session_id
        total_cost = 0.0
        current_prompt = prompt

        for attempt in range(1, max_retries + 2):  # +2 because range is exclusive and attempt 1 is the initial try
            if attempt > max_retries + 1:
                break

            is_retry = attempt > 1

            if is_retry:
                # Re-check circuit breaker before each retry
                try:
                    await self._circuit_breaker.raise_if_open()
                except CircuitOpenError:
                    logger.warning(
                        f"Circuit breaker opened during retries (attempt {attempt})"
                    )
                    raise

                logger.info(
                    f"SDK query retry {attempt - 1}/{max_retries} "
                    f"(previous error: {last_response.error_category.value if last_response else 'none'})"
                )

            response = await self.query(
                prompt=current_prompt,
                system_prompt=system_prompt,
                cwd=cwd,
                session_id=current_session,
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                permission_mode=permission_mode,
                on_stream=on_stream,
                on_tool_use=on_tool_use,
                allowed_tools=allowed_tools,
                tools=tools,
            )

            # Accumulate cost across retries
            total_cost += response.cost_usd

            if not response.is_error:
                response.retry_count = attempt - 1
                response.cost_usd = total_cost  # Include cost from failed attempts
                # Record success with circuit breaker
                await self._circuit_breaker.record_success()
                return response

            # Record failure with circuit breaker
            await self._circuit_breaker.record_failure()

            last_response = response
            category = response.error_category

            # Don't retry permanent errors
            if category in (ErrorCategory.AUTH, ErrorCategory.BUDGET, ErrorCategory.PERMANENT):
                logger.error(
                    f"SDK non-retryable error ({category.value}): {response.error_message}"
                )
                response.cost_usd = total_cost
                return response

            # Check if we have retries left
            if attempt > max_retries:
                break

            # Compute backoff based on error category
            current_prompt = prompt  # Reset to original prompt for next retry
            if category == ErrorCategory.RATE_LIMIT:
                # Aggressive backoff for rate limits: 5s, 15s, 30s
                backoff = min(5 * (3 ** (attempt - 1)), 30)
                logger.warning(
                    f"Rate limited, backing off {backoff}s: {response.error_message}"
                )
            elif category == ErrorCategory.SESSION:
                # Session errors: retry fresh immediately
                backoff = 0.5
                current_session = None  # Drop the stale session
                logger.warning(
                    f"Session error, retrying without session: {response.error_message}"
                )
            elif category == ErrorCategory.TRANSIENT:
                # Exponential backoff: 1s, 2s, 4s
                backoff = min(1 * (2 ** (attempt - 1)), 8)
                logger.warning(
                    f"Transient error, retrying in {backoff}s: {response.error_message}"
                )
            elif category == ErrorCategory.UNKNOWN:
                # Cautious single retry
                backoff = 2
                logger.warning(
                    f"Unknown error, cautious retry in {backoff}s: {response.error_message}"
                )
            else:
                # Shouldn't reach here, but be safe
                backoff = 2
                logger.warning(
                    f"Unexpected category {category.value}, retrying in {backoff}s"
                )

            # For timeouts, modify the prompt to encourage efficiency
            if "timeout" in response.error_message.lower() and not is_retry:
                current_prompt = (
                    "[SYSTEM: Previous attempt timed out. Please complete the task efficiently. "
                    "Focus on the most important parts first.]\n\n" + prompt
                )

            await asyncio.sleep(backoff)

        # All retries exhausted
        logger.error(
            f"All {max_retries} retry attempts exhausted. "
            f"Last error: {last_response.error_message if last_response else 'none'}, "
            f"Total cost: ${total_cost:.4f}"
        )
        if last_response:
            last_response.retry_count = max_retries
            last_response.cost_usd = total_cost
            return last_response
        return SDKResponse(
            text="Error: All retry attempts failed",
            is_error=True,
            error_message="All retry attempts failed",
            error_category=ErrorCategory.UNKNOWN,
            cost_usd=total_cost,
            retry_count=max_retries,
        )
