"""Tests for settings preset system (MTS-25, updated for MTS-173)."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from mts.config.presets import PRESETS, apply_preset
from mts.config.settings import load_settings


def test_preset_names() -> None:
    """All four named presets exist."""
    assert "quick" in PRESETS
    assert "standard" in PRESETS
    assert "deep" in PRESETS
    assert "rapid" in PRESETS


def test_quick_preset() -> None:
    """Quick preset has minimal matches and curator disabled."""
    overrides = PRESETS["quick"]
    assert overrides["curator_enabled"] is False
    assert overrides["matches_per_generation"] == 2


def test_standard_preset() -> None:
    """Standard preset enables curator and trend backpressure."""
    overrides = PRESETS["standard"]
    assert overrides["curator_enabled"] is True
    assert overrides["backpressure_mode"] == "trend"


def test_deep_preset() -> None:
    """Deep preset enables probes and coherence checks."""
    overrides = PRESETS["deep"]
    assert overrides["probe_matches"] == 2
    assert overrides["coherence_check_enabled"] is True


def test_apply_preset_returns_overrides() -> None:
    """apply_preset returns the preset's dict for a known name."""
    result = apply_preset("standard")
    assert isinstance(result, dict)
    assert "backpressure_mode" in result


def test_apply_preset_unknown_raises() -> None:
    """Unknown preset name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown preset"):
        apply_preset("nonexistent")


def test_load_settings_with_preset() -> None:
    """MTS_PRESET env var applies preset defaults."""
    env = {"MTS_PRESET": "quick"}
    with patch.dict(os.environ, env, clear=False):
        settings = load_settings()
    assert settings.curator_enabled is False
    assert settings.matches_per_generation == 2


def test_env_var_overrides_preset() -> None:
    """Explicit env var takes precedence over preset."""
    env = {
        "MTS_PRESET": "quick",
        "MTS_CURATOR_ENABLED": "true",  # Override quick's False
    }
    with patch.dict(os.environ, env, clear=False):
        settings = load_settings()
    assert settings.curator_enabled is True  # Explicit override wins
