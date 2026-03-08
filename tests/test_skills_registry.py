"""Comprehensive tests for skills_registry.py.

Tests are written against the ACTUAL source code which has:
  Constants: SKILL_AGENT_MAP (dict), _skills_cache (dict), _skills_dir (Path|None)
  Functions: scan_skills(), get_skill_content(), list_skills(),
             get_skills_for_agent(), build_skill_prompt(), _find_skills_dir(), _scan_dir()
"""
from pathlib import Path
from unittest.mock import patch

import pytest

import skills_registry


@pytest.fixture(autouse=True)
def reset_skills_cache():
    """Reset the skills cache before and after each test."""
    skills_registry._skills_cache.clear()
    saved_dir = skills_registry._skills_dir
    yield
    skills_registry._skills_cache.clear()
    skills_registry._skills_dir = saved_dir


# ════════════════════════════════════════════════════════════════════
#  1. Module-level constants
# ════════════════════════════════════════════════════════════════════


class TestModuleConstants:
    """Verify module-level data structures exist with correct types."""

    def test_skill_agent_map_is_dict(self):
        assert isinstance(skills_registry.SKILL_AGENT_MAP, dict)

    def test_skill_agent_map_has_known_entries(self):
        """Check that the expected skill→agent mappings are present."""
        m = skills_registry.SKILL_AGENT_MAP
        assert m.get("frontend-design") == "developer"
        assert m.get("claude-api") == "developer"
        assert m.get("webapp-testing") == "tester"

    def test_skill_agent_map_values_are_valid_roles(self):
        valid_roles = {"developer", "reviewer", "tester", "devops", "orchestrator", "researcher"}
        for skill, role in skills_registry.SKILL_AGENT_MAP.items():
            assert role in valid_roles, f"Skill '{skill}' mapped to invalid role '{role}'"

    def test_skills_cache_is_dict(self):
        assert isinstance(skills_registry._skills_cache, dict)


# ════════════════════════════════════════════════════════════════════
#  2. scan_skills()
# ════════════════════════════════════════════════════════════════════


class TestScanSkills:
    """scan_skills() scans .claude/skills/*/SKILL.md and returns {name: content}."""

    def test_returns_dict(self):
        result = skills_registry.scan_skills()
        assert isinstance(result, dict)

    def test_clears_cache_before_scanning(self):
        """scan_skills() should clear the cache, even if there's stale data."""
        skills_registry._skills_cache["stale"] = "old data"
        skills_registry.scan_skills()
        assert "stale" not in skills_registry._skills_cache

    def test_scan_with_nonexistent_extra_dirs(self):
        """Passing nonexistent extra dirs should not crash."""
        result = skills_registry.scan_skills(extra_dirs=["/nonexistent/path/xyz"])
        assert isinstance(result, dict)

    def test_scan_populates_cache(self):
        """After scan, _skills_cache should match the returned dict."""
        result = skills_registry.scan_skills()
        assert skills_registry._skills_cache == result

    def test_scan_with_temp_skill(self, tmp_path):
        """Scan a temp directory with a fake skill and verify it's loaded."""
        # Create a fake .claude/skills/test-skill/SKILL.md
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test Skill\nThis is a test.")

        result = skills_registry.scan_skills(extra_dirs=[str(tmp_path)])
        assert "test-skill" in result
        assert "This is a test." in result["test-skill"]


# ════════════════════════════════════════════════════════════════════
#  3. list_skills()
# ════════════════════════════════════════════════════════════════════


class TestListSkills:
    """list_skills() returns all available skill names from cache."""

    def test_returns_list(self):
        result = skills_registry.list_skills()
        assert isinstance(result, list)

    def test_matches_cache_keys(self):
        """list_skills() should return exactly the keys in _skills_cache."""
        skills_registry._skills_cache["alpha"] = "content-a"
        skills_registry._skills_cache["beta"] = "content-b"
        result = skills_registry.list_skills()
        assert set(result) == {"alpha", "beta"}


# ════════════════════════════════════════════════════════════════════
#  4. get_skill_content()
# ════════════════════════════════════════════════════════════════════


class TestGetSkillContent:
    """get_skill_content(name) returns SKILL.md content or None."""

    def test_returns_none_for_unknown_skill(self):
        skills_registry._skills_cache.clear()
        skills_registry._skills_cache["known"] = "data"
        assert skills_registry.get_skill_content("unknown") is None

    def test_returns_content_for_known_skill(self):
        skills_registry._skills_cache["my-skill"] = "# My Skill Content"
        result = skills_registry.get_skill_content("my-skill")
        assert result == "# My Skill Content"


# ════════════════════════════════════════════════════════════════════
#  5. get_skills_for_agent()
# ════════════════════════════════════════════════════════════════════


class TestGetSkillsForAgent:
    """get_skills_for_agent(role) returns skills auto-mapped to that agent role."""

    def test_returns_list(self):
        result = skills_registry.get_skills_for_agent("developer")
        assert isinstance(result, list)

    def test_returns_only_cached_skills(self):
        """Should only return skills that are both in SKILL_AGENT_MAP AND in cache."""
        skills_registry._skills_cache.clear()
        # "frontend-design" is mapped to "developer" in SKILL_AGENT_MAP
        # but only include it in results if it's also in the cache
        skills_registry._skills_cache["frontend-design"] = "content"
        result = skills_registry.get_skills_for_agent("developer")
        assert "frontend-design" in result

    def test_no_results_for_unknown_role(self):
        """A role not in SKILL_AGENT_MAP should return empty."""
        result = skills_registry.get_skills_for_agent("nonexistent-role")
        assert result == []

    def test_tester_gets_webapp_testing(self):
        """webapp-testing is mapped to tester."""
        skills_registry._skills_cache["webapp-testing"] = "testing content"
        result = skills_registry.get_skills_for_agent("tester")
        assert "webapp-testing" in result


# ════════════════════════════════════════════════════════════════════
#  6. build_skill_prompt()
# ════════════════════════════════════════════════════════════════════


class TestBuildSkillPrompt:
    """build_skill_prompt(names) builds prompt text from cached skills."""

    def test_returns_empty_string_for_unknown_skills(self):
        skills_registry._skills_cache.clear()
        result = skills_registry.build_skill_prompt(["nonexistent"])
        assert result == ""

    def test_returns_empty_string_for_empty_list(self):
        result = skills_registry.build_skill_prompt([])
        assert result == ""

    def test_includes_skill_name_in_output(self):
        skills_registry._skills_cache["my-skill"] = "# Skill Content Here"
        result = skills_registry.build_skill_prompt(["my-skill"])
        assert "my-skill" in result
        assert "Skill Content Here" in result

    def test_includes_available_skills_header(self):
        skills_registry._skills_cache["test-sk"] = "content"
        result = skills_registry.build_skill_prompt(["test-sk"])
        assert "AVAILABLE SKILLS" in result

    def test_truncates_long_skill_content(self):
        """Skills longer than 4000 chars should be truncated."""
        long_content = "x" * 5000
        skills_registry._skills_cache["long-skill"] = long_content
        result = skills_registry.build_skill_prompt(["long-skill"])
        assert "truncated" in result
        # The output should contain at most 4000 chars of content
        assert "x" * 4000 in result
        assert "x" * 5000 not in result

    def test_multiple_skills_in_prompt(self):
        skills_registry._skills_cache["skill-a"] = "Content A"
        skills_registry._skills_cache["skill-b"] = "Content B"
        result = skills_registry.build_skill_prompt(["skill-a", "skill-b"])
        assert "skill-a" in result
        assert "skill-b" in result
        assert "Content A" in result
        assert "Content B" in result
