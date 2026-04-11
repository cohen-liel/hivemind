"""Agent Runtime Abstraction Layer.

Hivemind supports multiple agent runtimes (Claude Code, OpenClaw, Codex, etc.)
through a unified interface.  Each runtime implements the ``AgentRuntime`` protocol,
and the ``RuntimeRegistry`` selects the appropriate runtime for each task based on
configuration.

Architecture
------------
    DAG Executor
      └─ calls ``runtime_registry.execute(role, prompt, ...)``
           └─ selects the runtime for the role (Claude Code, OpenClaw, etc.)
                └─ calls ``runtime.execute(prompt, ...)``
                     └─ returns ``RuntimeResponse``

Adding a new runtime
--------------------
1. Create a class that implements ``AgentRuntime``
2. Register it in ``RUNTIME_REGISTRY`` at the bottom of this file
3. Configure which roles use it via ``AGENT_RUNTIME_MAP`` in config or ``.env``

Example ``.env`` configuration::

    # Default runtime for all agents
    AGENT_RUNTIME_DEFAULT=claude_code

    # Per-role overrides (JSON)
    AGENT_RUNTIME_MAP={"researcher": "openclaw", "devops": "bash"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── Response types ──────────────────────────────────────────────────────


class RuntimeStatus(Enum):
    """Outcome of a runtime execution."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class RuntimeResponse:
    """Unified response from any agent runtime."""

    status: RuntimeStatus
    result_text: str = ""
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    error_message: str = ""
    runtime_name: str = ""
    raw: Any = None  # Runtime-specific raw response


@dataclass
class StreamEvent:
    """A streaming event from an agent runtime."""

    kind: str  # "text" | "tool_use" | "progress" | "error" | "done"
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Runtime Protocol ────────────────────────────────────────────────────


@runtime_checkable
class AgentRuntime(Protocol):
    """Protocol that all agent runtimes must implement."""

    @property
    def name(self) -> str:
        """Human-readable name of the runtime (e.g. 'Claude Code', 'OpenClaw')."""
        ...

    async def execute(
        self,
        prompt: str,
        *,
        working_dir: str,
        role: str = "",
        max_turns: int = 100,
        timeout: int = 900,
        budget_usd: float = 50.0,
        allowed_tools: list[str] | None = None,
        system_prompt: str = "",
        context_files: list[str] | None = None,
    ) -> RuntimeResponse:
        """Execute a prompt and return the response."""
        ...

    async def execute_streaming(
        self,
        prompt: str,
        *,
        working_dir: str,
        role: str = "",
        max_turns: int = 100,
        timeout: int = 900,
        budget_usd: float = 50.0,
        allowed_tools: list[str] | None = None,
        system_prompt: str = "",
        context_files: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Execute a prompt with streaming output."""
        ...

    async def health_check(self) -> bool:
        """Return True if the runtime is available and healthy."""
        ...

    async def shutdown(self) -> None:
        """Clean up resources."""
        ...


# ── Claude Code Runtime ─────────────────────────────────────────────────


class ClaudeCodeRuntime:
    """Runtime that delegates to the Claude Code SDK (existing behavior).

    This is the default runtime.  It wraps the existing ``isolated_query()``
    function from ``sdk_client.py`` / ``isolated_query.py`` so that all
    existing behavior is preserved.
    """

    @property
    def name(self) -> str:
        return "Claude Code"

    async def execute(
        self,
        prompt: str,
        *,
        working_dir: str,
        role: str = "",
        max_turns: int = 100,
        timeout: int = 900,
        budget_usd: float = 50.0,
        allowed_tools: list[str] | None = None,
        system_prompt: str = "",
        context_files: list[str] | None = None,
    ) -> RuntimeResponse:
        """Execute via Claude Code SDK (delegates to isolated_query)."""
        import state
        from isolated_query import isolated_query

        sdk = state.sdk_client
        if sdk is None:
            return RuntimeResponse(
                status=RuntimeStatus.ERROR,
                error_message="SDK not initialized (state.sdk_client is None)",
                runtime_name=self.name,
            )

        start = time.monotonic()
        try:
            response = await isolated_query(
                sdk,
                prompt=prompt,
                cwd=working_dir,
                max_turns=max_turns,
                max_budget_usd=budget_usd,
                system_prompt=system_prompt,
                per_message_timeout=timeout,
                allowed_tools=allowed_tools,
            )
            elapsed = time.monotonic() - start

            return RuntimeResponse(
                status=RuntimeStatus.SUCCESS if not response.is_error else RuntimeStatus.ERROR,
                result_text=response.text,
                cost_usd=response.cost_usd,
                duration_seconds=elapsed,
                tokens_in=response.input_tokens,
                tokens_out=response.output_tokens,
                error_message=response.error_message if response.is_error else "",
                runtime_name=self.name,
                raw=response,
            )
        except asyncio.CancelledError:
            return RuntimeResponse(
                status=RuntimeStatus.CANCELLED,
                duration_seconds=time.monotonic() - start,
                runtime_name=self.name,
            )
        except Exception as e:
            return RuntimeResponse(
                status=RuntimeStatus.ERROR,
                error_message=str(e),
                duration_seconds=time.monotonic() - start,
                runtime_name=self.name,
            )

    async def execute_streaming(self, prompt, **kwargs):
        """Streaming not yet implemented for Claude Code — falls back to execute."""
        response = await self.execute(prompt, **kwargs)
        yield StreamEvent(kind="text", content=response.result_text)
        yield StreamEvent(kind="done", metadata={"cost_usd": response.cost_usd})

    async def health_check(self) -> bool:
        """Check if Claude Code CLI is available."""
        try:
            from config import CLAUDE_CLI_PATH

            result = subprocess.run(
                [CLAUDE_CLI_PATH, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def shutdown(self) -> None:
        pass


# ── OpenClaw Runtime ─────────────────────────────────────────────────────


class OpenClawRuntime:
    """Runtime that delegates to OpenClaw agents.

    OpenClaw agents are spawned as subprocesses or via the OpenClaw SDK.
    This runtime supports the full OpenClaw lifecycle: spawn, execute, collect.

    Configuration via environment:
        OPENCLAW_PATH       — Path to openclaw binary (default: 'openclaw')
        OPENCLAW_MODEL      — Default model (default: 'claude-sonnet-4-20250514')
        OPENCLAW_MAX_TURNS  — Default max turns (default: 100)
    """

    def __init__(self):
        self._openclaw_path = os.getenv("OPENCLAW_PATH", "openclaw")
        self._default_model = os.getenv("OPENCLAW_MODEL", "claude-sonnet-4-20250514")

    @property
    def name(self) -> str:
        return "OpenClaw"

    async def execute(
        self,
        prompt: str,
        *,
        working_dir: str,
        role: str = "",
        max_turns: int = 100,
        timeout: int = 900,
        budget_usd: float = 50.0,
        allowed_tools: list[str] | None = None,
        system_prompt: str = "",
        context_files: list[str] | None = None,
    ) -> RuntimeResponse:
        """Execute via OpenClaw subprocess."""
        start = time.monotonic()

        cmd = [
            self._openclaw_path,
            "--print",
            "--model",
            self._default_model,
            "--max-turns",
            str(max_turns),
        ]

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        if allowed_tools:
            for tool in allowed_tools:
                cmd.extend(["--allowedTools", tool])

        # Add the prompt
        cmd.extend(["-p", prompt])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return RuntimeResponse(
                    status=RuntimeStatus.TIMEOUT,
                    error_message=f"OpenClaw timed out after {timeout}s",
                    duration_seconds=time.monotonic() - start,
                    runtime_name=self.name,
                )

            elapsed = time.monotonic() - start
            result_text = stdout.decode("utf-8", errors="replace").strip()
            error_text = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                return RuntimeResponse(
                    status=RuntimeStatus.ERROR,
                    result_text=result_text,
                    error_message=error_text or f"OpenClaw exited with code {proc.returncode}",
                    duration_seconds=elapsed,
                    runtime_name=self.name,
                )

            return RuntimeResponse(
                status=RuntimeStatus.SUCCESS,
                result_text=result_text,
                duration_seconds=elapsed,
                runtime_name=self.name,
            )

        except FileNotFoundError:
            return RuntimeResponse(
                status=RuntimeStatus.ERROR,
                error_message=(
                    f"OpenClaw binary not found at '{self._openclaw_path}'. "
                    "Install with: npm install -g @anthropic-ai/openclaw"
                ),
                duration_seconds=time.monotonic() - start,
                runtime_name=self.name,
            )
        except Exception as e:
            return RuntimeResponse(
                status=RuntimeStatus.ERROR,
                error_message=str(e),
                duration_seconds=time.monotonic() - start,
                runtime_name=self.name,
            )

    async def execute_streaming(self, prompt, **kwargs):
        """Streaming execution via OpenClaw."""
        response = await self.execute(prompt, **kwargs)
        yield StreamEvent(kind="text", content=response.result_text)
        yield StreamEvent(kind="done")

    async def health_check(self) -> bool:
        """Check if OpenClaw is installed and available."""
        try:
            result = subprocess.run(
                [self._openclaw_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def shutdown(self) -> None:
        pass


# ── Bash Runtime ─────────────────────────────────────────────────────────


class BashRuntime:
    """Runtime that executes prompts as bash scripts.

    Useful for DevOps tasks, simple file operations, or as a fallback.
    The prompt is expected to be a valid bash script.
    """

    @property
    def name(self) -> str:
        return "Bash"

    async def execute(
        self,
        prompt: str,
        *,
        working_dir: str,
        role: str = "",
        max_turns: int = 1,
        timeout: int = 300,
        budget_usd: float = 0.0,
        allowed_tools: list[str] | None = None,
        system_prompt: str = "",
        context_files: list[str] | None = None,
    ) -> RuntimeResponse:
        """Execute the prompt as a bash script."""
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                prompt,
                cwd=working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return RuntimeResponse(
                    status=RuntimeStatus.TIMEOUT,
                    error_message=f"Bash timed out after {timeout}s",
                    duration_seconds=time.monotonic() - start,
                    runtime_name=self.name,
                )

            elapsed = time.monotonic() - start
            return RuntimeResponse(
                status=RuntimeStatus.SUCCESS if proc.returncode == 0 else RuntimeStatus.ERROR,
                result_text=stdout.decode("utf-8", errors="replace").strip(),
                error_message=stderr.decode("utf-8", errors="replace").strip()
                if proc.returncode != 0
                else "",
                duration_seconds=elapsed,
                runtime_name=self.name,
            )

        except Exception as e:
            return RuntimeResponse(
                status=RuntimeStatus.ERROR,
                error_message=str(e),
                duration_seconds=time.monotonic() - start,
                runtime_name=self.name,
            )

    async def execute_streaming(self, prompt, **kwargs):
        response = await self.execute(prompt, **kwargs)
        yield StreamEvent(kind="text", content=response.result_text)
        yield StreamEvent(kind="done")

    async def health_check(self) -> bool:
        return True

    async def shutdown(self) -> None:
        pass


# ── HTTP Runtime ─────────────────────────────────────────────────────────


class HTTPRuntime:
    """Runtime that sends prompts to an HTTP endpoint.

    Useful for connecting to any LLM API, custom agent servers, or
    external services.

    Configuration via environment:
        HTTP_RUNTIME_URL     — Endpoint URL (required)
        HTTP_RUNTIME_TOKEN   — Bearer token (optional)
        HTTP_RUNTIME_TIMEOUT — Request timeout in seconds (default: 300)
    """

    def __init__(self):
        self._url = os.getenv("HTTP_RUNTIME_URL", "")
        self._token = os.getenv("HTTP_RUNTIME_TOKEN", "")
        self._timeout = int(os.getenv("HTTP_RUNTIME_TIMEOUT", "300"))

    @property
    def name(self) -> str:
        return "HTTP"

    async def execute(
        self,
        prompt: str,
        *,
        working_dir: str,
        role: str = "",
        max_turns: int = 100,
        timeout: int = 300,
        budget_usd: float = 50.0,
        allowed_tools: list[str] | None = None,
        system_prompt: str = "",
        context_files: list[str] | None = None,
    ) -> RuntimeResponse:
        """Send prompt to HTTP endpoint and return response."""
        start = time.monotonic()

        if not self._url:
            return RuntimeResponse(
                status=RuntimeStatus.ERROR,
                error_message="HTTP_RUNTIME_URL not configured",
                duration_seconds=0,
                runtime_name=self.name,
            )

        try:
            import aiohttp

            headers = {"Content-Type": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            payload = {
                "prompt": prompt,
                "role": role,
                "working_dir": working_dir,
                "max_turns": max_turns,
                "system_prompt": system_prompt,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout or self._timeout),
                ) as resp:
                    data = await resp.json()
                    elapsed = time.monotonic() - start

                    return RuntimeResponse(
                        status=RuntimeStatus.SUCCESS if resp.status == 200 else RuntimeStatus.ERROR,
                        result_text=data.get("result", ""),
                        cost_usd=data.get("cost_usd", 0.0),
                        duration_seconds=elapsed,
                        error_message=data.get("error", "") if resp.status != 200 else "",
                        runtime_name=self.name,
                        raw=data,
                    )

        except ImportError:
            return RuntimeResponse(
                status=RuntimeStatus.ERROR,
                error_message="aiohttp not installed. Run: pip install aiohttp",
                duration_seconds=time.monotonic() - start,
                runtime_name=self.name,
            )
        except Exception as e:
            return RuntimeResponse(
                status=RuntimeStatus.ERROR,
                error_message=str(e),
                duration_seconds=time.monotonic() - start,
                runtime_name=self.name,
            )

    async def execute_streaming(self, prompt, **kwargs):
        response = await self.execute(prompt, **kwargs)
        yield StreamEvent(kind="text", content=response.result_text)
        yield StreamEvent(kind="done")

    async def health_check(self) -> bool:
        return bool(self._url)

    async def shutdown(self) -> None:
        pass


# ── Runtime Registry ─────────────────────────────────────────────────────

# All available runtimes
AVAILABLE_RUNTIMES: dict[str, AgentRuntime] = {
    "claude_code": ClaudeCodeRuntime(),
    "openclaw": OpenClawRuntime(),
    "bash": BashRuntime(),
    "http": HTTPRuntime(),
}

# Default runtime
_DEFAULT_RUNTIME: str = os.getenv("AGENT_RUNTIME_DEFAULT", "claude_code")

# Per-role runtime overrides (JSON from env)
_RUNTIME_MAP_RAW: str = os.getenv("AGENT_RUNTIME_MAP", "{}")
try:
    _RUNTIME_MAP: dict[str, str] = json.loads(_RUNTIME_MAP_RAW)
except (json.JSONDecodeError, TypeError):
    _RUNTIME_MAP = {}
    logger.warning("Failed to parse AGENT_RUNTIME_MAP — using defaults")


def get_runtime(role: str = "") -> AgentRuntime:
    """Get the appropriate runtime for a given agent role.

    Resolution order:
    1. Per-role override from AGENT_RUNTIME_MAP
    2. Default runtime from AGENT_RUNTIME_DEFAULT
    3. Claude Code (hardcoded fallback)
    """
    runtime_key = _RUNTIME_MAP.get(role, _DEFAULT_RUNTIME)

    if runtime_key not in AVAILABLE_RUNTIMES:
        logger.warning(
            "Unknown runtime '%s' for role '%s' — falling back to claude_code",
            runtime_key,
            role,
        )
        runtime_key = "claude_code"

    return AVAILABLE_RUNTIMES[runtime_key]


def get_runtime_name(role: str = "") -> str:
    """Get the name of the runtime assigned to a role."""
    return get_runtime(role).name


async def check_all_runtimes() -> dict[str, bool]:
    """Health check all registered runtimes."""
    results = {}
    for key, runtime in AVAILABLE_RUNTIMES.items():
        try:
            results[key] = await runtime.health_check()
        except Exception:
            results[key] = False
    return results
