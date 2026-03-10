"""Settings preset definitions.

Each preset is a dict of field_name -> default_value overrides.
These are applied before individual env var overrides, so explicit
env vars always win.

Usage: MTS_PRESET=conservative
"""
from __future__ import annotations

PRESETS: dict[str, dict[str, object]] = {
    "conservative": {
        "backpressure_min_delta": 0.01,
        "backpressure_mode": "trend",
        "backpressure_plateau_window": 5,
        "curator_enabled": True,
        "curator_consolidate_every_n_gens": 2,
        "max_retries": 3,
        "matches_per_generation": 5,
    },
    "aggressive": {
        "backpressure_min_delta": 0.002,
        "backpressure_mode": "simple",
        "curator_enabled": False,
        "max_retries": 1,
        "matches_per_generation": 2,
    },
    "experimental": {
        "stagnation_reset_enabled": True,
        "stagnation_rollback_threshold": 3,
        "stagnation_plateau_window": 4,
        "backpressure_min_delta": 0.003,
        "backpressure_mode": "trend",
        "curator_enabled": True,
        "code_strategies_enabled": True,
    },
    "rapid": {
        "backpressure_min_delta": 0.0,
        "backpressure_mode": "simple",
        "curator_enabled": False,
        "max_retries": 0,
        "matches_per_generation": 2,
        "rlm_max_turns": 5,
        "probe_matches": 0,
        "coherence_check_enabled": False,
        "constraint_prompts_enabled": False,
    },
}


def apply_preset(name: str) -> dict[str, object]:
    """Return overrides for a named preset, or empty dict if unknown."""
    return dict(PRESETS.get(name, {}))
