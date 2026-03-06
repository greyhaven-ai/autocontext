"""Integration tests for model router wiring into orchestrator."""
from __future__ import annotations

from mts.agents.model_router import ModelRouter, TierConfig
from mts.config.settings import AppSettings


def test_settings_has_tier_fields() -> None:
    settings = AppSettings()
    assert hasattr(settings, "tier_routing_enabled")
    assert settings.tier_routing_enabled is False


def test_settings_tier_models_configurable() -> None:
    settings = AppSettings(tier_routing_enabled=True, tier_haiku_model="custom-haiku")
    assert settings.tier_haiku_model == "custom-haiku"


def test_settings_tier_defaults() -> None:
    settings = AppSettings()
    assert settings.tier_haiku_model == "claude-haiku-4-5-20251001"
    assert settings.tier_sonnet_model == "claude-sonnet-4-5-20250929"
    assert settings.tier_opus_model == "claude-opus-4-6"
    assert settings.tier_competitor_haiku_max_gen == 3


def test_router_from_settings() -> None:
    """ModelRouter can be constructed from AppSettings fields."""
    settings = AppSettings(tier_routing_enabled=True)
    config = TierConfig(
        enabled=settings.tier_routing_enabled,
        tier_haiku_model=settings.tier_haiku_model,
        tier_sonnet_model=settings.tier_sonnet_model,
        tier_opus_model=settings.tier_opus_model,
        competitor_haiku_max_gen=settings.tier_competitor_haiku_max_gen,
    )
    router = ModelRouter(config)
    model = router.select("competitor", generation=1, retry_count=0, is_plateau=False)
    assert model == settings.tier_haiku_model
