"""Tests for settings simplification (AC-25)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.llm_client import AnthropicClient, build_client_from_settings
from autocontext.config.settings import AppSettings, load_settings


def test_unused_fields_removed() -> None:
    """Unused subsystem fields should not exist on AppSettings."""
    settings = AppSettings()
    # Phase 7: Adapt — removed
    assert not hasattr(settings, "adapt_enabled")
    assert not hasattr(settings, "adapt_min_confidence")
    assert not hasattr(settings, "adapt_max_changes_per_cycle")
    assert not hasattr(settings, "adapt_dry_run")
    # Phase 8: Trust — removed
    assert not hasattr(settings, "trust_enabled")
    assert not hasattr(settings, "trust_min_observations")
    assert not hasattr(settings, "trust_confidence_saturation")
    assert not hasattr(settings, "trust_decay_rate")
    # Phase 9: Identity — removed
    assert not hasattr(settings, "identity_enabled")
    assert not hasattr(settings, "identity_dir")
    # Phase 10: Heartbeat — removed
    assert not hasattr(settings, "heartbeat_enabled")
    assert not hasattr(settings, "heartbeat_stall_timeout_seconds")
    assert not hasattr(settings, "heartbeat_escalation_interval_seconds")
    assert not hasattr(settings, "heartbeat_max_restart_attempts")


def test_active_fields_still_exist() -> None:
    """Active subsystem fields should still exist."""
    settings = AppSettings()
    # Core
    assert hasattr(settings, "db_path")
    assert hasattr(settings, "agent_provider")
    # RLM (active subsystem)
    assert hasattr(settings, "rlm_enabled")
    # Curator (active subsystem)
    assert hasattr(settings, "curator_enabled")
    # Stagnation (active subsystem)
    assert hasattr(settings, "stagnation_reset_enabled")


def test_load_settings_without_removed_env_vars() -> None:
    """load_settings works after removing unused env var mappings."""
    settings = load_settings()
    assert settings.agent_provider == "anthropic"
    assert settings.curator_enabled is True


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_agent_provider_uses_default_before_client_construction(blank: str) -> None:
    settings = AppSettings(agent_provider=blank, anthropic_api_key="test-key")

    assert settings.agent_provider == "anthropic"
    with patch("autocontext.agents.llm_client.Anthropic", return_value=MagicMock()):
        assert isinstance(build_client_from_settings(settings), AnthropicClient)


def test_blank_primary_provider_env_falls_through_to_compatibility_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "   ")
    monkeypatch.setenv("AUTOCONTEXT_PROVIDER", "deterministic")

    assert load_settings().agent_provider == "deterministic"
