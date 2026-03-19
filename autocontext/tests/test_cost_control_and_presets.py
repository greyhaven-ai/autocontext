"""Tests for AC-327 + AC-329: cost-aware control and long-run presets.

AC-327: CostBudget, CostTracker, CostPolicy, evaluate_cost_effectiveness, should_throttle
AC-329: RunPreset, LONG_RUN_PRESET, SHORT_RUN_PRESET, apply_preset
"""

from __future__ import annotations

# ===========================================================================
# AC-327: CostBudget
# ===========================================================================


class TestCostBudget:
    def test_defaults(self) -> None:
        from autocontext.loop.cost_control import CostBudget

        budget = CostBudget()
        assert budget.total_usd == 0.0  # unlimited
        assert budget.per_generation_usd == 0.0

    def test_custom(self) -> None:
        from autocontext.loop.cost_control import CostBudget

        budget = CostBudget(total_usd=10.0, per_generation_usd=1.0)
        assert budget.total_usd == 10.0


# ===========================================================================
# AC-327: CostTracker
# ===========================================================================


class TestCostTracker:
    def test_record_and_total(self) -> None:
        from autocontext.loop.cost_control import CostTracker

        tracker = CostTracker()
        tracker.record(generation=1, cost_usd=0.15, tokens=30000)
        tracker.record(generation=2, cost_usd=0.20, tokens=40000)

        assert tracker.total_cost_usd == 0.35
        assert tracker.total_tokens == 70000
        assert len(tracker.per_generation) == 2

    def test_generation_cost(self) -> None:
        from autocontext.loop.cost_control import CostTracker

        tracker = CostTracker()
        tracker.record(generation=1, cost_usd=0.15, tokens=30000)
        assert tracker.generation_cost(1) == 0.15
        assert tracker.generation_cost(99) == 0.0


# ===========================================================================
# AC-327: CostPolicy + evaluate_cost_effectiveness
# ===========================================================================


class TestCostPolicy:
    def test_defaults(self) -> None:
        from autocontext.loop.cost_control import CostPolicy

        policy = CostPolicy()
        assert policy.max_cost_per_delta_point > 0

    def test_custom(self) -> None:
        from autocontext.loop.cost_control import CostPolicy

        policy = CostPolicy(max_cost_per_delta_point=5.0, throttle_above_total=8.0)
        assert policy.max_cost_per_delta_point == 5.0


class TestEvaluateCostEffectiveness:
    def test_good_efficiency(self) -> None:
        from autocontext.loop.cost_control import evaluate_cost_effectiveness

        result = evaluate_cost_effectiveness(
            cost_usd=0.15, score_delta=0.10,
        )
        assert result["cost_per_delta_point"] == 1.5
        assert result["efficient"] is True

    def test_poor_efficiency(self) -> None:
        from autocontext.loop.cost_control import evaluate_cost_effectiveness

        result = evaluate_cost_effectiveness(
            cost_usd=5.0, score_delta=0.01,
        )
        assert result["cost_per_delta_point"] == 500.0
        assert result["efficient"] is False

    def test_zero_delta(self) -> None:
        from autocontext.loop.cost_control import evaluate_cost_effectiveness

        result = evaluate_cost_effectiveness(cost_usd=1.0, score_delta=0.0)
        assert result["cost_per_delta_point"] == float("inf")
        assert result["efficient"] is False


class TestShouldThrottle:
    def test_under_budget_no_throttle(self) -> None:
        from autocontext.loop.cost_control import CostBudget, CostTracker, should_throttle

        budget = CostBudget(total_usd=10.0, per_generation_usd=2.0)
        tracker = CostTracker()
        tracker.record(1, 1.5, 30000)

        assert should_throttle(tracker, budget) is False

    def test_over_total_budget(self) -> None:
        from autocontext.loop.cost_control import CostBudget, CostTracker, should_throttle

        budget = CostBudget(total_usd=1.0)
        tracker = CostTracker()
        tracker.record(1, 0.6, 10000)
        tracker.record(2, 0.5, 10000)

        assert should_throttle(tracker, budget) is True

    def test_over_per_generation_budget(self) -> None:
        from autocontext.loop.cost_control import CostBudget, CostTracker, should_throttle

        budget = CostBudget(per_generation_usd=0.5)
        tracker = CostTracker()
        tracker.record(1, 0.2, 1000)
        tracker.record(2, 0.75, 1000)

        assert should_throttle(tracker, budget, generation=2) is True

    def test_policy_total_threshold(self) -> None:
        from autocontext.loop.cost_control import CostBudget, CostPolicy, CostTracker, should_throttle

        tracker = CostTracker()
        tracker.record(1, 0.6, 1000)
        tracker.record(2, 0.45, 1000)

        assert should_throttle(
            tracker,
            CostBudget(),
            generation=2,
            policy=CostPolicy(throttle_above_total=1.0),
        ) is True

    def test_unlimited_budget_never_throttles(self) -> None:
        from autocontext.loop.cost_control import CostBudget, CostTracker, should_throttle

        budget = CostBudget()  # unlimited
        tracker = CostTracker()
        tracker.record(1, 100.0, 1000000)

        assert should_throttle(tracker, budget) is False


# ===========================================================================
# AC-329: RunPreset
# ===========================================================================


class TestRunPreset:
    def test_construction(self) -> None:
        from autocontext.loop.presets import RunPreset

        preset = RunPreset(
            name="test",
            description="Test preset",
            settings={
                "stagnation_reset_enabled": True,
                "two_tier_gating_enabled": True,
            },
        )
        assert preset.name == "test"
        assert preset.settings["stagnation_reset_enabled"] is True

    def test_roundtrip(self) -> None:
        from autocontext.loop.presets import RunPreset

        preset = RunPreset(name="x", description="y", settings={"a": 1})
        d = preset.to_dict()
        restored = RunPreset.from_dict(d)
        assert restored.name == "x"
        assert restored.settings["a"] == 1


class TestBuiltinPresets:
    def test_long_run_preset_exists(self) -> None:
        from autocontext.loop.presets import LONG_RUN_PRESET

        assert LONG_RUN_PRESET.name == "long_run"
        assert LONG_RUN_PRESET.settings.get("stagnation_reset_enabled") is True

    def test_short_run_preset_exists(self) -> None:
        from autocontext.loop.presets import SHORT_RUN_PRESET

        assert SHORT_RUN_PRESET.name == "short_run"

    def test_long_run_has_safeguards(self) -> None:
        from autocontext.loop.presets import LONG_RUN_PRESET

        s = LONG_RUN_PRESET.settings
        # Anti-stall safeguards should be on
        assert s.get("stagnation_reset_enabled") is True
        assert s.get("dead_end_tracking_enabled") is True
        assert s.get("curator_enabled") is True

    def test_presets_are_distinct(self) -> None:
        from autocontext.loop.presets import LONG_RUN_PRESET, SHORT_RUN_PRESET

        assert LONG_RUN_PRESET.name != SHORT_RUN_PRESET.name


class TestApplyPreset:
    def test_applies_settings(self) -> None:
        from autocontext.loop.presets import RunPreset, apply_preset

        preset = RunPreset(
            name="test",
            description="test",
            settings={"max_retries": 5, "stagnation_reset_enabled": True},
        )
        base = {"max_retries": 3, "stagnation_reset_enabled": False, "other": "value"}
        result = apply_preset(base, preset)

        assert result["max_retries"] == 5
        assert result["stagnation_reset_enabled"] is True
        assert result["other"] == "value"  # preserved

    def test_none_preset_returns_original(self) -> None:
        from autocontext.loop.presets import apply_preset

        base = {"a": 1}
        result = apply_preset(base, None)
        assert result == {"a": 1}

    def test_get_preset_by_name(self) -> None:
        from autocontext.loop.presets import get_preset

        preset = get_preset("long_run")
        assert preset is not None
        assert preset.name == "long_run"

    def test_get_unknown_preset(self) -> None:
        from autocontext.loop.presets import get_preset

        assert get_preset("nonexistent") is None


class TestPresetLoaderIntegration:
    def test_long_run_preset_loads_through_live_settings_path(self, monkeypatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_PRESET", "long_run")
        settings = load_settings()

        assert settings.stagnation_reset_enabled is True
        assert settings.dead_end_tracking_enabled is True
        assert settings.two_tier_gating_enabled is True
        assert settings.max_retries == 3

    def test_short_run_preset_loads_through_live_settings_path(self, monkeypatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_PRESET", "short_run")
        settings = load_settings()

        assert settings.stagnation_reset_enabled is False
        assert settings.dead_end_tracking_enabled is False
        assert settings.curator_enabled is False
