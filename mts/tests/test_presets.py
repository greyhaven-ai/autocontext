"""Tests for settings preset system (MTS-25)."""
from __future__ import annotations

import os
from unittest.mock import patch

from mts.config.presets import PRESETS, apply_preset
from mts.config.settings import load_settings


def test_preset_names() -> None:
    """All three presets exist."""
    assert "conservative" in PRESETS
    assert "aggressive" in PRESETS
    assert "experimental" in PRESETS


def test_conservative_preset() -> None:
    """Conservative preset has high thresholds and curator enabled."""
    overrides = PRESETS["conservative"]
    assert overrides["curator_enabled"] is True
    assert overrides["backpressure_min_delta"] >= 0.01


def test_aggressive_preset() -> None:
    """Aggressive preset has lower thresholds and curator disabled."""
    overrides = PRESETS["aggressive"]
    assert overrides["curator_enabled"] is False
    assert overrides["backpressure_min_delta"] <= 0.003


def test_experimental_preset() -> None:
    """Experimental preset enables stagnation resets."""
    overrides = PRESETS["experimental"]
    assert overrides["stagnation_reset_enabled"] is True


def test_apply_preset_returns_overrides() -> None:
    """apply_preset returns the preset's dict for a known name."""
    result = apply_preset("conservative")
    assert isinstance(result, dict)
    assert "backpressure_min_delta" in result


def test_apply_preset_unknown_returns_empty() -> None:
    """Unknown preset name returns empty dict."""
    result = apply_preset("nonexistent")
    assert result == {}


def test_load_settings_with_preset() -> None:
    """MTS_PRESET env var applies preset defaults."""
    env = {"MTS_PRESET": "aggressive"}
    with patch.dict(os.environ, env, clear=False):
        settings = load_settings()
    assert settings.curator_enabled is False
    assert settings.backpressure_min_delta <= 0.003


def test_env_var_overrides_preset() -> None:
    """Explicit env var takes precedence over preset."""
    env = {
        "MTS_PRESET": "aggressive",
        "MTS_CURATOR_ENABLED": "true",  # Override aggressive's False
    }
    with patch.dict(os.environ, env, clear=False):
        settings = load_settings()
    assert settings.curator_enabled is True  # Explicit override wins
