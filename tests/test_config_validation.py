"""Tests for validate_config() — AGENT_REGISTRY invariants.

Covers the three validation rules added to config.validate_config():
  (1) AGENT_REGISTRY entries with timeout <= 0, turns <= 0, or budget <= 0 → ConfigError
  (2) Any agent budget exceeds MAX_BUDGET_USD → logging.warning (non-fatal)
  (3) Two non-legacy roles share the same emoji → ConfigError

Strategy: monkeypatch config.AGENT_REGISTRY with carefully constructed
registries (copies of the real one + one injected bad entry) so that all
OTHER validate_config() checks still pass and only the rule under test fires.
"""

from __future__ import annotations

import logging

import pytest

import config
from config import AgentConfig, ConfigError, validate_config

# ── helpers ────────────────────────────────────────────────────────────────


def _good_registry() -> dict[str, AgentConfig]:
    """Return a deep-copy of the real AGENT_REGISTRY (known-good baseline)."""
    return dict(config.AGENT_REGISTRY.items())


def _inject(
    monkeypatch,
    extra_role: str,
    agent_cfg: AgentConfig,
    base: dict[str, AgentConfig] | None = None,
) -> dict[str, AgentConfig]:
    """Patch config.AGENT_REGISTRY with *base* + one injected entry."""
    registry = base if base is not None else _good_registry()
    registry[extra_role] = agent_cfg
    monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
    return registry


# ═══════════════════════════════════════════════════════════════════════════
#  Group 1 — timeout / turns / budget must be > 0
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentRegistryPositiveFields:
    """validate_config() must raise ConfigError when timeout/turns/budget <= 0."""

    # ── timeout ──────────────────────────────────────────────────────────

    def test_timeout_zero_raises_config_error(self, monkeypatch):
        _inject(
            monkeypatch,
            "bad_agent",
            AgentConfig(timeout=0, turns=10, budget=5.0),
        )
        with pytest.raises(ConfigError, match="timeout must be > 0"):
            validate_config()

    def test_timeout_negative_raises_config_error(self, monkeypatch):
        _inject(
            monkeypatch,
            "bad_agent",
            AgentConfig(timeout=-100, turns=10, budget=5.0),
        )
        with pytest.raises(ConfigError, match="timeout must be > 0"):
            validate_config()

    def test_timeout_positive_is_accepted(self, monkeypatch):
        """A valid timeout should not cause an error (only AGENT_REGISTRY entries tested)."""
        _inject(
            monkeypatch,
            "good_agent",
            AgentConfig(timeout=300, turns=10, budget=5.0),
        )
        # Should not raise — might return warnings but no ConfigError
        result = validate_config()
        assert isinstance(result, list)

    # ── turns ────────────────────────────────────────────────────────────

    def test_turns_zero_raises_config_error(self, monkeypatch):
        _inject(
            monkeypatch,
            "bad_agent",
            AgentConfig(timeout=300, turns=0, budget=5.0),
        )
        with pytest.raises(ConfigError, match="turns must be > 0"):
            validate_config()

    def test_turns_negative_raises_config_error(self, monkeypatch):
        _inject(
            monkeypatch,
            "bad_agent",
            AgentConfig(timeout=300, turns=-1, budget=5.0),
        )
        with pytest.raises(ConfigError, match="turns must be > 0"):
            validate_config()

    def test_turns_positive_is_accepted(self, monkeypatch):
        _inject(
            monkeypatch,
            "good_agent",
            AgentConfig(timeout=300, turns=50, budget=5.0),
        )
        result = validate_config()
        assert isinstance(result, list)

    # ── budget ───────────────────────────────────────────────────────────

    def test_budget_zero_raises_config_error(self, monkeypatch):
        _inject(
            monkeypatch,
            "bad_agent",
            AgentConfig(timeout=300, turns=10, budget=0.0),
        )
        with pytest.raises(ConfigError, match="budget must be > 0"):
            validate_config()

    def test_budget_negative_raises_config_error(self, monkeypatch):
        _inject(
            monkeypatch,
            "bad_agent",
            AgentConfig(timeout=300, turns=10, budget=-5.0),
        )
        with pytest.raises(ConfigError, match="budget must be > 0"):
            validate_config()

    def test_budget_positive_is_accepted(self, monkeypatch):
        _inject(
            monkeypatch,
            "good_agent",
            AgentConfig(timeout=300, turns=10, budget=1.0),
        )
        result = validate_config()
        assert isinstance(result, list)

    # ── error message includes the role name ─────────────────────────────

    def test_error_message_includes_role_name_for_timeout(self, monkeypatch):
        _inject(
            monkeypatch,
            "my_custom_role",
            AgentConfig(timeout=0, turns=10, budget=5.0),
        )
        with pytest.raises(ConfigError) as exc_info:
            validate_config()
        assert "my_custom_role" in str(exc_info.value)

    def test_error_message_includes_role_name_for_turns(self, monkeypatch):
        _inject(
            monkeypatch,
            "my_custom_role",
            AgentConfig(timeout=300, turns=0, budget=5.0),
        )
        with pytest.raises(ConfigError) as exc_info:
            validate_config()
        assert "my_custom_role" in str(exc_info.value)

    def test_error_message_includes_role_name_for_budget(self, monkeypatch):
        _inject(
            monkeypatch,
            "my_custom_role",
            AgentConfig(timeout=300, turns=10, budget=0.0),
        )
        with pytest.raises(ConfigError) as exc_info:
            validate_config()
        assert "my_custom_role" in str(exc_info.value)

    # ── multiple bad fields accumulate into one ConfigError ───────────────

    def test_multiple_invalid_fields_raise_single_config_error(self, monkeypatch):
        _inject(
            monkeypatch,
            "all_bad",
            AgentConfig(timeout=0, turns=0, budget=0.0),
        )
        with pytest.raises(ConfigError):
            validate_config()


# ═══════════════════════════════════════════════════════════════════════════
#  Group 2 — budget > MAX_BUDGET_USD emits logging.warning (non-fatal)
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentBudgetExceedsMaxBudget:
    """Warns (via logging) when an agent budget exceeds MAX_BUDGET_USD."""

    def test_budget_exceeding_max_emits_warning(self, monkeypatch, caplog):
        """Patching MAX_BUDGET_USD to a tiny value forces any agent over it."""
        monkeypatch.setattr(config, "MAX_BUDGET_USD", 0.01)
        # All existing real registry entries have budgets >> 0.01
        # (pm=5, architect=5, etc.) → all will trigger the warning
        with caplog.at_level(logging.WARNING, logger="config"):
            result = validate_config()
        # validate_config() returns without raising (budget-exceeds is a warning, not an error)
        assert isinstance(result, list)
        # At least one logging.warning should have been emitted mentioning MAX_BUDGET_USD
        warning_texts = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        budget_warnings = [
            t for t in warning_texts if "MAX_BUDGET_USD" in t or "budget" in t.lower()
        ]
        assert budget_warnings, (
            "Expected at least one budget-exceeds-MAX_BUDGET_USD warning in logs; got none. "
            f"All warnings: {warning_texts}"
        )

    def test_budget_exceeding_max_does_not_raise_config_error(self, monkeypatch):
        """Budget exceeding MAX_BUDGET_USD must NOT raise ConfigError — it's advisory."""
        monkeypatch.setattr(config, "MAX_BUDGET_USD", 0.01)
        # Should return a list (possibly with path warnings), not raise
        result = validate_config()
        assert isinstance(result, list)

    def test_budget_exactly_at_max_does_not_warn(self, monkeypatch, caplog):
        """An agent budget == MAX_BUDGET_USD is not over the limit → no agent budget warning."""
        # Use a small registry with exactly one agent at the limit
        registry = {
            "exact_budget_agent": AgentConfig(
                timeout=300,
                turns=10,
                budget=5.0,
                emoji="\U0001f9f9",  # unique emoji: 🧹
            )
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        monkeypatch.setattr(config, "MAX_BUDGET_USD", 5.0)
        # Also align SDK budget so SDK_MAX_BUDGET_PER_QUERY > MAX_BUDGET_USD warning doesn't fire
        monkeypatch.setattr(config, "SDK_MAX_BUDGET_PER_QUERY", 5.0)

        with caplog.at_level(logging.WARNING, logger="config"):
            validate_config()

        # The agent budget warning specifically mentions AGENT_REGISTRY and "exceeds"
        agent_budget_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "AGENT_REGISTRY" in r.getMessage()
            and "exceeds" in r.getMessage()
        ]
        assert not agent_budget_warnings, (
            f"Unexpected agent budget warnings: {agent_budget_warnings}"
        )

    def test_budget_strictly_above_max_warns_for_specific_role(self, monkeypatch, caplog):
        """The warning must reference the role whose budget is over the limit."""
        registry = {
            "rich_agent": AgentConfig(
                timeout=300,
                turns=10,
                budget=99.0,
                emoji="\U0001f4b0",  # 💰 unique
            )
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        monkeypatch.setattr(config, "MAX_BUDGET_USD", 50.0)

        with caplog.at_level(logging.WARNING, logger="config"):
            validate_config()

        warning_texts = " ".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "rich_agent" in warning_texts, (
            f"Expected 'rich_agent' in warning messages; got: {warning_texts}"
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Group 3 — duplicate emoji for non-legacy roles → ConfigError
# ═══════════════════════════════════════════════════════════════════════════


class TestDuplicateEmojiDetection:
    """validate_config() raises ConfigError when two non-legacy roles share an emoji."""

    # Unique emoji unlikely to collide with any real registry entry
    _SHARED_EMOJI = "\U0001f47e"  # 👾

    def test_two_non_legacy_roles_same_emoji_raises(self, monkeypatch):
        registry = {
            "role_alpha": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
            "role_beta": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        with pytest.raises(ConfigError, match="Duplicate emoji"):
            validate_config()

    def test_duplicate_emoji_error_mentions_both_roles(self, monkeypatch):
        registry = {
            "alpha_role": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
            "beta_role": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        with pytest.raises(ConfigError) as exc_info:
            validate_config()
        error_msg = str(exc_info.value)
        assert "alpha_role" in error_msg
        assert "beta_role" in error_msg

    def test_legacy_role_shares_emoji_with_non_legacy_does_not_raise(self, monkeypatch):
        """Legacy roles are excluded from the duplicate-emoji check."""
        registry = {
            "active_role": AgentConfig(
                timeout=300,
                turns=10,
                budget=5.0,
                emoji=self._SHARED_EMOJI,
                legacy=False,
            ),
            "old_alias": AgentConfig(
                timeout=300,
                turns=10,
                budget=5.0,
                emoji=self._SHARED_EMOJI,
                legacy=True,  # legacy → skipped
            ),
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        # Should NOT raise — legacy roles are excluded from emoji dedup
        result = validate_config()
        assert isinstance(result, list)

    def test_two_legacy_roles_same_emoji_does_not_raise(self, monkeypatch):
        """Two legacy roles sharing an emoji is allowed (both excluded from check)."""
        registry = {
            "legacy_a": AgentConfig(
                timeout=300,
                turns=10,
                budget=5.0,
                emoji=self._SHARED_EMOJI,
                legacy=True,
            ),
            "legacy_b": AgentConfig(
                timeout=300,
                turns=10,
                budget=5.0,
                emoji=self._SHARED_EMOJI,
                legacy=True,
            ),
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        result = validate_config()
        assert isinstance(result, list)

    def test_unique_emojis_across_non_legacy_roles_does_not_raise(self, monkeypatch):
        """Distinct emojis — no error expected."""
        registry = {
            "role_one": AgentConfig(timeout=300, turns=10, budget=5.0, emoji="\U0001f600"),
            "role_two": AgentConfig(timeout=300, turns=10, budget=5.0, emoji="\U0001f601"),
            "role_three": AgentConfig(timeout=300, turns=10, budget=5.0, emoji="\U0001f602"),
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        result = validate_config()
        assert isinstance(result, list)

    def test_three_roles_sharing_emoji_raises(self, monkeypatch):
        """Three roles sharing an emoji should also be caught."""
        registry = {
            "role_x": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
            "role_y": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
            "role_z": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        with pytest.raises(ConfigError, match="Duplicate emoji"):
            validate_config()

    def test_duplicate_emoji_error_includes_emoji_value(self, monkeypatch):
        """The error message should show which emoji is duplicated."""
        registry = {
            "r1": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
            "r2": AgentConfig(timeout=300, turns=10, budget=5.0, emoji=self._SHARED_EMOJI),
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        with pytest.raises(ConfigError) as exc_info:
            validate_config()
        assert self._SHARED_EMOJI in str(exc_info.value)


# ═══════════════════════════════════════════════════════════════════════════
#  Group 4 — healthy registry passes validation
# ═══════════════════════════════════════════════════════════════════════════


class TestValidRegistryPasses:
    """The real AGENT_REGISTRY (unmodified) must pass validate_config()."""

    def test_real_agent_registry_is_valid(self):
        """The production registry should pass all invariants without raising."""
        result = validate_config()
        assert isinstance(result, list), "validate_config() must return a list of warnings"

    def test_validate_config_returns_list(self):
        result = validate_config()
        assert isinstance(result, list)

    def test_minimal_single_valid_agent_passes(self, monkeypatch):
        registry = {
            "solo": AgentConfig(
                timeout=60,
                turns=5,
                budget=1.0,
                emoji="\U0001f916",  # 🤖
            )
        }
        monkeypatch.setattr(config, "AGENT_REGISTRY", registry)
        result = validate_config()
        assert isinstance(result, list)
