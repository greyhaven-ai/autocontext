"""Small descriptive statistics shared by bounded reuse experiments."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence


def quantile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank quantile used by the original AC-1018 pilot."""
    if not 0 <= q <= 1:
        raise ValueError("quantile must be between zero and one")
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def paired_interval(differences: Sequence[float], *, seed: int, resamples: int) -> tuple[float, float]:
    """Conditional paired bootstrap; callers supply independent sampling units."""
    if not differences or resamples < 1:
        raise ValueError("paired observations and positive resamples required")
    rng = random.Random(seed)
    boot = [statistics.mean(rng.choices(differences, k=len(differences))) for _ in range(resamples)]
    low, high = quantile(boot, .025), quantile(boot, .975)
    assert low is not None and high is not None
    return low, high


def wilson_interval(successes: int, count: int) -> tuple[float, float] | None:
    """Descriptive 95% binomial interval; clustered fixtures need cluster analysis."""
    if not 0 <= successes <= count:
        raise ValueError("invalid success count")
    if not count:
        return None
    z = 1.959963984540054
    p = successes / count
    divisor = 1 + z * z / count
    center = (p + z * z / (2 * count)) / divisor
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / divisor
    return max(0, center - radius), min(1, center + radius)
