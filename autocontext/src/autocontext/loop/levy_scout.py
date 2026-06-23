from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Intensity = Literal["local", "scout", "jump"]


@dataclass(frozen=True, slots=True)
class LevyScoutConfig:
    enabled: bool = False
    alpha: float = 1.5
    scale: float = 0.2


@dataclass(frozen=True, slots=True)
class LevyScoutMutation:
    enabled: bool
    random_value: float
    step_size: float
    intensity: Intensity
    alpha: float
    scale: float

    def model_dump(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "random_value": self.random_value,
            "step_size": self.step_size,
            "intensity": self.intensity,
            "alpha": self.alpha,
            "scale": self.scale,
        }


def levy_scout_random_value(seed_base: int, generation: int, attempt: int = 0) -> float:
    value = 2_166_136_261
    for byte in f"levy:{seed_base}:{generation}:{attempt}".encode():
        value = ((value ^ byte) * 16_777_619) & 0xFFFFFFFF
    return value / 0x1_0000_0000


def levy_scout_step_size(random_value: float, *, alpha: float = 1.5, scale: float = 0.2) -> float:
    safe_alpha = max(float(alpha), 1e-9)
    safe_scale = max(float(scale), 0.0)
    clamped = min(max(float(random_value), 1e-12), 1.0 - 1e-12)
    return float(min(1.0, safe_scale / ((1.0 - clamped) ** (1.0 / safe_alpha))))


def levy_scout_intensity(step_size: float) -> Intensity:
    if step_size < 0.33:
        return "local"
    if step_size < 0.66:
        return "scout"
    return "jump"


def evaluate_levy_scout(
    config: LevyScoutConfig,
    *,
    seed_base: int,
    generation: int,
    attempt: int = 0,
) -> LevyScoutMutation:
    random_value = levy_scout_random_value(seed_base, generation, attempt)
    step_size = levy_scout_step_size(random_value, alpha=config.alpha, scale=config.scale)
    return LevyScoutMutation(
        enabled=config.enabled,
        random_value=random_value,
        step_size=step_size,
        intensity=levy_scout_intensity(step_size),
        alpha=config.alpha,
        scale=config.scale,
    )


def render_levy_scout_guidance(
    config: LevyScoutConfig,
    *,
    seed_base: int,
    generation: int,
    attempt: int = 0,
) -> str:
    if not config.enabled:
        return ""
    outcome = evaluate_levy_scout(config, seed_base=seed_base, generation=generation, attempt=attempt)
    verb = {
        "local": "adjust one or two parameters while preserving the current approach",
        "scout": "try a noticeably different mix of tactics without ignoring proven constraints",
        "jump": "make a broad scout jump and rethink the strategy shape",
    }[outcome.intensity]
    return (
        "Lévy scout mutation guidance:\n"
        f"- intensity: {outcome.intensity}\n"
        f"- step_size: {outcome.step_size:.3f}\n"
        f"- instruction: {verb}."
    )
