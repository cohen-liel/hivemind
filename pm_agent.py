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

import html
import json
import logging
import re
from pathlib import Path

import state
from config import SUBPROCESS_SHORT_TIMEOUT
from contracts import (
    AgentRole,
    ArtifactType,
    TaskGraph,
    TaskInput,
    task_graph_schema,
)
from isolated_query import isolated_query  # ← FIX: run PM in isolated event loop
from org_hierarchy import build_org_prompt_section

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PM System Prompt
# ---------------------------------------------------------------------------

# Role descriptions for PM team listing (keyed by role name)
_ROLE_DESCRIPTIONS: dict[str, str] = {
    "frontend_developer": "React/TypeScript, Tailwind, state management, accessibility, UX",
    "backend_developer": "FastAPI, async Python, REST APIs, WebSockets, auth",
    "database_expert": "Schema design, query optimisation, migrations, SQLAlchemy, Postgres",
    "devops": "Docker, CI/CD, deployment, environment config, infrastructure",
    "security_auditor": "CVEs, injection prevention, secrets scanning",
    "test_engineer": "Pytest, TDD, E2E tests, coverage, edge cases",
    "researcher": "Web research, competitive analysis, documentation",
    "reviewer": "Code review, architecture critique, final sign-off",
    "ux_critic": "UX review, usability heuristics, accessibility audit",
    "memory": "Project memory, context persistence, knowledge base",
}


def _build_team_section() -> str:
    """Build the <team> section dynamically from AGENT_REGISTRY."""
    from config import AGENT_REGISTRY

    lines = ["<team>"]
    # Group by layer
    for layer_name, layer_label in [
        ("execution", "Execution (write code)"),
        ("quality", "Quality (read/analyse only)"),
    ]:
        lines.append(f"Layer — {layer_label}:")
        for role, cfg in AGENT_REGISTRY.items():
            if cfg.layer == layer_name and not cfg.legacy:
                desc = _ROLE_DESCRIPTIONS.get(role, cfg.label)
                lines.append(f"  - {role}: {desc}")
        lines.append("")
    # List retired/legacy roles
    legacy_roles = [r for r, c in AGENT_REGISTRY.items() if c.legacy]
    if legacy_roles:
        lines.append(f"RETIRED roles (do NOT use): {', '.join(legacy_roles)}")
    lines.append("</team>")
    return "\n".join(lines)


PM_SYSTEM_PROMPT = (
    "<role>\n"
    "You are the Project Manager (PM) of a world-class software engineering team.\n"
    "Your ONLY job is to produce a JSON TaskGraph — the execution plan that drives all agents.\n"
    "You do NOT read code, do NOT write code, do NOT commit. You ONLY plan.\n"
    "</role>\n\n" + _build_team_section() + "\n" + build_org_prompt_section() + "\n\n"
    # Team listing — dynamically generated from AGENT_REGISTRY
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
    "<critical_rule>\n"
    "NEVER create a single-task plan. Every request MUST be decomposed into MULTIPLE tasks\n"
    "assigned to DIFFERENT specialist agents. You are managing a TEAM, not a single developer.\n"
    "Minimum 3 tasks for simple requests, 5+ for complex requests.\n"
    "If the user asks for broad improvements (e.g., 'make this better', 'improve quality',\n"
    "'be the best version'), you MUST create tasks for ALL relevant specialists:\n"
    "  - backend_developer for Python/API improvements\n"
    "  - frontend_developer for React/TypeScript/UI improvements\n"
    "  - test_engineer for writing tests\n"
    "  - security_auditor for security review\n"
    "  - reviewer for final code review\n"
    "These specialists work IN PARALLEL when they don't share files.\n"
    "</critical_rule>\n\n"
    "<instructions>\n"
    "Think step-by-step before producing JSON:\n"
    "1. VISION — One sentence: 'We will [outcome] by [method].'\n"
    "2. EPICS — 3-7 high-level epics (what, not how)\n"
    "3. TASKS — For each epic, 1-4 specific tasks with:\n"
    "   - role: the RIGHT specialist (USE MULTIPLE DIFFERENT ROLES)\n"
    "   - goal: CLEAR, MEASURABLE, >= 15 words, describes WHAT + WHY + HOW\n"
    "     IMPORTANT: Reformulate the user's raw message into a professional,\n"
    "     specific task description. Do NOT copy the user's message verbatim.\n"
    "     Bad: 'Do what the user asked' / Good: 'Refactor the auth module to eliminate\n"
    "     duplicated validation logic across login and register endpoints'\n"
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
    "    {\n"
    '      "id": "task_001",\n'
    '      "role": "database_expert",\n'
    '      "goal": "Design and create the users table with fields for email, hashed_password, created_at, and is_active, including unique constraint on email and proper indexing for login queries",\n'
    '      "constraints": ["Use SQLAlchemy models", "Add Alembic migration"],\n'
    '      "depends_on": [],\n'
    '      "context_from": [],\n'
    '      "files_scope": ["src/models/user.py", "alembic/versions/"],\n'
    '      "acceptance_criteria": ["User model exists with all fields", "Migration runs without errors"],\n'
    '      "required_artifacts": ["schema", "file_manifest"]\n'
    "    },\n"
    "    {\n"
    '      "id": "task_002",\n'
    '      "role": "backend_developer",\n'
    '      "goal": "Implement POST /api/auth/register and POST /api/auth/login endpoints with bcrypt password hashing, JWT token generation with 24h expiry, and proper error handling for duplicate emails and invalid credentials",\n'
    '      "constraints": ["Use the User model from task_001", "Return consistent error format"],\n'
    '      "depends_on": ["task_001"],\n'
    '      "context_from": ["task_001"],\n'
    '      "files_scope": ["src/api/auth.py", "src/utils/jwt_helper.py"],\n'
    '      "acceptance_criteria": ["Register creates user and returns token", "Login validates password and returns token", "Duplicate email returns 409"],\n'
    '      "required_artifacts": ["api_contract", "file_manifest"]\n'
    "    },\n"
    "    {\n"
    '      "id": "task_003",\n'
    '      "role": "test_engineer",\n'
    '      "goal": "Write comprehensive pytest tests for the auth endpoints including happy path registration, duplicate email handling, successful login, wrong password rejection, and token validation",\n'
    '      "constraints": ["Use pytest fixtures for test database", "Mock external services"],\n'
    '      "depends_on": ["task_002"],\n'
    '      "context_from": ["task_001", "task_002"],\n'
    '      "files_scope": ["tests/test_auth.py"],\n'
    '      "acceptance_criteria": ["All tests pass", "Coverage > 80% for auth module"],\n'
    '      "required_artifacts": ["test_report"]\n'
    "    },\n"
    "    {\n"
    '      "id": "task_004",\n'
    '      "role": "security_auditor",\n'
    '      "goal": "Audit the authentication implementation for security vulnerabilities including password storage, token handling, injection attacks, and rate limiting gaps",\n'
    '      "constraints": ["Do not modify code, only report findings"],\n'
    '      "depends_on": ["task_002"],\n'
    '      "context_from": ["task_002"],\n'
    '      "files_scope": [],\n'
    '      "acceptance_criteria": ["Security report with severity ratings", "No CRITICAL issues left unaddressed"],\n'
    '      "required_artifacts": ["security_report"]\n'
    "    },\n"
    "    {\n"
    '      "id": "task_005",\n'
    '      "role": "reviewer",\n'
    '      "goal": "Review all code changes from the authentication feature for code quality, consistency with project patterns, error handling completeness, and adherence to security best practices",\n'
    '      "constraints": ["Do not modify code, only report findings"],\n'
    '      "depends_on": ["task_002", "task_003", "task_004"],\n'
    '      "context_from": ["task_002", "task_003", "task_004"],\n'
    '      "files_scope": [],\n'
    '      "acceptance_criteria": ["Review report with actionable findings", "Clear approve/reject decision"],\n'
    '      "required_artifacts": ["review_report"]\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "```\n"
    "Notice how: task_003 and task_004 can run in PARALLEL (no shared files_scope), \n"
    "task_002 has context_from: ['task_001'] so it receives the DB schema, \n"
    "and task_005 (reviewer) waits for ALL tasks and gets ALL context.\n"
    "</example>\n\n"
    "<output_format>\n"
    "Before producing JSON, think in <self_review> tags:\n"
    "<self_review>\n"
    "1. Do I have AT LEAST 3 tasks? (If not, I need to decompose further!)\n"
    "2. Am I using MULTIPLE DIFFERENT agent roles? (If all tasks go to one role, I'm not using the team!)\n"
    "3. Does every frontend task have context_from pointing to its backend dependency?\n"
    "4. Do all code-writing tasks have required_artifacts: ['file_manifest']?\n"
    "5. Are there tasks that could run in parallel (no shared files_scope)?\n"
    "6. Does the reviewer task depend on ALL code tasks?\n"
    "7. Did I reformulate the user's message into clear, professional task goals?\n"
    "</self_review>\n\n"
    "Then OUTPUT ONLY THE JSON. No markdown, no explanation. Start with { and end with }.\n\n"
    "JSON Schema:\n"
    "```json\n" + json.dumps(task_graph_schema(), indent=2) + "\n```\n"
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
        memory_snapshot: JSON string of MemorySnapshot (structured memory)

    Raises ValueError if the graph cannot be parsed after max_retries.
    """
    sdk = state.sdk_client
    if sdk is None:
        raise RuntimeError("SDK client not initialized. Call state.initialize() first.")

    prompt = _build_pm_prompt(user_message, project_id, manifest, file_tree, memory_snapshot)

    from config import get_agent_config

    _pm_cfg = get_agent_config("pm")

    last_error: str = ""
    for attempt in range(max_retries + 1):
        if attempt > 0:
            logger.warning(f"[PM] Retry {attempt}/{max_retries} after parse error: {last_error}")
            prompt = _build_retry_prompt(prompt, last_error)

        logger.info(
            f"[PM] Attempt {attempt + 1}/{max_retries + 1}: calling SDK via isolated_query (max_turns={_pm_cfg.turns}, budget=${_pm_cfg.budget})"
        )
        # PM runs via isolated_query so anyio's cleanup is contained in its own
        # thread/event-loop and never injects CancelledError into the main loop.
        response = await isolated_query(
            sdk,
            prompt=prompt,
            system_prompt=PM_SYSTEM_PROMPT,
            cwd=str(Path.cwd()),
            max_turns=_pm_cfg.turns,
            max_budget_usd=_pm_cfg.budget,
            allowed_tools=[],
            max_retries=1,
            per_message_timeout=_pm_cfg.timeout,
        )

        if response.is_error:
            last_error = response.error_message
            continue

        graph, error = _parse_task_graph(response.text, project_id, user_message)
        if graph is not None:
            # Post-process: ensure artifact wiring is correct
            graph = _enforce_artifact_requirements(graph)

            roles_used = list({t.role.value for t in graph.tasks})
            logger.info(
                f"[PM] ✅ Task graph: {len(graph.tasks)} tasks × {len(roles_used)} roles "
                f"| vision='{graph.vision[:80]}'"
            )
            for t in graph.tasks:
                deps = t.depends_on or []
                logger.info(
                    f"[PM]   {t.id} ({t.role.value}): {t.goal[:70]}{'...' if len(t.goal) > 70 else ''} deps={deps}"
                )
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
        f"<project_id>{html.escape(project_id)}</project_id>",
        f"<user_request>{html.escape(user_message)}</user_request>",
    ]
    if memory_snapshot:
        parts.append(f"<project_memory>\n{memory_snapshot[:4000]}\n</project_memory>")
    elif manifest:
        parts.append(f"<project_manifest>\n{manifest[:3000]}\n</project_manifest>")
    if file_tree:
        parts.append(f"<file_tree>\n{file_tree[:3000]}\n</file_tree>")

    # Add recent git activity so PM knows what was recently changed
    try:
        import subprocess as _sp

        _git = _sp.run(
            ["git", "log", "--oneline", "-10"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_SHORT_TIMEOUT,
            cwd=str(Path.cwd()),
        )
        if _git.returncode == 0 and _git.stdout.strip():
            parts.append(f"<recent_git_log>\n{_git.stdout.strip()}\n</recent_git_log>")
    except Exception:
        logger.debug("git log collection failed (non-critical)", exc_info=True)

    parts.append("\nCreate the TaskGraph JSON now. Output ONLY the JSON object, nothing else.")
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
            # Guard against oversized JSON that could exhaust memory
            if len(candidate) > 500_000:
                logger.warning("[PM] Skipping oversized JSON candidate (%d bytes)", len(candidate))
                continue
            data = json.loads(candidate)
            data.setdefault("project_id", project_id)
            data.setdefault("user_message", user_message)

            graph = TaskGraph(**data)

            # Validate goal quality — reject vague one-liner goals that won't guide agents
            for t in graph.tasks:
                if len(t.goal.split()) < 8:
                    raise ValueError(
                        f"Task {t.id} goal too vague ({len(t.goal.split())} words): '{t.goal}'. "
                        "Each goal must be a detailed, actionable instruction (≥8 words)."
                    )

            # Validate DAG
            errors = graph.validate_dag()
            if errors:
                return None, f"DAG validation errors: {'; '.join(errors)}"

            if not graph.tasks:
                return None, "TaskGraph has no tasks"

            # Reject single-task plans for complex requests.
            # Simple requests (short messages, single-file fixes) can be 1 task.
            # Complex requests (long messages, broad scope) must be decomposed.
            if len(graph.tasks) < 2 and len(user_message.split()) > 15:
                return None, (
                    "TaskGraph has only 1 task for a complex request. You MUST decompose "
                    "into MULTIPLE tasks assigned to DIFFERENT specialist agents. "
                    "Minimum 3 tasks for simple requests, 5+ for complex requests."
                )

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
                f"[PM] Auto-added required_artifacts to {task.id}: {[a.value for a in defaults]}"
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
# Fallback: intelligent multi-task graph when PM fails
# ---------------------------------------------------------------------------


def _fallback_task_defs(user_message: str) -> list[dict]:
    """Build a simple developer + reviewer task graph as fallback.

    This is only used when the PM agent (LLM) fails to produce a task
    graph.  We intentionally keep it simple — the LLM is responsible
    for understanding user intent and picking the right agents/tasks.
    """
    return [
        {
            "id": "task_001",
            "role": "backend_developer",
            "goal": (
                "Analyze the codebase and complete the following request. "
                "Choose the best approach and make the necessary changes: "
                f"{user_message[:500]}"
            ),
            "constraints": ["Do not break existing functionality"],
            "depends_on": [],
            "context_from": [],
            "files_scope": [],
            "acceptance_criteria": ["Request completed as described"],
            "required_artifacts": ["file_manifest"],
        },
        {
            "id": "task_002",
            "role": "reviewer",
            "goal": "Review all changes for code quality, correctness, and completeness",
            "constraints": ["Do not modify code"],
            "depends_on": ["task_001"],
            "context_from": ["task_001"],
            "files_scope": [],
            "acceptance_criteria": ["Review report with findings"],
            "required_artifacts": ["review_report"],
        },
    ]


def fallback_single_task_graph(
    user_message: str,
    project_id: str,
    role: AgentRole = AgentRole.BACKEND_DEVELOPER,
) -> TaskGraph:
    """
    Intelligent fallback: create a proper multi-task graph when PM agent fails.
    Instead of dumping the raw user message to a single agent, we analyze the
    request and create structured tasks with clear goals.
    """
    logger.warning("[PM] Using fallback task graph (PM failed, creating structured plan locally)")

    # Classify the request and build tasks
    task_defs = _fallback_task_defs(user_message)

    # Convert to TaskInput objects
    tasks = []
    for td in task_defs:
        tasks.append(
            TaskInput(
                id=td["id"],
                role=AgentRole(td["role"]),
                goal=td["goal"],
                constraints=td.get("constraints", []),
                depends_on=td.get("depends_on", []),
                context_from=td.get("context_from", []),
                files_scope=td.get("files_scope", []),
                acceptance_criteria=td.get("acceptance_criteria", []),
                required_artifacts=[
                    ArtifactType(a) for a in td.get("required_artifacts", ["file_manifest"])
                ],
            )
        )

    # Build vision from the request
    vision = f"Complete the user's request: {user_message[:150]}"
    epics = list({t.role.value for t in tasks})

    logger.info(
        f"[PM] Fallback graph created: {len(tasks)} tasks, roles={[t.role.value for t in tasks]}"
    )

    return TaskGraph(
        project_id=project_id,
        user_message=user_message,
        vision=vision,
        epic_breakdown=epics,
        tasks=tasks,
    )


# ---------------------------------------------------------------------------
# Graph Quality Validator — Evaluator-Optimizer pattern (Critic)
# ---------------------------------------------------------------------------


def validate_graph_quality(graph: TaskGraph) -> list[str]:
    """Validate the quality of a generated TaskGraph and return improvement hints.

    This implements the Critic half of the Evaluator-Optimizer pattern:
    after the PM Agent creates a plan, this function checks it for common
    quality issues before sending it to the DAG executor.

    Issues found here are logged as warnings but don't block execution —
    they're advisory only. The orchestrator can log them for debugging.

    Args:
        graph: The TaskGraph to validate.

    Returns:
        A list of quality issue descriptions (empty = high quality).
    """
    issues: list[str] = []

    # 1. Graph completeness
    if not graph.tasks:
        issues.append("CRITICAL: Graph has no tasks — nothing to execute")
        return issues  # No point checking further

    if not graph.vision:
        issues.append("WARNING: Missing vision — agents won't understand the big picture")

    if not graph.epic_breakdown:
        issues.append("INFO: No epics defined — consider adding high-level groupings")

    # 2. Task quality checks
    for task in graph.tasks:
        # Every task should have acceptance criteria
        if not task.acceptance_criteria:
            issues.append(
                f"WARNING: Task {task.id} ({task.role.value}) has no acceptance_criteria — "
                f"agents won't know what 'done' looks like"
            )

        # Every task should have at least one constraint
        if not task.constraints:
            issues.append(
                f"INFO: Task {task.id} ({task.role.value}) has no constraints — "
                f"consider adding quality/performance/security constraints"
            )

        # Goals should be specific (> 40 chars)
        if len(task.goal) < 40:
            issues.append(
                f"WARNING: Task {task.id} has a very short goal ('{task.goal}') — "
                f"more specificity leads to better agent output"
            )

        # Writer tasks should specify files_scope
        writer_roles = {
            "frontend_developer",
            "backend_developer",
            "database_expert",
            "devops",
            "typescript_architect",
            "python_backend",
            "developer",
        }
        if task.role.value in writer_roles and not task.files_scope:
            issues.append(
                f"INFO: Task {task.id} ({task.role.value}) is a writer but has no files_scope — "
                f"parallel execution conflicts may occur"
            )

    # 3. DAG structure checks
    task_ids = {t.id for t in graph.tasks}
    roles_used = {t.role.value for t in graph.tasks}

    # Check for missing dependencies (frontend without backend, tests without code)
    has_frontend = any(r in roles_used for r in ["frontend_developer", "typescript_architect"])
    has_backend = any(r in roles_used for r in ["backend_developer", "python_backend"])
    has_tests = any(r in roles_used for r in ["test_engineer", "tester"])
    has_reviewer = "reviewer" in roles_used

    if has_frontend and not has_backend and len(graph.tasks) > 2:
        issues.append("INFO: Frontend tasks present but no backend — verify this is intentional")

    if has_tests and not (has_frontend or has_backend) and len(graph.tasks) > 2:
        issues.append(
            "WARNING: Test engineer present but no code-writing agents — what will the tests test?"
        )

    # 4. Context wiring checks
    for task in graph.tasks:
        for ctx_id in task.context_from:
            if ctx_id not in task_ids:
                issues.append(
                    f"ERROR: Task {task.id} references non-existent context_from task '{ctx_id}'"
                )
        for dep_id in task.depends_on:
            if dep_id not in task_ids:
                issues.append(f"ERROR: Task {task.id} depends on non-existent task '{dep_id}'")

    # 5. Completeness: large projects should have a reviewer
    if len(graph.tasks) >= 5 and not has_reviewer:
        issues.append(
            "INFO: Large project (5+ tasks) has no reviewer — "
            "consider adding a code review step for quality assurance"
        )

    # 6. Run the built-in DAG validation too
    dag_errors = graph.validate_dag()
    for err in dag_errors:
        issues.append(f"ERROR (DAG): {err}")

    return issues
