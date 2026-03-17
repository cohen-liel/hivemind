"""
Architect Agent — Pre-planning architecture review for complex tasks.

The Architect Agent runs BEFORE the PM Agent for EPIC/LARGE tasks and:
1. Analyses the existing codebase structure
2. Identifies architectural constraints and patterns
3. Produces an ArchitectureBrief that guides the PM's planning
4. Flags potential risks (e.g., circular dependencies, scaling bottlenecks)

The Architect does NOT write code. It only reads, analyses, and produces
a structured brief for the PM to consume.

Suggested in code review: "Add an Architect Agent that runs BEFORE the PM
to analyse the existing codebase and produce an architecture brief."
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Architecture Brief Schema
# ---------------------------------------------------------------------------


class ArchitectureBrief(BaseModel):
    """Structured output from the Architect Agent."""

    project_id: str = ""
    codebase_summary: str = Field(
        default="",
        description="High-level summary of the existing codebase structure",
    )
    tech_stack: dict[str, str] = Field(
        default_factory=dict,
        description="Detected technology stack, e.g. {'frontend': 'React+TS', 'backend': 'FastAPI'}",
    )
    architecture_patterns: list[str] = Field(
        default_factory=list,
        description="Detected patterns (e.g., 'MVC', 'Event-driven', 'Monolith')",
    )
    key_files: dict[str, str] = Field(
        default_factory=dict,
        description="Critical files and their purpose, e.g. {'src/api/auth.py': 'JWT auth'}",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Hard constraints the PM must respect (e.g., 'Do not modify shared DB schema')",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="Potential risks (e.g., 'Circular dependency between auth and user modules')",
    )
    recommended_approach: str = Field(
        default="",
        description="Suggested implementation approach for the PM to follow",
    )
    parallelism_hints: list[str] = Field(
        default_factory=list,
        description="Hints about what can safely run in parallel vs. must be sequential",
    )


# ---------------------------------------------------------------------------
# Architect System Prompt
# ---------------------------------------------------------------------------

ARCHITECT_SYSTEM_PROMPT = (
    "<role>\n"
    "You are the Architect Agent — a senior software architect who reviews the codebase\n"
    "BEFORE the PM creates the execution plan.\n"
    "Your job is to understand the existing architecture and produce a structured brief\n"
    "that guides the PM's planning decisions.\n"
    "You do NOT write code. You only read, analyse, and advise.\n"
    "</role>\n\n"
    "<input>\n"
    "You receive:\n"
    "  - The user's task description\n"
    "  - The project directory path\n"
    "  - The existing memory snapshot (if available)\n"
    "</input>\n\n"
    "<instructions>\n"
    "1. Scan the project structure (ls, find, read key files)\n"
    "2. Identify the tech stack, architecture patterns, and key files\n"
    "3. Assess risks: circular dependencies, tight coupling, missing tests\n"
    "4. Determine what can be parallelised safely\n"
    "5. Produce a JSON ArchitectureBrief\n"
    "</instructions>\n\n"
    "<output_schema>\n"
    "Produce a JSON object with these fields:\n"
    "  - codebase_summary: 3-5 sentence overview\n"
    "  - tech_stack: {layer: technology} mapping\n"
    "  - architecture_patterns: list of detected patterns\n"
    "  - key_files: {path: purpose} for critical files\n"
    "  - constraints: hard rules the PM must follow\n"
    "  - risks: potential issues to watch for\n"
    "  - recommended_approach: suggested implementation strategy\n"
    "  - parallelism_hints: what can/cannot run in parallel\n"
    "</output_schema>\n\n"
    "<output_format>\n"
    "OUTPUT ONLY THE JSON. No markdown, no explanation. Start with { and end with }.\n"
    "</output_format>"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_architect_review(
    project_dir: str,
    project_id: str,
    user_task: str,
    memory_snapshot: dict[str, Any] | None = None,
) -> ArchitectureBrief:
    """Run the Architect Agent to produce an architecture brief.

    Args:
        project_dir: Project working directory
        project_id: Project identifier
        user_task: The user's task description
        memory_snapshot: Existing memory snapshot (optional)

    Returns:
        ArchitectureBrief with analysis results
    """
    import state

    sdk = state.sdk_client
    if sdk is None:
        logger.warning("[Architect] SDK not available, returning empty brief")
        return ArchitectureBrief(project_id=project_id)

    prompt = _build_architect_prompt(project_id, project_dir, user_task, memory_snapshot)

    try:
        response = await sdk.query_with_retry(
            prompt=prompt,
            system_prompt=ARCHITECT_SYSTEM_PROMPT,
            cwd=project_dir,
            max_turns=5,
            max_budget_usd=1.0,
            permission_mode="bypassPermissions",
            allowed_tools=[
                "Read",
                "Glob",
                "Grep",
                "LS",
                "Bash(find *)",
                "Bash(cat *)",
                "Bash(head *)",
                "Bash(wc *)",
            ],
            agent_role="architect",
        )

        if response.is_error:
            logger.warning(
                f"[Architect] LLM error: {response.error_message}. Returning empty brief."
            )
            return ArchitectureBrief(project_id=project_id)

        brief = _parse_architect_response(response.text, project_id)
        logger.info(
            f"[Architect] Brief produced: {len(brief.key_files)} key files, "
            f"{len(brief.risks)} risks, {len(brief.constraints)} constraints"
        )
        return brief

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            f"[Architect] Review failed: {type(exc).__name__}: {exc}. Returning empty brief.",
            exc_info=True,
        )
        return ArchitectureBrief(project_id=project_id)


def should_run_architect(task: str, has_memory: bool) -> bool:
    """Decide whether the Architect Agent should run before the PM.

    Returns True for EPIC tasks (always) or LARGE tasks when there's no
    existing memory (first time working on a project).
    """
    from orch_watchdog import estimate_task_complexity

    complexity = estimate_task_complexity(task)

    # Always run for EPIC tasks
    if complexity == "EPIC":
        return True

    # Run for LARGE tasks when there's no memory (new project)
    if complexity == "LARGE" and not has_memory:
        return True

    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_architect_prompt(
    project_id: str,
    project_dir: str,
    user_task: str,
    memory_snapshot: dict[str, Any] | None,
) -> str:
    """Build the prompt for the Architect Agent."""
    parts = [
        f"<project_id>{project_id}</project_id>",
        f"<project_dir>{project_dir}</project_dir>",
        f"<user_task>{user_task}</user_task>",
    ]

    if memory_snapshot:
        parts.append(
            f"<existing_memory>\n"
            f"Previous knowledge about this project:\n"
            f"{json.dumps(memory_snapshot, indent=2, default=str)[:3000]}\n"
            f"</existing_memory>"
        )

    parts.append(
        "\nAnalyse the codebase and produce the ArchitectureBrief JSON. "
        "Focus on what's relevant to the user's task."
    )
    return "\n".join(parts)


def _parse_architect_response(raw_text: str, project_id: str) -> ArchitectureBrief:
    """Parse the Architect Agent's response into an ArchitectureBrief."""
    import re

    json_re = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

    candidates: list[str] = []
    for match in json_re.finditer(raw_text):
        candidates.append(match.group(1).strip())

    # Try raw JSON
    start = raw_text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(raw_text)):
            if raw_text[i] == "{":
                depth += 1
            elif raw_text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw_text[start : i + 1])
                    break

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            data.setdefault("project_id", project_id)
            return ArchitectureBrief(**data)
        except Exception:
            continue

    # Fallback: return empty brief
    logger.warning("[Architect] Could not parse response, returning empty brief")
    return ArchitectureBrief(project_id=project_id)
