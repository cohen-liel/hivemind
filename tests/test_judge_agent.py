"""Tests for judge_agent module."""

import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import AgentRole, TaskInput, TaskOutput, TaskGraph, TaskStatus
from judge_agent import (
    JudgeAgent,
    JudgeVerdict,
    DimensionScore,
    Dimension,
    DIMENSION_WEIGHTS,
)


def _make_graph() -> TaskGraph:
    tasks = [
        TaskInput(
            id="t1",
            goal="Build the REST API for user authentication with JWT tokens and session management",
            role=AgentRole.BACKEND_DEVELOPER,
            context_from=[],
        ),
        TaskInput(
            id="t2",
            goal="Write unit tests for the authentication API endpoints and middleware",
            role=AgentRole.TEST_ENGINEER,
            context_from=["t1"],
        ),
    ]
    return TaskGraph(
        project_id="test-project",
        user_message="Build user authentication system with tests",
        vision="Build a secure user authentication system",
        tasks=tasks,
    )


def _make_results() -> dict[str, TaskOutput]:
    return {
        "t1": TaskOutput(
            task_id="t1",
            status=TaskStatus.COMPLETED,
            summary="Built REST API with JWT auth",
            files_modified=["src/auth.py", "src/routes.py"],
            issues=[],
        ),
        "t2": TaskOutput(
            task_id="t2",
            status=TaskStatus.COMPLETED,
            summary="Wrote 15 unit tests, all passing",
            files_modified=["tests/test_auth.py"],
            issues=[],
        ),
    }


# ── Build task summary tests ────────────────────────────────────────────────

class TestBuildTaskSummary:
    def test_summary_includes_all_tasks(self):
        judge = JudgeAgent()
        graph = _make_graph()
        results = _make_results()
        summary = judge._build_task_summary(graph, results)
        assert "t1" in summary
        assert "t2" in summary
        assert "COMPLETED" in summary

    def test_summary_handles_missing_results(self):
        judge = JudgeAgent()
        graph = _make_graph()
        results = {}  # no results
        summary = judge._build_task_summary(graph, results)
        assert "NOT EXECUTED" in summary

    def test_summary_shows_failed_tasks(self):
        judge = JudgeAgent()
        graph = _make_graph()
        results = {
            "t1": TaskOutput(
                task_id="t1",
                status=TaskStatus.FAILED,
                summary="Build failed due to syntax error",
                files_modified=[],
                issues=["SyntaxError in auth.py"],
            ),
        }
        summary = judge._build_task_summary(graph, results)
        assert "FAILED" in summary
        assert "SyntaxError" in summary


# ── Parse evaluation tests ───────────────────────────────────────────────────

class TestParseEvaluation:
    def test_parse_well_formatted_response(self):
        judge = JudgeAgent()
        text = (
            "DIMENSION: correctness\n"
            "SCORE: 8\n"
            "REASONING: Code is correct and handles edge cases\n"
            "SUGGESTIONS: Add input validation\n"
            "DIMENSION: completeness\n"
            "SCORE: 7\n"
            "REASONING: Most features implemented\n"
            "SUGGESTIONS: Add password reset\n"
            "DIMENSION: security\n"
            "SCORE: 9\n"
            "REASONING: Good JWT implementation\n"
            "SUGGESTIONS: Add rate limiting\n"
            "DIMENSION: performance\n"
            "SCORE: 7\n"
            "REASONING: Acceptable performance\n"
            "SUGGESTIONS: Add caching\n"
            "DIMENSION: maintainability\n"
            "SCORE: 8\n"
            "REASONING: Clean code structure\n"
            "SUGGESTIONS: Add more comments\n"
            "DIMENSION: style\n"
            "SCORE: 8\n"
            "REASONING: Consistent style\n"
            "SUGGESTIONS: None\n"
            "OVERALL: Good work overall\n"
            "CRITICAL: NONE"
        )
        verdict = judge._parse_evaluation(text, "summary")
        assert verdict.passed is True
        assert verdict.weighted_score > 6.0
        assert len(verdict.dimension_scores) == 6
        assert verdict.critical_issues == []

    def test_parse_low_scores_fails(self):
        judge = JudgeAgent(pass_threshold=6.0)
        text = (
            "DIMENSION: correctness\n"
            "SCORE: 2\n"
            "REASONING: Many bugs\n"
            "SUGGESTIONS: Fix bugs\n"
            "DIMENSION: completeness\n"
            "SCORE: 3\n"
            "REASONING: Missing features\n"
            "SUGGESTIONS: Add features\n"
            "DIMENSION: security\n"
            "SCORE: 1\n"
            "REASONING: SQL injection vulnerability\n"
            "SUGGESTIONS: Use parameterized queries\n"
            "DIMENSION: performance\n"
            "SCORE: 2\n"
            "REASONING: Very slow\n"
            "SUGGESTIONS: Optimize queries\n"
            "DIMENSION: maintainability\n"
            "SCORE: 3\n"
            "REASONING: Spaghetti code\n"
            "SUGGESTIONS: Refactor\n"
            "DIMENSION: style\n"
            "SCORE: 2\n"
            "REASONING: Inconsistent\n"
            "SUGGESTIONS: Use linter\n"
            "OVERALL: Needs significant work\n"
            "CRITICAL: SQL injection, missing auth checks"
        )
        verdict = judge._parse_evaluation(text, "summary")
        assert verdict.passed is False
        assert verdict.weighted_score < 6.0
        assert len(verdict.critical_issues) >= 1

    def test_parse_garbage_defaults_to_neutral(self):
        judge = JudgeAgent()
        text = "This is not a valid evaluation response."
        verdict = judge._parse_evaluation(text, "summary")
        # All dimensions default to 5.0
        assert verdict.weighted_score == pytest.approx(5.0, abs=0.1)

    def test_parse_extracts_critical_issues(self):
        judge = JudgeAgent()
        text = "CRITICAL: SQL injection, XSS vulnerability, missing CSRF protection"
        verdict = judge._parse_evaluation(text, "summary")
        assert len(verdict.critical_issues) == 3
        assert "SQL injection" in verdict.critical_issues[0]

    def test_parse_critical_none(self):
        judge = JudgeAgent()
        text = "CRITICAL: NONE"
        verdict = judge._parse_evaluation(text, "summary")
        assert verdict.critical_issues == []


# ── Extract dimension score tests ────────────────────────────────────────────

class TestExtractDimensionScore:
    def test_extract_from_structured_block(self):
        text = (
            "DIMENSION: correctness\n"
            "SCORE: 8\n"
            "REASONING: Good implementation\n"
            "SUGGESTIONS: Minor improvements\n"
            "OVERALL: Done"
        )
        score = JudgeAgent._extract_dimension_score(text, Dimension.CORRECTNESS)
        assert score.score == 8.0
        assert "Good implementation" in score.reasoning

    def test_extract_from_inline_format(self):
        text = "correctness: 7/10 - decent work"
        score = JudgeAgent._extract_dimension_score(text, Dimension.CORRECTNESS)
        assert score.score == 7.0

    def test_default_when_not_found(self):
        text = "Nothing relevant here"
        score = JudgeAgent._extract_dimension_score(text, Dimension.SECURITY)
        assert score.score == 5.0
        assert "Could not parse" in score.reasoning

    def test_clamps_score_to_range(self):
        text = (
            "DIMENSION: correctness\n"
            "SCORE: 15\n"
            "REASONING: Amazing\n"
            "SUGGESTIONS: None\n"
            "OVERALL: Done"
        )
        score = JudgeAgent._extract_dimension_score(text, Dimension.CORRECTNESS)
        assert score.score == 10.0


# ── Full async evaluation tests ──────────────────────────────────────────────

class TestEvaluateDagResults:
    @pytest.mark.asyncio
    async def test_evaluate_full_flow(self):
        judge = JudgeAgent()
        graph = _make_graph()
        results = _make_results()

        mock_response = MagicMock()
        mock_response.text = (
            "DIMENSION: correctness\nSCORE: 8\nREASONING: Good\nSUGGESTIONS: None\n"
            "DIMENSION: completeness\nSCORE: 7\nREASONING: Good\nSUGGESTIONS: None\n"
            "DIMENSION: security\nSCORE: 8\nREASONING: Good\nSUGGESTIONS: None\n"
            "DIMENSION: performance\nSCORE: 7\nREASONING: Good\nSUGGESTIONS: None\n"
            "DIMENSION: maintainability\nSCORE: 8\nREASONING: Good\nSUGGESTIONS: None\n"
            "DIMENSION: style\nSCORE: 8\nREASONING: Good\nSUGGESTIONS: None\n"
            "OVERALL: Solid work\nCRITICAL: NONE"
        )

        with patch("isolated_query.isolated_query", new_callable=AsyncMock, return_value=mock_response):
            verdict = await judge.evaluate_dag_results(
                graph, results, "/tmp/project", goal="Build auth system"
            )

        assert verdict.passed is True
        assert len(verdict.dimension_scores) == 6
        assert len(judge.history) == 1

    @pytest.mark.asyncio
    async def test_evaluate_handles_none_response(self):
        judge = JudgeAgent()
        graph = _make_graph()
        results = _make_results()

        with patch("isolated_query.isolated_query", new_callable=AsyncMock, return_value=None):
            verdict = await judge.evaluate_dag_results(
                graph, results, "/tmp/project"
            )

        # All scores default to 5.0
        assert verdict.weighted_score == pytest.approx(5.0, abs=0.1)


class TestGetSummary:
    def test_empty_summary(self):
        judge = JudgeAgent()
        s = judge.get_summary()
        assert s["total_evaluations"] == 0

    def test_summary_with_results(self):
        judge = JudgeAgent()
        judge.history.append(JudgeVerdict(
            passed=True,
            weighted_score=7.5,
            dimension_scores=[],
            overall_feedback="Good",
            critical_issues=[],
            task_results_summary="",
        ))
        s = judge.get_summary()
        assert s["total_evaluations"] == 1
        assert s["passed"] == 1
        assert s["average_score"] == 7.5
