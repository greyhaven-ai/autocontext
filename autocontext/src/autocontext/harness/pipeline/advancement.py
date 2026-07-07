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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from autocontext.harness_optimization.calibration import cite_margin_vs_noise
from autocontext.harness_optimization.contract.models import Components, Weights
from autocontext.harness_optimization.scoring import beats_incumbent, harness_promotion_score

if TYPE_CHECKING:
    from autocontext.harness_optimization.contract.models import CalibrationReport

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


@dataclass(slots=True)
class HarnessPromotionInputs:
    """Opt-in inputs for the recompute-both harness promotion gate (AC-877).

    Carries only RAW component metrics (never a stored score) for the
    challenger and the optional incumbent, plus the weights and their
    version tag. The gate recomputes BOTH scores from these components
    under the SAME current weights, so a stale stored score can never
    enter the comparison.

    Stale-weight safety: if the incumbent's components were recorded under
    an older weight version, they are STILL re-scored under the current
    ``weights`` here (because the score is derived from raw components, not
    read from storage). There is no code path where a stored score computed
    under an old weight version is compared against a fresh challenger score.
    """

    challenger_components: Components
    weights: Weights
    weight_version: str
    min_margin: float = 0.0
    incumbent_components: Components | None = None


def _evaluate_harness_promotion(inputs: HarnessPromotionInputs) -> AdvancementRationale:
    """Recompute-both, weight-versioned promotion gate (AC-877).

    Recomputes the challenger score (and the incumbent score when present)
    from raw components under ``inputs.weights`` and advances only when the
    challenger beats the incumbent by more than ``inputs.min_margin``. A
    challenger with no incumbent is promotable.
    """
    challenger = inputs.challenger_components
    weights = inputs.weights
    weight_version = inputs.weight_version
    challenger_score = harness_promotion_score(challenger, weights)

    components: dict[str, float] = {
        "challenger_score": challenger_score,
        "dense_quality_score": challenger.dense_quality_score,
        "sparse_success_rate": challenger.sparse_success_rate,
        "tokens_per_million": challenger.tokens_per_million,
        "error_rate": challenger.error_rate,
        "score_variance": challenger.score_variance,
    }
    metadata: dict[str, Any] = {
        "harness_promotion": True,
        "weight_version": weight_version,
        "min_margin": inputs.min_margin,
        "challenger_components": challenger.model_dump(),
        "weights": weights.model_dump(),
    }

    if inputs.incumbent_components is None:
        components["promotion_margin"] = challenger_score
        metadata["incumbent_components"] = None
        return AdvancementRationale(
            decision="advance",
            reason=(f"No incumbent: challenger promotable at score {challenger_score:.4f} (weights {weight_version})"),
            component_scores=components,
            binding_checks=["harness_promotion_score"],
            proxy_signals=[],
            risk_flags=[],
            metadata=metadata,
        )

    incumbent = inputs.incumbent_components
    incumbent_score = harness_promotion_score(incumbent, weights)
    margin = challenger_score - incumbent_score
    # Decision comes from the shared never-stale primitive, which recomputes
    # both scores under the same weights, identical to the margin above.
    advances = beats_incumbent(challenger, incumbent, weights, inputs.min_margin)

    components["incumbent_score"] = incumbent_score
    components["promotion_margin"] = margin
    metadata["incumbent_components"] = incumbent.model_dump()

    if advances:
        return AdvancementRationale(
            decision="advance",
            reason=(
                f"Challenger promotion score {challenger_score:.4f} beats incumbent "
                f"{incumbent_score:.4f} by {margin:.4f} (> margin {inputs.min_margin}, "
                f"weights {weight_version})"
            ),
            component_scores=components,
            binding_checks=["harness_promotion_score"],
            proxy_signals=[],
            risk_flags=[],
            metadata=metadata,
        )

    return AdvancementRationale(
        decision="rollback",
        reason=(
            f"Challenger promotion score {challenger_score:.4f} does not beat incumbent "
            f"{incumbent_score:.4f} by enough (margin {margin:.4f} <= {inputs.min_margin}, "
            f"weights {weight_version})"
        ),
        component_scores=components,
        binding_checks=["harness_promotion_score"],
        proxy_signals=[],
        risk_flags=["promotion_margin_below_threshold"],
        metadata=metadata,
    )


def _evaluate_advancement_core(
    metrics: AdvancementMetrics,
    *,
    min_delta: float = 0.005,
    max_retries: int = 3,
    retry_count: int = 0,
    harness_promotion: HarnessPromotionInputs | None = None,
) -> AdvancementRationale:
    """Evaluate whether a generation should advance, retry, or rollback.

    Multi-objective evaluation considering:
    1. Score delta (binding)
    2. Error rate (binding, vetoes advance)
    3. Confidence / sample agreement (risk flag)
    4. Resolved truth score (binding when present, overrides proxy)
    5. Score variance (risk flag)

    Opt-in harness-promotion path (AC-877): when ``harness_promotion`` is
    provided, the decision is driven by the recompute-both, weight-versioned
    promotion gate instead. When it is ``None`` (the default), this function
    behaves exactly as before and ``metrics`` alone drives the decision.
    """
    if harness_promotion is not None:
        return _evaluate_harness_promotion(harness_promotion)

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


def evaluate_advancement(
    metrics: AdvancementMetrics,
    *,
    min_delta: float = 0.005,
    max_retries: int = 3,
    retry_count: int = 0,
    harness_promotion: HarnessPromotionInputs | None = None,
    calibration: CalibrationReport | None = None,
) -> AdvancementRationale:
    """Evaluate advancement, optionally citing the margin against the noise floor.

    Delegates the whole decision to :func:`_evaluate_advancement_core`, so every
    decision path (advance, retry, rollback, harness-promotion) is scored exactly
    as before. This wrapper only adds an opt-in, caller-gated post-processing step:

    Noise calibration citation (AC-881): when the caller passes a
    ``calibration`` report, a single one-line citation of whether the current
    margin is above or below the estimated noise floor is appended to the
    rationale's ``proxy_signals``, and nothing else changes. When ``calibration``
    is ``None`` (the default) the returned rationale is byte-identical to the core
    evaluation. The caller decides whether to build a report and pass it (gated by
    ``harness_calibration_enabled``); this function never reads settings.
    """
    rationale = _evaluate_advancement_core(
        metrics,
        min_delta=min_delta,
        max_retries=max_retries,
        retry_count=retry_count,
        harness_promotion=harness_promotion,
    )
    if calibration is not None:
        rationale = rationale.model_copy(update={"proxy_signals": [*rationale.proxy_signals, cite_margin_vs_noise(calibration)]})
    return rationale
