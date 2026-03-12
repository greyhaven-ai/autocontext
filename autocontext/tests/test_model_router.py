"""Tests for tiered model routing."""
from __future__ import annotations

from autocontext.agents.model_router import ModelRouter, TierConfig

ENABLED = TierConfig(enabled=True)


def test_default_tier_returns_role_minimum() -> None:
    """For analyst (min_tier=haiku), default returns haiku model."""
    router = ModelRouter(ENABLED)
    model = router.select("analyst", generation=1, retry_count=0, is_plateau=False)
    assert model == ENABLED.tier_haiku_model


def test_early_generation_uses_haiku_for_competitor() -> None:
    router = ModelRouter(ENABLED)
    model = router.select("competitor", generation=1, retry_count=0, is_plateau=False)
    assert model == ENABLED.tier_haiku_model


def test_late_generation_uses_sonnet_for_competitor() -> None:
    router = ModelRouter(ENABLED)
    model = router.select("competitor", generation=5, retry_count=0, is_plateau=False)
    assert model == ENABLED.tier_sonnet_model


def test_retry_escalates_to_sonnet() -> None:
    router = ModelRouter(ENABLED)
    model = router.select("competitor", generation=5, retry_count=1, is_plateau=False)
    assert model == ENABLED.tier_sonnet_model


def test_plateau_escalates_to_opus() -> None:
    router = ModelRouter(ENABLED)
    model = router.select("competitor", generation=5, retry_count=0, is_plateau=True)
    assert model == ENABLED.tier_opus_model


def test_coach_always_uses_sonnet_or_higher() -> None:
    router = ModelRouter(ENABLED)
    model = router.select("coach", generation=1, retry_count=0, is_plateau=False)
    assert model in (ENABLED.tier_sonnet_model, ENABLED.tier_opus_model)


def test_architect_always_uses_opus() -> None:
    router = ModelRouter(ENABLED)
    model = router.select("architect", generation=1, retry_count=0, is_plateau=False)
    assert model == ENABLED.tier_opus_model


def test_disabled_router_returns_none() -> None:
    config = TierConfig(enabled=False)
    router = ModelRouter(config)
    model = router.select("competitor", generation=5, retry_count=2, is_plateau=True)
    assert model is None
