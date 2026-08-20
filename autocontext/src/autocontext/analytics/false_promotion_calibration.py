"""Deterministic operating-characteristic simulation for AC-986 policies."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Literal

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.context_bundles.false_promotion import (
    CampaignFalsePromotionPolicy,
    required_confidence_z,
)
from autocontext.context_bundles.models import ConfirmationPolicy

CalibrationDistribution = Literal["normal", "heteroskedastic", "clustered", "bounded_heavy_tail"]


@dataclass(frozen=True, slots=True)
class FalsePromotionCalibrationCase:
    name: str
    true_effect: float
    distribution: CalibrationDistribution
    independent_blocks: int
    heldout_blocks: int = 8


@dataclass(frozen=True, slots=True)
class FalsePromotionCalibrationResult:
    name: str
    promotion_rate: float
    average_candidates_evaluated: float
    average_confirmation_blocks: float
    campaigns: int


def simulate_false_promotion_campaigns(
    policy: CampaignFalsePromotionPolicy,
    case: FalsePromotionCalibrationCase,
    *,
    campaigns: int = 2_000,
    max_candidates: int = 8,
    seed: int = 986,
    confirmation_policy: ConfirmationPolicy | None = None,
) -> FalsePromotionCalibrationResult:
    """Measure the sequential confirmation/held-out gate across challengers.

    Screen-lane generation and scoring are intentionally outside this
    statistical calibration, so the reported block cost covers confirmation
    sampling only rather than the full production evaluation workflow.
    """

    if campaigns < 1 or max_candidates < 1:
        raise ValueError("calibration campaigns and max_candidates must be positive")
    if case.independent_blocks < 2 or case.heldout_blocks < 1:
        raise ValueError("calibration cases require at least two confirmation and one held-out block")
    effective_confirmation_policy = confirmation_policy or ConfirmationPolicy(
        min_confirmation_pairs=min(6, case.independent_blocks),
        max_confirmation_pairs=case.independent_blocks,
        min_heldout_pairs=min(2, case.heldout_blocks),
    )
    if case.independent_blocks < effective_confirmation_policy.min_confirmation_pairs:
        raise ValueError("calibration case has fewer blocks than the confirmation minimum")
    if case.heldout_blocks < effective_confirmation_policy.min_heldout_pairs:
        raise ValueError("calibration case has fewer held-out blocks than the confirmation minimum")
    rng = random.Random(seed)
    promotions = 0
    candidates_evaluated = 0
    confirmation_blocks = 0
    for _ in range(campaigns):
        for candidate_index in range(max_candidates):
            candidates_evaluated += 1
            confirmation = [
                _sample_effect(rng, case, block_index)
                for block_index in range(case.independent_blocks)
            ]
            heldout = [
                _sample_effect(rng, case, block_index + case.independent_blocks)
                for block_index in range(case.heldout_blocks)
            ]
            allocated_alpha = policy.alpha_for_candidate(candidate_index)
            adjusted_policy = ConfirmationPolicy(
                **{
                    **effective_confirmation_policy.to_dict(),
                    "confidence_z": max(
                        effective_confirmation_policy.confidence_z,
                        required_confidence_z(allocated_alpha),
                    ),
                }
            )
            used_blocks, clears_confirmation = _sequential_confirmation(
                policy,
                confirmation,
                adjusted_policy,
            )
            confirmation_blocks += used_blocks
            heldout_effect = statistics.fmean(heldout[: adjusted_policy.min_heldout_pairs])
            if clears_confirmation and heldout_effect > adjusted_policy.min_effect:
                promotions += 1
                break
    return FalsePromotionCalibrationResult(
        name=case.name,
        promotion_rate=promotions / campaigns,
        average_candidates_evaluated=candidates_evaluated / campaigns,
        average_confirmation_blocks=confirmation_blocks / campaigns,
        campaigns=campaigns,
    )


def _sequential_confirmation(
    policy: CampaignFalsePromotionPolicy,
    effects: list[float],
    confirmation_policy: ConfirmationPolicy,
) -> tuple[int, bool]:
    maximum = min(len(effects), confirmation_policy.max_confirmation_pairs)
    max_looks = confirmation_policy.max_confirmation_pairs - confirmation_policy.min_confirmation_pairs + 1
    for count in range(confirmation_policy.min_confirmation_pairs, maximum + 1):
        sampled = effects[:count]
        _, ordinary_low, ordinary_high = paired_confidence_interval(
            sampled,
            confirmation_policy.confidence_z,
            max_looks=max_looks,
        )
        if ordinary_high is not None and ordinary_high <= confirmation_policy.min_effect:
            return count, False
        if ordinary_low is None or ordinary_low <= confirmation_policy.min_effect:
            continue
        if _clears_campaign_confirmation(policy, sampled, confirmation_policy):
            return count, True
    return maximum, False


def _clears_campaign_confirmation(
    policy: CampaignFalsePromotionPolicy,
    effects: list[float],
    confirmation_policy: ConfirmationPolicy,
) -> bool:
    max_looks = confirmation_policy.max_confirmation_pairs - confirmation_policy.min_confirmation_pairs + 1
    if policy.robust_method == "bounded_hoeffding":
        if any(effect < policy.effect_lower_bound or effect > policy.effect_upper_bound for effect in effects):
            return False
        family_alpha = math.erfc(confirmation_policy.confidence_z / math.sqrt(2.0))
        look_alpha = family_alpha / max_looks
        if look_alpha == 0.0:
            return False
        width = policy.effect_upper_bound - policy.effect_lower_bound
        lower = statistics.fmean(effects) - width * math.sqrt(
            math.log(1.0 / look_alpha) / (2.0 * len(effects))
        )
        return lower > confirmation_policy.min_effect
    _, confidence_low, _ = paired_confidence_interval(
        effects,
        confirmation_policy.confidence_z,
        max_looks=max_looks,
    )
    return confidence_low is not None and confidence_low > confirmation_policy.min_effect


def _sample_effect(
    rng: random.Random,
    case: FalsePromotionCalibrationCase,
    block_index: int,
) -> float:
    if case.distribution == "normal":
        return rng.gauss(case.true_effect, 0.2)
    if case.distribution == "heteroskedastic":
        return rng.gauss(case.true_effect, 0.08 if block_index % 2 == 0 else 0.35)
    if case.distribution == "clustered":
        # One draw is one fixture-level dependence block. Repeated seeds are
        # intentionally absent from the independent sample count.
        return rng.gauss(case.true_effect, 0.25)
    if case.distribution == "bounded_heavy_tail":
        if rng.random() < 0.12:
            return 1.0 if rng.random() < (1.0 + case.true_effect) / 2.0 else -1.0
        return max(-1.0, min(1.0, rng.gauss(case.true_effect, 0.08)))
    raise ValueError(f"unsupported calibration distribution: {case.distribution}")


__all__ = [
    "CalibrationDistribution",
    "FalsePromotionCalibrationCase",
    "FalsePromotionCalibrationResult",
    "simulate_false_promotion_campaigns",
]
