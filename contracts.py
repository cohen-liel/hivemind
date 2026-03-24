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
import logging
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_FOLLOWUP = "needs_followup"
    REMEDIATION = "remediation"  # Auto-generated fix task


class AgentRole(str, Enum):
    # Layer 1: Brain
    PM = "pm"
    ORCHESTRATOR = "orchestrator"
    MEMORY = "memory"  # NEW: Memory/Architect agent
    # Layer 2: Execution
    FRONTEND_DEVELOPER = "frontend_developer"
    BACKEND_DEVELOPER = "backend_developer"
    DATABASE_EXPERT = "database_expert"
    DEVOPS = "devops"
    # Layer 3: Quality
    SECURITY_AUDITOR = "security_auditor"
    TEST_ENGINEER = "test_engineer"
    REVIEWER = "reviewer"
    RESEARCHER = "researcher"
    # Legacy (backward compat)
    TYPESCRIPT_ARCHITECT = "typescript_architect"
    PYTHON_BACKEND = "python_backend"
    UX_CRITIC = "ux_critic"
    DEVELOPER = "developer"
    TESTER = "tester"


class ArtifactType(str, Enum):
    """Types of structured artifacts agents can produce."""

    API_CONTRACT = "api_contract"  # OpenAPI / endpoint definitions
    SCHEMA = "schema"  # DB schema, TypeScript interfaces
    COMPONENT_MAP = "component_map"  # React component tree / props
    TEST_REPORT = "test_report"  # Test results with pass/fail
    SECURITY_REPORT = "security_report"  # Vulnerability findings
    REVIEW_REPORT = "review_report"  # Code review findings
    ARCHITECTURE = "architecture"  # Architecture decisions
    RESEARCH = "research"  # Research findings
    DEPLOYMENT = "deployment"  # Deployment config / instructions
    FILE_MANIFEST = "file_manifest"  # List of files created/modified with descriptions
    CUSTOM = "custom"  # Freeform structured data


class FailureCategory(str, Enum):
    """Classification of WHY a task failed — drives remediation strategy.

    Top-level categories cover broad failure modes. Subcategories (prefixed
    with their parent, e.g. BUILD_TYPE_ERROR) provide finer granularity for
    targeted retry strategies.
    """

    # --- Top-level categories ---
    DEPENDENCY_MISSING = "dependency_missing"  # Upstream task didn't produce what we need
    API_MISMATCH = "api_mismatch"  # Frontend/backend contract mismatch
    TEST_FAILURE = "test_failure"  # Code written but tests fail
    BUILD_ERROR = "build_error"  # Compilation / syntax error
    TIMEOUT = "timeout"  # Agent ran out of turns/budget
    PERMISSION = "permission"  # File access / auth issue
    UNCLEAR_GOAL = "unclear_goal"  # Task goal was ambiguous
    MISSING_CONTEXT = "missing_context"  # File/dependency not found
    EXTERNAL = "external"  # External service / API down
    UNKNOWN = "unknown"  # Unclassified

    # --- Subcategories of BUILD_ERROR ---
    BUILD_TYPE_ERROR = "build_type_error"  # Type mismatch (TypeScript, mypy, Pydantic)
    BUILD_IMPORT_ERROR = "build_import_error"  # Import resolution failure
    BUILD_SYNTAX_ERROR = "build_syntax_error"  # Syntax / parse error
    BUILD_MISSING_DEP = "build_missing_dep"  # Missing package / dependency

    # --- Subcategories of TEST_FAILURE ---
    TEST_ASSERTION = "test_assertion"  # Assertion mismatch
    TEST_RUNTIME_ERROR = "test_runtime_error"  # Uncaught exception during tests

    # --- Subcategory of EXTERNAL ---
    EXTERNAL_RATE_LIMIT = "external_rate_limit"  # Third-party API rate limit


# Mapping: subcategory → parent category (for fallback lookups)
_SUBCATEGORY_PARENT: dict[FailureCategory, FailureCategory] = {
    FailureCategory.BUILD_TYPE_ERROR: FailureCategory.BUILD_ERROR,
    FailureCategory.BUILD_IMPORT_ERROR: FailureCategory.BUILD_ERROR,
    FailureCategory.BUILD_SYNTAX_ERROR: FailureCategory.BUILD_ERROR,
    FailureCategory.BUILD_MISSING_DEP: FailureCategory.BUILD_ERROR,
    FailureCategory.TEST_ASSERTION: FailureCategory.TEST_FAILURE,
    FailureCategory.TEST_RUNTIME_ERROR: FailureCategory.TEST_FAILURE,
    FailureCategory.EXTERNAL_RATE_LIMIT: FailureCategory.EXTERNAL,
}


def get_parent_category(category: FailureCategory) -> FailureCategory:
    """Return the parent category for a subcategory, or the category itself if top-level."""
    return _SUBCATEGORY_PARENT.get(category, category)


def is_subcategory(category: FailureCategory) -> bool:
    """Return True if this is a subcategory (not a top-level category)."""
    return category in _SUBCATEGORY_PARENT


# Retry configuration per (sub)category:
#   max_retries: task-level retries before escalating to remediation
#   backoff_seconds: delay between retries
#   remediation_allowed: whether to create a remediation task if retries exhausted
_RETRY_STRATEGY: dict[FailureCategory, dict[str, int | float | bool]] = {
    # --- Subcategories with targeted strategies ---
    FailureCategory.BUILD_SYNTAX_ERROR: {
        "max_retries": 2,
        "backoff_seconds": 1,
        "remediation_allowed": True,
    },
    FailureCategory.BUILD_TYPE_ERROR: {
        "max_retries": 1,
        "backoff_seconds": 2,
        "remediation_allowed": True,
    },
    FailureCategory.BUILD_IMPORT_ERROR: {
        "max_retries": 2,
        "backoff_seconds": 1,
        "remediation_allowed": True,
    },
    FailureCategory.BUILD_MISSING_DEP: {
        "max_retries": 1,
        "backoff_seconds": 3,
        "remediation_allowed": True,
    },
    FailureCategory.TEST_ASSERTION: {
        "max_retries": 2,
        "backoff_seconds": 2,
        "remediation_allowed": True,
    },
    FailureCategory.TEST_RUNTIME_ERROR: {
        "max_retries": 1,
        "backoff_seconds": 2,
        "remediation_allowed": True,
    },
    FailureCategory.EXTERNAL_RATE_LIMIT: {
        "max_retries": 3,
        "backoff_seconds": 10,
        "remediation_allowed": False,
    },
    # --- Top-level defaults ---
    FailureCategory.BUILD_ERROR: {
        "max_retries": 2,
        "backoff_seconds": 2,
        "remediation_allowed": True,
    },
    FailureCategory.TEST_FAILURE: {
        "max_retries": 2,
        "backoff_seconds": 2,
        "remediation_allowed": True,
    },
    FailureCategory.DEPENDENCY_MISSING: {
        "max_retries": 1,
        "backoff_seconds": 2,
        "remediation_allowed": True,
    },
    FailureCategory.API_MISMATCH: {
        "max_retries": 1,
        "backoff_seconds": 2,
        "remediation_allowed": True,
    },
    FailureCategory.TIMEOUT: {"max_retries": 1, "backoff_seconds": 5, "remediation_allowed": True},
    FailureCategory.MISSING_CONTEXT: {
        "max_retries": 1,
        "backoff_seconds": 2,
        "remediation_allowed": True,
    },
    FailureCategory.UNKNOWN: {"max_retries": 1, "backoff_seconds": 3, "remediation_allowed": True},
    # No retry:
    FailureCategory.UNCLEAR_GOAL: {
        "max_retries": 0,
        "backoff_seconds": 0,
        "remediation_allowed": False,
    },
    FailureCategory.PERMISSION: {
        "max_retries": 0,
        "backoff_seconds": 0,
        "remediation_allowed": False,
    },
    FailureCategory.EXTERNAL: {
        "max_retries": 0,
        "backoff_seconds": 0,
        "remediation_allowed": False,
    },
}

# Default strategy for any category not listed
_DEFAULT_RETRY_STRATEGY: dict[str, int | float | bool] = {
    "max_retries": 1,
    "backoff_seconds": 2,
    "remediation_allowed": True,
}


def get_retry_strategy(category: FailureCategory) -> dict[str, int | float | bool]:
    """Get the retry strategy for a failure (sub)category.

    Looks up the specific subcategory first, then falls back to the parent
    category, then to a conservative default.
    """
    if category in _RETRY_STRATEGY:
        return _RETRY_STRATEGY[category]
    parent = get_parent_category(category)
    if parent in _RETRY_STRATEGY:
        return _RETRY_STRATEGY[parent]
    return dict(_DEFAULT_RETRY_STRATEGY)


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
    file_path: str = Field(
        default="", description="Path to the artifact file (relative to project root)"
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured data payload — schema depends on artifact type",
    )
    summary: str = Field(default="", description="1-2 sentence human-readable summary")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        """Validate that the task title is non-empty and within length limits."""
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
    constraints: list[str] = Field(
        default_factory=list, description="Hard rules the agent must follow"
    )
    depends_on: list[str] = Field(
        default_factory=list, description="Task IDs that must complete before this one"
    )
    context_from: list[str] = Field(
        default_factory=list, description="Task IDs whose output should be injected as context"
    )
    files_scope: list[str] = Field(
        default_factory=list,
        description="Files this task is expected to touch (for conflict detection)",
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list, description="Explicit conditions that define 'done'"
    )
    # v2: Artifact requirements
    required_artifacts: list[ArtifactType] = Field(
        default_factory=list,
        description="Artifact types this task MUST produce (enforced by DAG executor)",
    )
    input_artifacts: list[str] = Field(
        default_factory=list,
        description="Artifact file paths from upstream tasks to read before starting",
    )
    # v2: Remediation metadata
    is_remediation: bool = Field(
        default=False, description="True if this task was auto-generated to fix a failure"
    )
    original_task_id: str = Field(default="", description="If remediation, the task that failed")
    failure_context: str = Field(
        default="", description="If remediation, description of what went wrong"
    )
    # v3: Cross-agent artifact contract validation
    expected_input_artifact_types: list[ArtifactType] = Field(
        default_factory=list,
        description="Artifact types this task expects from upstream tasks via context_from (for pre-execution validation)",
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Validate that the task ID matches the expected format."""
        if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", v):
            raise ValueError(f"Invalid task id '{v}': use letters, digits, _ or - only")
        return v

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, v: str) -> str:
        """Validate that the task goal is non-empty."""
        if len(v.strip()) < 10:
            raise ValueError("Task goal must be at least 10 characters")
        return v.strip()


class DiscoveredTask(BaseModel):
    """A task proposed by an agent during execution (Dynamic DAG).

    Agents can propose new tasks when they discover work that wasn't
    anticipated in the original plan. The DAG executor injects these
    into the live graph with safety constraints (max count, depth limit).
    """

    goal: str = Field(..., min_length=10, description="Clear objective for the new task")
    role: str = Field(..., description="Agent role to handle this (e.g. 'backend_developer')")
    reason: str = Field(..., description="Why this task is needed — what was discovered")
    priority: str = Field(default="normal", description="high, normal, or low")
    depends_on_source: bool = Field(
        default=True,
        description="If True, this task depends on the source task completing first",
    )


class TaskOutput(BaseModel):
    """What an agent returns — the contract coming OUT."""

    model_config = {"extra": "allow"}  # Allow dynamic attrs like _progress

    task_id: str
    status: TaskStatus
    summary: str = Field(..., description="2-3 sentences describing what was done")
    artifacts: list[str] = Field(default_factory=list, description="Files created or modified")
    issues: list[str] = Field(
        default_factory=list, max_length=50, description="Problems or concerns found"
    )
    blockers: list[str] = Field(
        default_factory=list, max_length=50, description="Things preventing completion"
    )
    followups: list[str] = Field(
        default_factory=list, max_length=50, description="Recommended follow-up tasks"
    )
    cost_usd: float = Field(default=0.0, ge=0.0)  # Deprecated — kept for backward compat
    input_tokens: int = Field(default=0, ge=0, description="Input tokens consumed by this task")
    output_tokens: int = Field(default=0, ge=0, description="Output tokens produced by this task")
    total_tokens: int = Field(default=0, ge=0, description="Total tokens (input + output)")
    turns_used: int = Field(default=0, ge=0)
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Agent's confidence in its output (0-1). Defaults to 0.5 — must be explicitly set by the agent or extract_task_output().",
    )
    # v2: Structured artifacts (max 20 per task to prevent memory issues)
    structured_artifacts: list[Artifact] = Field(
        default_factory=list,
        description="Typed artifacts produced by this task (API contracts, schemas, reports)",
        max_length=20,
    )
    # v2: Failure classification
    failure_category: FailureCategory | None = Field(
        default=None, description="If status=failed, WHY it failed (drives auto-remediation)"
    )
    failure_details: str = Field(
        default="", description="Detailed explanation of the failure for remediation agent"
    )
    # v3: Dynamic DAG — agents can propose new tasks discovered during execution
    discovered_tasks: list[DiscoveredTask] = Field(
        default_factory=list,
        description="Tasks discovered during execution that weren't in the original plan",
        max_length=5,
    )

    def is_successful(self) -> bool:
        """Return True if the task result indicates success."""
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

    This gets written to .hivemind/PROJECT_MANIFEST.md and is read by the PM
    at the start of every new task to maintain cross-session continuity.
    """

    project_id: str
    architecture_summary: str = Field(
        default="", description="Current architecture in 3-5 sentences"
    )
    tech_stack: dict[str, str] = Field(
        default_factory=dict,
        description="Technology choices, e.g. {'frontend': 'React+TS', 'backend': 'FastAPI'}",
    )
    key_decisions: list[str] = Field(
        default_factory=list, description="Important architectural decisions made (append-only log)"
    )
    known_issues: list[str] = Field(
        default_factory=list, description="Unresolved issues or tech debt"
    )
    api_surface: list[dict[str, str]] = Field(
        default_factory=list, description="Current API endpoints: [{method, path, description}]"
    )
    db_tables: list[str] = Field(default_factory=list, description="Current database tables")
    file_map: dict[str, str] = Field(
        default_factory=dict,
        description="Key files and their purpose, e.g. {'src/api/auth.py': 'JWT authentication'}",
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
    epic_breakdown: list[str] = Field(
        default_factory=list, description="High-level epics (3-7 items)"
    )
    tasks: list[TaskInput] = Field(..., description="All tasks with dependency wiring")

    def get_task(self, task_id: str) -> TaskInput | None:
        """Return the task node with the given ID, or None."""
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
        """Return True if all tasks in the graph have finished."""
        return all(t.id in completed for t in self.tasks)

    def has_failed(self, completed: dict[str, TaskOutput]) -> bool:
        """True if a blocked/failed task has no downstream path to completion."""
        blocked = {
            t.id
            for t in self.tasks
            if t.id in completed
            and completed[t.id].status in (TaskStatus.FAILED, TaskStatus.BLOCKED)
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

    def remove_task(
        self, task_id: str, completed: dict[str, TaskOutput] | set[str] | None = None
    ) -> bool:
        """Remove a pending task from the graph.

        Only removes tasks that have NOT been completed yet.  Also cleans up
        dangling dependency references in remaining tasks.

        Returns True if the task was removed, False if not found or already
        completed.
        """
        completed_ids = set(completed) if completed else set()
        if task_id in completed_ids:
            return False  # already executed — cannot remove
        idx = next((i for i, t in enumerate(self.tasks) if t.id == task_id), None)
        if idx is None:
            return False
        self.tasks.pop(idx)
        # Clean up dangling deps
        for t in self.tasks:
            if task_id in t.depends_on:
                t.depends_on = [d for d in t.depends_on if d != task_id]
        return True

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
            """Return True if the task dependency graph contains a cycle."""
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
# DAG Checkpoint — durable state for resuming interrupted graph executions
# ---------------------------------------------------------------------------


class DAGCheckpoint(BaseModel):
    """Serializable snapshot of a DAG execution for crash recovery.

    Persisted to SQLite after each completed round so that a long-running
    graph execution can resume from the last checkpoint rather than
    restarting from scratch.
    """

    project_id: str
    graph_json: str = Field(
        ..., description="JSON-serialized TaskGraph (including dynamically added remediation tasks)"
    )
    completed_tasks: dict[str, dict] = Field(
        default_factory=dict,
        description="Map of task_id -> TaskOutput.model_dump() for every finished task",
    )
    retries: dict[str, int] = Field(
        default_factory=dict,
        description="Per-task retry counts",
    )
    total_cost: float = Field(default=0.0, ge=0.0)
    remediation_count: int = Field(default=0, ge=0)
    healing_history: list[dict[str, str]] = Field(default_factory=list)
    round_num: int = Field(default=0, ge=0, description="Last completed round number")
    created_at: float = Field(default=0.0, description="Epoch timestamp of checkpoint creation")
    status: str = Field(
        default="running",
        description="Checkpoint status: running, completed, failed, interrupted",
    )


def compute_task_complexity(task: TaskInput) -> float:
    """Compute a complexity score (1.0 – 5.0) for adaptive timeout scaling.

    Delegates to the unified classifier in blackboard.classify_complexity().
    """
    from blackboard import classify_complexity

    result = classify_complexity(
        text=task.goal,
        acceptance_criteria=task.acceptance_criteria,
        constraints=task.constraints,
        files_scope=task.files_scope,
        depends_on=task.depends_on,
        role=task.role.value if hasattr(task.role, "value") else str(task.role),
        is_remediation=getattr(task, "is_remediation", False),
    )
    return result.score


# ---------------------------------------------------------------------------
# Failure Classification — auto-detect WHY a task failed
# ---------------------------------------------------------------------------

_FAILURE_PATTERNS: list[tuple[FailureCategory, list[str]]] = [
    # --- Subcategories first (more specific patterns win by score) ---
    (
        FailureCategory.BUILD_TYPE_ERROR,
        [
            "typeerror",
            "type error",
            "type mismatch",
            "incompatible type",
            "expected type",
            "cannot assign",
            "mypy",
            "type 'nonetype'",
            "referenceerror",
            "is not assignable to type",
            "property does not exist on type",
            "argument of type",
        ],
    ),
    (
        FailureCategory.BUILD_IMPORT_ERROR,
        [
            "importerror",
            "import error",
            "no module named",
            "cannot find module",
            "unresolved import",
            "module not found",
            "modulenotfounderror",
            "could not resolve",
        ],
    ),
    (
        FailureCategory.BUILD_SYNTAX_ERROR,
        [
            "syntaxerror",
            "syntax error",
            "parse error",
            "unexpected token",
            "indentation",
            "unterminated",
            "invalid syntax",
            "unexpected end of input",
            "unexpected identifier",
        ],
    ),
    (
        FailureCategory.BUILD_MISSING_DEP,
        [
            "not installed",
            "package not found",
            "missing module",
            "pip install",
            "npm install",
            "no such package",
            "dependency not found",
            "requirements.txt",
        ],
    ),
    (
        FailureCategory.TEST_ASSERTION,
        [
            "assertionerror",
            "assertion error",
            "assert false",
            "expected.*but got",
            "not equal",
            "assertEqual",
            "assert.*==",
            "mismatch in expected",
        ],
    ),
    (
        FailureCategory.TEST_RUNTIME_ERROR,
        [
            "runtime error during test",
            "exception in test",
            "error during test setup",
            "fixture.*error",
            "collection error",
            "test setup failed",
        ],
    ),
    (
        FailureCategory.EXTERNAL_RATE_LIMIT,
        [
            "rate limit",
            "rate_limit",
            "429",
            "too many requests",
            "throttled",
            "quota exceeded",
        ],
    ),
    # --- Top-level categories (broader patterns) ---
    (
        FailureCategory.DEPENDENCY_MISSING,
        [
            "import error",
            "importerror",
            "module not found",
            "modulenotfounderror",
            "no such file",
            "dependency",
            "not installed",
            "missing module",
            "cannot find module",
            "no module named",
            "package not found",
            "could not resolve",
            "unresolved import",
        ],
    ),
    (
        FailureCategory.API_MISMATCH,
        [
            "404",
            "endpoint not found",
            "api mismatch",
            "contract",
            "expected response",
            "schema mismatch",
            "property does not exist",
            "undefined is not",
            "missing field",
            "wrong status code",
        ],
    ),
    (
        FailureCategory.TEST_FAILURE,
        [
            "test failed",
            "assertion error",
            "expected",
            "assert",
            "pytest",
            "test_",
            "FAILED",
            "failures=",
        ],
    ),
    (
        FailureCategory.BUILD_ERROR,
        [
            "syntax error",
            "syntaxerror",
            "compilation",
            "build failed",
            "tsc",
            "cannot compile",
            "parse error",
            "unexpected token",
            "indentation",
            "unterminated",
            "invalid syntax",
            "typeerror",
            "nameerror",
            "referenceerror",
        ],
    ),
    (
        FailureCategory.TIMEOUT,
        [
            "timeout",
            "timed out",
            "max turns",
            "budget exceeded",
            "too many iterations",
            "deadline",
        ],
    ),
    (
        FailureCategory.PERMISSION,
        [
            "permission denied",
            "permissionerror",
            "access denied",
            "forbidden",
            "eacces",
            "read-only",
            "not writable",
        ],
    ),
    (
        FailureCategory.MISSING_CONTEXT,
        [
            "filenotfounderror",
            "file not found",
            "no such file or directory",
            "missing context",
            "dependency not completed",
            "upstream task",
            "context_from",
            "required artifact missing",
        ],
    ),
    (
        FailureCategory.UNCLEAR_GOAL,
        [
            "unclear",
            "ambiguous",
            "not sure what",
            "need clarification",
            "insufficient context",
            "cannot determine",
        ],
    ),
    (
        FailureCategory.EXTERNAL,
        [
            "connection refused",
            "network error",
            "dns",
            "502",
            "503",
            "service unavailable",
            "rate limit",
            "api key",
        ],
    ),
]


def classify_failure(output: TaskOutput) -> FailureCategory:
    """Auto-classify a failed task's failure category from its output text.

    Scans the summary, issues, blockers, and failure_details for known patterns.
    Returns the most specific category found, or UNKNOWN.
    """
    if output.failure_category and output.failure_category != FailureCategory.UNKNOWN:
        return output.failure_category  # Agent already classified it

    # Build searchable text from all output fields
    search_text = " ".join(
        [
            output.summary,
            output.failure_details,
            " ".join(output.issues),
            " ".join(output.blockers),
        ]
    ).lower()

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
    # --- Subcategory-specific strategies (more targeted) ---
    FailureCategory.BUILD_TYPE_ERROR: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix type error from task {task_id}: {failure_details}. "
            "Check the type annotations, Pydantic models, and function signatures. "
            "Run mypy or tsc to verify type correctness after the fix."
        ),
        "constraints": [
            "Focus only on fixing the type mismatch — do not change logic",
            "Verify with type checker after fixing",
        ],
    },
    FailureCategory.BUILD_IMPORT_ERROR: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix import error from task {task_id}: {failure_details}. "
            "Check if the module exists, is spelled correctly, and is in the right location. "
            "Fix the import path or create the missing module."
        ),
        "constraints": [
            "Only fix the import — do not refactor unrelated code",
            "Verify by running python -c 'import <module>' or tsc after fixing",
        ],
    },
    FailureCategory.BUILD_SYNTAX_ERROR: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix syntax error from task {task_id}: {failure_details}. "
            "The error message contains the exact line and position. "
            "Fix the syntax and verify with python -m py_compile or tsc."
        ),
        "constraints": [
            "Fix only the syntax error — minimal change",
            "Compile-check the file after fixing",
        ],
    },
    FailureCategory.BUILD_MISSING_DEP: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Install missing dependency from task {task_id}: {failure_details}. "
            "Add the package to requirements.txt or package.json and install it. "
            "Then verify the import works."
        ),
        "constraints": [
            "Add the dependency to the project manifest (requirements.txt / package.json)",
            "Pin the version with ==",
        ],
    },
    FailureCategory.TEST_ASSERTION: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix assertion failure from task {task_id}: {failure_details}. "
            "The test expected a specific value but got a different one. "
            "Fix the implementation (not the test) to produce the expected output."
        ),
        "constraints": [
            "Fix the implementation, not the test assertions",
            "Run the specific failing test after fixing to verify",
        ],
    },
    FailureCategory.TEST_RUNTIME_ERROR: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Fix test runtime error from task {task_id}: {failure_details}. "
            "An exception was raised during test execution. "
            "Check test fixtures, setup, and the code under test."
        ),
        "constraints": [
            "Check if the error is in test setup/fixtures or the implementation",
            "Run pytest -x --tb=long on the failing test for full traceback",
        ],
    },
    FailureCategory.EXTERNAL_RATE_LIMIT: {
        "role": AgentRole.BACKEND_DEVELOPER,
        "goal_template": (
            "Handle rate limit from task {task_id}: {failure_details}. "
            "Add retry with backoff for the rate-limited API call, or "
            "implement caching to reduce call frequency."
        ),
        "constraints": [
            "Do not remove the API call — add resilience instead",
            "Use exponential backoff with jitter",
        ],
    },
    # --- Top-level strategies (fallback for unsubcategorized failures) ---
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
        context_from=list(dict.fromkeys([*failed_task.context_from, failed_task.id])),  # Deduped
        files_scope=failed_task.files_scope,
        acceptance_criteria=[
            *failed_task.acceptance_criteria,
            f"The issue from {failed_task.id} is resolved",
            "All related tests pass (if applicable)",
        ],
        input_artifacts=([a.file_path for a in failed_output.structured_artifacts if a.file_path]),
        is_remediation=True,
        original_task_id=failed_task.id,
        failure_context=f"[{category.value}] {failure_details[:500]}",
    )


# ---------------------------------------------------------------------------
# Artifact Contract Validation — pre-execution cross-agent checks
# ---------------------------------------------------------------------------


class ArtifactContractError(Exception):
    """Raised when artifact contracts between tasks are inconsistent.

    Contains a list of mismatch descriptions that can be reported to the user
    or logged before execution starts.
    """

    def __init__(self, mismatches: list[str]) -> None:
        self.mismatches: list[str] = mismatches
        super().__init__(
            f"Artifact contract validation failed with {len(mismatches)} mismatch(es): "
            + "; ".join(mismatches[:5])
        )


def validate_artifact_contracts(graph: TaskGraph) -> list[str]:
    """Pre-execution validation of artifact contracts across the task graph.

    Checks that every task's ``expected_input_artifact_types`` can be
    satisfied by the ``required_artifacts`` of its ``context_from`` producers.

    Also performs best-effort *inferred* checks: if a task's goal mentions
    specific artifact types (e.g. "api_contract", "schema") and its
    ``context_from`` producers don't list those types in
    ``required_artifacts``, a warning is emitted.

    Performance: O(T × A) where T = task count, A = max artifacts per task.
    Well within the 50 ms constraint for any graph under 1 000 tasks.

    Args:
        graph: The TaskGraph to validate.

    Returns:
        A list of mismatch descriptions (empty == valid).

    Raises:
        ArtifactContractError: If ``raise_on_error`` is used via the DAG
            executor wrapper (see ``dag_executor.py``).
    """
    mismatches: list[str] = []
    task_map: dict[str, TaskInput] = {t.id: t for t in graph.tasks}

    for task in graph.tasks:
        # --- Explicit contract: expected_input_artifact_types ---
        if task.expected_input_artifact_types:
            # Gather all artifact types the upstream tasks promise to produce
            upstream_artifact_types: set[ArtifactType] = set()
            for upstream_id in task.context_from:
                upstream = task_map.get(upstream_id)
                if upstream is not None:
                    upstream_artifact_types.update(upstream.required_artifacts)

            for expected_type in task.expected_input_artifact_types:
                if expected_type not in upstream_artifact_types:
                    # Find which upstream tasks exist
                    producer_ids = [uid for uid in task.context_from if uid in task_map]
                    mismatches.append(
                        f"Task '{task.id}' expects artifact '{expected_type.value}' "
                        f"from upstream {producer_ids}, but none of them list it "
                        f"in required_artifacts"
                    )

        # --- Inferred contract: goal mentions artifact type names ---
        _INFERRED_ARTIFACT_KEYWORDS: dict[ArtifactType, list[str]] = {
            ArtifactType.API_CONTRACT: ["api_contract", "api contract", "endpoint definition"],
            ArtifactType.SCHEMA: ["schema", "database schema", "db schema"],
            ArtifactType.COMPONENT_MAP: ["component_map", "component map", "component tree"],
            ArtifactType.TEST_REPORT: ["test_report", "test report"],
        }

        if task.context_from:
            goal_lower = task.goal.lower()
            upstream_types: set[ArtifactType] = set()
            for uid in task.context_from:
                upstream = task_map.get(uid)
                if upstream is not None:
                    upstream_types.update(upstream.required_artifacts)

            for art_type, keywords in _INFERRED_ARTIFACT_KEYWORDS.items():
                if any(kw in goal_lower for kw in keywords):
                    if art_type not in upstream_types and task.context_from:
                        # Soft warning — inferred, not explicit
                        mismatches.append(
                            f"Task '{task.id}' goal mentions '{art_type.value}' "
                            f"but no upstream task in context_from "
                            f"{task.context_from} produces it (inferred check)"
                        )

        # --- Dangling context_from references ---
        for ref_id in task.context_from:
            if ref_id not in task_map:
                mismatches.append(
                    f"Task '{task.id}' has context_from reference to unknown task '{ref_id}'"
                )

    return mismatches


# ---------------------------------------------------------------------------
# JSON Output Extraction
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
# Pre-compiled patterns used in extract_task_output() — avoids recompiling on every call
_FILE_PATH_RE = re.compile(
    r"[\w./-]+\.(?:py|ts|tsx|js|jsx|json|md|yaml|yml|css|html|sql|sh|env|toml|cfg)",
)
_CODE_BLOCK_RE = re.compile(r"```(?:python|typescript|javascript|bash|sql|\w+)?\n")


def extract_task_output(
    raw_text: str, task_id: str, task_role: str = "", tool_uses: list[str] | None = None
) -> TaskOutput:
    """
    Parse a TaskOutput from an agent's raw text response.

    Tries in order:
    1. Fenced JSON code block (```json ... ```)
    2. Last JSON object in the text
    3. Multi-signal work detection (tool use, file paths, action verbs, text volume)
    4. Fallback: synthesise a FAILED output so the DAG can handle it

    This is the ONLY place where we parse agent text output.
    """
    # ── Step 1: Try fenced JSON block ──
    for match in _JSON_BLOCK_RE.finditer(raw_text):
        try:
            data = json.loads(match.group(1).strip())
            data.setdefault("task_id", task_id)
            return TaskOutput(**data)
        except Exception:
            continue

    # ── Step 2: Try last JSON object in text ──
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

    # ── Step 3: Multi-signal work detection ──
    # Instead of a naive keyword list, we score multiple independent signals.
    # Each signal contributes points. If total score >= threshold → COMPLETED.
    logger.warning(
        f"[extract_task_output] No JSON found for {task_id}. "
        f"Text length={len(raw_text)}. Running multi-signal work detection."
    )

    score = 0.0
    signals: list[str] = []  # Human-readable log of what we detected
    lower = raw_text.lower() if raw_text else ""

    # Signal 1: Tool use indicators (agent used CLI, read/wrote files)
    _TOOL_PATTERNS = [
        (r"\$ .+", "shell commands"),  # $ command output
        (r"running:? `[^`]+`", "tool execution"),  # Running: `cmd`
        (r"reading:? .+\.\w+", "file reads"),
        (r"writing:? .+\.\w+", "file writes"),
        (r"editing:? .+\.\w+", "file edits"),
        (r"completed via tool use", "tool completion marker"),
    ]
    tool_hits = 0
    for pattern, label in _TOOL_PATTERNS:
        matches = re.findall(pattern, lower)
        if matches:
            tool_hits += len(matches)
            signals.append(f"{label}({len(matches)}x)")
    if tool_hits >= 3:
        score += 0.4
    elif tool_hits >= 1:
        score += 0.2

    # Signal 2: File paths mentioned (strong indicator of real work)
    file_paths = _FILE_PATH_RE.findall(raw_text)
    unique_files = list(dict.fromkeys(file_paths))[:30]
    if len(unique_files) >= 5:
        score += 0.3
        signals.append(f"files_mentioned({len(unique_files)})")
    elif len(unique_files) >= 2:
        score += 0.15
        signals.append(f"files_mentioned({len(unique_files)})")

    # Signal 3: Action verbs — the agent describes doing things
    _ACTION_VERBS = [
        "created ",
        "modified ",
        "updated ",
        "wrote ",
        "implemented",
        "fixed ",
        "added ",
        "refactored",
        "installed ",
        "configured",
        "deployed",
        "migrated",
        "deleted ",
        "removed ",
        "replaced ",
        "built ",
        "compiled",
        "tested ",
        "verified",
        "committed",
        "now let me",
        "i'll now",
        "next i",
        "let me update",
        "i have ",
        "i've ",
        "successfully",
    ]
    verb_hits = sum(1 for v in _ACTION_VERBS if v in lower)
    if verb_hits >= 4:
        score += 0.3
        signals.append(f"action_verbs({verb_hits})")
    elif verb_hits >= 2:
        score += 0.15
        signals.append(f"action_verbs({verb_hits})")

    # Signal 4: Structured report sections
    _REPORT_MARKERS = [
        "## summary",
        "## files changed",
        "## actions taken",
        "## issues found",
        "## status",
        "# summary",
    ]
    report_hits = sum(1 for m in _REPORT_MARKERS if m in lower)
    if report_hits >= 2:
        score += 0.3
        signals.append(f"report_sections({report_hits})")
    elif report_hits >= 1:
        score += 0.1
        signals.append(f"report_sections({report_hits})")

    # Signal 5: Git activity
    if "git commit" in lower or "git add" in lower:
        score += 0.3
        signals.append("git_activity")

    # Signal 6: Substantial text output (agent was clearly working, not empty)
    if len(raw_text) >= 2000:
        score += 0.15
        signals.append(f"text_volume({len(raw_text)})")
    elif len(raw_text) >= 500:
        score += 0.05
        signals.append(f"text_volume({len(raw_text)})")

    # Signal 7: Code blocks (agent wrote or showed code)
    code_blocks = _CODE_BLOCK_RE.findall(raw_text)
    if len(code_blocks) >= 2:
        score += 0.2
        signals.append(f"code_blocks({len(code_blocks)})")
    elif len(code_blocks) >= 1:
        score += 0.1
        signals.append(f"code_blocks({len(code_blocks)})")

    # Signal 8: Write operations (REQUIRED for execution agents)
    # Primary source: actual tool_uses list from SDK (authoritative)
    # Fallback: text pattern matching (for legacy/summary phase)
    _WRITE_TOOL_NAMES = {"Write", "write_file", "create_file", "Edit", "edit_file"}
    _READ_TOOL_NAMES = {
        "Read",
        "read_file",
        "Glob",
        "glob",
        "ListFiles",
        "Grep",
        "grep",
        "SearchFiles",
    }
    _EXEC_TOOL_NAMES = {"Bash", "execute_bash", "bash"}
    write_hits = 0
    if tool_uses:
        # Count actual write tools used by the agent
        for t in tool_uses:
            if t in _WRITE_TOOL_NAMES:
                write_hits += 1
            elif t in _EXEC_TOOL_NAMES:
                write_hits += 0.5  # Bash MIGHT write files
        read_count = sum(1 for t in tool_uses if t in _READ_TOOL_NAMES)
        exec_count = sum(1 for t in tool_uses if t in _EXEC_TOOL_NAMES)
        signals.append(
            f"sdk_tools(writes={write_hits},reads={read_count},exec={exec_count},total={len(tool_uses)})"
        )
    else:
        # Fallback: text pattern matching (when tool_uses not available)
        _WRITE_PATTERNS = [
            (r"writing:? .+\.\w+", "file_writes"),
            (r"editing:? .+\.\w+", "file_edits"),
            (r"created? .+\.\w+", "file_creates"),
        ]
        for pattern, label in _WRITE_PATTERNS:
            matches = re.findall(pattern, lower)
            if matches:
                write_hits += len(matches)
                signals.append(f"{label}({len(matches)}x)")
        signals.append("text_fallback_write_detection")
    if write_hits >= 1:
        score += 0.3
        signals.append(f"has_write_ops({write_hits})")

    # Execution agents without write operations → not really completed
    _EXECUTION_ROLES = {
        "backend_developer",
        "frontend_developer",
        "database_expert",
        "devops",
    }
    if task_role in _EXECUTION_ROLES and write_hits == 0:
        score = min(score, 0.35)  # Below WORK_THRESHOLD
        signals.append("NO_WRITES(execution_agent)")

    logger.info(
        f"[extract_task_output] {task_id}: work score={score:.2f} signals=[{', '.join(signals)}]"
    )

    # ── Extract summary from text ──
    inferred_summary = ""
    for marker in ["## SUMMARY", "## Summary", "# Summary"]:
        idx = raw_text.find(marker)
        if idx != -1:
            after = raw_text[idx + len(marker) :].strip()
            end = after.find("\n\n")
            inferred_summary = after[:end].strip() if end != -1 else after[:300].strip()
            break
    if not inferred_summary and raw_text:
        # Use last 300 chars as summary hint
        inferred_summary = raw_text[-300:].strip()

    # ── Decision: score >= 0.4 → COMPLETED ──
    WORK_THRESHOLD = 0.4

    if score >= WORK_THRESHOLD:
        confidence = min(0.5 + score * 0.3, 0.85)  # Scale: 0.62 to 0.85
        fallback = TaskOutput(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            summary=(
                f"Agent completed work (inferred, score={score:.2f}). {inferred_summary[:200]}"
            ),
            artifacts=unique_files,
            issues=[
                "Agent did not produce TaskOutput JSON — output inferred via multi-signal detection"
            ],
            confidence=confidence,
        )
        logger.info(
            f"[extract_task_output] {task_id}: inferred COMPLETED "
            f"(score={score:.2f}, confidence={confidence:.2f}, "
            f"{len(unique_files)} files, signals={signals})"
        )
    else:
        fallback = TaskOutput(
            task_id=task_id,
            status=TaskStatus.FAILED,
            summary=(
                f"Agent output could not be parsed and work score too low "
                f"({score:.2f} < {WORK_THRESHOLD}). Last output: {inferred_summary[:200]}"
            ),
            issues=[f"No JSON output and low work score ({score:.2f}). Signals: {signals}"],
            failure_details=raw_text[-500:] if raw_text else "",
            confidence=0.0,
        )
        fallback.failure_category = classify_failure(fallback)
        logger.warning(
            f"[extract_task_output] {task_id}: FAILED (score={score:.2f}, signals={signals})"
        )

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
        if truncated[i] in (",", "{", "[", "\n"):
            truncated = truncated[: i + 1]
            break
    return truncated + "\n    ... (truncated — read the file for full data)"


def task_input_to_prompt(
    task: TaskInput,
    context_outputs: dict[str, TaskOutput],
    graph_vision: str = "",
    graph_epics: list[str] | None = None,
    user_message: str = "",
) -> str:
    """Serialise a TaskInput into a structured XML prompt for the agent.

    v4: Full user context injection — every agent sees the original user prompt
    so critical details (API keys, code examples, architecture decisions) are
    never lost in the "telephone game" between PM → DAG → agent.
    """
    parts: list[str] = []

    # ── Big Picture: every agent sees the original mission ──
    if graph_vision or graph_epics or user_message:
        parts.append("<mission>")
        if graph_vision:
            parts.append(f"  <vision>{graph_vision}</vision>")
        if graph_epics:
            parts.append("  <epics>")
            for i, epic in enumerate(graph_epics, 1):
                parts.append(f"    <epic id='{i}'>{epic}</epic>")
            parts.append("  </epics>")
        # ── Original user prompt — the full context that started this project ──
        # This ensures agents don't lose critical details like API keys,
        # code examples, specific instructions, or architectural decisions
        # that were in the user's original message but not in their narrow task goal.
        if user_message:
            # Truncate very long messages to avoid bloating the prompt,
            # but keep enough to preserve all important details.
            _max_user_msg = 8000  # ~2k tokens — generous enough for API keys, examples, etc.
            _truncated = user_message[:_max_user_msg]
            if len(user_message) > _max_user_msg:
                _truncated += "\n... (truncated — see project files for full context)"
            parts.append("  <original_user_request>")
            parts.append(f"    {_truncated}")
            parts.append("  </original_user_request>")
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
                parts.append(
                    f"    <files_changed>{', '.join(output.artifacts[:15])}</files_changed>"
                )
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

    # ── Thinking step (Anthropic best practice: reason before acting) ──
    parts.append(
        "<thinking_protocol>\n"
        "Before starting your work, think step-by-step inside <thinking> tags:\n"
        "1. What exactly is being asked? What does 'done' look like?\n"
        "2. What files/systems are involved? What do I need to read first?\n"
        "3. What are the constraints I must respect?\n"
        "4. What is my plan of action? (ordered steps)\n"
        "5. What could go wrong? How will I verify success?\n\n"
        "Only AFTER completing your <thinking> block, begin the actual work.\n"
        "This reasoning step dramatically improves output quality.\n"
        "</thinking_protocol>\n"
    )

    # ── Lightweight output request ──
    parts.append(
        "---\n"
        "After completing your work, briefly list what files you created or modified.\n"
        f"Include your task_id: {task.id}\n"
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
                        "expected_input_artifact_types": {
                            "type": "array",
                            "items": {"type": "string", "enum": [a.value for a in ArtifactType]},
                            "description": "Artifact types expected from upstream context_from tasks",
                        },
                    },
                },
            },
        },
    }
