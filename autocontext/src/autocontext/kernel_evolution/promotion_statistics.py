"""Deterministic paired statistics for kernel promotion."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections.abc import Sequence


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


def bootstrap_lcb(
    blocks: Sequence[tuple[float, float]],
    *,
    samples: int,
    seed_material: str,
    alpha: float,
) -> float:
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
