"""Comprehensive tests for config.py — application configuration.

Tests are written against the ACTUAL config.py which has these attributes
(NOT 'CLAUDE_MODEL' or 'MAX_TURNS' — those don't exist):

  Paths: PROJECTS_BASE_DIR, STORE_DIR, _PROJECT_ROOT
  Ints: MAX_TURNS_PER_CYCLE, AGENT_TIMEOUT_SECONDS, SESSION_TIMEOUT_SECONDS,
        SDK_MAX_RETRIES, SDK_MAX_TURNS_PER_QUERY, SESSION_EXPIRY_HOURS,
        STUCK_WINDOW_SIZE, MAX_ORCHESTRATOR_LOOPS, STALL_ALERT_SECONDS,
        PIPELINE_MAX_STEPS, SCHEDULER_CHECK_INTERVAL, MAX_USER_MESSAGE_LENGTH
  Floats: MAX_BUDGET_USD, SDK_MAX_BUDGET_PER_QUERY, STUCK_SIMILARITY_THRESHOLD,
          RATE_LIMIT_SECONDS, BUDGET_WARNING_THRESHOLD
  Lists: CORS_ORIGINS, DEFAULT_AGENTS
  Dicts: PREDEFINED_PROJECTS, SUB_AGENT_PROMPTS
  Strings: ORCHESTRATOR_SYSTEM_PROMPT, SOLO_AGENT_PROMPT
  Internal: _get() helper function
"""

from pathlib import Path

import config

# ════════════════════════════════════════════════════════════════════
#  1. Integer config values — existence, type, and sanity
# ════════════════════════════════════════════════════════════════════


class TestIntegerConfigs:
    """All integer configs must exist, be int, and be positive."""

    def test_max_turns_per_cycle_type_and_positive(self):
        assert isinstance(config.MAX_TURNS_PER_CYCLE, int)
        assert config.MAX_TURNS_PER_CYCLE > 0

    def test_agent_timeout_seconds(self):
        assert isinstance(config.AGENT_TIMEOUT_SECONDS, int)
        assert config.AGENT_TIMEOUT_SECONDS > 0

    def test_session_timeout_seconds(self):
        assert isinstance(config.SESSION_TIMEOUT_SECONDS, int)
        assert config.SESSION_TIMEOUT_SECONDS > 0

    def test_sdk_max_retries(self):
        assert isinstance(config.SDK_MAX_RETRIES, int)
        assert config.SDK_MAX_RETRIES >= 0

    def test_sdk_max_turns_per_query(self):
        assert isinstance(config.SDK_MAX_TURNS_PER_QUERY, int)
        assert config.SDK_MAX_TURNS_PER_QUERY > 0

    def test_session_expiry_hours(self):
        assert isinstance(config.SESSION_EXPIRY_HOURS, int)
        assert config.SESSION_EXPIRY_HOURS > 0

    def test_stuck_window_size(self):
        assert isinstance(config.STUCK_WINDOW_SIZE, int)
        assert config.STUCK_WINDOW_SIZE >= 2  # needs at least 2 for comparison

    def test_max_orchestrator_loops(self):
        assert isinstance(config.MAX_ORCHESTRATOR_LOOPS, int)
        assert config.MAX_ORCHESTRATOR_LOOPS > 0

    def test_max_user_message_length(self):
        assert isinstance(config.MAX_USER_MESSAGE_LENGTH, int)
        assert config.MAX_USER_MESSAGE_LENGTH > 0

    def test_pipeline_max_steps(self):
        assert isinstance(config.PIPELINE_MAX_STEPS, int)
        assert config.PIPELINE_MAX_STEPS > 0

    def test_scheduler_check_interval(self):
        assert isinstance(config.SCHEDULER_CHECK_INTERVAL, int)
        assert config.SCHEDULER_CHECK_INTERVAL > 0

    def test_stall_alert_seconds(self):
        assert isinstance(config.STALL_ALERT_SECONDS, int)
        assert config.STALL_ALERT_SECONDS > 0


# ════════════════════════════════════════════════════════════════════
#  2. Float config values
# ════════════════════════════════════════════════════════════════════


class TestFloatConfigs:
    """All float configs must exist, be float, and be positive."""

    def test_max_budget_usd(self):
        assert isinstance(config.MAX_BUDGET_USD, float)
        assert config.MAX_BUDGET_USD > 0

    def test_sdk_max_budget_per_query(self):
        assert isinstance(config.SDK_MAX_BUDGET_PER_QUERY, float)
        assert config.SDK_MAX_BUDGET_PER_QUERY > 0

    def test_stuck_similarity_threshold(self):
        assert isinstance(config.STUCK_SIMILARITY_THRESHOLD, float)
        assert 0.0 < config.STUCK_SIMILARITY_THRESHOLD <= 1.0

    def test_rate_limit_seconds(self):
        assert isinstance(config.RATE_LIMIT_SECONDS, float)
        assert config.RATE_LIMIT_SECONDS >= 0

    def test_budget_warning_threshold(self):
        assert isinstance(config.BUDGET_WARNING_THRESHOLD, float)
        assert 0.0 < config.BUDGET_WARNING_THRESHOLD <= 1.0


# ════════════════════════════════════════════════════════════════════
#  3. Path config values
# ════════════════════════════════════════════════════════════════════


class TestPathConfigs:
    """Path-based configs must be valid Path or str objects."""

    def test_projects_base_dir_is_path(self):
        assert isinstance(config.PROJECTS_BASE_DIR, Path)

    def test_store_dir_is_path(self):
        assert isinstance(config.STORE_DIR, Path)

    def test_project_root_is_path(self):
        assert isinstance(config._PROJECT_ROOT, Path)
        assert config._PROJECT_ROOT.exists()


# ════════════════════════════════════════════════════════════════════
#  4. Collection config values
# ════════════════════════════════════════════════════════════════════


class TestCollectionConfigs:
    """Test list/dict configs for structure and content."""

    def test_cors_origins_is_list(self):
        assert isinstance(config.CORS_ORIGINS, list)
        assert len(config.CORS_ORIGINS) >= 1

    def test_default_agents_structure(self):
        assert isinstance(config.DEFAULT_AGENTS, list)
        assert len(config.DEFAULT_AGENTS) >= 5  # at least the core 5 roles
        for agent in config.DEFAULT_AGENTS:
            assert "name" in agent
            assert "role" in agent

    def test_default_agents_has_all_roles(self):
        names = {a["name"] for a in config.DEFAULT_AGENTS}
        # Must include at least the core active roles (non-legacy)
        assert {
            "orchestrator",
            "frontend_developer",
            "backend_developer",
            "reviewer",
            "test_engineer",
            "devops",
        }.issubset(names)

    def test_predefined_projects_is_dict(self):
        assert isinstance(config.PREDEFINED_PROJECTS, dict)

    def test_sub_agent_prompts_keys(self):
        from prompts import PROMPT_REGISTRY

        assert isinstance(PROMPT_REGISTRY, dict)
        # Must include at least the core 4 sub-agent roles
        assert {"developer", "reviewer", "tester", "devops"}.issubset(set(PROMPT_REGISTRY.keys()))

    def test_sub_agent_prompts_values_are_strings(self):
        from prompts import PROMPT_REGISTRY

        for name, prompt in PROMPT_REGISTRY.items():
            assert isinstance(prompt, str), f"{name} prompt is not a string"
            assert len(prompt) > 50, f"{name} prompt is suspiciously short"


# ════════════════════════════════════════════════════════════════════
#  5. System prompts
# ════════════════════════════════════════════════════════════════════


class TestSystemPrompts:
    """Verify system prompts exist and have expected content."""

    def test_orchestrator_prompt_is_string(self):
        assert isinstance(config.ORCHESTRATOR_SYSTEM_PROMPT, str)
        assert len(config.ORCHESTRATOR_SYSTEM_PROMPT) > 100

    def test_orchestrator_prompt_mentions_delegate(self):
        assert "<delegate>" in config.ORCHESTRATOR_SYSTEM_PROMPT

    def test_orchestrator_prompt_mentions_task_complete(self):
        assert "TASK_COMPLETE" in config.ORCHESTRATOR_SYSTEM_PROMPT

    def test_solo_agent_prompt_is_string(self):
        assert isinstance(config.SOLO_AGENT_PROMPT, str)
        assert len(config.SOLO_AGENT_PROMPT) > 20

    def test_solo_prompt_does_not_mention_delegate(self):
        assert (
            "delegate" not in config.SOLO_AGENT_PROMPT.lower()
            or "do NOT delegate" in config.SOLO_AGENT_PROMPT
        )


# ════════════════════════════════════════════════════════════════════
#  6. _get() helper function
# ════════════════════════════════════════════════════════════════════


class TestGetHelper:
    """The _get() function resolves overrides > env > default."""

    def test_get_returns_default_for_unknown_key(self):
        result = config._get("TOTALLY_UNKNOWN_KEY_12345", "fallback")
        assert result == "fallback"

    def test_get_applies_type_fn(self):
        result = config._get("TOTALLY_UNKNOWN_KEY_67890", "42", int)
        assert result == 42
        assert isinstance(result, int)

    def test_get_float_conversion(self):
        result = config._get("UNKNOWN_FLOAT_KEY_99999", "3.14", float)
        assert abs(result - 3.14) < 0.001


# ════════════════════════════════════════════════════════════════════
#  7. AGENT_REGISTRY — Single Source of Truth
# ════════════════════════════════════════════════════════════════════


class TestAgentRegistry:
    """Verify the centralized AGENT_REGISTRY is consistent and complete."""

    def test_registry_exists(self):
        assert hasattr(config, "AGENT_REGISTRY")
        assert isinstance(config.AGENT_REGISTRY, dict)
        assert len(config.AGENT_REGISTRY) >= 12  # at least 12 roles

    def test_all_configs_have_required_fields(self):
        for role, cfg in config.AGENT_REGISTRY.items():
            assert isinstance(cfg.timeout, int), f"{role}.timeout is not int"
            assert cfg.timeout > 0, f"{role}.timeout must be positive"
            assert isinstance(cfg.turns, int), f"{role}.turns is not int"
            assert cfg.turns > 0, f"{role}.turns must be positive"
            assert isinstance(cfg.budget, float), f"{role}.budget is not float"
            assert cfg.budget > 0, f"{role}.budget must be positive"
            assert cfg.layer in ("brain", "execution", "quality"), f"{role}.layer invalid"
            assert isinstance(cfg.emoji, str), f"{role}.emoji is not str"
            assert len(cfg.emoji) > 0, f"{role}.emoji is empty"

    def test_timeout_map_derived_from_registry(self):
        """AGENT_TIMEOUT_MAP must be derived from AGENT_REGISTRY."""
        for role, cfg in config.AGENT_REGISTRY.items():
            assert config.AGENT_TIMEOUT_MAP.get(role) == cfg.timeout, (
                f"AGENT_TIMEOUT_MAP['{role}'] != AGENT_REGISTRY['{role}'].timeout"
            )

    def test_default_agents_derived_from_registry(self):
        """DEFAULT_AGENTS must contain only non-legacy roles."""
        names = {a["name"] for a in config.DEFAULT_AGENTS}
        for role, cfg in config.AGENT_REGISTRY.items():
            if cfg.legacy:
                assert role not in names, f"Legacy role '{role}' should not be in DEFAULT_AGENTS"
            else:
                assert role in names, f"Active role '{role}' missing from DEFAULT_AGENTS"

    def test_emoji_map_derived_from_registry(self):
        """AGENT_EMOJI must be derived from AGENT_REGISTRY."""
        for role, cfg in config.AGENT_REGISTRY.items():
            assert config.AGENT_EMOJI.get(role) == cfg.emoji, (
                f"AGENT_EMOJI['{role}'] != AGENT_REGISTRY['{role}'].emoji"
            )

    def test_helper_functions(self):
        """Verify helper functions return correct values."""
        assert config.get_agent_timeout("pm") == 300
        assert config.get_agent_turns("pm") == 8
        assert config.get_agent_budget("pm") == 5.0
        assert config.get_agent_emoji("orchestrator") == "🎯"
        assert "orchestrator" in config.get_all_role_names()
        assert "developer" not in config.get_active_role_names()  # legacy
        assert "frontend_developer" in config.get_active_role_names()

    def test_legacy_roles_are_marked(self):
        """Legacy aliases must be marked as legacy=True."""
        legacy_roles = {"developer", "tester", "typescript_architect", "python_backend"}
        for role in legacy_roles:
            assert role in config.AGENT_REGISTRY, f"Legacy role '{role}' missing"
            assert config.AGENT_REGISTRY[role].legacy, f"'{role}' should be legacy=True"
