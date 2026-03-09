"""
Agent Protocol Layer — Typed Contracts for the Multi-Agent System.

This module defines the shared language between ALL agents.
Every agent receives a TaskInput and must return a TaskOutput.
No free text, no regex parsing — pure structured contracts.
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


class AgentRole(str, Enum):
    PM                  = "pm"
    TYPESCRIPT_ARCHITECT = "typescript_architect"
    PYTHON_BACKEND      = "python_backend"
    TEST_ENGINEER       = "test_engineer"
    SECURITY_AUDITOR    = "security_auditor"
    UX_CRITIC           = "ux_critic"
    DATABASE_EXPERT     = "database_expert"
    DEVOPS              = "devops"
    RESEARCHER          = "researcher"
    REVIEWER            = "reviewer"
    # Legacy roles kept for backward compatibility
    DEVELOPER           = "developer"
    TESTER              = "tester"
    ORCHESTRATOR        = "orchestrator"


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

    task_id: str
    status: TaskStatus
    summary: str = Field(..., description="2–3 sentences describing what was done")
    artifacts: list[str] = Field(default_factory=list, description="Files created or modified")
    issues: list[str] = Field(default_factory=list, description="Problems or concerns found")
    blockers: list[str] = Field(default_factory=list, description="Things preventing completion")
    followups: list[str] = Field(default_factory=list, description="Recommended follow-up tasks")
    cost_usd: float = Field(default=0.0, ge=0.0)
    turns_used: int = Field(default=0, ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Agent's confidence in its output (0–1)")

    def is_successful(self) -> bool:
        return self.status == TaskStatus.COMPLETED

    def is_terminal(self) -> bool:
        """True if this task cannot be retried meaningfully."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.BLOCKED)


class TaskGraph(BaseModel):
    """The full execution plan produced by the PM Agent."""

    project_id: str
    user_message: str
    vision: str = Field(..., description="One-sentence mission statement for this task")
    epic_breakdown: list[str] = Field(default_factory=list, description="High-level epics (3–7 items)")
    tasks: list[TaskInput] = Field(..., description="All tasks with dependency wiring")

    def get_task(self, task_id: str) -> TaskInput | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def ready_tasks(self, completed: dict[str, TaskOutput]) -> list[TaskInput]:
        """Return tasks whose dependencies are all successfully completed."""
        result = []
        for task in self.tasks:
            if task.id in completed:
                continue
            if all(
                dep in completed and completed[dep].is_successful()
                for dep in task.depends_on
            ):
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

    def validate_dag(self) -> list[str]:
        """Check for cycles and missing dependency references. Returns error list."""
        errors: list[str] = []
        task_ids = {t.id for t in self.tasks}

        for task in self.tasks:
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
# JSON Output Extraction
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_task_output(raw_text: str, task_id: str) -> TaskOutput | None:
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

    # Fallback — could not parse
    return TaskOutput(
        task_id=task_id,
        status=TaskStatus.FAILED,
        summary="Agent returned unparseable output. The DAG executor will handle retry.",
        issues=["Could not parse TaskOutput JSON from agent response"],
        confidence=0.0,
    )


# ---------------------------------------------------------------------------
# Prompt Serialisation
# ---------------------------------------------------------------------------

def task_input_to_prompt(task: TaskInput, context_outputs: dict[str, TaskOutput]) -> str:
    """Serialise a TaskInput into a human-readable prompt section for the agent."""
    lines = [
        f"## Your Task (ID: {task.id})",
        f"**Role:** {task.role.value}",
        f"**Goal:** {task.goal}",
    ]
    if task.acceptance_criteria:
        lines.append("\n**Acceptance Criteria:**")
        for c in task.acceptance_criteria:
            lines.append(f"  - {c}")
    if task.constraints:
        lines.append("\n**Constraints:**")
        for c in task.constraints:
            lines.append(f"  - {c}")
    if task.files_scope:
        lines.append(f"\n**Files in scope:** {', '.join(task.files_scope)}")

    if context_outputs:
        lines.append("\n---\n## Context from Previous Tasks")
        for tid, output in context_outputs.items():
            lines.append(f"\n### [{tid}] — {output.status.value.upper()}")
            lines.append(f"{output.summary}")
            if output.artifacts:
                lines.append(f"Files changed: {', '.join(output.artifacts)}")
            if output.issues:
                lines.append("Issues: " + "; ".join(output.issues))

    lines.append(
        "\n---\n## REQUIRED: End your response with ONLY this JSON object "
        "(no text after it):\n"
        '```json\n'
        '{\n'
        f'  "task_id": "{task.id}",\n'
        '  "status": "completed",\n'
        '  "summary": "what you did in 2-3 sentences",\n'
        '  "artifacts": ["list/of/files.py"],\n'
        '  "issues": [],\n'
        '  "blockers": [],\n'
        '  "followups": [],\n'
        '  "confidence": 0.95\n'
        '}\n'
        '```'
    )
    return "\n".join(lines)


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
                "description": "3–7 high-level epics",
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
                    },
                },
            },
        },
    }
