"""Multi-objective advancement contract for generation gating (AC-322).

Defines the canonical metrics, rationale, and evaluation logic for
deciding whether a generation should advance, retry, or rollback.
Supports composite metrics (robustness, confidence, error rate),
separates search-proxy from resolved-truth scores, and makes gate
rationales auditable and operator-visible.

Key types:
- AdvancementMetrics: composite input to gate decisions
- AdvancementRationale: operator-visible explanation with component scores
- evaluate_advancement(): canonical multi-objective gate evaluation
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Thresholds
_ERROR_RATE_THRESHOLD = 0.2
_LOW_CONFIDENCE_THRESHOLD = 0.5
_HIGH_VARIANCE_THRESHOLD = 0.04


class AdvancementMetrics(BaseModel):
    """Composite metrics input to gate decisions."""

    best_score: float
    mean_score: float
    previous_best: float
    score_variance: float
    sample_count: int
    error_rate: float = 0.0
    crash_count: int = 0
    confidence: float = 1.0
    sample_agreement: float = 1.0
    search_proxy_score: float | None = None
    resolved_truth_score: float | None = None
    previous_resolved_truth_score: float | None = None
    generalization_gap: float | None = None
    cost_usd: float = 0.0
    tokens_used: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def delta(self) -> float:
        return round(self.best_score - self.previous_best, 6)

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["delta"] = self.delta
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdvancementMetrics:
        return cls.model_validate(data)


class AdvancementRationale(BaseModel):
    """Operator-visible gate decision explanation."""

    decision: str  # advance, retry, rollback
    reason: str
    component_scores: dict[str, float]
    binding_checks: list[str]
    proxy_signals: list[str]
    risk_flags: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdvancementRationale:
        return cls.model_validate(data)


def evaluate_advancement(
    metrics: AdvancementMetrics,
    *,
    min_delta: float = 0.005,
    max_retries: int = 3,
    retry_count: int = 0,
) -> AdvancementRationale:
    """Evaluate whether a generation should advance, retry, or rollback.

    Multi-objective evaluation considering:
    1. Score delta (binding)
    2. Error rate (binding — vetoes advance)
    3. Confidence / sample agreement (risk flag)
    4. Resolved truth score (binding when present, overrides proxy)
    5. Score variance (risk flag)
    """
    risk_flags: list[str] = []
    binding_checks: list[str] = ["score_delta"]
    proxy_signals: list[str] = []
    components: dict[str, float] = {}

    # 1. Score delta
    delta = metrics.delta
    components["score_delta"] = delta

    # 2. Error rate (binding veto)
    components["error_rate"] = metrics.error_rate
    if metrics.error_rate > _ERROR_RATE_THRESHOLD:
        risk_flags.append(f"error rate {metrics.error_rate:.0%} exceeds threshold {_ERROR_RATE_THRESHOLD:.0%}")
        binding_checks.append("error_rate")
        return AdvancementRationale(
            decision="rollback",
            reason=f"Error rate {metrics.error_rate:.0%} too high — vetoes advancement",
            component_scores=components,
            binding_checks=binding_checks,
            proxy_signals=proxy_signals,
            risk_flags=risk_flags,
        )

    # 3. Confidence / uncertainty
    components["confidence"] = metrics.confidence
    if metrics.confidence < _LOW_CONFIDENCE_THRESHOLD:
        risk_flags.append(f"low confidence {metrics.confidence:.2f}")
        proxy_signals.append("confidence")

    components["sample_agreement"] = metrics.sample_agreement
    if metrics.sample_agreement < _LOW_CONFIDENCE_THRESHOLD:
        risk_flags.append(f"low sample agreement {metrics.sample_agreement:.2f}")
        proxy_signals.append("sample_agreement")

    # 4. Score variance
    components["score_variance"] = metrics.score_variance
    if metrics.score_variance > _HIGH_VARIANCE_THRESHOLD:
        risk_flags.append(f"high variance {metrics.score_variance:.4f}")
        proxy_signals.append("score_variance")

    # 5. Resolved truth score (binding when present)
    if metrics.resolved_truth_score is not None:
        components["resolved_truth_score"] = metrics.resolved_truth_score
        binding_checks.append("resolved_truth_score")
        if metrics.previous_resolved_truth_score is not None:
            components["previous_resolved_truth_score"] = metrics.previous_resolved_truth_score
            truth_delta = round(metrics.resolved_truth_score - metrics.previous_resolved_truth_score, 6)
            components["truth_delta"] = truth_delta
            if truth_delta < min_delta:
                return AdvancementRationale(
                    decision="retry" if retry_count < max_retries else "rollback",
                    reason=(
                        f"Resolved truth score {metrics.resolved_truth_score:.4f} "
                        f"does not improve enough over prior truth {metrics.previous_resolved_truth_score:.4f} "
                        f"(delta {truth_delta:.4f} < {min_delta})"
                    ),
                    component_scores=components,
                    binding_checks=binding_checks,
                    proxy_signals=proxy_signals,
                    risk_flags=risk_flags,
                )
        else:
            risk_flags.append("resolved truth present without prior truth baseline")
    else:
        if metrics.search_proxy_score is not None:
            components["search_proxy_score"] = metrics.search_proxy_score
            proxy_signals.append("search_proxy_score")

    # 6. Main delta check — negative delta always rolls back
    if delta < 0:
        return AdvancementRationale(
            decision="rollback",
            reason=f"Score regressed by {abs(delta):.4f}",
            component_scores=components,
            binding_checks=binding_checks,
            proxy_signals=proxy_signals,
            risk_flags=[*risk_flags, "score_regression"],
        )

    if delta >= min_delta:
        return AdvancementRationale(
            decision="advance",
            reason=f"Score improved by {delta:.4f} (>= {min_delta})",
            component_scores=components,
            binding_checks=binding_checks,
            proxy_signals=proxy_signals,
            risk_flags=risk_flags,
        )

    if retry_count < max_retries:
        return AdvancementRationale(
            decision="retry",
            reason=f"Delta {delta:.4f} below threshold {min_delta}, retrying",
            component_scores=components,
            binding_checks=binding_checks,
            proxy_signals=proxy_signals,
            risk_flags=risk_flags,
        )

    return AdvancementRationale(
        decision="rollback",
        reason=f"Delta {delta:.4f} below threshold {min_delta} after max retries",
        component_scores=components,
        binding_checks=binding_checks,
        proxy_signals=proxy_signals,
        risk_flags=risk_flags,
    )
