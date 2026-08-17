"""Shared statistics for matched incumbent/challenger observations.

The implementation intentionally stays dependency-free.  In particular, small
matched samples use Student's t distribution rather than a normal approximation.
Callers that inspect the same experiment more than once can reserve the desired
family-wise error rate with ``max_looks``; a Bonferroni spending rule controls
simultaneous coverage at those pre-declared looks under the paired-t model.
"""

from __future__ import annotations

import math
import statistics
import sys
from collections.abc import Sequence
from functools import lru_cache


def paired_confidence_interval(
    effects: Sequence[float],
    confidence_z: float,
    *,
    max_looks: int = 1,
) -> tuple[float | None, float | None, float | None]:
    """Return a small-sample interval for the mean of paired effects.

    ``confidence_z`` remains the public configuration surface for backwards
    compatibility.  It is converted to the equivalent two-sided normal tail
    probability, which is then spent equally across ``max_looks``.  The actual
    interval uses a Student's t critical value with ``n - 1`` degrees of
    freedom.
    """

    if not effects:
        return None, None, None
    if not math.isfinite(confidence_z) or confidence_z <= 0:
        raise ValueError("confidence_z must be finite and positive")
    if max_looks < 1:
        raise ValueError("max_looks must be at least one")
    if any(not math.isfinite(effect) for effect in effects):
        raise ValueError("paired effects must be finite")
    mean = statistics.fmean(effects)
    if len(effects) == 1:
        return mean, None, None
    standard_error = statistics.stdev(effects) / math.sqrt(len(effects))
    if standard_error == 0.0:
        return mean, mean, mean

    # erfc(z / sqrt(2)) is the two-sided tail probability associated with z.
    # Spending it over every possible look controls the family-wise chance that
    # any interval misses the true effect, without assuming independent looks.
    family_alpha = math.erfc(confidence_z / math.sqrt(2.0))
    look_alpha = family_alpha / max_looks
    critical = _student_t_upper_quantile(look_alpha / 2.0, len(effects) - 1)
    if not math.isfinite(critical):
        return mean, -sys.float_info.max, sys.float_info.max
    half_width = critical * standard_error
    if not math.isfinite(half_width):
        return mean, -sys.float_info.max, sys.float_info.max
    return mean, mean - half_width, mean + half_width


@lru_cache(maxsize=256)
def _student_t_quantile(probability: float, degrees_of_freedom: int) -> float:
    """Numerically invert Student's t CDF for ``0 < probability < 1``."""

    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be between zero and one")
    if degrees_of_freedom < 1:
        raise ValueError("degrees_of_freedom must be positive")
    if probability == 0.5:
        return 0.0
    if probability < 0.5:
        return -_student_t_quantile(1.0 - probability, degrees_of_freedom)

    return _student_t_upper_quantile(1.0 - probability, degrees_of_freedom)


@lru_cache(maxsize=256)
def _student_t_upper_quantile(tail_probability: float, degrees_of_freedom: int) -> float:
    """Invert the positive Student-t survival function without ``1 - CDF`` cancellation."""

    if tail_probability < 0.0 or tail_probability >= 0.5:
        raise ValueError("tail_probability must be between zero and one half")
    if degrees_of_freedom < 1:
        raise ValueError("degrees_of_freedom must be positive")
    if tail_probability == 0.0:
        return math.inf

    lower = 0.0
    upper = 1.0
    while _student_t_survival(upper, degrees_of_freedom) > tail_probability:
        upper *= 2.0
        if not math.isfinite(upper):
            return upper
    for _ in range(80):
        midpoint = (lower + upper) / 2.0
        if _student_t_survival(midpoint, degrees_of_freedom) > tail_probability:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def _student_t_cdf(value: float, degrees_of_freedom: int) -> float:
    if value == 0.0:
        return 0.5
    tail = _student_t_survival(abs(value), degrees_of_freedom)
    return 1.0 - tail if value > 0.0 else tail


def _student_t_survival(value: float, degrees_of_freedom: int) -> float:
    degrees = float(degrees_of_freedom)
    beta_x = degrees / (degrees + value * value)
    return 0.5 * _regularized_incomplete_beta(beta_x, degrees / 2.0, 0.5)


def _regularized_incomplete_beta(value: float, alpha: float, beta: float) -> float:
    """Evaluate the regularized incomplete beta using a continued fraction."""

    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(alpha + beta)
        - math.lgamma(alpha)
        - math.lgamma(beta)
        + alpha * math.log(value)
        + beta * math.log1p(-value)
    )
    front = math.exp(log_front)
    if value < (alpha + 1.0) / (alpha + beta + 2.0):
        return front * _beta_continued_fraction(alpha, beta, value) / alpha
    return 1.0 - front * _beta_continued_fraction(beta, alpha, 1.0 - value) / beta


def _beta_continued_fraction(alpha: float, beta: float, value: float) -> float:
    """Lentz evaluation used by the incomplete-beta implementation."""

    max_iterations = 200
    epsilon = 3.0e-14
    tiny = 1.0e-300
    qab = alpha + beta
    qap = alpha + 1.0
    qam = alpha - 1.0
    numerator = 1.0
    denominator = 1.0 - qab * value / qap
    if abs(denominator) < tiny:
        denominator = tiny
    denominator = 1.0 / denominator
    result = denominator

    for iteration in range(1, max_iterations + 1):
        even_step = 2 * iteration
        coefficient = iteration * (beta - iteration) * value / ((qam + even_step) * (alpha + even_step))
        denominator = 1.0 + coefficient * denominator
        if abs(denominator) < tiny:
            denominator = tiny
        numerator = 1.0 + coefficient / numerator
        if abs(numerator) < tiny:
            numerator = tiny
        denominator = 1.0 / denominator
        result *= denominator * numerator

        coefficient = -(alpha + iteration) * (qab + iteration) * value / (
            (alpha + even_step) * (qap + even_step)
        )
        denominator = 1.0 + coefficient * denominator
        if abs(denominator) < tiny:
            denominator = tiny
        numerator = 1.0 + coefficient / numerator
        if abs(numerator) < tiny:
            numerator = tiny
        denominator = 1.0 / denominator
        delta = denominator * numerator
        result *= delta
        if abs(delta - 1.0) < epsilon:
            return result
    raise ArithmeticError("incomplete beta continued fraction did not converge")


__all__ = ["paired_confidence_interval"]
