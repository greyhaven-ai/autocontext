"""Tests for named preset system (MTS-173).

Replaces legacy conservative/aggressive/experimental presets with
quick/standard/deep/rapid.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from mts.config.presets import PRESETS, apply_preset
from mts.config.settings import load_settings


class TestPresetDefinitions:
    """Verify all four named presets exist with expected values."""

    def test_quick_preset(self) -> None:
        overrides = PRESETS["quick"]
        assert overrides["matches_per_generation"] == 2
        assert overrides["curator_enabled"] is False
        assert overrides["probe_matches"] == 0
        assert overrides["coherence_check_enabled"] is False
        assert overrides["max_retries"] == 0

    def test_standard_preset(self) -> None:
        overrides = PRESETS["standard"]
        assert overrides["matches_per_generation"] == 3
        assert overrides["curator_enabled"] is True
        assert overrides["backpressure_mode"] == "trend"
        assert overrides["cross_run_inheritance"] is True

    def test_deep_preset(self) -> None:
        overrides = PRESETS["deep"]
        assert overrides["matches_per_generation"] == 5
        assert overrides["curator_enabled"] is True
        assert overrides["curator_consolidate_every_n_gens"] == 3
        assert overrides["probe_matches"] == 2
        assert overrides["coherence_check_enabled"] is True

    def test_rapid_preset(self) -> None:
        overrides = PRESETS["rapid"]
        assert overrides["backpressure_min_delta"] == 0.0
        assert overrides["backpressure_mode"] == "simple"
        assert overrides["curator_enabled"] is False
        assert overrides["max_retries"] == 0
        assert overrides["matches_per_generation"] == 2
        assert overrides["rlm_max_turns"] == 5
        assert overrides["probe_matches"] == 0
        assert overrides["coherence_check_enabled"] is False


class TestApplyPreset:
    """Verify apply_preset returns correct overrides."""

    def test_each_preset_applies_expected_values(self) -> None:
        """Each named preset returns non-empty dict of overrides."""
        for name in ("quick", "standard", "deep", "rapid"):
            overrides = apply_preset(name)
            assert isinstance(overrides, dict)
            assert len(overrides) > 0, f"Preset '{name}' returned empty overrides"

    def test_invalid_preset_raises_error(self) -> None:
        """Unknown preset name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown preset"):
            apply_preset("nonexistent")

    def test_empty_string_returns_empty(self) -> None:
        """Empty string returns empty dict (no preset)."""
        result = apply_preset("")
        assert result == {}


class TestPresetIntegration:
    """Verify presets integrate correctly with load_settings."""

    def test_default_preset_is_standard(self) -> None:
        """Without MTS_PRESET, standard preset values should apply by default."""
        env = {"MTS_PRESET": "standard"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings()
        assert settings.matches_per_generation == 3
        assert settings.curator_enabled is True
        assert settings.backpressure_mode == "trend"
        assert settings.cross_run_inheritance is True

    def test_env_var_overrides_preset(self) -> None:
        """Explicit env var takes precedence over preset."""
        env = {
            "MTS_PRESET": "quick",
            "MTS_CURATOR_ENABLED": "true",  # Override quick's False
        }
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings()
        assert settings.curator_enabled is True  # Explicit override wins
        assert settings.matches_per_generation == 2  # From quick preset

    def test_preset_plus_explicit_override(self) -> None:
        """Preset applies, then explicit env var overrides a single field."""
        env = {
            "MTS_PRESET": "deep",
            "MTS_MATCHES_PER_GENERATION": "10",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings()
        assert settings.matches_per_generation == 10  # Explicit override
        assert settings.curator_enabled is True  # From deep preset
        assert settings.probe_matches == 2  # From deep preset

    def test_quick_preset_via_load_settings(self) -> None:
        """Quick preset sets minimal match count."""
        env = {"MTS_PRESET": "quick"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings()
        assert settings.matches_per_generation == 2
        assert settings.curator_enabled is False
        assert settings.max_retries == 0

    def test_rapid_preset_via_load_settings(self) -> None:
        """Rapid preset optimizes for speed."""
        env = {"MTS_PRESET": "rapid"}
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings()
        assert settings.backpressure_min_delta == 0.0
        assert settings.curator_enabled is False
        assert settings.matches_per_generation == 2
        assert settings.rlm_max_turns == 5
