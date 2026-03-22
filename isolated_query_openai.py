"""OpenAI-based isolated_query replacement for benchmarking.

Drop-in replacement for ``isolated_query()`` that uses gpt-4.1-mini with
the same tool set that Claude Code CLI provides (Read, Write, Edit, Bash,
Glob, Grep).  This allows running *actual* HiveMind DAG execution without
needing a Claude Code CLI installation.

The agent loop:
  1. Send messages + tools to gpt-4.1-mini
  2. If the model returns tool_calls, execute them locally and feed results back
  3. Repeat until the model returns a text-only response or max_turns is reached

Tool implementations mirror Claude Code CLI behaviour:
  - Read: read file contents (with optional line range)
  - Write: create/overwrite a file
  - Edit: search-and-replace within a file
  - Bash: execute a shell command (with timeout)
  - Glob: find files matching a glob pattern
  - Grep: search file contents with regex
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import glob as glob_mod
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sdk_client import ErrorCategory, SDKResponse

logger = logging.getLogger(__name__)

# Thread pool — mirrors the original isolated_query design
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=10,
    thread_name_prefix="isolated-openai",
)

# ── OpenAI Tool Definitions ─────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": (
                "Read the contents of a file. Returns the full file content, "
                "or a specific line range if start_line/end_line are provided."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file to read",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-indexed). Optional.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (1-indexed, inclusive). Optional.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Write",
            "description": (
                "Create or overwrite a file with the given content. "
                "Creates parent directories automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write to the file",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Edit",
            "description": (
                "Edit a file by replacing an exact string match with new content. "
                "The old_string must match exactly (including whitespace and indentation). "
                "If old_string is empty, content is inserted at the beginning of the file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to find and replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement string",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": (
                "Execute a bash command in the project directory. "
                "Returns stdout and stderr. Use for running tests, "
                "installing packages, git operations, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 120)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": (
                "Find files matching a glob pattern. Returns a list of matching file paths."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (e.g., '**/*.py', 'src/**/*.ts')",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Lint",
            "description": (
                "Run the Ruff linter on Python files to find code quality issues. "
                "Returns a list of issues with file paths, line numbers, and descriptions. "
                "Use this AFTER writing code to check for issues and fix them. "
                "Can also auto-fix issues if fix=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or directory to lint (default: '.')",
                    },
                    "fix": {
                        "type": "boolean",
                        "description": "Auto-fix fixable issues (default: false)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": (
                "Search file contents using a regex pattern. "
                "Returns matching lines with file paths and line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in (default: '.')",
                    },
                    "include": {
                        "type": "string",
                        "description": "File glob to include (e.g., '*.py'). Optional.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]

# ── Tool Execution ───────────────────────────────────────────────────────


def _resolve_path(cwd: str, file_path: str) -> str:
    """Resolve a file path relative to the project directory.

    Handles several cases:
    - Absolute paths: used as-is
    - Relative paths that duplicate the cwd suffix: stripped and resolved
      (e.g., cwd='/a/b/c' and file_path='b/c/file.py' -> '/a/b/c/file.py')
    - Simple relative paths: resolved relative to cwd
    """
    p = Path(file_path)
    if p.is_absolute():
        return str(p.resolve())

    # Check if the relative path starts with a suffix of the cwd path.
    # This happens when the agent includes the project directory in the path
    # (e.g., system prompt says 'work in /a/b/c' and agent writes to 'b/c/file.py').
    cwd_parts = Path(cwd).resolve().parts
    file_parts = p.parts

    for i in range(len(cwd_parts)):
        suffix = cwd_parts[i:]
        if len(suffix) <= len(file_parts) and file_parts[:len(suffix)] == suffix:
            # The file_path starts with a suffix of cwd — strip the duplicate
            remaining = file_parts[len(suffix):]
            if remaining:
                return str(Path(cwd).resolve() / Path(*remaining))
            else:
                return str(Path(cwd).resolve())

    # Simple relative path
    return str((Path(cwd) / p).resolve())


def _exec_read(cwd: str, args: dict) -> str:
    """Execute the Read tool."""
    fpath = _resolve_path(cwd, args["file_path"])
    if not os.path.exists(fpath):
        return f"Error: File not found: {fpath}"
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        start = args.get("start_line")
        end = args.get("end_line")
        if start is not None:
            start = max(1, int(start)) - 1  # convert to 0-indexed
            end = int(end) if end is not None else len(lines)
            lines = lines[start:end]
        content = "".join(lines)
        # Truncate very large files
        if len(content) > 50_000:
            content = content[:50_000] + f"\n\n... [truncated, file has {len(lines)} lines total]"
        return content
    except Exception as e:
        return f"Error reading {fpath}: {e}"


def _auto_lint(fpath: str, cwd: str) -> str:
    """Auto-run ruff on a Python file and return issues if any."""
    if not fpath.endswith(".py"):
        return ""
    try:
        # First auto-fix what we can
        subprocess.run(
            ["ruff", "check", fpath, "--fix", "--quiet"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        # Then report remaining issues
        result = subprocess.run(
            ["ruff", "check", fpath, "--output-format=concise"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip():
            return f"\n\n⚠️ Lint issues (auto-fixed what was possible, these remain):\n{result.stdout.strip()}"
        return "\n✅ Lint: all checks passed."
    except Exception:
        return ""


def _exec_write(cwd: str, args: dict) -> str:
    """Execute the Write tool."""
    fpath = _resolve_path(cwd, args["file_path"])
    content = args.get("content", "")
    try:
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        msg = f"Successfully wrote {len(content)} bytes to {fpath}"
        msg += _auto_lint(fpath, cwd)
        return msg
    except Exception as e:
        return f"Error writing {fpath}: {e}"


def _exec_edit(cwd: str, args: dict) -> str:
    """Execute the Edit tool."""
    fpath = _resolve_path(cwd, args["file_path"])
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")

    if not os.path.exists(fpath):
        return f"Error: File not found: {fpath}"

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        # Safety: prevent replacing large chunks with empty string (file deletion)
        if old_string and not new_string and len(old_string) > 100:
            return (
                f"Error: Refusing to delete {len(old_string)} characters from {fpath}. "
                f"Use targeted edits instead of replacing large blocks with empty string."
            )

        # Safety: prevent replacing entire file content
        if old_string and old_string.strip() == content.strip():
            return (
                f"Error: Cannot replace the entire file content via Edit. "
                f"Use targeted edits to modify specific parts of the file."
            )

        if old_string == "":
            # Insert at beginning
            content = new_string + content
        elif old_string not in content:
            return (
                f"Error: Could not find the exact string to replace in {fpath}. "
                f"The old_string was not found. Make sure it matches exactly "
                f"including whitespace and indentation."
            )
        else:
            # Replace first occurrence only
            content = content.replace(old_string, new_string, 1)

        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        msg = f"Successfully edited {fpath}"
        msg += _auto_lint(fpath, cwd)
        return msg
    except Exception as e:
        return f"Error editing {fpath}: {e}"


def _exec_bash(cwd: str, args: dict) -> str:
    """Execute the Bash tool."""
    command = args.get("command", "")
    timeout = min(args.get("timeout", 30), 60)  # Cap at 60s to prevent blocking on servers
    if not command:
        return "Error: No command provided"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += f"STDERR:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[Exit code: {result.returncode}]"
        # Truncate very long output
        if len(output) > 30_000:
            output = output[:15_000] + "\n\n... [output truncated] ...\n\n" + output[-15_000:]
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout}s"
    except Exception as e:
        return f"Error executing command: {e}"


def _exec_glob(cwd: str, args: dict) -> str:
    """Execute the Glob tool."""
    pattern = args.get("pattern", "")
    try:
        matches = sorted(glob_mod.glob(pattern, root_dir=cwd, recursive=True))
        if not matches:
            return "No files matched the pattern."
        # Limit output
        if len(matches) > 200:
            return "\n".join(matches[:200]) + f"\n\n... and {len(matches) - 200} more files"
        return "\n".join(matches)
    except Exception as e:
        return f"Error in glob: {e}"


def _exec_lint(cwd: str, args: dict) -> str:
    """Execute the Lint tool using ruff."""
    path = args.get("path", ".")
    fix = args.get("fix", False)

    cmd = ["ruff", "check", path, "--output-format=full"]
    if fix:
        cmd.append("--fix")

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout or ""
        if result.stderr:
            output += f"\n{result.stderr}"
        if not output.strip():
            return "All checks passed! No issues found."
        # Truncate
        if len(output) > 20_000:
            lines = output.split("\n")
            output = "\n".join(lines[:100]) + f"\n\n... [{len(lines)} total issues]"
        return output
    except FileNotFoundError:
        return "Error: ruff is not installed. Use Bash to run: pip install ruff"
    except subprocess.TimeoutExpired:
        return "Error: Lint timed out"
    except Exception as e:
        return f"Error in lint: {e}"


def _exec_grep(cwd: str, args: dict) -> str:
    """Execute the Grep tool."""
    pattern = args.get("pattern", "")
    search_path = args.get("path", ".")
    include = args.get("include", "")

    cmd = ["grep", "-rn", "--color=never"]
    if include:
        cmd.extend(["--include", include])
    cmd.append(pattern)
    cmd.append(search_path)

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout
        if not output:
            return "No matches found."
        # Truncate
        if len(output) > 20_000:
            lines = output.split("\n")
            output = "\n".join(lines[:200]) + f"\n\n... [{len(lines)} total matches]"
        return output
    except subprocess.TimeoutExpired:
        return "Error: Grep timed out"
    except Exception as e:
        return f"Error in grep: {e}"


TOOL_EXECUTORS = {
    "Read": _exec_read,
    "Write": _exec_write,
    "Edit": _exec_edit,
    "Bash": _exec_bash,
    "Glob": _exec_glob,
    "Grep": _exec_grep,
    "Lint": _exec_lint,
}


# ── Agent Loop ───────────────────────────────────────────────────────────


def _run_agent_loop(
    *,
    prompt: str,
    system_prompt: str,
    cwd: str,
    max_turns: int,
    model: str,
    on_stream: Callable | None,
    on_tool_use: Callable | None,
    tools_enabled: bool = True,
    allowed_tools: list[str] | None = None,
) -> SDKResponse:
    """Synchronous agent loop using OpenAI chat completions with tool calling.

    Runs in a thread pool worker. Executes tools locally and feeds results
    back to the model until it produces a final text response or max_turns
    is exhausted.
    """
    from openai import OpenAI

    client = OpenAI()  # Uses OPENAI_API_KEY env var

    messages: list[dict[str, Any]] = []
    if system_prompt:
        # Add critical tool-usage instruction
        tool_instruction = (
            "\n\nIMPORTANT: You MUST use the provided tools (Write, Read, Edit, Bash, Lint) to do your work. "
            "ALWAYS use the Write tool to create or modify files. "
            "NEVER just print code as text in your response — the user cannot see your text output. "
            "The ONLY way to create files is by calling the Write tool. "
            "AFTER writing Python code, ALWAYS run the Lint tool to check for issues and fix them. "
            "Use Lint with fix=true to auto-fix simple issues, then manually fix any remaining ones."
        )
        messages.append({"role": "system", "content": system_prompt + tool_instruction})
    messages.append({"role": "user", "content": prompt})

    tool_uses: list[str] = []
    text_parts: list[str] = []
    total_input_tokens = 0
    total_output_tokens = 0
    t0 = time.monotonic()

    if not tools_enabled:
        active_tools = []
    elif allowed_tools is not None:
        active_tools = [t for t in TOOLS if t["function"]["name"] in allowed_tools]
    else:
        active_tools = TOOLS

    for turn in range(max_turns):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": 16_000,
                "temperature": 0.2,
            }
            if active_tools:
                kwargs["tools"] = active_tools

            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error(f"OpenAI API error on turn {turn}: {e}")
            return SDKResponse(
                text=f"Error: OpenAI API call failed: {e}",
                is_error=True,
                error_message=str(e),
                error_category=ErrorCategory.TRANSIENT,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                total_tokens=total_input_tokens + total_output_tokens,
                duration_ms=int((time.monotonic() - t0) * 1000),
                num_turns=turn,
                tool_uses=tool_uses or None,
            )

        usage = response.usage
        if usage:
            total_input_tokens += usage.prompt_tokens or 0
            total_output_tokens += usage.completion_tokens or 0

        choice = response.choices[0]
        msg = choice.message

        # Add assistant message to history
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            assistant_msg["content"] = msg.content
            text_parts.append(msg.content)
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_msg)

        # If no tool calls, we're done
        if not msg.tool_calls:
            break

        # Execute each tool call
        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_args = {}

            tool_uses.append(tool_name)

            # Log tool use
            if tool_name == "Read":
                path = tool_args.get("file_path", "")
                logger.info(f"  [Turn {turn}] Read: {path}")
            elif tool_name == "Write":
                path = tool_args.get("file_path", "")
                logger.info(f"  [Turn {turn}] Write: {path}")
            elif tool_name == "Edit":
                path = tool_args.get("file_path", "")
                logger.info(f"  [Turn {turn}] Edit: {path}")
            elif tool_name == "Bash":
                cmd = str(tool_args.get("command", ""))[:80]
                logger.info(f"  [Turn {turn}] Bash: {cmd}")
            elif tool_name == "Glob":
                pattern = tool_args.get("pattern", "")
                logger.info(f"  [Turn {turn}] Glob: {pattern}")
            elif tool_name == "Grep":
                pattern = tool_args.get("pattern", "")
                logger.info(f"  [Turn {turn}] Grep: {pattern}")

            # Execute tool
            executor = TOOL_EXECUTORS.get(tool_name)
            if executor:
                tool_result = executor(cwd, tool_args)
            else:
                tool_result = f"Error: Unknown tool '{tool_name}'"

            # Add tool result to messages
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                }
            )

            # Notify callbacks
            if on_tool_use:
                try:
                    tool_info = f"{tool_name}: {json.dumps(tool_args)[:100]}"
                    # on_tool_use may be async — we're in a sync context so just log
                    logger.debug(f"Tool use: {tool_info}")
                except Exception:
                    pass

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    combined_text = "\n\n".join(text_parts).strip()

    if not combined_text and tool_uses:
        tools_summary = ", ".join(set(tool_uses))
        combined_text = (
            f"Task completed via tool use ({len(tool_uses)} tool calls, "
            f"tools: {tools_summary}). No final text output."
        )

    return SDKResponse(
        text=combined_text,
        session_id="",  # No session persistence with OpenAI
        cost_usd=0.0,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        total_tokens=total_input_tokens + total_output_tokens,
        duration_ms=elapsed_ms,
        num_turns=turn + 1 if "turn" in dir() else 0,
        is_error=False,
        tool_uses=list(tool_uses) if tool_uses else None,
    )


# ── Public API (drop-in replacement) ────────────────────────────────────


async def isolated_query(
    sdk,  # Ignored — kept for API compatibility
    *,
    prompt: str,
    system_prompt: str,
    cwd: str,
    session_id: str | None = None,
    max_turns: int = 10,
    max_budget_usd: float = 2.0,
    max_retries: int = 2,
    permission_mode: str | None = None,
    on_stream: Callable | None = None,
    on_tool_use: Callable | None = None,
    allowed_tools: list[str] | None = None,
    tools: list[str] | None = None,
    per_message_timeout: int | None = None,
    model: str | None = None,
) -> SDKResponse:
    """Drop-in replacement for the Claude Code CLI isolated_query.

    Same signature, same return type (SDKResponse). Uses gpt-4.1-mini
    with local tool execution instead of Claude Code CLI subprocess.
    """
    request_id = f"oai_{int(time.monotonic() * 1000) % 100000}"
    effective_model = model or "gpt-4.1-mini"

    # If tools is explicitly set to empty list, disable tools (summary phase)
    tools_enabled = tools is None or len(tools) > 0
    # allowed_tools filters which tools are available (e.g., review agent gets no Write)
    _allowed_tools = allowed_tools

    logger.info(
        f"[{request_id}] OpenAI isolated_query: model={effective_model}, "
        f"max_turns={max_turns}, tools={'enabled' if tools_enabled else 'disabled'}, "
        f"cwd={cwd}"
    )

    loop = asyncio.get_running_loop()

    for attempt in range(1, max_retries + 2):
        try:
            result = await loop.run_in_executor(
                _executor,
                lambda: _run_agent_loop(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    cwd=cwd,
                    max_turns=max_turns,
                    model=effective_model,
                    on_stream=on_stream,
                    on_tool_use=on_tool_use,
                    tools_enabled=tools_enabled,
                    allowed_tools=_allowed_tools,
                ),
            )

            if not result.is_error:
                result.retry_count = attempt - 1
                logger.info(
                    f"[{request_id}] Completed: turns={result.num_turns}, "
                    f"tokens={result.total_tokens}, "
                    f"elapsed={result.duration_ms}ms"
                )
                return result

            # Retry on transient errors
            if attempt <= max_retries and result.error_category == ErrorCategory.TRANSIENT:
                logger.warning(
                    f"[{request_id}] Attempt {attempt} failed (transient), retrying..."
                )
                await asyncio.sleep(min(2 ** attempt, 10))
                continue

            return result

        except Exception as e:
            logger.error(f"[{request_id}] Attempt {attempt} exception: {e}", exc_info=True)
            if attempt > max_retries:
                return SDKResponse(
                    text=f"Error: {e}",
                    is_error=True,
                    error_message=str(e),
                    error_category=ErrorCategory.UNKNOWN,
                )
            await asyncio.sleep(min(2 ** attempt, 10))

    return SDKResponse(
        text="Error: All retry attempts failed",
        is_error=True,
        error_message="All retries exhausted",
        error_category=ErrorCategory.UNKNOWN,
    )
