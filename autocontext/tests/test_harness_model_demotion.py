"""Tests for AC-164: Extend ModelRouter with harness-aware dynamic demotion.

When harness coverage is strong, the competitor model can be demoted to a cheaper
tier (haiku/sonnet) since the harness catches invalid strategies.  Non-competitor
roles are never demoted.
"""
from __future__ import annotations

from autocontext.agents.model_router import ModelRouter, TierConfig
from autocontext.execution.harness_coverage import HarnessCoverage

# ── Helpers ─────────────────────────────────────────────────────────────


def _cov(score: float = 0.95) -> HarnessCoverage:
    """Build a HarnessCoverage with a given score."""
    return HarnessCoverage(
        has_validate_strategy=True,
        has_enumerate_legal_actions=True,
        has_parse_game_state=True,
        has_is_legal_action=True,
        validation_accuracy=1.0,
        function_count=1,
        coverage_score=score,
    )


def _config(**overrides: object) -> TierConfig:
    """Build a TierConfig with routing enabled and optional overrides."""
    defaults = {
        "enabled": True,
        "harness_aware_tiering_enabled": True,
        "harness_coverage_demotion_threshold": 0.8,
    }
    defaults.update(overrides)
    return TierConfig(**defaults)  # type: ignore[arg-type]


# ── Config field tests ──────────────────────────────────────────────────


class TestConfigFields:
    def test_tier_config_has_harness_aware_tiering_enabled(self) -> None:
        cfg = TierConfig()
        assert hasattr(cfg, "harness_aware_tiering_enabled")

    def test_harness_aware_tiering_defaults_false(self) -> None:
        cfg = TierConfig()
        assert cfg.harness_aware_tiering_enabled is False

    def test_harness_coverage_demotion_threshold_defaults(self) -> None:
        cfg = TierConfig()
        assert hasattr(cfg, "harness_coverage_demotion_threshold")
        assert cfg.harness_coverage_demotion_threshold == 0.8


# ── Backward compatibility tests ────────────────────────────────────────


class TestBackwardCompatibility:
    def test_select_without_harness_coverage_works(self) -> None:
        """Existing callers that don't pass harness_coverage should still work."""
        router = ModelRouter(_config(harness_aware_tiering_enabled=False))
        result = router.select("competitor", generation=1, retry_count=0, is_plateau=False)
        assert result is not None

    def test_select_with_none_harness_coverage(self) -> None:
        """Passing harness_coverage=None should behave like disabled."""
        router = ModelRouter(_config())
        result = router.select(
            "competitor", generation=1, retry_count=0, is_plateau=False,
            harness_coverage=None,
        )
        assert result is not None

    def test_disabled_returns_none(self) -> None:
        """When routing disabled entirely, still returns None."""
        router = ModelRouter(TierConfig(enabled=False))
        result = router.select("competitor", generation=1, retry_count=0, is_plateau=False)
        assert result is None


# ── Competitor demotion tests ───────────────────────────────────────────


class TestCompetitorDemotion:
    def test_high_coverage_demotes_to_haiku(self) -> None:
        """Coverage >= 0.9 → competitor demoted to haiku."""
        router = ModelRouter(_config())
        result = router.select(
            "competitor", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        assert result == TierConfig().tier_haiku_model

    def test_medium_coverage_demotes_to_sonnet(self) -> None:
        """0.5 <= coverage < 0.9 and above threshold → demoted to sonnet."""
        router = ModelRouter(_config(harness_coverage_demotion_threshold=0.5))
        result = router.select(
            "competitor", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.6),
        )
        assert result == TierConfig().tier_sonnet_model

    def test_coverage_below_threshold_no_demotion(self) -> None:
        """Coverage below threshold → normal tier selection (no demotion)."""
        router = ModelRouter(_config(harness_coverage_demotion_threshold=0.8))
        # Gen 10 + no retry/plateau → normally sonnet
        result = router.select(
            "competitor", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.3),
        )
        assert result == TierConfig().tier_sonnet_model

    def test_demotion_overrides_generation_escalation(self) -> None:
        """Even if generation > haiku_max_gen, high coverage demotes back to haiku."""
        router = ModelRouter(_config(competitor_haiku_max_gen=3))
        result = router.select(
            "competitor", generation=20, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        assert result == TierConfig().tier_haiku_model

    def test_retry_escalation_overrides_demotion(self) -> None:
        """Retry escalation should override harness demotion (safety first)."""
        router = ModelRouter(_config())
        result = router.select(
            "competitor", generation=10, retry_count=2, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        # Retry escalation → at least sonnet, demotion should not go below that
        assert result == TierConfig().tier_sonnet_model

    def test_plateau_overrides_demotion(self) -> None:
        """Plateau escalation to opus should not be overridden by demotion."""
        router = ModelRouter(_config())
        result = router.select(
            "competitor", generation=10, retry_count=0, is_plateau=True,
            harness_coverage=_cov(0.95),
        )
        assert result == TierConfig().tier_opus_model

    def test_demotion_disabled_ignores_coverage(self) -> None:
        """When harness_aware_tiering_enabled=False, coverage is ignored."""
        router = ModelRouter(_config(harness_aware_tiering_enabled=False))
        result = router.select(
            "competitor", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        # Gen 10 → sonnet normally
        assert result == TierConfig().tier_sonnet_model

    def test_early_gen_with_high_coverage_stays_haiku(self) -> None:
        """Early gen already uses haiku; high coverage keeps it there."""
        router = ModelRouter(_config())
        result = router.select(
            "competitor", generation=1, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        assert result == TierConfig().tier_haiku_model


# ── Non-competitor roles never demoted ──────────────────────────────────


class TestNonCompetitorNotDemoted:
    def test_analyst_not_demoted(self) -> None:
        """Analyst should never be demoted by harness coverage."""
        router = ModelRouter(_config())
        result_with = router.select(
            "analyst", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        result_without = router.select(
            "analyst", generation=10, retry_count=0, is_plateau=False,
        )
        assert result_with == result_without

    def test_coach_not_demoted(self) -> None:
        router = ModelRouter(_config())
        result_with = router.select(
            "coach", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        result_without = router.select(
            "coach", generation=10, retry_count=0, is_plateau=False,
        )
        assert result_with == result_without

    def test_architect_not_demoted(self) -> None:
        router = ModelRouter(_config())
        result_with = router.select(
            "architect", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        result_without = router.select(
            "architect", generation=10, retry_count=0, is_plateau=False,
        )
        assert result_with == result_without

    def test_curator_not_demoted(self) -> None:
        router = ModelRouter(_config())
        result_with = router.select(
            "curator", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.95),
        )
        assert result_with == TierConfig().tier_opus_model


# ── Threshold boundary tests ───────────────────────────────────────────


class TestThresholdBoundaries:
    def test_exact_threshold_triggers_demotion(self) -> None:
        """Coverage exactly at threshold should trigger demotion."""
        router = ModelRouter(_config(harness_coverage_demotion_threshold=0.8))
        result = router.select(
            "competitor", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.8),
        )
        # 0.8 < 0.9 → sonnet recommendation from analyzer, but it's a demotion
        assert result == TierConfig().tier_sonnet_model

    def test_just_below_threshold_no_demotion(self) -> None:
        """Coverage just below threshold should not trigger demotion."""
        router = ModelRouter(_config(harness_coverage_demotion_threshold=0.8))
        result = router.select(
            "competitor", generation=10, retry_count=0, is_plateau=False,
            harness_coverage=_cov(0.79),
        )
        # Normal gen 10 → sonnet (same result, but via normal path not demotion)
        assert result == TierConfig().tier_sonnet_model
