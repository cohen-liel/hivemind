"""Smart Model Router — RouteLLM-powered cost/quality optimization.

Outsources model routing decisions to RouteLLM (or a lightweight built-in
classifier) to dynamically choose between expensive (Opus/Sonnet) and cheap
(Haiku) models based on task complexity.

This module integrates with the existing ``agent_runtime.py`` RuntimeRegistry
to add an intelligent routing layer **before** the runtime is selected.
Since HiveMind uses Claude Code CLI (subprocess-based), the router works by
setting the ``--model`` flag on the CLI command rather than switching API
endpoints.

Architecture
------------
    DAG Executor
      └─ calls ``route_model_for_task(role, prompt)``
           └─ RouteLLM classifies complexity
           └─ returns model name (e.g., "claude-sonnet-4-20250514" or "claude-haiku-3-5-20241022")
      └─ passes model to ``isolated_query(model=...)``

Configuration via environment:
    SMART_ROUTER_ENABLED    — Enable/disable (default: true)
    SMART_ROUTER_BACKEND    — "routellm" or "builtin" (default: "builtin")
    SMART_ROUTER_THRESHOLD  — Complexity threshold 0.0-1.0 (default: 0.5)
    STRONG_MODEL            — Model for complex tasks (default: claude-sonnet-4-20250514)
    WEAK_MODEL              — Model for simple tasks (default: claude-haiku-3-5-20241022)
    ROUTER_ALWAYS_STRONG    — Comma-separated roles that always use strong model

References:
    - RouteLLM: https://github.com/lm-sys/RouteLLM
    - Paper: "RouteLLM: Learning to Route LLMs with Preference Data"
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

SMART_ROUTER_ENABLED = os.getenv("SMART_ROUTER_ENABLED", "true").lower() in ("true", "1", "yes")
SMART_ROUTER_BACKEND = os.getenv("SMART_ROUTER_BACKEND", "builtin").lower()
SMART_ROUTER_THRESHOLD = float(os.getenv("SMART_ROUTER_THRESHOLD", "0.5"))
STRONG_MODEL = os.getenv("STRONG_MODEL", "claude-sonnet-4-20250514")
WEAK_MODEL = os.getenv("WEAK_MODEL", "claude-haiku-3-5-20241022")

# Roles that always use the strong model (comma-separated)
_ALWAYS_STRONG_RAW = os.getenv("ROUTER_ALWAYS_STRONG", "architect,tech_lead,pm")
ALWAYS_STRONG_ROLES: set[str] = {
    r.strip().lower() for r in _ALWAYS_STRONG_RAW.split(",") if r.strip()
}

# ── RouteLLM integration ────────────────────────────────────────────────

_routellm_router: Any = None
_routellm_lock = threading.Lock()
_routellm_failed = False


def _get_routellm_router():
    """Lazy-load the RouteLLM router (singleton, thread-safe)."""
    global _routellm_router, _routellm_failed

    if SMART_ROUTER_BACKEND != "routellm":
        return None

    if _routellm_router is not None:
        return _routellm_router

    if _routellm_failed:
        return None

    with _routellm_lock:
        if _routellm_router is not None:
            return _routellm_router
        if _routellm_failed:
            return None

        try:
            from routellm.controller import Controller

            _routellm_router = Controller(
                routers=["mf"],  # Matrix factorization router (fastest)
                strong_model=STRONG_MODEL,
                weak_model=WEAK_MODEL,
            )
            logger.info(
                "[SmartRouter] RouteLLM loaded successfully "
                f"(strong={STRONG_MODEL}, weak={WEAK_MODEL})"
            )
            return _routellm_router

        except ImportError:
            logger.info(
                "[SmartRouter] RouteLLM not installed — using built-in classifier. "
                "Install with: pip install routellm"
            )
            _routellm_failed = True
            return None

        except Exception as e:
            logger.warning(
                f"[SmartRouter] Failed to load RouteLLM: {e} — using built-in classifier"
            )
            _routellm_failed = True
            return None


# ── Built-in complexity classifier ──────────────────────────────────────

# Patterns that indicate high complexity (need strong model)
_HIGH_COMPLEXITY_PATTERNS = [
    r"architect",
    r"design.*system",
    r"refactor",
    r"debug.*complex",
    r"security.*audit",
    r"performance.*optim",
    r"migration",
    r"integrate.*api",
    r"database.*schema",
    r"deploy",
    r"ci/cd",
    r"test.*strategy",
    r"review.*code",
    r"fix.*critical",
    r"implement.*feature",
    r"create.*from.*scratch",
]

# Patterns that indicate low complexity (can use weak model)
_LOW_COMPLEXITY_PATTERNS = [
    r"format",
    r"lint",
    r"rename",
    r"add.*comment",
    r"update.*readme",
    r"fix.*typo",
    r"simple.*change",
    r"bump.*version",
    r"add.*import",
    r"remove.*unused",
    r"update.*dependency",
    r"copy.*file",
    r"move.*file",
]


def _builtin_classify(prompt: str, role: str) -> float:
    """Built-in complexity classifier (0.0 = simple, 1.0 = complex).

    Uses pattern matching and heuristics as a lightweight alternative
    to RouteLLM when it's not installed.
    """
    score = 0.5  # Default: medium complexity

    prompt_lower = prompt.lower()

    # Check high complexity patterns
    high_matches = sum(1 for p in _HIGH_COMPLEXITY_PATTERNS if re.search(p, prompt_lower))
    low_matches = sum(1 for p in _LOW_COMPLEXITY_PATTERNS if re.search(p, prompt_lower))

    # Adjust score based on pattern matches
    score += high_matches * 0.1
    score -= low_matches * 0.1

    # Prompt length heuristic: longer prompts tend to be more complex
    word_count = len(prompt.split())
    if word_count > 500:
        score += 0.15
    elif word_count > 200:
        score += 0.05
    elif word_count < 50:
        score -= 0.1

    # Role-based adjustment
    complex_roles = {"architect", "tech_lead", "security", "devops", "pm"}
    simple_roles = {"formatter", "linter", "documenter"}

    if role.lower() in complex_roles:
        score += 0.2
    elif role.lower() in simple_roles:
        score -= 0.2

    # Clamp to [0, 1]
    return max(0.0, min(1.0, score))


# ── Public API ───────────────────────────────────────────────────────────


def route_model_for_task(
    role: str,
    prompt: str,
    *,
    force_strong: bool = False,
) -> str:
    """Determine which model to use for a given task.

    This is the main entry point called by the DAG executor or orchestrator
    before dispatching a task to an agent.

    Args:
        role: The agent role (e.g., "backend", "frontend", "architect").
        prompt: The task prompt being sent to the agent.
        force_strong: Override to always use the strong model.

    Returns:
        Model identifier string (e.g., "claude-sonnet-4-20250514").
    """
    if not SMART_ROUTER_ENABLED:
        return STRONG_MODEL

    # Always use strong model for critical roles
    if force_strong or role.lower() in ALWAYS_STRONG_ROLES:
        logger.debug(f"[SmartRouter] Role '{role}' → strong model (always-strong rule)")
        return STRONG_MODEL

    # Try RouteLLM first
    router = _get_routellm_router()
    if router is not None:
        try:
            # RouteLLM returns the model name directly
            result = router.route(
                prompt=prompt,
                threshold=SMART_ROUTER_THRESHOLD,
            )
            model = result if isinstance(result, str) else STRONG_MODEL
            logger.info(f"[SmartRouter] RouteLLM routed '{role}' → {model}")
            return model

        except Exception as e:
            logger.warning(f"[SmartRouter] RouteLLM routing failed: {e} — using built-in")

    # Fall back to built-in classifier
    complexity = _builtin_classify(prompt, role)
    model = STRONG_MODEL if complexity >= SMART_ROUTER_THRESHOLD else WEAK_MODEL

    logger.info(
        f"[SmartRouter] Built-in routed '{role}' → {model} "
        f"(complexity={complexity:.2f}, threshold={SMART_ROUTER_THRESHOLD:.2f})"
    )
    return model


def get_router_status() -> dict:
    """Return status information about the router for diagnostics."""
    return {
        "enabled": SMART_ROUTER_ENABLED,
        "backend": SMART_ROUTER_BACKEND,
        "routellm_loaded": _routellm_router is not None,
        "routellm_failed": _routellm_failed,
        "active_backend": (
            "routellm" if _routellm_router is not None
            else "builtin"
        ),
        "strong_model": STRONG_MODEL,
        "weak_model": WEAK_MODEL,
        "threshold": SMART_ROUTER_THRESHOLD,
        "always_strong_roles": sorted(ALWAYS_STRONG_ROLES),
    }


def estimate_cost_savings(total_tasks: int, strong_ratio: float) -> dict:
    """Estimate cost savings from smart routing.

    Args:
        total_tasks: Total number of tasks processed.
        strong_ratio: Fraction of tasks routed to strong model (0.0-1.0).

    Returns:
        Dict with estimated costs and savings.
    """
    # Approximate costs per task (based on typical token usage)
    STRONG_COST_PER_TASK = 0.15  # ~$0.15 per Sonnet task
    WEAK_COST_PER_TASK = 0.02   # ~$0.02 per Haiku task

    strong_tasks = int(total_tasks * strong_ratio)
    weak_tasks = total_tasks - strong_tasks

    actual_cost = (strong_tasks * STRONG_COST_PER_TASK) + (weak_tasks * WEAK_COST_PER_TASK)
    all_strong_cost = total_tasks * STRONG_COST_PER_TASK
    savings = all_strong_cost - actual_cost

    return {
        "total_tasks": total_tasks,
        "strong_tasks": strong_tasks,
        "weak_tasks": weak_tasks,
        "estimated_cost": round(actual_cost, 2),
        "all_strong_cost": round(all_strong_cost, 2),
        "estimated_savings": round(savings, 2),
        "savings_percent": round((savings / all_strong_cost * 100) if all_strong_cost > 0 else 0, 1),
    }
