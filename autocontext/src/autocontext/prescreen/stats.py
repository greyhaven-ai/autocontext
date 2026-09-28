"""Task-clustered bootstrap intervals (design section 5): rounds from one task are not independent."""

from __future__ import annotations

import random
from collections.abc import Hashable, Sequence

from autocontext.harness.benchmark_stats import quantile


def cluster_ratio_interval(
    numerators: Sequence[float],
    denominators: Sequence[float],
    groups: Sequence[Hashable],
    *,
    seed: int = 0,
    resamples: int = 2000,
) -> tuple[float, float] | None:
    """95% percentile interval for sum(num) / sum(den), resampling whole groups. None when no draw has a denominator."""
    if not (len(numerators) == len(denominators) == len(groups)):
        raise ValueError("numerators, denominators and groups must align")
    names = sorted(set(groups), key=repr)
    if not names:
        return None
    index = {name: i for i, name in enumerate(names)}
    num = [0.0] * len(names)
    den = [0.0] * len(names)
    for n, d, g in zip(numerators, denominators, groups, strict=True):
        num[index[g]] += n
        den[index[g]] += d
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(resamples):
        picks = [rng.randrange(len(names)) for _ in names]
        total = sum(den[i] for i in picks)
        if total > 0:
            draws.append(sum(num[i] for i in picks) / total)
    if not draws:
        return None
    low, high = quantile(draws, 0.025), quantile(draws, 0.975)
    if low is None or high is None:
        return None
    return low, high


def cluster_mean_difference_interval(
    a: Sequence[float],
    b: Sequence[float],
    groups: Sequence[Hashable],
    *,
    seed: int = 0,
    resamples: int = 2000,
) -> tuple[float, float] | None:
    """95% interval for the mean of (a - b) over paired items, resampling whole groups."""
    differences = [x - y for x, y in zip(a, b, strict=True)]
    return cluster_ratio_interval(differences, [1.0] * len(differences), groups, seed=seed, resamples=resamples)
