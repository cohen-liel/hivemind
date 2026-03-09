"""
PM Agent — Project Manager that creates the TaskGraph.

v2: Artifact-aware planning with Memory Agent integration.

The PM Agent's ONLY job is to:
1. Read the project's memory snapshot (if it exists)
2. Understand the user's intent
3. Create a clear Vision
4. Break it into Epics
5. Decompose into specific Tasks with:
   - Dependency wiring (depends_on)
   - Context wiring (context_from)
   - Required artifact types per task
   - File scope for conflict detection
   - Acceptance criteria for verification

The PM does NOT read code, does NOT write code, does NOT commit.
It only creates the structured execution plan (TaskGraph).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import state
from contracts import (
    AgentRole,
    ArtifactType,
    TaskGraph,
    TaskInput,
    TaskStatus,
    task_graph_schema,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PM System Prompt
# ---------------------------------------------------------------------------

PM_SYSTEM_PROMPT = """\
You are the Project Manager (PM) of a world-class software engineering team.

YOUR ONLY OUTPUT IS A JSON TaskGraph. No explanations before it, no text after it.
Just the raw JSON object matching the schema below.

## Your Team — 3-Layer Architecture (available agent roles):

### Layer 2: Execution (write code)
- frontend_developer    → React/TypeScript, Tailwind, state management, accessibility, animations, UX
- backend_developer     → FastAPI, async Python, REST APIs, WebSockets, auth, performance
- database_expert       → Schema design, query optimisation, migrations, SQLAlchemy, Postgres
- devops                → Docker, CI/CD, deployment, environment config, infrastructure

### Layer 3: Quality (read / analyse only — never write production code)
- security_auditor      → CVEs, injection prevention, secrets scanning, auth hardening
- test_engineer         → Pytest, TDD, E2E tests, coverage, mocking, edge cases
- researcher            → Web research, competitive analysis, documentation, summarisation
- reviewer              → Code review, architecture critique, diff analysis, final sign-off

### IMPORTANT role mapping:
- UX/frontend work → frontend_developer (NOT ux_critic — that role is retired)
- Backend/API work → backend_developer (NOT python_backend — that role is retired)
- TypeScript patterns → frontend_developer (NOT typescript_architect — that role is retired)
- Do NOT use developer, ux_critic, typescript_architect, python_backend — they are legacy aliases

## Artifact System — CRITICAL for agent coordination:
Each task can specify `required_artifacts` — structured outputs the agent MUST produce.
Downstream agents receive these artifacts as typed context, not free text.

Available artifact types:
- api_contract      → Backend MUST produce this: endpoint definitions for frontend to consume
- schema            → Database MUST produce this: table definitions, TypeScript interfaces
- component_map     → Frontend MUST produce this: component tree with props and API calls
- test_report       → Test engineer MUST produce this: pass/fail results
- security_report   → Security auditor MUST produce this: vulnerability findings
- review_report     → Reviewer MUST produce this: code quality findings
- architecture      → For architecture decisions
- research          → Researcher MUST produce this: findings summary
- deployment        → DevOps MUST produce this: deployment config
- file_manifest     → ALL agents should produce this: list of files created/modified

## Artifact wiring rules:
1. If frontend depends on backend → backend task MUST have required_artifacts: ["api_contract"]
   and frontend task MUST have context_from pointing to the backend task
2. If tests depend on code → code task MUST have required_artifacts: ["file_manifest"]
3. If security audit depends on code → code task MUST have required_artifacts: ["file_manifest"]
4. Database tasks MUST have required_artifacts: ["schema"]

## Your thinking process:
1. VISION — Write one sentence: "We will [outcome] by [method]."
2. EPICS — Break the request into 3-7 high-level epics (what, not how)
3. TASKS — For each epic, create 1-4 specific tasks:
   - Assign to the RIGHT specialist (not generic "developer")
   - Write a CLEAR, MEASURABLE goal (not vague)
   - Add acceptance_criteria so the agent knows when it's DONE
   - Add constraints (e.g. "Do not modify unrelated files")
   - Wire depends_on (e.g. backend task must complete before test task)
   - Wire context_from (e.g. test task needs backend output as context)
   - Add files_scope if you know which files will be touched
   - Add required_artifacts for the artifact types this task MUST produce

## Parallelism rules:
- Tasks with NO shared files_scope CAN run in parallel (no depends_on needed)
- Tasks touching the SAME files MUST be sequential (use depends_on)
- research/review/ux tasks can almost always run in parallel with others
- security_auditor should always come AFTER code is written

## Task ID format: "task_001", "task_002", etc. (zero-padded, sequential)

## CRITICAL:
- Do NOT assign tasks to "developer" (generic) — use the specific specialist
- Each task goal must be >= 15 words and describe the WHAT + WHY
- Maximum 20 tasks per graph
- Always include a reviewer task at the end
- Backend tasks that frontend depends on MUST have required_artifacts: ["api_contract", "file_manifest"]
- Database tasks MUST have required_artifacts: ["schema", "file_manifest"]

## JSON Schema you must follow:
```json
{schema}
```

OUTPUT ONLY THE JSON. No markdown, no explanation. Start with {{ and end with }}.
""".format(schema=json.dumps(task_graph_schema(), indent=2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_task_graph(
    user_message: str,
    project_id: str,
    manifest: str = "",
    file_tree: str = "",
    memory_snapshot: str = "",
    max_retries: int = 2,
) -> TaskGraph:
    """
    Query the PM Agent and return a validated TaskGraph.

    Args:
        user_message: The user's request
        project_id: Project identifier
        manifest: Contents of PROJECT_MANIFEST.md (human-readable)
        file_tree: Current file tree listing
        memory_snapshot: JSON string of MemorySnapshot (structured)
        max_retries: Number of retries on parse failure

    Raises ValueError if the graph cannot be parsed after max_retries.
    """
    sdk = state.sdk_client
    if sdk is None:
        raise RuntimeError("SDK client not initialized. Call state.initialize() first.")

    prompt = _build_pm_prompt(user_message, project_id, manifest, file_tree, memory_snapshot)

    last_error: str = ""
    for attempt in range(max_retries + 1):
        if attempt > 0:
            logger.warning(f"[PM] Retry {attempt}/{max_retries} after parse error: {last_error}")
            prompt = _build_retry_prompt(prompt, last_error)

        response = await sdk.query_with_retry(
            prompt=prompt,
            system_prompt=PM_SYSTEM_PROMPT,
            cwd=str(Path.cwd()),
            max_turns=3,           # PM only thinks, no tool use needed
            max_budget_usd=1.0,    # PM queries are cheap
            allowed_tools=[],      # PM has NO tools — only thinks
        )

        if response.is_error:
            last_error = response.error_message
            continue

        graph, error = _parse_task_graph(response.text, project_id, user_message)
        if graph is not None:
            # Post-process: ensure artifact wiring is correct
            graph = _enforce_artifact_requirements(graph)

            logger.info(
                f"[PM] Created TaskGraph: vision='{graph.vision[:80]}' "
                f"tasks={len(graph.tasks)} cost=${response.cost_usd:.4f}"
            )
            return graph

        last_error = error

    raise ValueError(
        f"PM Agent failed to produce a valid TaskGraph after {max_retries + 1} attempts. "
        f"Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_pm_prompt(
    user_message: str,
    project_id: str,
    manifest: str,
    file_tree: str,
    memory_snapshot: str = "",
) -> str:
    parts = [
        f"## Project ID: {project_id}",
        f"## User Request:\n{user_message}",
    ]
    if memory_snapshot:
        parts.append(
            f"## Project Memory (structured — use this for context):\n"
            f"```json\n{memory_snapshot[:4000]}\n```"
        )
    elif manifest:
        parts.append(f"## Project Manifest (team memory):\n{manifest[:3000]}")
    if file_tree:
        parts.append(f"## Current File Tree:\n{file_tree[:2000]}")
    parts.append(
        "\nCreate the TaskGraph JSON now. "
        "Remember: output ONLY the JSON, nothing else."
    )
    return "\n\n".join(parts)


def _build_retry_prompt(original_prompt: str, error: str) -> str:
    return (
        f"{original_prompt}\n\n"
        f"IMPORTANT: Your previous response had a validation error: {error}\n"
        "Please fix it and output ONLY valid JSON."
    )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _parse_task_graph(
    raw_text: str,
    project_id: str,
    user_message: str,
) -> tuple[TaskGraph | None, str]:
    """
    Try to extract and validate a TaskGraph from the PM's raw response.
    Returns (TaskGraph, "") on success or (None, error_message) on failure.
    """
    candidates: list[str] = []

    # Try fenced JSON block first
    for match in _JSON_FENCE_RE.finditer(raw_text):
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
            data.setdefault("user_message", user_message)

            graph = TaskGraph(**data)

            # Validate DAG
            errors = graph.validate_dag()
            if errors:
                return None, f"DAG validation errors: {'; '.join(errors)}"

            if not graph.tasks:
                return None, "TaskGraph has no tasks"

            return graph, ""

        except Exception as exc:
            logger.debug(f"[PM] Parse attempt failed: {exc}")
            continue

    return None, f"No valid JSON found in PM response (length={len(raw_text)})"


# ---------------------------------------------------------------------------
# Post-processing: enforce artifact requirements
# ---------------------------------------------------------------------------

# Role -> artifact types that should always be required
_ROLE_DEFAULT_ARTIFACTS: dict[AgentRole, list[ArtifactType]] = {
    AgentRole.BACKEND_DEVELOPER: [ArtifactType.API_CONTRACT, ArtifactType.FILE_MANIFEST],
    AgentRole.FRONTEND_DEVELOPER: [ArtifactType.COMPONENT_MAP, ArtifactType.FILE_MANIFEST],
    AgentRole.DATABASE_EXPERT: [ArtifactType.SCHEMA, ArtifactType.FILE_MANIFEST],
    AgentRole.DEVOPS: [ArtifactType.DEPLOYMENT, ArtifactType.FILE_MANIFEST],
    AgentRole.TEST_ENGINEER: [ArtifactType.TEST_REPORT],
    AgentRole.SECURITY_AUDITOR: [ArtifactType.SECURITY_REPORT],
    AgentRole.REVIEWER: [ArtifactType.REVIEW_REPORT],
    AgentRole.RESEARCHER: [ArtifactType.RESEARCH],
}


def _enforce_artifact_requirements(graph: TaskGraph) -> TaskGraph:
    """Ensure every task has appropriate required_artifacts based on its role.

    If the PM forgot to add required_artifacts, we add sensible defaults.
    This guarantees downstream agents always get structured context.
    """
    for task in graph.tasks:
        defaults = _ROLE_DEFAULT_ARTIFACTS.get(task.role, [])
        if not task.required_artifacts and defaults:
            task.required_artifacts = defaults
            logger.debug(
                f"[PM] Auto-added required_artifacts to {task.id}: "
                f"{[a.value for a in defaults]}"
            )

        # Ensure file_manifest is always required for writer roles
        if task.role in (
            AgentRole.BACKEND_DEVELOPER,
            AgentRole.FRONTEND_DEVELOPER,
            AgentRole.DATABASE_EXPERT,
            AgentRole.DEVOPS,
            AgentRole.DEVELOPER,
        ):
            if ArtifactType.FILE_MANIFEST not in task.required_artifacts:
                task.required_artifacts.append(ArtifactType.FILE_MANIFEST)

    return graph


# ---------------------------------------------------------------------------
# Fallback: simple single-task graph when PM fails
# ---------------------------------------------------------------------------

def fallback_single_task_graph(
    user_message: str,
    project_id: str,
    role: AgentRole = AgentRole.BACKEND_DEVELOPER,
) -> TaskGraph:
    """
    Emergency fallback: create a minimal 1-task graph when PM agent fails.
    This keeps the system running rather than crashing.
    """
    logger.warning("[PM] Using fallback single-task graph")
    return TaskGraph(
        project_id=project_id,
        user_message=user_message,
        vision=f"Complete the requested task: {user_message[:100]}",
        epic_breakdown=["Execute the user's request directly"],
        tasks=[
            TaskInput(
                id="task_001",
                role=role,
                goal=user_message[:500],
                acceptance_criteria=["Task completed as requested by user"],
                required_artifacts=[ArtifactType.FILE_MANIFEST],
            )
        ],
    )
