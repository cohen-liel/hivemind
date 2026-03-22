"""Sandbox Guard — Firejail-based process isolation for agent execution.

Outsources process isolation to Firejail, a lightweight Linux sandboxing tool
that uses namespaces and seccomp-bpf to restrict what agent subprocesses can
access.  This adds a critical security layer when agents execute arbitrary
code via Claude Code CLI.

Architecture
------------
    isolated_query.py
      └─ calls ``wrap_command_with_sandbox(cmd, cwd)``
           └─ prepends Firejail with appropriate restrictions
           └─ returns modified command list

    The wrapper is transparent: if Firejail is not installed or sandboxing
    is disabled, the original command is returned unchanged.

Security restrictions applied:
    - Network access limited to localhost (agents can't exfiltrate data)
    - No access to /home except the project working directory
    - Read-only access to system directories
    - No access to /etc/shadow, SSH keys, or other sensitive files
    - Process count limited (no fork bombs)
    - Temporary /tmp per sandbox (isolated from host)

Configuration via environment:
    SANDBOX_ENABLED         — Enable/disable (default: true)
    SANDBOX_BACKEND         — "firejail" or "none" (default: "firejail")
    SANDBOX_ALLOW_NETWORK   — Allow network access (default: false)
    SANDBOX_EXTRA_ARGS      — Additional Firejail args (JSON list, default: [])

References:
    - Firejail: https://github.com/netblue30/firejail
    - Firejail docs: https://firejail.wordpress.com/
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

SANDBOX_ENABLED = os.getenv("SANDBOX_ENABLED", "true").lower() in ("true", "1", "yes")
SANDBOX_BACKEND = os.getenv("SANDBOX_BACKEND", "firejail").lower()
SANDBOX_ALLOW_NETWORK = os.getenv("SANDBOX_ALLOW_NETWORK", "false").lower() in ("true", "1", "yes")

_EXTRA_ARGS_RAW = os.getenv("SANDBOX_EXTRA_ARGS", "[]")
try:
    SANDBOX_EXTRA_ARGS: list[str] = json.loads(_EXTRA_ARGS_RAW)
except (json.JSONDecodeError, TypeError):
    SANDBOX_EXTRA_ARGS = []

# ── Firejail detection ───────────────────────────────────────────────────

_firejail_path: str | None = None
_firejail_checked = False


def _find_firejail() -> str | None:
    """Find the Firejail binary on the system."""
    global _firejail_path, _firejail_checked

    if _firejail_checked:
        return _firejail_path

    _firejail_checked = True

    # Check common locations
    path = shutil.which("firejail")
    if path:
        _firejail_path = path
        logger.info(f"[SandboxGuard] Firejail found at {path}")
        return path

    for candidate in ["/usr/bin/firejail", "/usr/local/bin/firejail"]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            _firejail_path = candidate
            logger.info(f"[SandboxGuard] Firejail found at {candidate}")
            return candidate

    logger.info(
        "[SandboxGuard] Firejail not installed — running without sandbox. "
        "Install with: sudo apt-get install firejail"
    )
    return None


def is_sandbox_available() -> bool:
    """Check if sandboxing is available and enabled."""
    if not SANDBOX_ENABLED:
        return False
    if SANDBOX_BACKEND == "none":
        return False
    return _find_firejail() is not None


# ── Firejail profile generation ─────────────────────────────────────────


def _generate_firejail_profile(working_dir: str) -> list[str]:
    """Generate Firejail command-line arguments for agent isolation.

    Args:
        working_dir: The project directory the agent should have access to.

    Returns:
        List of Firejail arguments (without the firejail binary itself).
    """
    args = [
        # Filesystem isolation
        "--noprofile",           # Don't use default profiles
        "--private-tmp",         # Isolated /tmp
        "--private-dev",         # Minimal /dev
        "--read-only=/usr",      # System dirs read-only
        "--read-only=/lib",
        "--read-only=/lib64",
        "--read-only=/bin",
        "--read-only=/sbin",

        # Allow read-write access to the project directory
        f"--whitelist={working_dir}",

        # Allow access to node/npm/python (needed for Claude Code CLI)
        "--whitelist=/usr/bin",
        "--whitelist=/usr/lib",
        "--whitelist=/usr/local",

        # Block sensitive files
        "--blacklist=/etc/shadow",
        "--blacklist=/etc/gshadow",
        "--blacklist=/root",
        "--blacklist=~/.ssh",
        "--blacklist=~/.gnupg",
        "--blacklist=~/.aws",
        "--blacklist=~/.config/gcloud",

        # Process limits
        "--rlimit-nproc=100",    # Max 100 processes
        "--rlimit-nofile=1024",  # Max 1024 open files
        "--rlimit-fsize=500000000",  # Max 500MB file size

        # Security
        "--caps.drop=all",       # Drop all capabilities
        "--nonewprivs",          # No privilege escalation
        "--seccomp",             # Enable seccomp filter
    ]

    # Network isolation
    if not SANDBOX_ALLOW_NETWORK:
        args.append("--net=none")
    else:
        # Allow network but restrict to localhost
        args.append("--netfilter")

    # Allow access to Claude Code CLI config
    claude_config = os.path.expanduser("~/.claude")
    if os.path.isdir(claude_config):
        args.append(f"--whitelist={claude_config}")

    # Allow access to npm global modules (needed for Claude Code)
    npm_prefix = os.path.expanduser("~/.npm")
    if os.path.isdir(npm_prefix):
        args.append(f"--whitelist={npm_prefix}")

    # Node modules in the project
    node_modules = os.path.join(working_dir, "node_modules")
    if os.path.isdir(node_modules):
        args.append(f"--whitelist={node_modules}")

    # Add any extra user-configured args
    args.extend(SANDBOX_EXTRA_ARGS)

    return args


# ── Public API ───────────────────────────────────────────────────────────


def wrap_command_with_sandbox(
    cmd: list[str],
    working_dir: str,
    *,
    allow_network: bool | None = None,
) -> list[str]:
    """Wrap a command with Firejail sandboxing.

    This is the main entry point used by isolated_query.py.  It prepends
    the Firejail binary and security arguments to the command list.

    If Firejail is not available or sandboxing is disabled, returns the
    original command unchanged.

    Args:
        cmd: The original command list (e.g., ["claude", "--print", "-p", "..."]).
        working_dir: The project directory the agent should have access to.
        allow_network: Override network access setting for this command.

    Returns:
        Modified command list with Firejail prepended, or original if unavailable.
    """
    if not SANDBOX_ENABLED or SANDBOX_BACKEND == "none":
        return cmd

    firejail = _find_firejail()
    if firejail is None:
        return cmd

    # Resolve working directory to absolute path
    abs_working_dir = str(Path(working_dir).resolve())

    profile_args = _generate_firejail_profile(abs_working_dir)

    # Override network setting if specified
    if allow_network is not None:
        profile_args = [a for a in profile_args if not a.startswith("--net=") and a != "--netfilter"]
        if not allow_network:
            profile_args.append("--net=none")

    sandboxed_cmd = [firejail] + profile_args + ["--"] + cmd

    logger.debug(
        f"[SandboxGuard] Wrapped command with Firejail "
        f"(working_dir={abs_working_dir}, network={'allowed' if allow_network else 'blocked'})"
    )

    return sandboxed_cmd


def get_sandbox_status() -> dict:
    """Return status information about the sandbox for diagnostics."""
    firejail = _find_firejail()
    version = ""
    if firejail:
        try:
            result = subprocess.run(
                [firejail, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version = result.stdout.strip().split("\n")[0] if result.returncode == 0 else ""
        except Exception:
            pass

    return {
        "enabled": SANDBOX_ENABLED,
        "backend": SANDBOX_BACKEND,
        "firejail_path": firejail,
        "firejail_version": version,
        "available": is_sandbox_available(),
        "network_allowed": SANDBOX_ALLOW_NETWORK,
        "extra_args": SANDBOX_EXTRA_ARGS,
    }
