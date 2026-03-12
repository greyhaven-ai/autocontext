from __future__ import annotations

import pytest

from autocontext.config.presets import PRESETS, apply_preset
from autocontext.config.settings import AppSettings, load_settings
from autocontext.knowledge.rapid_gate import RapidGateResult, rapid_gate, should_transition_to_linear

# ---------------------------------------------------------------------------
# TestRapidExplorationSettings
# ---------------------------------------------------------------------------


class TestRapidExplorationSettings:
    """Settings fields for exploration mode (AR-4)."""

    def test_exploration_mode_defaults_linear(self) -> None:
        s = AppSettings()
        assert s.exploration_mode == "linear"

    def test_rapid_gens_defaults_zero(self) -> None:
        s = AppSettings()
        assert s.rapid_gens == 0

    def test_load_settings_reads_exploration_mode_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOCONTEXT_EXPLORATION_MODE", "rapid")
        s = load_settings()
        assert s.exploration_mode == "rapid"


# ---------------------------------------------------------------------------
# TestRapidPreset
# ---------------------------------------------------------------------------


class TestRapidPreset:
    """Preset configuration for rapid exploration mode."""

    def test_rapid_preset_exists(self) -> None:
        assert "rapid" in PRESETS

    def test_rapid_preset_values(self) -> None:
        preset = PRESETS["rapid"]
        assert preset["backpressure_min_delta"] == 0.0
        assert preset["max_retries"] == 0
        assert preset["curator_enabled"] is False

    def test_apply_preset_rapid(self) -> None:
        result = apply_preset("rapid")
        assert result["backpressure_min_delta"] == 0.0
        assert result["backpressure_mode"] == "simple"
        assert result["curator_enabled"] is False
        assert result["max_retries"] == 0
        assert result["matches_per_generation"] == 2
        assert result["rlm_max_turns"] == 5
        assert result["probe_matches"] == 0
        assert result["coherence_check_enabled"] is False
        assert result["constraint_prompts_enabled"] is False


# ---------------------------------------------------------------------------
# TestRapidGate
# ---------------------------------------------------------------------------


class TestRapidGate:
    """Binary keep/discard gate for rapid exploration."""

    def test_rapid_gate_positive_delta_advances(self) -> None:
        result = rapid_gate(0.6, 0.5)
        assert result.decision == "advance"

    def test_rapid_gate_zero_delta_rollback(self) -> None:
        result = rapid_gate(0.5, 0.5)
        assert result.decision == "rollback"

    def test_rapid_gate_negative_delta_rollback(self) -> None:
        result = rapid_gate(0.4, 0.5)
        assert result.decision == "rollback"

    def test_rapid_gate_result_fields(self) -> None:
        result = rapid_gate(0.6, 0.5)
        assert isinstance(result, RapidGateResult)
        assert abs(result.delta - 0.1) < 1e-9
        assert "improved" in result.reason.lower()


# ---------------------------------------------------------------------------
# TestAutoTransition
# ---------------------------------------------------------------------------


class TestAutoTransition:
    """Auto-transition from rapid to linear after N gens."""

    def test_should_transition_when_at_limit(self) -> None:
        assert should_transition_to_linear(10, 10) is True

    def test_should_not_transition_when_disabled(self) -> None:
        assert should_transition_to_linear(100, 0) is False
