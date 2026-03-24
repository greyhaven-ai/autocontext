"""Settings preset definitions (AC-173).

Named presets: quick, standard, deep, rapid, long_run, short_run.
Each preset is a dict of field_name -> default_value overrides.
These are applied before individual env var overrides, so explicit
env vars always win.

Config priority: CLI args > env vars > tuning.json > preset defaults > hardcoded defaults

Usage: AUTOCONTEXT_PRESET=quick
"""
from __future__ import annotations

LONG_RUN_PRESET_SETTINGS: dict[str, object] = {
    "stagnation_reset_enabled": True,
    "dead_end_tracking_enabled": True,
    "curator_enabled": True,
    "two_tier_gating_enabled": True,
    "max_retries": 3,
    "stagnation_rollback_threshold": 5,
    "stagnation_plateau_window": 3,
    "cross_run_inheritance": True,
}

SHORT_RUN_PRESET_SETTINGS: dict[str, object] = {
    "stagnation_reset_enabled": False,
    "dead_end_tracking_enabled": False,
    "curator_enabled": False,
    "two_tier_gating_enabled": False,
    "max_retries": 2,
}

PRESETS: dict[str, dict[str, object]] = {
    "quick": {
        "matches_per_generation": 2,
        "curator_enabled": False,
        "probe_matches": 0,
        "coherence_check_enabled": False,
        "max_retries": 0,
    },
    "standard": {
        "matches_per_generation": 3,
        "curator_enabled": True,
        "backpressure_mode": "trend",
        "cross_run_inheritance": True,
    },
    "deep": {
        "matches_per_generation": 5,
        "curator_enabled": True,
        "curator_consolidate_every_n_gens": 3,
        "probe_matches": 2,
        "coherence_check_enabled": True,
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
    "long_run": dict(LONG_RUN_PRESET_SETTINGS),
    "short_run": dict(SHORT_RUN_PRESET_SETTINGS),
}

VALID_PRESET_NAMES = frozenset(PRESETS.keys())


def apply_preset(name: str) -> dict[str, object]:
    """Return overrides for a named preset.

    Args:
        name: Preset name (quick, standard, deep, rapid) or empty string for none.

    Returns:
        Dict of field_name -> value overrides.

    Raises:
        ValueError: If *name* is non-empty and not a recognized preset.
    """
    if not name:
        return {}
    if name not in PRESETS:
        raise ValueError(
            f"Unknown preset '{name}'. Valid presets: {', '.join(sorted(VALID_PRESET_NAMES))}"
        )
    return dict(PRESETS[name])
