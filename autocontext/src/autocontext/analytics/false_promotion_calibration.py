"""Deterministic operating-characteristic simulation for AC-986 policies."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.context_bundles.false_promotion import CampaignFalsePromotionPolicy

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
) -> FalsePromotionCalibrationResult:
    """Measure full-campaign promotion probability under adaptive challengers."""

    if campaigns < 1 or max_candidates < 1:
        raise ValueError("calibration campaigns and max_candidates must be positive")
    if case.independent_blocks < 2 or case.heldout_blocks < 1:
        raise ValueError("calibration cases require at least two confirmation and one held-out block")
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
            confirmation_blocks += len(confirmation)
            allocated_alpha = policy.alpha_for_candidate(candidate_index)
            if _clears_confirmation(policy, confirmation, allocated_alpha) and statistics.fmean(heldout) > 0.0:
                promotions += 1
                break
    return FalsePromotionCalibrationResult(
        name=case.name,
        promotion_rate=promotions / campaigns,
        average_candidates_evaluated=candidates_evaluated / campaigns,
        average_confirmation_blocks=confirmation_blocks / campaigns,
        campaigns=campaigns,
    )


def _clears_confirmation(
    policy: CampaignFalsePromotionPolicy,
    effects: list[float],
    allocated_alpha: float,
) -> bool:
    if policy.robust_method == "bounded_hoeffding":
        if any(effect < policy.effect_lower_bound or effect > policy.effect_upper_bound for effect in effects):
            return False
        width = policy.effect_upper_bound - policy.effect_lower_bound
        lower = statistics.fmean(effects) - width * math.sqrt(
            math.log(1.0 / allocated_alpha) / (2.0 * len(effects))
        )
        return lower > 0.0
    confidence_z = round(NormalDist().inv_cdf(1.0 - allocated_alpha / 2.0), 12)
    _, confidence_low, _ = paired_confidence_interval(effects, confidence_z)
    return confidence_low is not None and confidence_low > 0.0


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
