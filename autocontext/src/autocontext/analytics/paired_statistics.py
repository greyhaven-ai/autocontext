"""Shared statistics for matched incumbent/challenger observations."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence


def paired_confidence_interval(
    effects: Sequence[float],
    confidence_z: float,
) -> tuple[float | None, float | None, float | None]:
    """Return the mean and normal confidence bounds for paired effects."""

    if not effects:
        return None, None, None
    mean = statistics.fmean(effects)
    if len(effects) == 1:
        return mean, None, None
    half_width = confidence_z * statistics.stdev(effects) / math.sqrt(len(effects))
    return mean, mean - half_width, mean + half_width


__all__ = ["paired_confidence_interval"]
