"""Default long-run presets with anti-stall safeguards (AC-329).

Named presets that bundle safe default configurations for different
run durations. Long runs enable all available safeguards.

Key types:
- RunPreset: named preset with settings overrides
- LONG_RUN_PRESET / SHORT_RUN_PRESET: builtin presets
- apply_preset(): merge preset settings into a base config
- get_preset(): look up preset by name
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from autocontext.config.presets import LONG_RUN_PRESET_SETTINGS, SHORT_RUN_PRESET_SETTINGS


class RunPreset(BaseModel):
    """Named preset with settings overrides."""

    name: str
    description: str
    settings: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunPreset:
        return cls.model_validate(data)


LONG_RUN_PRESET = RunPreset(
    name="long_run",
    description="Safe defaults for 10+ generation runs with all anti-stall safeguards",
    settings=dict(LONG_RUN_PRESET_SETTINGS),
)

SHORT_RUN_PRESET = RunPreset(
    name="short_run",
    description="Lightweight defaults for 1-5 generation runs",
    settings=dict(SHORT_RUN_PRESET_SETTINGS),
)

_PRESET_REGISTRY: dict[str, RunPreset] = {
    "long_run": LONG_RUN_PRESET,
    "short_run": SHORT_RUN_PRESET,
}


def get_preset(name: str) -> RunPreset | None:
    """Look up a preset by name."""
    return _PRESET_REGISTRY.get(name)


def apply_preset(
    base: dict[str, Any],
    preset: RunPreset | None,
) -> dict[str, Any]:
    """Merge preset settings into a base config dict."""
    if preset is None:
        return dict(base)
    result = dict(base)
    result.update(preset.settings)
    return result
