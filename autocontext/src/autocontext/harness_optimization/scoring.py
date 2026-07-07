"""Pure harness promotion scorer (AC-877).

This module is the parity heart of AC-877: the same weighted formula is
implemented here and in ``ts/src/harness-optimization/scoring.ts``, and a shared
numeric fixture (``fixtures/harness-optimization/promotion-score/score-cases.json``)
proves both languages compute identical scores.

The formula is::

    harness_promotion_score = dense_quality_score
                            + sparse_success_weight * sparse_success_rate
                            - token_cost_weight     * tokens_per_million
                            - error_weight          * error_rate
                            - variance_weight       * score_variance

Signature choice: the scorer accepts the generated ``Components`` and ``Weights``
pydantic models (reading ``.dense_quality_score`` etc.) so it is trivially
testable from the raw numbers. ``components_from_dict`` / ``weights_from_dict``
are provided to build those models from plain dicts (fixtures, JSON payloads).

``beats_incumbent`` is the never-stale primitive: it always recomputes BOTH
scores under the SAME weights and never reads a stored score.
"""

from __future__ import annotations

from typing import Any

from autocontext.harness_optimization.contract.models import Components, Weights

__all__ = [
    "beats_incumbent",
    "components_from_dict",
    "harness_promotion_score",
    "weights_from_dict",
]


def components_from_dict(data: dict[str, Any]) -> Components:
    """Build a validated ``Components`` model from a plain dict."""
    return Components(**data)


def weights_from_dict(data: dict[str, Any]) -> Weights:
    """Build a validated ``Weights`` model from a plain dict."""
    return Weights(**data)


def harness_promotion_score(components: Components, weights: Weights) -> float:
    """Compute the weighted harness promotion score for one candidate.

    Reward is the dense quality score plus the weighted sparse success rate;
    the weighted token cost, error rate, and score variance are penalties.
    """
    return (
        components.dense_quality_score
        + weights.sparse_success_weight * components.sparse_success_rate
        - weights.token_cost_weight * components.tokens_per_million
        - weights.error_weight * components.error_rate
        - weights.variance_weight * components.score_variance
    )


def beats_incumbent(
    challenger_components: Components,
    incumbent_components: Components,
    weights: Weights,
    min_margin: float,
) -> bool:
    """Return whether the challenger beats the incumbent by more than ``min_margin``.

    Both scores are recomputed here from their components under the SAME
    weights, so the comparison can never read a stale stored score.
    """
    challenger_score = harness_promotion_score(challenger_components, weights)
    incumbent_score = harness_promotion_score(incumbent_components, weights)
    return (challenger_score - incumbent_score) > min_margin
