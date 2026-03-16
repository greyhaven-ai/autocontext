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
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from autocontext.execution.judge import LLMJudge
from autocontext.providers.base import LLMProvider


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
    if not human_scores and not judge_scores:
        return AlignmentResult(
            mean_absolute_error=0.0, bias=0.0, correlation=0.0,
            num_pairs=0, per_anchor_errors=[],
        )

    if len(human_scores) != len(judge_scores):
        raise ValueError(
            "human_scores and judge_scores must have the same length; "
            f"got {len(human_scores)} and {len(judge_scores)}",
        )

    hs = human_scores
    js = judge_scores

    errors = [abs(j - h) for h, j in zip(hs, js, strict=True)]
    biases = [j - h for h, j in zip(hs, js, strict=True)]

    mae = round(statistics.mean(errors), 6)
    bias = round(statistics.mean(biases), 6)
    corr = round(_pearson_correlation(hs, js), 4)

    return AlignmentResult(
        mean_absolute_error=mae,
        bias=bias,
        correlation=corr,
        num_pairs=len(hs),
        per_anchor_errors=[round(e, 6) for e in errors],
    )


@dataclass(slots=True)
class AlignmentTolerance:
    """Per-domain tolerance thresholds for acceptable alignment."""

    domain: str
    max_mean_absolute_error: float
    max_bias: float
    min_correlation: float

    @classmethod
    def default_for_domain(cls, domain: str) -> AlignmentTolerance:
        """Default domain tolerances for phase-1 within-domain calibration."""
        normalized = domain.lower()
        if any(token in normalized for token in ["drug", "interaction", "l19"]):
            return cls(
                domain=domain,
                max_mean_absolute_error=0.12,
                max_bias=0.08,
                min_correlation=0.85,
            )
        if any(token in normalized for token in ["clinical", "trial", "protocol", "l16"]):
            return cls(
                domain=domain,
                max_mean_absolute_error=0.15,
                max_bias=0.10,
                min_correlation=0.80,
            )
        return cls(
            domain=domain,
            max_mean_absolute_error=0.15,
            max_bias=0.10,
            min_correlation=0.80,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "max_mean_absolute_error": self.max_mean_absolute_error,
            "max_bias": self.max_bias,
            "min_correlation": self.min_correlation,
        }

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


def _score_band_for_score(score: float) -> str:
    if score < 0.4:
        return "poor"
    if score < 0.7:
        return "fair"
    if score < 0.9:
        return "good"
    return "excellent"


def calibration_set_from_examples(
    domain: str,
    examples: Iterable[dict[str, Any]],
) -> CalibrationSet:
    """Build a calibration set from persisted human-feedback examples."""
    anchors: list[CalibrationAnchor] = []
    for idx, example in enumerate(examples):
        human_score = example.get("human_score")
        if human_score is None:
            continue
        score = float(human_score)
        anchors.append(
            CalibrationAnchor(
                anchor_id=str(example.get("id", idx)),
                domain=domain,
                output_text=str(example.get("agent_output", "")),
                human_score=score,
                score_band=str(example.get("score_band") or _score_band_for_score(score)),
                human_notes=str(example.get("human_notes", "")),
                metadata={
                    "created_at": example.get("created_at"),
                    "generation_id": example.get("generation_id"),
                },
            ),
        )
    return CalibrationSet(
        domain=domain,
        anchors=anchors,
        metadata={
            "source": "human_feedback",
            "cross_domain_normalization": "explicit second phase",
        },
    )


def _aggregate_variance(results: list[JudgeVarianceResult]) -> JudgeVarianceResult:
    """Aggregate per-anchor repeatability into one domain-level summary."""
    if not results:
        return JudgeVarianceResult(mean=0.0, variance=0.0, std_dev=0.0, range=0.0, num_samples=0)

    return JudgeVarianceResult(
        mean=round(statistics.mean(r.mean for r in results), 6),
        variance=round(statistics.mean(r.variance for r in results), 6),
        std_dev=round(statistics.mean(r.std_dev for r in results), 6),
        range=round(max(r.range for r in results), 6),
        num_samples=sum(r.num_samples for r in results),
    )


def run_judge_calibration(
    *,
    domain: str,
    task_prompt: str,
    rubric: str,
    provider: LLMProvider,
    model: str,
    calibration_examples: list[dict[str, Any]],
    reference_context: str | None = None,
    required_concepts: list[str] | None = None,
    repeat_judgments: int = 3,
    tolerance: AlignmentTolerance | None = None,
) -> CalibrationReport | None:
    """Run live rubric calibration against stored human anchors."""
    calibration_set = calibration_set_from_examples(domain, calibration_examples)
    if len(calibration_set.anchors) < 2:
        return None

    tolerance = tolerance or AlignmentTolerance.default_for_domain(domain)
    judge = LLMJudge(
        model=model or provider.default_model(),
        rubric=rubric,
        provider=provider,
        samples=1,
        temperature=0.0,
    )

    human_scores: list[float] = []
    judge_means: list[float] = []
    per_anchor_variance: dict[str, dict[str, Any]] = {}

    for anchor in calibration_set.anchors:
        leave_one_out = [
            example
            for example in calibration_examples
            if str(example.get("id")) != anchor.anchor_id
        ]
        repeated_scores: list[float] = []
        for _ in range(max(1, repeat_judgments)):
            result = judge.evaluate(
                task_prompt=task_prompt,
                agent_output=anchor.output_text,
                reference_context=reference_context,
                required_concepts=required_concepts,
                calibration_examples=leave_one_out if leave_one_out else None,
            )
            repeated_scores.append(result.score)

        variance = measure_judge_variance(repeated_scores)
        per_anchor_variance[anchor.anchor_id] = variance.to_dict()
        human_scores.append(anchor.human_score)
        judge_means.append(variance.mean)

    alignment = compute_alignment(human_scores, judge_means)
    aggregate_variance = _aggregate_variance(
        [JudgeVarianceResult.from_dict(data) for data in per_anchor_variance.values()],
    )
    tolerance_check = tolerance.check(alignment)

    return CalibrationReport(
        domain=domain,
        num_anchors=len(calibration_set.anchors),
        alignment=alignment,
        variance=aggregate_variance,
        calibrated=tolerance_check["passes"],
        metadata={
            "tolerance": tolerance.to_dict(),
            "tolerance_check": tolerance_check,
            "per_anchor_variance": per_anchor_variance,
            "cross_domain_normalization": "explicit second phase",
        },
    )
