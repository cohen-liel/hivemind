"""
Agent Protocol Layer — Typed Contracts for the Multi-Agent System.

This module defines the shared language between ALL agents.
Every agent receives a TaskInput and must return a TaskOutput.
No free text, no regex parsing — pure structured contracts.

v2: Added Artifact-Based Context, Failure Classification, Remediation Tasks,
    and Memory Agent contracts for production-grade agent management.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING        = "pending"
    RUNNING        = "running"
    COMPLETED      = "completed"
    FAILED         = "failed"
    BLOCKED        = "blocked"
    NEEDS_FOLLOWUP = "needs_followup"
    REMEDIATION    = "remediation"      # Auto-generated fix task


class AgentRole(str, Enum):
    # Layer 1: Brain
    PM                   = "pm"
    ORCHESTRATOR         = "orchestrator"
    MEMORY               = "memory"          # NEW: Memory/Architect agent
    # Layer 2: Execution
    FRONTEND_DEVELOPER   = "frontend_developer"
    BACKEND_DEVELOPER    = "backend_developer"
    DATABASE_EXPERT      = "database_expert"
    DEVOPS               = "devops"
    # Layer 3: Quality
    SECURITY_AUDITOR     = "security_auditor"
    TEST_ENGINEER        = "test_engineer"
    REVIEWER             = "reviewer"
    RESEARCHER           = "researcher"
    # Legacy (backward compat)
    TYPESCRIPT_ARCHITECT = "typescript_architect"
    PYTHON_BACKEND       = "python_backend"
    UX_CRITIC            = "ux_critic"
    DEVELOPER            = "developer"
    TESTER               = "tester"


class ArtifactType(str, Enum):
    """Types of structured artifacts agents can produce."""
    API_CONTRACT     = "api_contract"       # OpenAPI / endpoint definitions
    SCHEMA           = "schema"             # DB schema, TypeScript interfaces
    COMPONENT_MAP    = "component_map"      # React component tree / props
    TEST_REPORT      = "test_report"        # Test results with pass/fail
    SECURITY_REPORT  = "security_report"    # Vulnerability findings
    REVIEW_REPORT    = "review_report"      # Code review findings
    ARCHITECTURE     = "architecture"       # Architecture decisions
    RESEARCH         = "research"           # Research findings
    DEPLOYMENT       = "deployment"         # Deployment config / instructions
    FILE_MANIFEST    = "file_manifest"      # List of files created/modified with descriptions
    CUSTOM           = "custom"             # Freeform structured data


class FailureCategory(str, Enum):
    """Classification of WHY a task failed — drives remediation strategy."""
    DEPENDENCY_MISSING  = "dependency_missing"   # Upstream task didn't produce what we need
    API_MISMATCH        = "api_mismatch"         # Frontend/backend contract mismatch
    TEST_FAILURE        = "test_failure"          # Code written but tests fail
    BUILD_ERROR         = "build_error"           # Compilation / syntax error
    TIMEOUT             = "timeout"               # Agent ran out of turns/budget
    PERMISSION          = "permission"            # File access / auth issue
    UNCLEAR_GOAL        = "unclear_goal"          # Task goal was ambiguous
    MISSING_CONTEXT     = "missing_context"      # File/dependency not found
    EXTERNAL            = "external"              # External service / API down
    UNKNOWN             = "unknown"               # Unclassified


# ---------------------------------------------------------------------------
# Artifact Contract — structured knowledge transfer between agents
# ---------------------------------------------------------------------------

class Artifact(BaseModel):
    """A structured piece of knowledge produced by an agent.

    Instead of passing free-text summaries between agents, artifacts carry
    typed, machine-readable data that downstream agents can consume directly.
    """
    type: ArtifactType
    title: str = Field(..., description="Human-readable title, e.g. 'User API Endpoints'")
    file_path: str = Field(default="", description="Path to the artifact file (relative to project root)")
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured data payload — schema depends on artifact type"
    )
    summary: str = Field(default="", description="1-2 sentence human-readable summary")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if len(v.strip()) < 1:
            raise ValueError("Artifact title must not be empty")
        return v.strip()


# ---------------------------------------------------------------------------
# Core Models
# ---------------------------------------------------------------------------

class TaskInput(BaseModel):
    """What an agent receives — the contract going IN."""

    id: str = Field(..., description="Unique task ID, e.g. 'task_001'")
    role: AgentRole = Field(..., description="Which specialist handles this task")
    goal: str = Field(..., description="Clear, measurable objective for the agent")
    constraints: list[str] = Field(default_factory=list, description="Hard rules the agent must follow")
    depends_on: list[str] = Field(default_factory=list, description="Task IDs that must complete before this one")
    context_from: list[str] = Field(default_factory=list, description="Task IDs whose output should be injected as context")
    files_scope: list[str] = Field(default_factory=list, description="Files this task is expected to touch (for conflict detection)")
    acceptance_criteria: list[str] = Field(default_factory=list, description="Explicit conditions that define 'done'")
    # v2: Artifact requirements
    required_artifacts: list[ArtifactType] = Field(
        default_factory=list,
        description="Artifact types this task MUST produce (enforced by DAG executor)"
    )
    input_artifacts: list[str] = Field(
        default_factory=list,
        description="Artifact file paths from upstream tasks to read before starting"
    )
    # v2: Remediation metadata
    is_remediation: bool = Field(default=False, description="True if this task was auto-generated to fix a failure")
    original_task_id: str = Field(default="", description="If remediation, the task that failed")
    failure_context: str = Field(default="", description="If remediation, description of what went wrong")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", v):
            raise ValueError(f"Invalid task id '{v}': use letters, digits, _ or - only")
        return v

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("Task goal must be at least 10 characters")
        return v.strip()


class TaskOutput(BaseModel):
    """What an agent returns — the contract coming OUT."""

    model_config = {"extra": "allow"}  # Allow dynamic attrs like _progress

    task_id: str
    status: TaskStatus
    summary: str = Field(..., description="2-3 sentences describing what was done")
    artifacts: list[str] = Field(default_factory=list, description="Files created or modified")
    issues: list[str] = Field(default_factory=list, description="Problems or concerns found")
    blockers: list[str] = Field(default_factory=list, description="Things preventing completion")
    followups: list[str] = Field(default_factory=list, description="Recommended follow-up tasks")
    cost_usd: float = Field(default=0.0, ge=0.0)
    turns_used: int = Field(default=0, ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Agent's confidence in its output (0-1)")
    # v2: Structured artifacts (max 20 per task to prevent memory issues)
    structured_artifacts: list[Artifact] = Field(
        default_factory=list,
        description="Typed artifacts produced by this task (API contracts, schemas, reports)",
        max_length=20,
    )
    # v2: Failure classification
    failure_category: FailureCategory | None = Field(
        default=None,
        description="If status=failed, WHY it failed (drives auto-remediation)"
    )
    failure_details: str = Field(
        default="",
        description="Detailed explanation of the failure for remediation agent"
    )

    def is_successful(self) -> bool:
        return self.status == TaskStatus.COMPLETED

    def is_terminal(self) -> bool:
        """True if this task cannot be retried meaningfully."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.BLOCKED)

    def get_artifact(self, artifact_type: ArtifactType) -> Artifact | None:
        """Find a specific artifact by type."""
        return next((a for a in self.structured_artifacts if a.type == artifact_type), None)

    def get_all_artifact_paths(self) -> list[str]:
        """Get all file paths from structured artifacts."""
        return [a.file_path for a in self.structured_artifacts if a.file_path]


# ---------------------------------------------------------------------------
# Memory Snapshot — what the Memory Agent produces
# ---------------------------------------------------------------------------

class MemorySnapshot(BaseModel):
    """The Memory Agent's output — a structured summary of project state.

    This gets written to .nexus/PROJECT_MANIFEST.md and is read by the PM
    at the start of every new task to maintain cross-session continuity.
    """
    project_id: str
    architecture_summary: str = Field(default="", description="Current architecture in 3-5 sentences")
    tech_stack: dict[str, str] = Field(
        default_factory=dict,
        description="Technology choices, e.g. {'frontend': 'React+TS', 'backend': 'FastAPI'}"
    )
    key_decisions: list[str] = Field(
        default_factory=list,
        description="Important architectural decisions made (append-only log)"
    )
    known_issues: list[str] = Field(
        default_factory=list,
        description="Unresolved issues or tech debt"
    )
    api_surface: list[dict[str, str]] = Field(
        default_factory=list,
        description="Current API endpoints: [{method, path, description}]"
    )
    db_tables: list[str] = Field(
        default_factory=list,
        description="Current database tables"
    )
    file_map: dict[str, str] = Field(
        default_factory=dict,
        description="Key files and their purpose, e.g. {'src/api/auth.py': 'JWT authentication'}"
    )
    last_updated_by: str = Field(default="", description="Task ID that triggered this update")
    cumulative_cost_usd: float = Field(default=0.0, description="Total cost across all sessions")

    def add_decision(self, decision: str, reason: str = "", by: str = "") -> None:
        """Append a key decision to the log."""
        entry = decision
        if reason:
            entry += f" (reason: {reason})"
        if by:
            entry += f" [by {by}]"
        if entry not in self.key_decisions:
            self.key_decisions.append(entry)

    def add_api_endpoint(self, method: str, path: str, description: str = "") -> None:
        """Register an API endpoint in the surface."""
        endpoint = {"method": method, "path": path, "description": description}
        # Avoid duplicates
        for existing in self.api_surface:
            if existing.get("method") == method and existing.get("path") == path:
                existing["description"] = description  # Update description
                return
        self.api_surface.append(endpoint)

    def add_file(self, path: str, purpose: str) -> None:
        """Register a file and its purpose."""
        self.file_map[path] = purpose

    def add_issue(self, issue: str) -> None:
        """Add a known issue."""
        if issue not in self.known_issues:
            self.known_issues.append(issue)


# ---------------------------------------------------------------------------
# TaskGraph — the full execution plan
# ---------------------------------------------------------------------------

class TaskGraph(BaseModel):
    """The full execution plan produced by the PM Agent."""

    project_id: str
    user_message: str
    vision: str = Field(..., description="One-sentence mission statement for this task")
    epic_breakdown: list[str] = Field(default_factory=list, description="High-level epics (3-7 items)")
    tasks: list[TaskInput] = Field(..., description="All tasks with dependency wiring")

    def get_task(self, task_id: str) -> TaskInput | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def ready_tasks(self, completed: dict[str, TaskOutput] | set[str]) -> list[TaskInput]:
        """Return tasks whose dependencies are all successfully completed.

        `completed` can be either:
        - dict[str, TaskOutput]: checks that each dep is successful
        - set[str]: assumes all listed task IDs are successful
        """
        is_dict = isinstance(completed, dict)
        result = []
        for task in self.tasks:
            if task.id in completed:
                continue
            deps_ok = True
            for dep in task.depends_on:
                if dep not in completed:
                    deps_ok = False
                    break
                if is_dict and not completed[dep].is_successful():
                    deps_ok = False
                    break
            if deps_ok:
                result.append(task)
        return result

    def is_complete(self, completed: dict[str, TaskOutput]) -> bool:
        return all(t.id in completed for t in self.tasks)

    def has_failed(self, completed: dict[str, TaskOutput]) -> bool:
        """True if a blocked/failed task has no downstream path to completion."""
        blocked = {
            t.id for t in self.tasks
            if t.id in completed and completed[t.id].status in (TaskStatus.FAILED, TaskStatus.BLOCKED)
        }
        if not blocked:
            return False
        # Check if any pending task depends on a blocked task
        pending_ids = {t.id for t in self.tasks if t.id not in completed}
        for tid in pending_ids:
            task = self.get_task(tid)
            if task and any(dep in blocked for dep in task.depends_on):
                return True
        return False

    def add_task(self, task: TaskInput) -> None:
        """Dynamically add a task to the graph (used by self-healing DAG)."""
        self.tasks.append(task)

    def validate_dag(self) -> list[str]:
        """Check for cycles, self-deps, duplicate IDs, and missing deps. Returns error list."""
        errors: list[str] = []
        task_ids = {t.id for t in self.tasks}

        # Check for duplicate task IDs
        seen_ids: set[str] = set()
        for task in self.tasks:
            if task.id in seen_ids:
                errors.append(f"Duplicate task ID: '{task.id}'")
            seen_ids.add(task.id)

        for task in self.tasks:
            # Self-dependency check
            if task.id in task.depends_on:
                errors.append(f"Task '{task.id}' depends on itself")
            for dep in task.depends_on:
                if dep not in task_ids:
                    errors.append(f"Task '{task.id}' depends on unknown task '{dep}'")

        # Cycle detection via DFS
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            task = self.get_task(node)
            if task:
                for dep in task.depends_on:
                    if dep not in visited:
                        if has_cycle(dep):
                            return True
                    elif dep in rec_stack:
                        return True
            rec_stack.discard(node)
            return False

        for task in self.tasks:
            if task.id not in visited:
                if has_cycle(task.id):
                    errors.append(f"Cycle detected involving task '{task.id}'")
                    break

        return errors


# ---------------------------------------------------------------------------
# Failure Classification — auto-detect WHY a task failed
# ---------------------------------------------------------------------------

_FAILURE_PATTERNS: list[tuple[FailureCategory, list[str]]] = [
    (FailureCategory.DEPENDENCY_MISSING, [
        "import error", "importerror", "module not found", "modulenotfounderror",
        "no such file", "dependency", "not installed", "missing module",
        "cannot find module", "no module named", "package not found",
        "could not resolve", "unresolved import",
    ]),
    (FailureCategory.API_MISMATCH, [
        "404", "endpoint not found", "api mismatch", "contract",
        "expected response", "schema mismatch",
        "property does not exist", "undefined is not",
        "missing field", "wrong status code",
    ]),
    (FailureCategory.TEST_FAILURE, [
        "test failed", "assertion error", "expected", "assert",
        "pytest", "test_", "FAILED", "failures=",
    ]),
    (FailureCategory.BUILD_ERROR, [
        "syntax error", "syntaxerror", "compilation", "build failed", "tsc",
        "cannot compile", "parse error", "unexpected token",
        "indentation", "unterminated", "invalid syntax",
        "typeerror", "nameerror", "referenceerror",
    ]),
    (FailureCategory.TIMEOUT, [
        "timeout", "timed out", "max turns", "budget exceeded",
        "too many iterations", "deadline",
    ]),
    (FailureCategory.PERMISSION, [
        "permission denied", "permissionerror", "access denied", "forbidden",
        "eacces", "read-only", "not writable",
    ]),
    (FailureCategory.MISSING_CONTEXT, [
        "filenotfounderror", "file not found", "no such file or directory",
        "missing context", "dependency not completed", "upstream task",
        "context_from", "required artifact missing",
    ]),
    (FailureCategory.UNCLEAR_GOAL, [
        "unclear", "ambiguous", "not sure what", "need clarification",
        "insufficient context", "cannot determine",
    ]),
    (FailureCategory.EXTERNAL, [
        "connection refused", "network error", "dns", "502", "503",
        "service unavailable", "rate limit", "api key",
    ]),
]


def classify_failure(output: TaskOutput) -> FailureCategory:
    """Auto-classify a failed task's failure category from its output text.

    Scans the summary, issues, blockers, and failure_details for known patterns.
    Returns the most specific category found, or UNKNOWN.
    """
    if output.failure_category and output.failure_category != FailureCategory.UNKNOWN:
        return output.failure_category  # Agent already classified it

    # Build searchable text from all output fields
    search_text = " ".join([
        output.summary,
        output.failure_details,
        " ".join(output.issues),
        " ".join(output.blockers),
    ]).lower()

    if not search_text.strip():
        return FailureCategory.UNKNOWN

    # Score each category by number of pattern matches
    scores: dict[FailureCategory, int] = {}
    for category, patterns in _FAILURE_PATTERNS:
        score = sum(1 for p in patterns if p in search_text)
        if score > 0:
            scores[category] = score

    if not scores:
        return FailureCategory.UNKNOWN

    return max(scores, key=scores.get)


# ---------------------------------------------------------------------------
# Remediation — auto-generate fix tasks based on failure classification
# ---------------------------------------------------------------------------

_REMEDIATION_STRATEGIES: dict[FailureCategory, dict[str, Any]] = {
    FailureCategory.DEPENDENCY_MISSING: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix dependency issue from task {task_id}: {failure_details}. "
            "Install missing packages, fix import paths, or create missing files. "
            "Verify the fix by running the relevant code."
        ),
        "constraints": ["Only fix the dependency issue — do not refactor unrelated code"],
    },
    FailureCategory.API_MISMATCH: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix API contract mismatch from task {task_id}: {failure_details}. "
            "Read the API contract artifact from upstream tasks, then align the "
            "implementation to match the contract exactly."
        ),
        "constraints": [
            "Read the api_contract artifact before making changes",
            "Do not change the contract — change the implementation",
        ],
    },
    FailureCategory.TEST_FAILURE: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix failing tests from task {task_id}: {failure_details}. "
            "Run the tests first to reproduce, then fix the code (not the tests) "
            "to make them pass. Run tests again to verify."
        ),
        "constraints": [
            "Fix the implementation, not the test assertions",
            "Run pytest -x --tb=short before and after changes",
        ],
    },
    FailureCategory.BUILD_ERROR: {
        "role": AgentRole.FRONTEND_DEVELOPER,
        "goal_template": (
            "Fix build/compilation error from task {task_id}: {failure_details}. "
            "Read the error output carefully, fix the syntax or type errors, "
            "and verify the build passes cleanly."
        ),
        "constraints": ["Run the build command after fixing to verify"],
    },
    FailureCategory.TIMEOUT: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Complete the work that timed out in task {task_id}: {failure_details}. "
            "The previous agent ran out of turns. Pick up where it left off — "
            "check git diff to see what was already done, then complete the remaining work."
        ),
        "constraints": ["Check git status first to understand what was already done"],
    },
    FailureCategory.MISSING_CONTEXT: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix missing file/context issue from task {task_id}: {failure_details}. "
            "A required file or upstream dependency was not found. Check if the file "
            "needs to be created, or if an upstream task failed to produce it."
        ),
        "constraints": [
            "Check if the missing file should exist from an upstream task",
            "Create the file if it's a new requirement, or fix the import path",
        ],
    },
}


def create_remediation_task(
    failed_task: TaskInput,
    failed_output: TaskOutput,
    task_counter: int,
) -> TaskInput | None:
    """Create a remediation task to fix a failure, or None if not remediable.

    The remediation task is wired to depend on the same dependencies as the
    original task, and includes the failure context so the fixing agent
    knows exactly what went wrong.
    """
    category = classify_failure(failed_output)

    strategy = _REMEDIATION_STRATEGIES.get(category)
    if strategy is None:
        return None  # No auto-remediation for this category

    # Determine the right role for the fix — start with the original task's role
    # so remediation stays in the same domain, then override for specific cases
    role = failed_task.role
    # If the original task was frontend and the error is build-related, keep frontend
    if failed_task.role in (AgentRole.FRONTEND_DEVELOPER, AgentRole.TYPESCRIPT_ARCHITECT):
        if category in (FailureCategory.BUILD_ERROR, FailureCategory.TEST_FAILURE):
            role = AgentRole.FRONTEND_DEVELOPER

    failure_details = failed_output.failure_details or failed_output.summary
    goal = strategy["goal_template"].format(
        task_id=failed_task.id,
        failure_details=failure_details[:300],
    )

    # Ensure remediation ID stays within 64-char limit
    prefix = f"fix_{task_counter:03d}_"
    max_suffix_len = 64 - len(prefix)
    suffix = failed_task.id[:max_suffix_len]
    remediation_id = prefix + suffix

    return TaskInput(
        id=remediation_id,
        role=role,
        goal=goal,
        constraints=strategy.get("constraints", []) + failed_task.constraints,
        depends_on=failed_task.depends_on,  # Same deps as original
        context_from=list(dict.fromkeys(failed_task.context_from + [failed_task.id])),  # Deduped
        files_scope=failed_task.files_scope,
        acceptance_criteria=failed_task.acceptance_criteria + [
            f"The issue from {failed_task.id} is resolved",
            "All related tests pass (if applicable)",
        ],
        input_artifacts=[
            p for p in (
                [a.file_path for a in failed_output.structured_artifacts if a.file_path]
            )
        ],
        is_remediation=True,
        original_task_id=failed_task.id,
        failure_context=f"[{category.value}] {failure_details[:500]}",
    )


# ---------------------------------------------------------------------------
# JSON Output Extraction
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_task_output(raw_text: str, task_id: str) -> TaskOutput:
    """
    Parse a TaskOutput from an agent's raw text response.

    Tries in order:
    1. Fenced JSON code block (```json ... ```)
    2. Last JSON object in the text
    3. Fallback: synthesise a FAILED output so the DAG can handle it

    This is the ONLY place where we parse agent text output.
    """
    # Try fenced block first
    for match in _JSON_BLOCK_RE.finditer(raw_text):
        try:
            data = json.loads(match.group(1).strip())
            data.setdefault("task_id", task_id)
            return TaskOutput(**data)
        except Exception:
            continue

    # Try last JSON object
    start = raw_text.rfind("{")
    if start != -1:
        depth = 0
        for i in range(start, len(raw_text)):
            if raw_text[i] == "{":
                depth += 1
            elif raw_text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(raw_text[start : i + 1])
                        data.setdefault("task_id", task_id)
                        return TaskOutput(**data)
                    except Exception:
                        break

    # Fallback — could not parse. Auto-classify from raw text.
    fallback = TaskOutput(
        task_id=task_id,
        status=TaskStatus.FAILED,
        summary="Agent returned unparseable output. The DAG executor will handle retry.",
        issues=["Could not parse TaskOutput JSON from agent response"],
        failure_details=raw_text[-500:] if raw_text else "",
        confidence=0.0,
    )
    fallback.failure_category = classify_failure(fallback)
    return fallback


# ---------------------------------------------------------------------------
# Prompt Serialisation — Artifact-aware context passing
# ---------------------------------------------------------------------------

def _truncate_json_safely(data_str: str, max_len: int) -> str:
    """Truncate a JSON string at a safe boundary (complete line) to avoid broken JSON."""
    if len(data_str) <= max_len:
        return data_str
    truncated = data_str[:max_len]
    # Find last complete JSON line (ends with , or { or [)
    for i in range(len(truncated) - 1, 0, -1):
        if truncated[i] in (',', '{', '[', '\n'):
            truncated = truncated[:i + 1]
            break
    return truncated + '\n    ... (truncated — read the file for full data)'


def task_input_to_prompt(
    task: TaskInput,
    context_outputs: dict[str, TaskOutput],
    graph_vision: str = "",
    graph_epics: list[str] | None = None,
) -> str:
    """Serialise a TaskInput into a structured XML prompt for the agent.

    v3: XML-wrapped context, safe JSON truncation, big-picture injection.
    Every agent sees the mission vision and where their task fits in the plan.
    """
    parts: list[str] = []

    # ── Big Picture: every agent sees the original mission ──
    if graph_vision or graph_epics:
        parts.append("<mission>")
        if graph_vision:
            parts.append(f"  <vision>{graph_vision}</vision>")
        if graph_epics:
            parts.append("  <epics>")
            for i, epic in enumerate(graph_epics, 1):
                parts.append(f"    <epic id='{i}'>{epic}</epic>")
            parts.append("  </epics>")
        parts.append("</mission>\n")

    # ── Task Assignment ──
    parts.append("<task_assignment>")
    parts.append(f"  <task_id>{task.id}</task_id>")
    parts.append(f"  <role>{task.role.value}</role>")
    parts.append(f"  <goal>{task.goal}</goal>")

    if task.is_remediation:
        parts.append(f"  <remediation original_task='{task.original_task_id}'>")
        parts.append(f"    {task.failure_context}")
        parts.append("  </remediation>")

    if task.acceptance_criteria:
        parts.append("  <acceptance_criteria>")
        for c in task.acceptance_criteria:
            parts.append(f"    <criterion>{c}</criterion>")
        parts.append("  </acceptance_criteria>")

    if task.constraints:
        parts.append("  <constraints>")
        for c in task.constraints:
            parts.append(f"    <constraint>{c}</constraint>")
        parts.append("  </constraints>")

    if task.files_scope:
        parts.append(f"  <files_scope>{', '.join(task.files_scope)}</files_scope>")

    if task.required_artifacts:
        parts.append("  <required_artifacts>")
        for art_type in task.required_artifacts:
            parts.append(f"    <artifact_type>{art_type.value}</artifact_type>")
        parts.append("  </required_artifacts>")

    if task.input_artifacts:
        parts.append("  <input_artifacts>")
        for path in task.input_artifacts:
            parts.append(f"    <file>cat {path}</file>")
        parts.append("  </input_artifacts>")

    parts.append("</task_assignment>\n")

    # ── Context from upstream tasks — XML-wrapped with safe truncation ──
    if context_outputs:
        parts.append("<upstream_context>")
        for tid, output in context_outputs.items():
            parts.append(f"  <task_result id='{tid}' status='{output.status.value}'>")
            parts.append(f"    <summary>{output.summary}</summary>")
            if output.artifacts:
                parts.append(f"    <files_changed>{', '.join(output.artifacts[:15])}</files_changed>")
            if output.issues:
                parts.append("    <issues>")
                for issue in output.issues[:5]:
                    parts.append(f"      <issue>{issue}</issue>")
                parts.append("    </issues>")

            # Structured artifacts — XML-wrapped with safe truncation
            if output.structured_artifacts:
                parts.append("    <artifacts>")
                for art in output.structured_artifacts:
                    parts.append(f"      <artifact type='{art.type.value}'>")
                    parts.append(f"        <title>{art.title}</title>")
                    if art.file_path:
                        parts.append(f"        <file_path>{art.file_path}</file_path>")
                    if art.summary:
                        parts.append(f"        <summary>{art.summary}</summary>")
                    if art.data:
                        data_str = json.dumps(art.data, indent=2)
                        data_str = _truncate_json_safely(data_str, 1200)
                        parts.append(f"        <data>\n{data_str}\n        </data>")
                    parts.append("      </artifact>")
                parts.append("    </artifacts>")

            parts.append("  </task_result>")
        parts.append("</upstream_context>\n")

    # ── Required output format (kept as-is for JSON parsing compatibility) ──
    parts.append(
        "---\n"
        "REQUIRED: After completing your work, think step-by-step in <agent_thinking> tags, "
        "then end your response with ONLY this JSON block (no text after it):\n\n"
        '```json\n'
        '{\n'
        f'  "task_id": "{task.id}",\n'
        '  "status": "completed",\n'
        '  "summary": "what you did in 2-3 sentences",\n'
        '  "artifacts": ["list/of/files.py"],\n'
        '  "issues": [],\n'
        '  "blockers": [],\n'
        '  "followups": [],\n'
        '  "confidence": 0.95,\n'
        '  "structured_artifacts": [\n'
        '    {\n'
        '      "type": "file_manifest",\n'
        '      "title": "Files Modified",\n'
        '      "file_path": ".nexus/artifacts/<your_task_id>_manifest.json",\n'
        '      "data": {"files": {"path/to/file.py": "description of changes"}},\n'
        '      "summary": "Brief description"\n'
        '    }\n'
        '  ],\n'
        '  "failure_category": null,\n'
        '  "failure_details": ""\n'
        '}\n'
        '```'
    )
    return "\n".join(parts)


def task_graph_schema() -> dict[str, Any]:
    """JSON schema for the PM agent's TaskGraph output."""
    return {
        "type": "object",
        "required": ["project_id", "user_message", "vision", "tasks"],
        "properties": {
            "project_id": {"type": "string"},
            "user_message": {"type": "string"},
            "vision": {"type": "string", "description": "One-sentence mission"},
            "epic_breakdown": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-7 high-level epics",
            },
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "role", "goal"],
                    "properties": {
                        "id": {"type": "string"},
                        "role": {"type": "string", "enum": [r.value for r in AgentRole]},
                        "goal": {"type": "string"},
                        "constraints": {"type": "array", "items": {"type": "string"}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "context_from": {"type": "array", "items": {"type": "string"}},
                        "files_scope": {"type": "array", "items": {"type": "string"}},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "required_artifacts": {
                            "type": "array",
                            "items": {"type": "string", "enum": [a.value for a in ArtifactType]},
                        },
                        "input_artifacts": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }
