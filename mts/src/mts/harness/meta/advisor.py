"""Configuration advisor — recommends changes based on performance profiles."""

from __future__ import annotations

import math
from dataclasses import dataclass

from mts.harness.meta.profiler import PerformanceProfiler
from mts.harness.meta.types import ConfigRecommendation, RoleProfile

# Model tiers (cheaper -> more expensive)
MODEL_TIERS: list[list[str]] = [
    ["claude-haiku-4-5-20251001"],
    ["claude-sonnet-4-5-20250929"],
    ["claude-opus-4-6"],
]


@dataclass
class AdvisorConfig:
    """Thresholds for advisor recommendations."""

    high_advance_rate: float = 0.7  # above this -> consider downgrade
    low_advance_rate: float = 0.3  # below this -> consider upgrade
    high_cost_per_advance: float = 0.5  # above this -> consider cadence change
    min_generations: int = 5  # minimum data before recommending


def _model_tier(model: str) -> int:
    for i, tier in enumerate(MODEL_TIERS):
        if model in tier:
            return i
    return 1  # default to middle tier


def _cheaper_model(model: str) -> str | None:
    tier = _model_tier(model)
    if tier > 0:
        return MODEL_TIERS[tier - 1][0]
    return None


def _more_capable_model(model: str) -> str | None:
    tier = _model_tier(model)
    if tier < len(MODEL_TIERS) - 1:
        return MODEL_TIERS[tier + 1][0]
    return None


class ConfigAdvisor:
    """Recommends configuration changes based on measured performance profiles."""

    def __init__(
        self,
        profiler: PerformanceProfiler,
        current_config: dict[str, str] | None = None,
        config: AdvisorConfig | None = None,
    ) -> None:
        self._profiler = profiler
        self._current_config = current_config or {}
        self._config = config or AdvisorConfig()

    def recommend(self) -> list[ConfigRecommendation]:
        profiles = self._profiler.all_profiles()
        recommendations: list[ConfigRecommendation] = []

        for role, profile in profiles.items():
            if profile.generations_observed < self._config.min_generations:
                continue
            recommendations.extend(self._check_model(role, profile))
            recommendations.extend(self._check_cadence(role, profile))

        return recommendations

    def summary(self) -> str:
        recs = self.recommend()
        if not recs:
            return "No recommendations (insufficient data or all roles performing well)."
        lines = ["# Configuration Recommendations", ""]
        for r in recs:
            lines.append(
                f"- **{r.role}** -> {r.parameter}: "
                f"`{r.current_value}` -> `{r.recommended_value}` "
                f"(confidence: {r.confidence:.0%}) -- {r.rationale}"
            )
        return "\n".join(lines)

    def _check_model(self, role: str, profile: RoleProfile) -> list[ConfigRecommendation]:
        current_model = self._current_config.get(f"model_{role}", "")
        if not current_model:
            return []

        recs: list[ConfigRecommendation] = []

        # High advance rate + expensive model -> try cheaper
        if profile.advance_rate >= self._config.high_advance_rate:
            cheaper = _cheaper_model(current_model)
            if cheaper:
                confidence = min(0.9, (profile.advance_rate - self._config.high_advance_rate) * 3 + 0.5)
                recs.append(
                    ConfigRecommendation(
                        role=role,
                        parameter="model",
                        current_value=current_model,
                        recommended_value=cheaper,
                        confidence=round(confidence, 2),
                        rationale=(
                            f"advance rate {profile.advance_rate:.0%} suggests a cheaper model "
                            f"may suffice (cost/gen: ${profile.mean_cost_per_gen:.4f})"
                        ),
                    )
                )

        # Low advance rate + cheap model -> try more capable
        if profile.advance_rate <= self._config.low_advance_rate:
            stronger = _more_capable_model(current_model)
            if stronger:
                confidence = min(0.9, (self._config.low_advance_rate - profile.advance_rate) * 3 + 0.5)
                recs.append(
                    ConfigRecommendation(
                        role=role,
                        parameter="model",
                        current_value=current_model,
                        recommended_value=stronger,
                        confidence=round(confidence, 2),
                        rationale=(
                            f"advance rate {profile.advance_rate:.0%} suggests a more capable model "
                            f"may improve outcomes"
                        ),
                    )
                )

        return recs

    def _check_cadence(self, role: str, profile: RoleProfile) -> list[ConfigRecommendation]:
        if not math.isfinite(profile.cost_per_advance):
            return []
        if profile.cost_per_advance <= self._config.high_cost_per_advance:
            return []

        confidence = min(0.8, (profile.cost_per_advance / self._config.high_cost_per_advance - 1.0) * 0.4 + 0.4)
        return [
            ConfigRecommendation(
                role=role,
                parameter="cadence",
                current_value="every generation",
                recommended_value="every 2-3 generations",
                confidence=round(confidence, 2),
                rationale=(
                    f"cost per advance ${profile.cost_per_advance:.4f} is high; "
                    f"running less frequently may improve cost efficiency"
                ),
            )
        ]
