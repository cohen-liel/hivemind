"""Tests for the 5 new features from code review:
1. Architect Agent
2. Cross-Project Memory
3. Active Escalation
4. Dynamic Skill Discovery
5. Human-in-the-loop Nudge
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════════════════════════════════════════
# 1. Architect Agent Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestArchitectAgent:
    """Tests for architect_agent.py"""

    def test_architecture_brief_model(self):
        """ArchitectureBrief can be created with defaults."""
        from architect_agent import ArchitectureBrief

        brief = ArchitectureBrief(project_id="test-123")
        assert brief.project_id == "test-123"
        assert brief.codebase_summary == ""
        assert brief.tech_stack == {}
        assert brief.architecture_patterns == []
        assert brief.key_files == {}
        assert brief.constraints == []
        assert brief.risks == []
        assert brief.recommended_approach == ""
        assert brief.parallelism_hints == []

    def test_architecture_brief_with_data(self):
        """ArchitectureBrief stores data correctly."""
        from architect_agent import ArchitectureBrief

        brief = ArchitectureBrief(
            project_id="proj-1",
            codebase_summary="A FastAPI backend with React frontend",
            tech_stack={"backend": "FastAPI", "frontend": "React"},
            architecture_patterns=["MVC", "Event-driven"],
            key_files={"src/main.py": "Entry point"},
            constraints=["Do not modify shared DB schema"],
            risks=["Circular dependency between auth and user"],
            recommended_approach="Start with backend API",
            parallelism_hints=["Frontend and backend can run in parallel"],
        )
        assert brief.tech_stack["backend"] == "FastAPI"
        assert len(brief.risks) == 1
        assert "parallel" in brief.parallelism_hints[0]

    def test_parse_architect_response_valid_json(self):
        """Parser handles valid JSON response."""
        from architect_agent import _parse_architect_response

        raw = json.dumps(
            {
                "codebase_summary": "Test project",
                "tech_stack": {"lang": "Python"},
                "architecture_patterns": ["Monolith"],
                "key_files": {},
                "constraints": [],
                "risks": ["No tests"],
                "recommended_approach": "Add tests first",
                "parallelism_hints": [],
            }
        )
        brief = _parse_architect_response(raw, "test-proj")
        assert brief.codebase_summary == "Test project"
        assert brief.project_id == "test-proj"
        assert "No tests" in brief.risks

    def test_parse_architect_response_json_in_markdown(self):
        """Parser extracts JSON from markdown code blocks."""
        from architect_agent import _parse_architect_response

        raw = (
            "Here's my analysis:\n```json\n"
            + json.dumps(
                {
                    "codebase_summary": "Markdown wrapped",
                    "tech_stack": {},
                }
            )
            + "\n```\nDone."
        )
        brief = _parse_architect_response(raw, "test")
        assert brief.codebase_summary == "Markdown wrapped"

    def test_parse_architect_response_invalid(self):
        """Parser returns empty brief for invalid response."""
        from architect_agent import _parse_architect_response

        brief = _parse_architect_response("This is not JSON at all", "test")
        assert brief.project_id == "test"
        assert brief.codebase_summary == ""

    def test_should_run_architect_epic(self):
        """Architect should run for EPIC tasks."""
        from architect_agent import should_run_architect

        assert (
            should_run_architect(
                "Build a complete e-commerce platform from scratch", has_memory=True
            )
            is True
        )

    def test_should_run_architect_simple(self):
        """Architect should NOT run for SIMPLE tasks."""
        from architect_agent import should_run_architect

        assert should_run_architect("Fix the typo in README", has_memory=True) is False

    def test_should_run_architect_large_no_memory(self):
        """Architect should run for LARGE tasks when no memory exists."""
        from architect_agent import should_run_architect

        assert should_run_architect("Add authentication system with JWT", has_memory=False) is True

    def test_should_run_architect_large_with_memory(self):
        """Architect should NOT run for LARGE tasks when memory exists."""
        from architect_agent import should_run_architect

        assert should_run_architect("Add authentication system with JWT", has_memory=True) is False

    def test_build_architect_prompt(self):
        """Prompt builder includes all components."""
        from architect_agent import _build_architect_prompt

        prompt = _build_architect_prompt(
            project_id="p1",
            project_dir="/tmp/test",
            user_task="Build auth system",
            memory_snapshot={"tech": "FastAPI"},
        )
        assert "<project_id>p1</project_id>" in prompt
        assert "<project_dir>/tmp/test</project_dir>" in prompt
        assert "Build auth system" in prompt
        assert "FastAPI" in prompt


# ═══════════════════════════════════════════════════════════════════════════
# 2. Cross-Project Memory Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossProjectMemory:
    """Tests for cross_project_memory.py"""

    def test_create_memory_store(self, tmp_path):
        """Memory store creates correctly."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        assert mem.stats["lessons"] == 0
        assert mem.stats["tech_patterns"] == 0
        assert mem.stats["conventions"] == 0

    def test_add_and_get_lesson(self, tmp_path):
        """Can add and retrieve lessons."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        mem.add_lesson(
            project_id="proj-1",
            category="docker",
            lesson="Always use multi-stage builds for Python",
            tech_stack=["python", "docker"],
            severity="info",
        )
        assert mem.stats["lessons"] == 1

        lessons = mem.get_lessons(category="docker")
        assert len(lessons) == 1
        assert "multi-stage" in lessons[0]["lesson"]

    def test_get_lessons_by_tech_stack(self, tmp_path):
        """Lessons can be filtered by tech stack."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        mem.add_lesson("p1", "config", "Use .env for FastAPI config", ["fastapi", "python"])
        mem.add_lesson("p2", "config", "Use next.config.js for Next.js", ["nextjs", "react"])
        mem.add_lesson("p3", "config", "Use tsconfig for TypeScript", ["typescript", "react"])

        # Filter by FastAPI
        lessons = mem.get_lessons(tech_stack=["fastapi"])
        assert len(lessons) == 1
        assert "FastAPI" in lessons[0]["lesson"]

        # Filter by React (should match 2)
        lessons = mem.get_lessons(tech_stack=["react"])
        assert len(lessons) == 2

    def test_lesson_fifo_limit(self, tmp_path):
        """Lessons are capped at 200 (FIFO)."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        for i in range(210):
            mem.add_lesson("p1", "test", f"Lesson {i}")

        assert mem.stats["lessons"] == 200

    def test_record_and_get_tech_pattern(self, tmp_path):
        """Can record and retrieve tech patterns."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        mem.record_tech_pattern(
            pattern_key="fastapi-docker",
            description="FastAPI needs specific Dockerfile config",
            config_snippet="FROM python:3.11-slim\nWORKDIR /app",
            project_id="proj-1",
        )
        assert mem.stats["tech_patterns"] == 1

        patterns = mem.get_tech_patterns(keywords=["fastapi"])
        assert "fastapi-docker" in patterns

    def test_set_and_get_convention(self, tmp_path):
        """Can set and get conventions."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        mem.set_convention("python_naming", "Use snake_case for all Python files")
        conventions = mem.get_conventions()
        assert conventions["python_naming"] == "Use snake_case for all Python files"

    def test_persistence(self, tmp_path):
        """Memory persists across instances."""
        from cross_project_memory import CrossProjectMemory

        mem1 = CrossProjectMemory(tmp_path)
        mem1.add_lesson("p1", "test", "Persistent lesson", ["python"])
        mem1.set_convention("style", "Use black formatter")

        # Create new instance from same directory
        mem2 = CrossProjectMemory(tmp_path)
        assert mem2.stats["lessons"] == 1
        assert mem2.get_conventions()["style"] == "Use black formatter"

    def test_build_context_for_task(self, tmp_path):
        """Context builder produces formatted output."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        mem.add_lesson("p1", "docker", "Use multi-stage builds", ["docker", "python"])
        mem.record_tech_pattern("docker-python", "Python Docker best practices")
        mem.set_convention("testing", "Always write tests first")

        context = mem.build_context_for_task(
            task="Build a Docker deployment",
            tech_stack=["docker", "python"],
        )
        assert "<cross_project_lessons>" in context
        assert "<cross_project_conventions>" in context

    def test_build_context_empty(self, tmp_path):
        """Context builder returns empty string when no knowledge."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        context = mem.build_context_for_task("Some task")
        assert context == ""

    def test_extract_lessons_from_outputs(self, tmp_path):
        """Auto-extraction finds lessons from task outputs."""
        from cross_project_memory import CrossProjectMemory

        mem = CrossProjectMemory(tmp_path)
        outputs = [
            {"status": "failed", "summary": "Docker build failed due to missing dependency"},
            {"status": "success", "summary": "Tests passed", "issues": ["config version mismatch"]},
        ]
        count = mem.extract_lessons_from_outputs("p1", outputs, ["docker"])
        assert count >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. Active Escalation Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestActiveEscalation:
    """Tests for active_escalation.py"""

    def _make_mock_mgr(self):
        """Create a mock OrchestratorManager."""
        mgr = MagicMock()
        mgr.project_id = "test-proj"
        mgr.current_agent = "frontend_developer"
        mgr._agents_used = {"frontend_developer"}
        mgr._escalation_counts = {}
        mgr._notify = AsyncMock()
        mgr._emit_event = AsyncMock()
        mgr.session_mgr = MagicMock()
        mgr.session_mgr.invalidate_session = AsyncMock()
        mgr.user_id = 1
        mgr.shared_context = []
        mgr.inject_user_message = AsyncMock()
        return mgr

    def test_decide_escalation_reassign(self):
        """Circular delegation triggers reassignment."""
        from active_escalation import EscalationAction, decide_escalation

        mgr = self._make_mock_mgr()
        signal = {
            "signal": "circular_delegations",
            "severity": "warning",
            "strategy": "change_agents",
            "details": "Same pattern repeated",
        }
        action = decide_escalation(mgr, signal)
        assert action.action == EscalationAction.REASSIGN
        assert action.new_agent is not None

    def test_decide_escalation_simplify(self):
        """Repeated errors trigger simplification."""
        from active_escalation import EscalationAction, decide_escalation

        mgr = self._make_mock_mgr()
        signal = {
            "signal": "repeated_errors",
            "severity": "critical",
            "strategy": "simplify_task",
            "details": "Same error 3 times",
        }
        action = decide_escalation(mgr, signal)
        assert action.action == EscalationAction.SIMPLIFY

    def test_decide_escalation_kill_respawn(self):
        """No file progress triggers kill and respawn."""
        from active_escalation import EscalationAction, decide_escalation

        mgr = self._make_mock_mgr()
        signal = {
            "signal": "no_file_progress",
            "severity": "warning",
            "strategy": "force_implementation",
            "details": "No file changes",
        }
        action = decide_escalation(mgr, signal)
        assert action.action == EscalationAction.KILL_RESPAWN

    def test_decide_escalation_notify_after_max_retries(self):
        """After max retries, escalation notifies user."""
        from active_escalation import MAX_ESCALATION_RETRIES, EscalationAction, decide_escalation

        mgr = self._make_mock_mgr()
        mgr._escalation_counts = {"frontend_developer": MAX_ESCALATION_RETRIES}
        signal = {
            "signal": "text_similarity",
            "severity": "critical",
            "strategy": "change_approach",
            "details": "Stuck",
        }
        action = decide_escalation(mgr, signal)
        assert action.action == EscalationAction.NOTIFY_USER

    @pytest.mark.asyncio
    async def test_execute_escalation_reassign(self):
        """Execute reassign escalation."""
        from active_escalation import EscalationAction, execute_escalation

        mgr = self._make_mock_mgr()
        action = EscalationAction(
            action=EscalationAction.REASSIGN,
            agent_role="frontend_developer",
            reason="Stuck",
            new_agent="backend_developer",
        )
        result = await execute_escalation(mgr, action, "Build login page")
        assert result is True
        mgr._notify.assert_called_once()
        mgr._emit_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_escalation_notify_user(self):
        """Execute notify_user escalation returns False."""
        from active_escalation import EscalationAction, execute_escalation

        mgr = self._make_mock_mgr()
        action = EscalationAction(
            action=EscalationAction.NOTIFY_USER,
            agent_role="frontend_developer",
            reason="Cannot recover",
        )
        result = await execute_escalation(mgr, action)
        assert result is False

    def test_simplify_task(self):
        """Task simplification adds instructions."""
        from active_escalation import _simplify_task

        simplified = _simplify_task("Build a complex auth system with OAuth2 and JWT")
        assert "[SIMPLIFIED" in simplified
        assert "MINIMUM viable" in simplified
        assert "Build a complex auth system" in simplified

    def test_escalation_action_repr(self):
        """EscalationAction has useful repr."""
        from active_escalation import EscalationAction

        action = EscalationAction(
            action=EscalationAction.REASSIGN,
            agent_role="frontend_developer",
            reason="test",
        )
        assert "REASSIGN" in repr(action) or "reassign" in repr(action)

    def test_init_escalation_tracking(self):
        """init_escalation_tracking sets up the dict."""
        from active_escalation import init_escalation_tracking

        mgr = MagicMock(spec=[])  # No attributes
        init_escalation_tracking(mgr)
        assert hasattr(mgr, "_escalation_counts")
        assert mgr._escalation_counts == {}

    def test_agent_fallback_map_coverage(self):
        """All main agent roles have fallbacks."""
        from active_escalation import AGENT_FALLBACK_MAP

        expected_roles = [
            "frontend_developer",
            "backend_developer",
            "database_expert",
            "test_engineer",
            "security_auditor",
            "reviewer",
            "researcher",
        ]
        for role in expected_roles:
            assert role in AGENT_FALLBACK_MAP, f"Missing fallback for {role}"
            assert len(AGENT_FALLBACK_MAP[role]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Nudge Endpoint Tests (API)
# ═══════════════════════════════════════════════════════════════════════════


class TestNudgeEndpoint:
    """Tests for the /nudge/{agent} API endpoint."""

    def test_nudge_request_model_valid(self):
        """NudgeRequest accepts valid data."""
        # Import from the patched api module
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))
        from dashboard.api import NudgeRequest

        req = NudgeRequest(message="Change the color to blue", priority="normal")
        assert req.message == "Change the color to blue"
        assert req.priority == "normal"

    def test_nudge_request_model_high_priority(self):
        """NudgeRequest accepts high priority."""
        from dashboard.api import NudgeRequest

        req = NudgeRequest(message="URGENT: Stop modifying auth.py", priority="high")
        assert req.priority == "high"

    def test_nudge_request_model_empty_message(self):
        """NudgeRequest rejects empty message."""
        from dashboard.api import NudgeRequest

        with pytest.raises(Exception):
            NudgeRequest(message="", priority="normal")

    def test_nudge_request_model_invalid_priority(self):
        """NudgeRequest normalizes invalid priority to 'normal'."""
        from dashboard.api import NudgeRequest

        req = NudgeRequest(message="Test", priority="invalid")
        assert req.priority == "normal"
