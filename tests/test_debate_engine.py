"""Tests for debate_engine module."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import AgentRole, TaskInput
from debate_engine import (
    CHALLENGER_MAP,
    DEBATE_ELIGIBLE_ROLES,
    DebateEngine,
    DebateResult,
    DebateVerdict,
)


def _make_task(
    task_id: str = "t1",
    role: AgentRole = AgentRole.DATABASE_EXPERT,
    goal: str = "Design the database schema for the user authentication module",
) -> TaskInput:
    return TaskInput(id=task_id, goal=goal, role=role, context_from=[])


# ── should_debate tests ─────────────────────────────────────────────────────


class TestShouldDebate:
    def test_disabled_by_default(self):
        engine = DebateEngine()
        task = _make_task(role=AgentRole.DATABASE_EXPERT)
        assert engine.should_debate(task) is False, "Debate should be off by default"

    @patch.dict(os.environ, {"HIVEMIND_DEBATE_ENABLED": "true"})
    def test_eligible_role_triggers_debate(self):
        engine = DebateEngine()
        for role in DEBATE_ELIGIBLE_ROLES:
            task = _make_task(role=role, goal="Do something simple and routine")
            assert engine.should_debate(task) is True, f"{role} should trigger debate"

    @patch.dict(os.environ, {"HIVEMIND_DEBATE_ENABLED": "true"})
    def test_non_eligible_role_no_debate(self):
        engine = DebateEngine()
        task = _make_task(
            role=AgentRole.FRONTEND_DEVELOPER,
            goal="Fix the CSS padding on the login button",
        )
        assert engine.should_debate(task) is False

    @patch.dict(os.environ, {"HIVEMIND_DEBATE_ENABLED": "true"})
    def test_keyword_triggers_debate(self):
        engine = DebateEngine()
        task = _make_task(
            role=AgentRole.FRONTEND_DEVELOPER,
            goal="Plan the security architecture for the app",
        )
        assert engine.should_debate(task) is True

    @patch.dict(os.environ, {"HIVEMIND_DEBATE_ENABLED": "true"})
    def test_strict_keywords_no_false_positives(self):
        engine = DebateEngine()
        task = _make_task(
            role=AgentRole.FRONTEND_DEVELOPER,
            goal="Add auth token to the login form",
        )
        # Single words like "auth" no longer trigger debates
        assert engine.should_debate(task) is False

    @patch.dict(os.environ, {"HIVEMIND_DEBATE_ENABLED": "true"})
    def test_no_keyword_no_debate(self):
        engine = DebateEngine()
        task = _make_task(
            role=AgentRole.FRONTEND_DEVELOPER,
            goal="Fix the CSS padding on the login button",
        )
        assert engine.should_debate(task) is False


class TestGetChallengerRole:
    def test_known_mapping(self):
        engine = DebateEngine()
        for role, challenger in CHALLENGER_MAP.items():
            task = _make_task(role=role)
            assert engine.get_challenger_role(task) == challenger

    def test_unknown_role_defaults_to_reviewer(self):
        engine = DebateEngine()
        task = _make_task(role=AgentRole.TEST_ENGINEER)
        assert engine.get_challenger_role(task) == AgentRole.REVIEWER


class TestParseVerdict:
    def test_parse_original(self):
        text = "VERDICT: original\nREASONING: The proposer had a better approach.\nAPPROACH: Use PostgreSQL with proper indexing."
        v, r, a = DebateEngine._parse_verdict(text)
        assert v == DebateVerdict.ORIGINAL
        assert "better approach" in r
        assert "PostgreSQL" in a

    def test_parse_challenger(self):
        text = "VERDICT: challenger\nREASONING: The challenger identified critical flaws.\nAPPROACH: Use MongoDB instead."
        v, _r, _a = DebateEngine._parse_verdict(text)
        assert v == DebateVerdict.CHALLENGER

    def test_parse_merged(self):
        text = (
            "VERDICT: merged\nREASONING: Both had good points.\nAPPROACH: Combine both approaches."
        )
        v, _r, _a = DebateEngine._parse_verdict(text)
        assert v == DebateVerdict.MERGED

    def test_parse_garbage_defaults_to_merged(self):
        text = "I think both are fine."
        v, _r, _a = DebateEngine._parse_verdict(text)
        assert v == DebateVerdict.MERGED

    def test_parse_case_insensitive(self):
        text = "VERDICT: Original\nREASONING: Good.\nAPPROACH: Do it."
        v, _r, _a = DebateEngine._parse_verdict(text)
        assert v == DebateVerdict.ORIGINAL


class TestBuildDebateContext:
    def test_context_includes_verdict(self):
        engine = DebateEngine()
        result = DebateResult(
            task_id="t1",
            proposer_role=AgentRole.DATABASE_EXPERT,
            challenger_role=AgentRole.BACKEND_DEVELOPER,
            rounds=[],
            verdict=DebateVerdict.MERGED,
            verdict_reasoning="Both approaches have merit",
            merged_approach="Use PostgreSQL with Redis caching",
        )
        ctx = engine.build_debate_context(result)
        assert "merged" in ctx.lower()
        assert "Both approaches have merit" in ctx
        assert "PostgreSQL with Redis" in ctx

    def test_context_without_merged_approach(self):
        engine = DebateEngine()
        result = DebateResult(
            task_id="t1",
            proposer_role=AgentRole.DATABASE_EXPERT,
            challenger_role=AgentRole.BACKEND_DEVELOPER,
            rounds=[],
            verdict=DebateVerdict.ORIGINAL,
            verdict_reasoning="Proposer was right",
            merged_approach="",
        )
        ctx = engine.build_debate_context(result)
        assert "original" in ctx.lower()
        assert "Recommended approach" not in ctx


class TestRunDebate:
    @pytest.mark.asyncio
    async def test_run_debate_full_flow(self):
        engine = DebateEngine(max_rounds=1)
        task = _make_task()

        mock_response = MagicMock(num_turns=2)
        mock_response.text = (
            "VERDICT: original\nREASONING: Good approach.\nAPPROACH: Use PostgreSQL."
        )

        mock_sdk = MagicMock()
        with patch(
            "isolated_query.isolated_query", new_callable=AsyncMock, return_value=mock_response
        ):
            with patch(
                "config.SPECIALIST_PROMPTS",
                {
                    "database_expert": "You are a DB expert",
                    "backend_developer": "You are a backend dev",
                },
            ):
                with patch("config.get_agent_turns", return_value=10):
                    result = await engine.run_debate(task, "/tmp/project", sdk=mock_sdk)

        assert result.task_id == "t1"
        assert len(result.rounds) == 1
        assert result.cost_turns == 6  # 2 turns x 3 calls
        assert len(engine.history) == 1

    @pytest.mark.asyncio
    async def test_run_debate_multiple_rounds(self):
        engine = DebateEngine(max_rounds=2)
        task = _make_task()

        mock_response = MagicMock(num_turns=1)
        mock_response.text = "VERDICT: merged\nREASONING: Both good.\nAPPROACH: Combine."

        mock_sdk = MagicMock()
        with patch(
            "isolated_query.isolated_query", new_callable=AsyncMock, return_value=mock_response
        ):
            with patch("config.SPECIALIST_PROMPTS", {}):
                with patch("config.get_agent_turns", return_value=10):
                    result = await engine.run_debate(task, "/tmp/project", sdk=mock_sdk)

        assert len(result.rounds) == 2
        assert result.cost_turns == 5  # 1 turn x 5 calls (2 rounds x 2 + judge)

    @pytest.mark.asyncio
    async def test_run_debate_handles_none_response(self):
        engine = DebateEngine(max_rounds=1)
        task = _make_task()

        mock_sdk = MagicMock()
        with patch("isolated_query.isolated_query", new_callable=AsyncMock, return_value=None):
            with patch("config.SPECIALIST_PROMPTS", {}):
                with patch("config.get_agent_turns", return_value=10):
                    result = await engine.run_debate(task, "/tmp/project", sdk=mock_sdk)

        assert result.verdict == DebateVerdict.MERGED  # default when no text
        assert result.cost_turns == 0


class TestGetSummary:
    def test_empty_summary(self):
        engine = DebateEngine()
        s = engine.get_summary()
        assert s["total_debates"] == 0
        assert s["total_turns_used"] == 0

    def test_summary_with_results(self):
        engine = DebateEngine()
        engine.history.append(
            DebateResult(
                task_id="t1",
                proposer_role=AgentRole.DATABASE_EXPERT,
                challenger_role=AgentRole.BACKEND_DEVELOPER,
                rounds=[],
                verdict=DebateVerdict.ORIGINAL,
                verdict_reasoning="",
                merged_approach="",
                cost_turns=10,
            )
        )
        s = engine.get_summary()
        assert s["total_debates"] == 1
        assert s["verdicts"]["original"] == 1
        assert s["total_turns_used"] == 10
