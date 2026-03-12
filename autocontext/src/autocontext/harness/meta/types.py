"""Meta-optimization types — role metrics, profiles, and recommendations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoleMetric:
    """Single observation of a role's performance in one generation."""

    role: str
    generation: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost: float
    gate_decision: str  # "advance", "retry", "rollback" for the generation
    score_delta: float  # score change for the generation

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class RoleProfile:
    """Aggregated performance profile for a role across multiple generations."""

    role: str
    generations_observed: int
    advance_rate: float  # fraction of generations that advanced
    mean_tokens: float  # mean total tokens per generation
    mean_latency_ms: float
    mean_cost_per_gen: float  # mean cost per generation
    cost_per_advance: float  # total cost / number of advances (infinity if 0 advances)
    token_efficiency: float  # mean score_delta per 1000 tokens (positive deltas only)


@dataclass(frozen=True, slots=True)
class ConfigRecommendation:
    """Recommended configuration change based on performance data."""

    role: str
    parameter: str  # "model", "max_tokens", "temperature", "cadence"
    current_value: str
    recommended_value: str
    confidence: float  # 0.0 to 1.0
    rationale: str
