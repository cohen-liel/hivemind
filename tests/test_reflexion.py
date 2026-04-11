"""
tests/test_reflexion.py — Unit tests for the Reflexion engine.

Tests cover:
- should_reflect: gating logic for when reflexion triggers
- build_critique_prompt: prompt construction
- build_fix_prompt: fix prompt construction
- parse_critique_response: JSON parsing with various formats
- ReflexionVerdict: dataclass behavior

These are pure unit tests — no real SDK calls, no Claude API.
"""

from __future__ import annotations

import json

from contracts import AgentRole, TaskInput, TaskOutput, TaskStatus
from reflexion import (
    REFLEXION_CONFIDENCE_THRESHOLD,
    ReflexionVerdict,
    build_critique_prompt,
    build_fix_prompt,
    parse_critique_response,
    should_reflect,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "task_1",
    role: AgentRole = AgentRole.BACKEND_DEVELOPER,
    goal: str = "Build a REST API for user management",
    is_remediation: bool = False,
) -> TaskInput:
    t = TaskInput(
        id=task_id,
        role=role,
        goal=goal,
        acceptance_criteria=["API returns 200 on GET /users", "Tests pass"],
        constraints=["Follow existing patterns"],
        files_scope=[],
        depends_on=[],
    )
    t.is_remediation = is_remediation
    return t


def _make_output(
    task_id: str = "task_1",
    status: TaskStatus = TaskStatus.COMPLETED,
    confidence: float = 0.85,
    summary: str = "Built the REST API with CRUD endpoints",
    artifacts: list[str] | None = None,
    issues: list[str] | None = None,
) -> TaskOutput:
    return TaskOutput(
        task_id=task_id,
        status=status,
        summary=summary,
        confidence=confidence,
        artifacts=artifacts or ["src/api/users.py"],
        issues=issues or [],
        cost_usd=0.05,
        turns_used=10,
    )


# ---------------------------------------------------------------------------
# should_reflect tests
# ---------------------------------------------------------------------------


class TestShouldReflect:
    def test_successful_task_below_threshold_should_reflect(self):
        task = _make_task()
        output = _make_output(confidence=0.85)
        from unittest.mock import patch

        with patch("reflexion.REFLEXION_ENABLED", True):
            assert should_reflect(task, output) is True

    def test_successful_task_above_threshold_should_not_reflect(self):
        task = _make_task()
        output = _make_output(confidence=0.99)
        assert should_reflect(task, output) is False

    def test_failed_task_should_not_reflect(self):
        task = _make_task()
        output = _make_output(status=TaskStatus.FAILED, confidence=0.5)
        assert should_reflect(task, output) is False

    def test_remediation_task_should_not_reflect(self):
        task = _make_task(is_remediation=True)
        output = _make_output(confidence=0.85)
        assert should_reflect(task, output) is False

    def test_at_exact_threshold_should_not_reflect(self):
        task = _make_task()
        output = _make_output(confidence=REFLEXION_CONFIDENCE_THRESHOLD)
        assert should_reflect(task, output) is False


# ---------------------------------------------------------------------------
# parse_critique_response tests
# ---------------------------------------------------------------------------


class TestParseCritiqueResponse:
    def test_clean_json_pass(self):
        response = json.dumps(
            {
                "verdict": "pass",
                "issues": [],
                "suggestions": [],
                "confidence_adjustment": 0.0,
            }
        )
        verdict = parse_critique_response(response)
        assert verdict.should_fix is False
        assert verdict.issues == []

    def test_clean_json_needs_fix(self):
        response = json.dumps(
            {
                "verdict": "needs_fix",
                "issues": ["Missing error handling", "No input validation"],
                "suggestions": ["Add try/except", "Add pydantic models"],
                "confidence_adjustment": -0.1,
            }
        )
        verdict = parse_critique_response(response)
        assert verdict.should_fix is True
        assert len(verdict.issues) == 2
        assert len(verdict.suggestions) == 2

    def test_json_in_markdown_fences(self):
        response = '```json\n{"verdict": "pass", "issues": [], "suggestions": []}\n```'
        verdict = parse_critique_response(response)
        assert verdict.should_fix is False

    def test_json_embedded_in_text(self):
        response = (
            "Here is my analysis:\n\n"
            '{"verdict": "needs_fix", "issues": ["Bug in line 42"], "suggestions": ["Fix it"]}\n\n'
            "That is all."
        )
        verdict = parse_critique_response(response)
        assert verdict.should_fix is True
        assert len(verdict.issues) == 1

    def test_invalid_json_defaults_to_pass(self):
        response = "I think everything looks great!"
        verdict = parse_critique_response(response)
        assert verdict.should_fix is False

    def test_needs_fix_with_empty_issues_defaults_to_pass(self):
        """needs_fix verdict but no actual issues = treat as pass."""
        response = json.dumps(
            {
                "verdict": "needs_fix",
                "issues": [],
                "suggestions": [],
            }
        )
        verdict = parse_critique_response(response)
        assert verdict.should_fix is False


# ---------------------------------------------------------------------------
# build_critique_prompt tests
# ---------------------------------------------------------------------------


class TestBuildCritiquePrompt:
    def test_includes_task_goal(self):
        task = _make_task(goal="Build user authentication")
        output = _make_output()
        prompt = build_critique_prompt(task, output)
        assert "Build user authentication" in prompt

    def test_includes_acceptance_criteria(self):
        task = _make_task()
        output = _make_output()
        prompt = build_critique_prompt(task, output)
        assert "API returns 200 on GET /users" in prompt

    def test_includes_output_summary(self):
        task = _make_task()
        output = _make_output(summary="Built CRUD endpoints")
        prompt = build_critique_prompt(task, output)
        assert "Built CRUD endpoints" in prompt

    def test_includes_artifacts(self):
        task = _make_task()
        output = _make_output(artifacts=["src/api.py", "tests/test_api.py"])
        prompt = build_critique_prompt(task, output)
        assert "src/api.py" in prompt

    def test_includes_known_issues(self):
        task = _make_task()
        output = _make_output(issues=["Missing pagination"])
        prompt = build_critique_prompt(task, output)
        assert "Missing pagination" in prompt


# ---------------------------------------------------------------------------
# build_fix_prompt tests
# ---------------------------------------------------------------------------


class TestBuildFixPrompt:
    def test_includes_issues(self):
        verdict = ReflexionVerdict(
            should_fix=True,
            issues=["Missing error handling", "No tests"],
            suggestions=["Add try/except", "Write pytest tests"],
        )
        prompt = build_fix_prompt(verdict)
        assert "Missing error handling" in prompt
        assert "No tests" in prompt

    def test_includes_suggestions(self):
        verdict = ReflexionVerdict(
            should_fix=True,
            issues=["Bug"],
            suggestions=["Fix the bug by checking None"],
        )
        prompt = build_fix_prompt(verdict)
        assert "Fix the bug by checking None" in prompt


# ---------------------------------------------------------------------------
# ReflexionVerdict tests
# ---------------------------------------------------------------------------


class TestReflexionVerdict:
    def test_summary_when_pass(self):
        verdict = ReflexionVerdict(should_fix=False)
        assert "no fixes needed" in verdict.summary()

    def test_summary_when_needs_fix(self):
        verdict = ReflexionVerdict(
            should_fix=True,
            issues=["Bug A", "Bug B"],
            suggestions=["Fix A", "Fix B"],
        )
        summary = verdict.summary()
        assert "2 issue(s)" in summary
