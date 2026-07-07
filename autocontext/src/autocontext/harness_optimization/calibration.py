"""Noise calibration engine for the harness optimization loop.

Pure functions that turn a score series into a :class:`CalibrationReport`.
The float formulas here are mirrored by the TypeScript port and must agree to
1e-9, so the arithmetic order below is intentional and should not be changed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from autocontext.harness_optimization.contract.models import CalibrationReport


def compute_calibration(
    scores: Sequence[float],
    *,
    scenario_id: str,
    current_min_delta: float,
    max_trials: int,
    noise_multiplier: float = 2.0,
    noisy_cv_threshold: float = 0.25,
) -> CalibrationReport:
    """Compute noise statistics and gating recommendations for a score series.

    Args:
        scores: Observed scores for the scenario.
        scenario_id: Scenario or family the series came from.
        current_min_delta: The promotion margin currently configured.
        max_trials: Cost budget ceiling on recommended trial count.
        noise_multiplier: Multiplier on the standard error for the recommended margin.
        noisy_cv_threshold: Coefficient-of-variation threshold above which the
            sparse metric is judged too noisy to gate on.

    Returns:
        A :class:`CalibrationReport` describing the noise floor and recommendations.
    """
    # Clamp the budget to at least 1 so a degenerate 0/negative budget yields a sane count of 1.
    max_trials = max(1, max_trials)
    n = len(scores)
    mean = sum(scores) / n if n > 0 else 0.0
    variance = sum((x - mean) ** 2 for x in scores) / (n - 1) if n >= 2 else 0.0
    std_dev = math.sqrt(variance)
    standard_error = std_dev / math.sqrt(n) if n >= 2 else 0.0
    recommended_min_delta = noise_multiplier * standard_error

    if current_min_delta > 0 and std_dev > 0:
        k = math.ceil((std_dev / current_min_delta) ** 2)
    else:
        k = max_trials
    recommended_trial_count = max(1, min(k, max_trials))

    margin_vs_noise: Literal["above_noise", "below_noise"] = (
        "above_noise" if current_min_delta >= recommended_min_delta else "below_noise"
    )

    if abs(mean) > 0:
        sparse_metric_too_noisy = standard_error / abs(mean) > noisy_cv_threshold
    elif standard_error > 0:
        sparse_metric_too_noisy = True
    else:
        sparse_metric_too_noisy = False

    notes = f"SE={standard_error:.4f} over n={n}; margin {margin_vs_noise}"

    return CalibrationReport(
        schema_version=1,
        scenario_id=scenario_id,
        sample_size=n,
        mean=mean,
        variance=variance,
        std_dev=std_dev,
        standard_error=standard_error,
        recommended_min_delta=recommended_min_delta,
        recommended_trial_count=recommended_trial_count,
        current_min_delta=current_min_delta,
        margin_vs_noise=margin_vs_noise,
        sparse_metric_too_noisy=sparse_metric_too_noisy,
        notes=notes,
    )


# Numeric fields are rendered with a fixed 6-decimal format so the text is
# identical across languages. Do NOT use raw str(): Python str(2.0) -> "2.0"
# while JS String(2) -> "2", which would diverge. The TypeScript port must use
# the same 6-decimal fixed format (value.toFixed(6)) so the two reports match
# character-for-character.
def _fmt_float(value: float) -> str:
    return f"{value:.6f}"


_SPARSE_NOISE_LINE = "sparse metric too noisy: optimize a denser verifier signal instead"


def render_calibration_report(report: CalibrationReport) -> str:
    """Render a calibration report as a stable multi-line string.

    The wording is mirrored character-for-character by the TypeScript port, so
    every numeric field is formatted through :func:`_fmt_float` (6-decimal fixed)
    for the float fields and plain integer ``str`` for the counts.
    """
    lines = [
        f"calibration report: {report.scenario_id}",
        f"samples: {report.sample_size}",
        f"mean: {_fmt_float(report.mean)}",
        f"std_dev: {_fmt_float(report.std_dev)}",
        f"standard_error: {_fmt_float(report.standard_error)}",
        f"recommended_min_delta: {_fmt_float(report.recommended_min_delta)}",
        f"recommended_trial_count: {report.recommended_trial_count}",
        f"current_min_delta: {_fmt_float(report.current_min_delta)}",
        f"margin: {report.margin_vs_noise}",
    ]
    if report.sparse_metric_too_noisy:
        lines.append(_SPARSE_NOISE_LINE)
    return "\n".join(lines)


def cite_margin_vs_noise(report: CalibrationReport) -> str:
    """Render a one-line citation of the margin against the noise floor.

    Both numbers use the same 6-decimal fixed format as
    :func:`render_calibration_report` so the TypeScript port (toFixed(6))
    produces identical text.
    """
    return (
        f"margin {_fmt_float(report.current_min_delta)} is {report.margin_vs_noise} "
        f"(recommended >= {_fmt_float(report.recommended_min_delta)})"
    )
