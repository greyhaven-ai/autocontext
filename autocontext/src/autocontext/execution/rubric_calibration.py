"""Rubric calibration — human anchors, judge variance, and alignment (AC-283).

Infrastructure for validating LLM-generated rubrics against human baselines.
Measures judge repeatability, computes alignment between human and LLM scores,
and defines per-domain tolerance thresholds.

Key types:
- CalibrationAnchor: a human-scored output with score band and notes
- CalibrationSet: collection of anchors for one domain
- JudgeVarianceResult: variance metrics from repeat-judging same output
- AlignmentResult: alignment between human and judge scores
- AlignmentTolerance: per-domain tolerance thresholds
- CalibrationReport: aggregate calibration report
- measure_judge_variance(): compute variance from repeated scores
- compute_alignment(): compute correlation, bias, MAE from score pairs
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CalibrationAnchor:
    """A human-scored output serving as calibration reference."""

    anchor_id: str
    domain: str
    output_text: str
    human_score: float
    score_band: str  # poor, fair, good, excellent
    human_notes: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "domain": self.domain,
            "output_text": self.output_text,
            "human_score": self.human_score,
            "score_band": self.score_band,
            "human_notes": self.human_notes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationAnchor:
        return cls(
            anchor_id=data["anchor_id"],
            domain=data.get("domain", ""),
            output_text=data.get("output_text", ""),
            human_score=data.get("human_score", 0.0),
            score_band=data.get("score_band", ""),
            human_notes=data.get("human_notes", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class CalibrationSet:
    """Collection of calibration anchors for one domain."""

    domain: str
    anchors: list[CalibrationAnchor]
    metadata: dict[str, Any] = field(default_factory=dict)

    def score_bands(self) -> dict[str, list[CalibrationAnchor]]:
        """Group anchors by score band."""
        bands: dict[str, list[CalibrationAnchor]] = {}
        for anchor in self.anchors:
            bands.setdefault(anchor.score_band, []).append(anchor)
        return bands

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "anchors": [a.to_dict() for a in self.anchors],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationSet:
        return cls(
            domain=data["domain"],
            anchors=[CalibrationAnchor.from_dict(a) for a in data.get("anchors", [])],
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class JudgeVarianceResult:
    """Variance metrics from repeat-judging the same output."""

    mean: float
    variance: float
    std_dev: float
    range: float
    num_samples: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": self.mean,
            "variance": self.variance,
            "std_dev": self.std_dev,
            "range": self.range,
            "num_samples": self.num_samples,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JudgeVarianceResult:
        return cls(
            mean=data.get("mean", 0.0),
            variance=data.get("variance", 0.0),
            std_dev=data.get("std_dev", 0.0),
            range=data.get("range", 0.0),
            num_samples=data.get("num_samples", 0),
        )


def measure_judge_variance(scores: list[float]) -> JudgeVarianceResult:
    """Compute variance metrics from repeated judge scores on the same output."""
    if not scores:
        return JudgeVarianceResult(mean=0.0, variance=0.0, std_dev=0.0, range=0.0, num_samples=0)

    mean = statistics.mean(scores)
    var = statistics.pvariance(scores) if len(scores) > 1 else 0.0
    std = math.sqrt(var)
    score_range = round(max(scores) - min(scores), 6)

    return JudgeVarianceResult(
        mean=round(mean, 6),
        variance=round(var, 6),
        std_dev=round(std, 6),
        range=score_range,
        num_samples=len(scores),
    )


@dataclass(slots=True)
class AlignmentResult:
    """Alignment between human scores and judge scores."""

    mean_absolute_error: float
    bias: float  # positive = judge overestimates
    correlation: float
    num_pairs: int
    per_anchor_errors: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_absolute_error": self.mean_absolute_error,
            "bias": self.bias,
            "correlation": self.correlation,
            "num_pairs": self.num_pairs,
            "per_anchor_errors": self.per_anchor_errors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlignmentResult:
        return cls(
            mean_absolute_error=data.get("mean_absolute_error", 0.0),
            bias=data.get("bias", 0.0),
            correlation=data.get("correlation", 0.0),
            num_pairs=data.get("num_pairs", 0),
            per_anchor_errors=data.get("per_anchor_errors", []),
        )


def _pearson_correlation(xs: list[float], ys: list[float]) -> float:
    """Compute Pearson correlation coefficient."""
    n = len(xs)
    if n < 2:
        return 0.0

    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)

    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denom_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))

    if denom_x == 0 or denom_y == 0:
        return 0.0

    return numerator / (denom_x * denom_y)


def compute_alignment(
    human_scores: list[float],
    judge_scores: list[float],
) -> AlignmentResult:
    """Compute alignment between human and judge scores."""
    if not human_scores or not judge_scores:
        return AlignmentResult(
            mean_absolute_error=0.0, bias=0.0, correlation=0.0,
            num_pairs=0, per_anchor_errors=[],
        )

    n = min(len(human_scores), len(judge_scores))
    hs = human_scores[:n]
    js = judge_scores[:n]

    errors = [abs(j - h) for h, j in zip(hs, js, strict=True)]
    biases = [j - h for h, j in zip(hs, js, strict=True)]

    mae = round(statistics.mean(errors), 6)
    bias = round(statistics.mean(biases), 6)
    corr = round(_pearson_correlation(hs, js), 4)

    return AlignmentResult(
        mean_absolute_error=mae,
        bias=bias,
        correlation=corr,
        num_pairs=n,
        per_anchor_errors=[round(e, 6) for e in errors],
    )


@dataclass(slots=True)
class AlignmentTolerance:
    """Per-domain tolerance thresholds for acceptable alignment."""

    domain: str
    max_mean_absolute_error: float
    max_bias: float
    min_correlation: float

    def check(self, alignment: AlignmentResult) -> dict[str, Any]:
        """Check alignment against tolerance thresholds."""
        violations: list[str] = []

        if alignment.mean_absolute_error > self.max_mean_absolute_error:
            violations.append(
                f"mean_absolute_error {alignment.mean_absolute_error:.4f} "
                f"> max {self.max_mean_absolute_error:.4f}"
            )
        if abs(alignment.bias) > self.max_bias:
            violations.append(
                f"bias {alignment.bias:.4f} > max {self.max_bias:.4f}"
            )
        if alignment.correlation < self.min_correlation and alignment.num_pairs >= 3:
            violations.append(
                f"correlation {alignment.correlation:.4f} "
                f"< min {self.min_correlation:.4f}"
            )

        return {
            "passes": len(violations) == 0,
            "violations": violations,
            "domain": self.domain,
        }


@dataclass(slots=True)
class CalibrationReport:
    """Aggregate calibration report for one domain."""

    domain: str
    num_anchors: int
    alignment: AlignmentResult
    variance: JudgeVarianceResult
    calibrated: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"Calibration Report: {self.domain}",
            f"Anchors: {self.num_anchors}",
            f"MAE: {self.alignment.mean_absolute_error:.4f}",
            f"Bias: {self.alignment.bias:+.4f}",
            f"Correlation: {self.alignment.correlation:.4f}",
            f"Judge variance (std): {self.variance.std_dev:.4f}",
            f"Judge variance (range): {self.variance.range:.4f}",
            f"Calibrated: {'yes' if self.calibrated else 'no'}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "num_anchors": self.num_anchors,
            "alignment": self.alignment.to_dict(),
            "variance": self.variance.to_dict(),
            "calibrated": self.calibrated,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationReport:
        return cls(
            domain=data["domain"],
            num_anchors=data.get("num_anchors", 0),
            alignment=AlignmentResult.from_dict(data.get("alignment", {})),
            variance=JudgeVarianceResult.from_dict(data.get("variance", {})),
            calibrated=data.get("calibrated", False),
            metadata=data.get("metadata", {}),
        )
