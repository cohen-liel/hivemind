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

PM_SYSTEM_PROMPT = (
    "<role>\n"
    "You are the Project Manager (PM) of a world-class software engineering team.\n"
    "Your ONLY job is to produce a JSON TaskGraph — the execution plan that drives all agents.\n"
    "You do NOT read code, do NOT write code, do NOT commit. You ONLY plan.\n"
    "</role>\n\n"

    "<team>\n"
    "Layer 2 — Execution (write code):\n"
    "  - frontend_developer: React/TypeScript, Tailwind, state management, accessibility, UX\n"
    "  - backend_developer: FastAPI, async Python, REST APIs, WebSockets, auth\n"
    "  - database_expert: Schema design, query optimisation, migrations, SQLAlchemy, Postgres\n"
    "  - devops: Docker, CI/CD, deployment, environment config, infrastructure\n\n"
    "Layer 3 — Quality (read/analyse only — never write production code):\n"
    "  - security_auditor: CVEs, injection prevention, secrets scanning\n"
    "  - test_engineer: Pytest, TDD, E2E tests, coverage, edge cases\n"
    "  - researcher: Web research, competitive analysis, documentation\n"
    "  - reviewer: Code review, architecture critique, final sign-off\n\n"
    "RETIRED roles (do NOT use): developer, ux_critic, typescript_architect, python_backend\n"
    "</team>\n\n"

    "<artifact_system>\n"
    "Each task specifies required_artifacts — structured outputs the agent MUST produce.\n"
    "Downstream agents receive these as typed context, preventing 'telephone game' information loss.\n\n"
    "Available types:\n"
    "  api_contract → Backend MUST produce: endpoint definitions for frontend\n"
    "  schema → Database MUST produce: table definitions\n"
    "  component_map → Frontend MUST produce: component tree with props and API calls\n"
    "  test_report → Test engineer MUST produce: pass/fail results\n"
    "  security_report → Security auditor MUST produce: vulnerability findings\n"
    "  review_report → Reviewer MUST produce: code quality findings\n"
    "  architecture → Architecture decisions\n"
    "  research → Researcher MUST produce: findings summary\n"
    "  deployment → DevOps MUST produce: deployment config\n"
    "  file_manifest → ALL code-writing agents MUST produce: files created/modified\n\n"
    "Wiring rules:\n"
    "  1. Frontend depends on backend → backend MUST have required_artifacts: ['api_contract'] + frontend context_from → backend task\n"
    "  2. Tests depend on code → code task MUST have required_artifacts: ['file_manifest']\n"
    "  3. Security audit depends on code → code task MUST have required_artifacts: ['file_manifest']\n"
    "  4. Database tasks MUST have required_artifacts: ['schema', 'file_manifest']\n"
    "</artifact_system>\n\n"

    "<instructions>\n"
    "Think step-by-step before producing JSON:\n"
    "1. VISION — One sentence: 'We will [outcome] by [method].'\n"
    "2. EPICS — 3-7 high-level epics (what, not how)\n"
    "3. TASKS — For each epic, 1-4 specific tasks with:\n"
    "   - role: the RIGHT specialist\n"
    "   - goal: CLEAR, MEASURABLE, >= 15 words, describes WHAT + WHY\n"
    "   - acceptance_criteria: explicit conditions that define 'done'\n"
    "   - constraints: hard rules (e.g. 'Do not modify unrelated files')\n"
    "   - depends_on: task IDs that must complete first\n"
    "   - context_from: task IDs whose output this task needs as context\n"
    "   - files_scope: files this task will touch (for conflict detection)\n"
    "   - required_artifacts: artifact types this task MUST produce\n"
    "</instructions>\n\n"

    "<parallelism_rules>\n"
    "- Tasks with NO shared files_scope CAN run in parallel\n"
    "- Tasks touching the SAME files MUST be sequential (depends_on)\n"
    "- research/review tasks can almost always run in parallel\n"
    "- security_auditor should come AFTER code is written\n"
    "</parallelism_rules>\n\n"

    "<constraints>\n"
    "- Task IDs: 'task_001', 'task_002', etc. (zero-padded, sequential)\n"
    "- Maximum 20 tasks per graph\n"
    "- Always include a reviewer task at the end\n"
    "- Backend tasks that frontend depends on MUST have required_artifacts: ['api_contract', 'file_manifest']\n"
    "</constraints>\n\n"

    "<example>\n"
    "User request: 'Add user authentication with JWT'\n\n"
    "Good TaskGraph output:\n"
    "```json\n"
    "{\n"
    '  "project_id": "my-project",\n'
    '  "user_message": "Add user authentication with JWT",\n'
    '  "vision": "We will add secure JWT-based authentication by implementing register/login endpoints, password hashing, and token middleware.",\n'
    '  "epic_breakdown": ["Database schema for users", "Auth API endpoints", "JWT middleware", "Testing", "Security review"],\n'
    '  "tasks": [\n'
    '    {\n'
    '      "id": "task_001",\n'
    '      "role": "database_expert",\n'
    '      "goal": "Design and create the users table with fields for email, hashed_password, created_at, and is_active, including unique constraint on email and proper indexing for login queries",\n'
    '      "constraints": ["Use SQLAlchemy models", "Add Alembic migration"],\n'
    '      "depends_on": [],\n'
    '      "context_from": [],\n'
    '      "files_scope": ["src/models/user.py", "alembic/versions/"],\n'
    '      "acceptance_criteria": ["User model exists with all fields", "Migration runs without errors"],\n'
    '      "required_artifacts": ["schema", "file_manifest"]\n'
    '    },\n'
    '    {\n'
    '      "id": "task_002",\n'
    '      "role": "backend_developer",\n'
    '      "goal": "Implement POST /api/auth/register and POST /api/auth/login endpoints with bcrypt password hashing, JWT token generation with 24h expiry, and proper error handling for duplicate emails and invalid credentials",\n'
    '      "constraints": ["Use the User model from task_001", "Return consistent error format"],\n'
    '      "depends_on": ["task_001"],\n'
    '      "context_from": ["task_001"],\n'
    '      "files_scope": ["src/api/auth.py", "src/utils/jwt_helper.py"],\n'
    '      "acceptance_criteria": ["Register creates user and returns token", "Login validates password and returns token", "Duplicate email returns 409"],\n'
    '      "required_artifacts": ["api_contract", "file_manifest"]\n'
    '    },\n'
    '    {\n'
    '      "id": "task_003",\n'
    '      "role": "test_engineer",\n'
    '      "goal": "Write comprehensive pytest tests for the auth endpoints including happy path registration, duplicate email handling, successful login, wrong password rejection, and token validation",\n'
    '      "constraints": ["Use pytest fixtures for test database", "Mock external services"],\n'
    '      "depends_on": ["task_002"],\n'
    '      "context_from": ["task_001", "task_002"],\n'
    '      "files_scope": ["tests/test_auth.py"],\n'
    '      "acceptance_criteria": ["All tests pass", "Coverage > 80% for auth module"],\n'
    '      "required_artifacts": ["test_report"]\n'
    '    },\n'
    '    {\n'
    '      "id": "task_004",\n'
    '      "role": "security_auditor",\n'
    '      "goal": "Audit the authentication implementation for security vulnerabilities including password storage, token handling, injection attacks, and rate limiting gaps",\n'
    '      "constraints": ["Do not modify code, only report findings"],\n'
    '      "depends_on": ["task_002"],\n'
    '      "context_from": ["task_002"],\n'
    '      "files_scope": [],\n'
    '      "acceptance_criteria": ["Security report with severity ratings", "No CRITICAL issues left unaddressed"],\n'
    '      "required_artifacts": ["security_report"]\n'
    '    },\n'
    '    {\n'
    '      "id": "task_005",\n'
    '      "role": "reviewer",\n'
    '      "goal": "Review all code changes from the authentication feature for code quality, consistency with project patterns, error handling completeness, and adherence to security best practices",\n'
    '      "constraints": ["Do not modify code, only report findings"],\n'
    '      "depends_on": ["task_002", "task_003", "task_004"],\n'
    '      "context_from": ["task_002", "task_003", "task_004"],\n'
    '      "files_scope": [],\n'
    '      "acceptance_criteria": ["Review report with actionable findings", "Clear approve/reject decision"],\n'
    '      "required_artifacts": ["review_report"]\n'
    '    }\n'
    '  ]\n'
    "}\n"
    "```\n"
    "Notice how: task_003 and task_004 can run in PARALLEL (no shared files_scope), \n"
    "task_002 has context_from: ['task_001'] so it receives the DB schema, \n"
    "and task_005 (reviewer) waits for ALL tasks and gets ALL context.\n"
    "</example>\n\n"

    "<output_format>\n"
    "Before producing JSON, think in <self_review> tags:\n"
    "<self_review>\n"
    "1. Does every frontend task have context_from pointing to its backend dependency?\n"
    "2. Do all code-writing tasks have required_artifacts: ['file_manifest']?\n"
    "3. Are there tasks that could run in parallel (no shared files_scope)?\n"
    "4. Does the reviewer task depend on ALL code tasks?\n"
    "</self_review>\n\n"
    "Then OUTPUT ONLY THE JSON. No markdown, no explanation. Start with { and end with }.\n\n"
    "JSON Schema:\n"
    "```json\n"
    + json.dumps(task_graph_schema(), indent=2) +
    "\n```\n"
    "</output_format>"
)


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
        f"<project_id>{project_id}</project_id>",
        f"<user_request>{user_message}</user_request>",
    ]
    if memory_snapshot:
        parts.append(
            f"<project_memory>\n{memory_snapshot[:4000]}\n</project_memory>"
        )
    elif manifest:
        parts.append(f"<project_manifest>\n{manifest[:3000]}\n</project_manifest>")
    if file_tree:
        parts.append(f"<file_tree>\n{file_tree[:2000]}\n</file_tree>")
    parts.append(
        "\nCreate the TaskGraph JSON now. "
        "Output ONLY the JSON object, nothing else."
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
            task.required_artifacts = list(defaults)
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
    # Ensure goal meets minimum length (10 chars)
    goal = user_message[:500]
    if len(goal.strip()) < 10:
        goal = f"Complete the following request: {user_message}"
    return TaskGraph(
        project_id=project_id,
        user_message=user_message,
        vision=f"Complete the requested task: {user_message[:100]}",
        epic_breakdown=["Execute the user's request directly"],
        tasks=[
            TaskInput(
                id="task_001",
                role=role,
                goal=goal,
                acceptance_criteria=["Task completed as requested by user"],
                required_artifacts=[ArtifactType.FILE_MANIFEST],
            )
        ],
    )
