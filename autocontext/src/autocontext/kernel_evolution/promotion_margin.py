"""Exact aggregate-margin checks for finite-sample kernel evidence."""

from __future__ import annotations

from fractions import Fraction

from autocontext.kernel_evolution.models import KernelBenchmarkObservation


def _exact_median(values: list[Fraction]) -> Fraction:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def environment_drift_margin_passed(
    observation: KernelBenchmarkObservation,
    margin: float,
    *,
    finite_sample: bool,
) -> bool:
    """Compare v4 reference drift exactly while retaining legacy float behavior."""
    if not finite_sample:
        return observation.environment_drift_ratio is not None and observation.environment_drift_ratio <= margin
    report = observation.report
    if report is None or report.performance is None:
        return False
    reference = [Fraction(str(block.reference_ms)) for block in report.performance.blocks]
    quartile = max(1, len(reference) // 4)
    first = _exact_median(reference[:quartile])
    last = _exact_median(reference[-quartile:])
    return abs(last - first) <= first * Fraction(str(margin))


def peak_memory_fraction_passed(
    candidate_peak: int,
    device_capacity: int,
    fraction: float,
    *,
    finite_sample: bool,
) -> bool:
    """Compare v4 integer memory telemetry to its policy fraction exactly."""
    if finite_sample:
        return candidate_peak <= device_capacity * Fraction(str(fraction))
    return candidate_peak <= device_capacity * fraction


def finite_sample_aggregate_margin_passed(
    observation: KernelBenchmarkObservation,
    margin: float,
) -> bool:
    """Compare the geometric aggregate margin without rounding products."""
    report = observation.report
    if report is None or report.performance is None:
        return False
    candidate_product = Fraction(1)
    incumbent_product = Fraction(1)
    for block in report.performance.blocks:
        candidate_product *= Fraction(str(block.candidate_ms))
        incumbent_product *= Fraction(str(block.incumbent_ms))
    exact_margin = Fraction(str(margin))
    return candidate_product <= incumbent_product * (Fraction(1) - exact_margin) ** len(
        report.performance.blocks
    )


def finite_sample_tail_margin_passed(
    observation: KernelBenchmarkObservation,
    margin: float,
) -> bool:
    """Compare the tail-latency margin using exact canonical float spellings."""
    candidate_p95 = observation.candidate_p95_ms
    incumbent_p95 = observation.incumbent_p95_ms
    if candidate_p95 is None or incumbent_p95 is None:
        return False
    return Fraction(str(candidate_p95)) <= Fraction(str(incumbent_p95)) * (
        Fraction(1) + Fraction(str(margin))
    )


__all__ = [
    "environment_drift_margin_passed",
    "finite_sample_aggregate_margin_passed",
    "finite_sample_tail_margin_passed",
    "peak_memory_fraction_passed",
]
