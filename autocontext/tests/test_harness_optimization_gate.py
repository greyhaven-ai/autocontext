"""Tests for the recompute-both weight-versioned promotion gate (AC-877).

Exercises the opt-in ``harness_promotion`` path of ``evaluate_advancement``:
challenger vs incumbent scores are always recomputed from raw components under
the SAME current weights, so a stale stored score can never enter the gate.
"""

from __future__ import annotations

from autocontext.harness.pipeline.advancement import (
    AdvancementMetrics,
    HarnessPromotionInputs,
    evaluate_advancement,
)
from autocontext.harness_optimization.contract.models import Components, Weights
from autocontext.harness_optimization.scoring import harness_promotion_score

# The harness-promotion path never reads ``metrics``; a fixed placeholder makes
# that explicit across every case below.
_UNUSED_METRICS = AdvancementMetrics(
    best_score=0.0,
    mean_score=0.0,
    previous_best=0.0,
    score_variance=0.0,
    sample_count=1,
)

# Standard weight set used by most cases. Cost, error, and variance are
# genuine penalties so the gate is not driven by dense quality alone.
_WEIGHTS = Weights(
    sparse_success_weight=1.0,
    token_cost_weight=0.1,
    error_weight=1.0,
    variance_weight=1.0,
)


def _components(
    *,
    dense: float,
    sparse: float = 0.0,
    tpm: float = 0.0,
    err: float = 0.0,
    var: float = 0.0,
) -> Components:
    return Components(
        dense_quality_score=dense,
        sparse_success_rate=sparse,
        tokens_per_million=tpm,
        error_rate=err,
        score_variance=var,
    )


def test_positive_advance_beats_incumbent_by_margin() -> None:
    challenger = _components(dense=0.9, sparse=0.8, tpm=1.0)
    incumbent = _components(dense=0.7, sparse=0.5, tpm=1.0)
    rationale = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=challenger,
            incumbent_components=incumbent,
            weights=_WEIGHTS,
            weight_version="v1",
            min_margin=0.05,
        ),
    )
    assert rationale.decision == "advance"
    assert rationale.component_scores["promotion_margin"] > 0.05


def test_no_advance_when_within_margin() -> None:
    challenger = _components(dense=0.72, sparse=0.5, tpm=1.0)
    incumbent = _components(dense=0.70, sparse=0.5, tpm=1.0)
    rationale = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=challenger,
            incumbent_components=incumbent,
            weights=_WEIGHTS,
            weight_version="v1",
            min_margin=0.05,
        ),
    )
    assert rationale.decision == "rollback"
    assert 0.0 < rationale.component_scores["promotion_margin"] <= 0.05


def test_no_advance_when_challenger_worse() -> None:
    challenger = _components(dense=0.6, sparse=0.4)
    incumbent = _components(dense=0.9, sparse=0.6)
    rationale = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=challenger,
            incumbent_components=incumbent,
            weights=_WEIGHTS,
            weight_version="v1",
            min_margin=0.05,
        ),
    )
    assert rationale.decision == "rollback"
    assert rationale.component_scores["promotion_margin"] < 0.0


def test_cost_regression_flips_would_advance_to_no_advance() -> None:
    # On dense quality alone the challenger (1.5) beats the incumbent (1.0),
    # but a high tokens_per_million re-scores it below the incumbent.
    challenger = _components(dense=1.5, tpm=20.0)
    incumbent = _components(dense=1.0, tpm=1.0)

    # Sanity: challenger wins on dense quality alone.
    assert challenger.dense_quality_score > incumbent.dense_quality_score

    rationale = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=challenger,
            incumbent_components=incumbent,
            weights=_WEIGHTS,
            weight_version="v1",
            min_margin=0.05,
        ),
    )
    assert rationale.decision == "rollback"
    # Cost penalty is what sinks it: challenger score is now below incumbent.
    assert rationale.component_scores["challenger_score"] < rationale.component_scores["incumbent_score"]


def test_high_variance_flips_would_advance_to_no_advance() -> None:
    weights = Weights(
        sparse_success_weight=1.0,
        token_cost_weight=0.1,
        error_weight=1.0,
        variance_weight=5.0,
    )
    # Same challenger that beats the incumbent on dense quality.
    incumbent = _components(dense=1.0)

    low_variance = _components(dense=1.2, var=0.0)
    high_variance = _components(dense=1.2, var=0.2)

    # Control: with no variance the challenger advances.
    control = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=low_variance,
            incumbent_components=incumbent,
            weights=weights,
            weight_version="v1",
            min_margin=0.05,
        ),
    )
    assert control.decision == "advance"

    # High variance re-scores the identical dense quality below the margin.
    rationale = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=high_variance,
            incumbent_components=incumbent,
            weights=weights,
            weight_version="v1",
            min_margin=0.05,
        ),
    )
    assert rationale.decision == "rollback"


def test_no_incumbent_is_promotable() -> None:
    challenger = _components(dense=0.5, sparse=0.3, tpm=2.0)
    rationale = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=challenger,
            incumbent_components=None,
            weights=_WEIGHTS,
            weight_version="v1",
            min_margin=0.05,
        ),
    )
    assert rationale.decision == "advance"
    assert "incumbent_score" not in rationale.component_scores
    assert rationale.metadata["incumbent_components"] is None


def test_stale_weight_incumbent_is_rescored_under_current_weights() -> None:
    """Never-stale proof.

    The incumbent's components were recorded under an OLD weight set that
    ignored token cost (token_cost_weight=0). Under the CURRENT weights the
    incumbent's high token cost is penalized. Because the gate recomputes
    both scores from raw components, the challenger correctly advances — even
    though a naive comparison against the incumbent's stale stored score would
    (wrongly) reject it.
    """
    old_weights = Weights(
        sparse_success_weight=1.0,
        token_cost_weight=0.0,  # old regime ignored token cost
        error_weight=1.0,
        variance_weight=1.0,
    )
    current_weights = Weights(
        sparse_success_weight=1.0,
        token_cost_weight=0.1,  # current regime penalizes token cost
        error_weight=1.0,
        variance_weight=1.0,
    )

    # Incumbent looked great under the old weights (high token cost was free).
    incumbent = _components(dense=0.9, tpm=5.0)
    challenger = _components(dense=0.6, tpm=0.0)

    stale_incumbent_score = harness_promotion_score(incumbent, old_weights)
    fresh_challenger_score = harness_promotion_score(challenger, current_weights)
    current_incumbent_score = harness_promotion_score(incumbent, current_weights)

    # A NAIVE stale comparison (fresh challenger vs stored old-weight score)
    # would reject the challenger...
    assert fresh_challenger_score - stale_incumbent_score < 0.05
    # ...but the correct recompute-both comparison accepts it.
    assert fresh_challenger_score - current_incumbent_score > 0.05

    rationale = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=challenger,
            incumbent_components=incumbent,
            weights=current_weights,
            weight_version="v2",
            min_margin=0.05,
        ),
    )
    # The gate advances (recompute-both), never using the stale stored score.
    assert rationale.decision == "advance"
    assert rationale.component_scores["incumbent_score"] == current_incumbent_score
    assert rationale.metadata["weight_version"] == "v2"


def test_rationale_records_scores_weight_version_components_and_margin() -> None:
    challenger = _components(dense=0.9, sparse=0.8, tpm=1.0, err=0.05, var=0.01)
    incumbent = _components(dense=0.7, sparse=0.5, tpm=1.0)
    rationale = evaluate_advancement(
        _UNUSED_METRICS,
        harness_promotion=HarnessPromotionInputs(
            challenger_components=challenger,
            incumbent_components=incumbent,
            weights=_WEIGHTS,
            weight_version="v7",
            min_margin=0.05,
        ),
    )
    scores = rationale.component_scores
    assert "challenger_score" in scores
    assert "incumbent_score" in scores
    assert "promotion_margin" in scores
    # Score components are surfaced for the audit trail.
    assert scores["dense_quality_score"] == 0.9
    assert scores["sparse_success_rate"] == 0.8
    assert scores["tokens_per_million"] == 1.0
    assert scores["error_rate"] == 0.05
    assert scores["score_variance"] == 0.01
    # Weight version and raw component breakdown live in metadata.
    assert rationale.metadata["weight_version"] == "v7"
    assert rationale.metadata["challenger_components"]["dense_quality_score"] == 0.9
    assert rationale.metadata["incumbent_components"]["dense_quality_score"] == 0.7
    # Margin equals the recomputed challenger minus incumbent score.
    assert scores["promotion_margin"] == scores["challenger_score"] - scores["incumbent_score"]
