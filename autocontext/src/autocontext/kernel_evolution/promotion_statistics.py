"""Deterministic paired statistics for kernel promotion."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections.abc import Sequence

MIN_BOOTSTRAP_TAIL_DRAWS = 100


def percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def geometric_mean_ratio(numerators: Sequence[float], denominators: Sequence[float]) -> float:
    mean_log = statistics.fmean(math.log(num) - math.log(den) for num, den in zip(numerators, denominators, strict=True))
    result = math.exp(mean_log)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("derived geometric mean ratio is not a positive finite number")
    return result


def minimum_bootstrap_samples(alpha: float) -> int:
    """Return the minimum sample count that can resolve ``alpha``.

    A minimally resolved empirical percentile is not useful production
    evidence: rank one or two is dominated by Monte Carlo noise. Require at
    least ``MIN_BOOTSTRAP_TAIL_DRAWS`` expected draws in the requested tail.
    Keep this calculation next to the statistic so every caller uses the same
    fail-closed contract.
    """
    if not math.isfinite(alpha) or not 0 < alpha < 0.5:
        raise ValueError("alpha must be finite and in (0, 0.5)")
    required = MIN_BOOTSTRAP_TAIL_DRAWS / alpha
    return max(1, math.ceil(math.nextafter(required, -math.inf)))


def bootstrap_lcb(
    blocks: Sequence[tuple[float, float]],
    *,
    samples: int,
    seed_material: str,
    alpha: float,
) -> float:
    required_samples = minimum_bootstrap_samples(alpha)
    if samples < required_samples:
        raise ValueError(
            f"bootstrap_samples ({samples}) cannot resolve alpha={alpha:.12g}; "
            f"at least {required_samples} samples are required"
        )
    if not blocks:
        raise ValueError("bootstrap lower bounds require at least one paired block")
    logs = [math.log(incumbent) - math.log(candidate) for candidate, incumbent in blocks]
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    bootstrapped: list[float] = []
    for _ in range(samples):
        mean_log = statistics.fmean(logs[rng.randrange(len(logs))] for _ in logs)
        value = math.exp(mean_log)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("derived bootstrap speedup is not a positive finite number")
        bootstrapped.append(value)
    return percentile(bootstrapped, alpha)
