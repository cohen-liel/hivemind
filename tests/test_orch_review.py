"""Unit tests for orch_review.build_review_prompt hints format."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdk_client import SDKResponse


def _make_mgr(**overrides):
    """Create a minimal mock OrchestratorManager for build_review_prompt."""
    mgr = MagicMock()
    mgr.total_cost_usd = overrides.get("total_cost_usd", 0.05)
    mgr._effective_budget = overrides.get("budget", 10.0)
    mgr._current_loop = overrides.get("loop", 2)
    mgr._agents_used = overrides.get("agents_used", set())
    mgr._stuck_escalation_hint = ""
    mgr.project_dir = "/tmp/test-project"
    mgr.shared_context = []
    mgr._completed_rounds = []
    return mgr


def _ok_response(text="## SUMMARY\nAll good."):
    return SDKResponse(text=text, cost_usd=0.01, num_turns=1)


def _error_response(msg="timeout"):
    return SDKResponse(text="", is_error=True, error_message=msg, cost_usd=0.0)


class TestBuildReviewPromptHints:
    """Verify that build_review_prompt produces <hints> for actionable issues."""

    @pytest.mark.asyncio
    async def test_failed_agent_produces_hint(self):
        """A failed agent should appear in <hints> with its error."""
        mgr = _make_mgr()
        results = {"developer": [_error_response("timeout after 120s")]}

        with patch(
            "orch_review.detect_file_changes",
            new_callable=AsyncMock,
            return_value="(no file changes detected)",
        ):
            from orch_review import build_review_prompt

            prompt = await build_review_prompt(mgr, results)

        assert "<hints>" in prompt
        assert "developer FAILED" in prompt
        assert "timeout" in prompt

    @pytest.mark.asyncio
    async def test_successful_round_no_hints(self):
        """When all agents succeed and code is reviewed+tested, no hints needed."""
        mgr = _make_mgr(agents_used={"developer", "reviewer", "tester"})
        results = {
            "developer": [_ok_response("## FILES CHANGED\n- src/app.py\n## SUMMARY\nDone.")],
            "reviewer": [_ok_response("## SUMMARY\nCode looks good.")],
            "tester": [_ok_response("## SUMMARY\nAll tests pass.")],
        }

        with patch(
            "orch_review.detect_file_changes",
            new_callable=AsyncMock,
            return_value="src/app.py | 5 +",
        ):
            from orch_review import build_review_prompt

            prompt = await build_review_prompt(mgr, results)

        assert "<hints>" not in prompt
        assert "<decision>" in prompt

    @pytest.mark.asyncio
    async def test_missing_review_produces_hint(self):
        """Code changed without review should produce a 'missing review' hint."""
        mgr = _make_mgr()
        results = {
            "developer": [_ok_response("## FILES CHANGED\n- src/app.py\n## SUMMARY\nDone.")],
        }

        with patch(
            "orch_review.detect_file_changes",
            new_callable=AsyncMock,
            return_value="src/app.py | 5 +",
        ):
            from orch_review import build_review_prompt

            prompt = await build_review_prompt(mgr, results)

        assert "<hints>" in prompt
        assert "code review" in prompt

    @pytest.mark.asyncio
    async def test_hints_are_concise(self):
        """Hints should not contain full <delegate> JSON blocks with agent/task JSON."""
        mgr = _make_mgr()
        results = {"developer": [_error_response("some error")]}

        with patch(
            "orch_review.detect_file_changes",
            new_callable=AsyncMock,
            return_value="(no file changes detected)",
        ):
            from orch_review import build_review_prompt

            prompt = await build_review_prompt(mgr, results)

        assert "</delegate>" not in prompt
        assert '"agent":' not in prompt
