"""Finite-sample statistics and canonical receipts for kernel promotion."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from autocontext.kernel_evolution.protocols import KernelStatisticsPolicy

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
NonNegativeFiniteFloat = Annotated[FiniteFloat, Field(ge=0)]
PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]


def _canonical_digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class _FiniteSampleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class KernelDerivedStatisticsReceipt(_FiniteSampleModel):
    """Replayable derivation receipt for one authoritative v4 report."""

    schema_version: Literal["autocontext.kernel-derived-statistics/v1"] = (
        "autocontext.kernel-derived-statistics/v1"
    )
    method: Literal["paired-sign-eprocess/v1"] = "paired-sign-eprocess/v1"
    statistics_policy_id: Digest
    raw_report_digest: Digest
    raw_blocks_digest: Digest
    schedule_seed_material_digest: Digest
    sample_count: int = Field(ge=2)
    improvement_margin: Annotated[FiniteFloat, Field(ge=0, lt=1)]
    null_win_probability: Annotated[FiniteFloat, Field(gt=0, le=0.5)]
    betting_fraction: Annotated[FiniteFloat, Field(gt=0, le=1)]
    candidate_wins: int = Field(ge=0)
    non_wins: int = Field(ge=0)
    terminal_e_value_zeroed: bool
    log_terminal_e_value: NonNegativeFiniteFloat
    p_value_bound: Annotated[FiniteFloat, Field(ge=0, le=1)]
    per_look_alpha: Annotated[FiniteFloat, Field(gt=0, lt=0.5)]
    finite_sample_gate_passed: bool
    candidate_median_ms: PositiveFiniteFloat
    incumbent_median_ms: PositiveFiniteFloat
    reference_median_ms: PositiveFiniteFloat
    speedup_vs_incumbent: PositiveFiniteFloat
    speedup_vs_reference: PositiveFiniteFloat
    relative_improvement: FiniteFloat
    candidate_p95_ms: PositiveFiniteFloat
    incumbent_p95_ms: PositiveFiniteFloat
    environment_drift_ratio: NonNegativeFiniteFloat
    all_case_no_regression_passed: bool | None

    @model_validator(mode="after")
    def validate_evidence_arithmetic(self) -> Self:
        if self.candidate_wins + self.non_wins != self.sample_count:
            raise ValueError("finite-sample win counts must sum to sample_count")
        all_wins = self.non_wins == 0
        if self.terminal_e_value_zeroed == all_wins:
            raise ValueError("terminal e-value zero flag disagrees with the paired outcomes")
        expected_log = self.sample_count * math.log(1.0 / float(self.null_win_probability)) if all_wins else 0.0
        if abs(float(self.log_terminal_e_value) - expected_log) > 1e-12:
            raise ValueError("log terminal e-value does not replay from the paired outcomes")
        expected_p = math.exp(-expected_log) if all_wins else 1.0
        if abs(float(self.p_value_bound) - expected_p) > 1e-15:
            raise ValueError("finite-sample p-value bound does not replay from the paired outcomes")
        if self.finite_sample_gate_passed != (expected_p <= float(self.per_look_alpha)):
            raise ValueError("finite-sample gate disagrees with its p-value bound and alpha")
        return self

    @property
    def receipt_id(self) -> str:
        return _canonical_digest(self.model_dump(mode="json"))


def minimum_sign_eprocess_blocks(alpha: float, *, null_win_probability: float = 0.5) -> int:
    """Minimum all-success block count needed to cross ``1 / alpha``.

    With the pre-registered all-in sign bet, a success multiplies the e-value
    by ``1 / p0`` and any non-success reduces the terminal e-value to zero.
    """

    if not math.isfinite(alpha) or not 0 < alpha < 0.5:
        raise ValueError("alpha must be finite and in (0, 0.5)")
    if not math.isfinite(null_win_probability) or not 0 < null_win_probability <= 0.5:
        raise ValueError("null_win_probability must be finite and in (0, 0.5]")
    return max(2, math.ceil(math.log(alpha) / math.log(null_win_probability) - 1e-15))


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _geometric_mean_ratio(numerators: Sequence[float], denominators: Sequence[float]) -> float:
    log_ratios = (
        math.log(numerator) - math.log(denominator)
        for numerator, denominator in zip(numerators, denominators, strict=True)
    )
    return math.exp(statistics.fmean(log_ratios))


def derive_finite_sample_receipt(
    *,
    blocks: Sequence[tuple[float, float, float]],
    statistics_policy: KernelStatisticsPolicy,
    raw_report_digest: str,
    schedule_seed_material: str,
    per_look_alpha: float,
    all_case_no_regression_passed: bool | None,
) -> KernelDerivedStatisticsReceipt:
    """Derive every promotion-affecting v4 metric from raw paired blocks."""

    if statistics_policy.schema_version != "autocontext.kernel-statistics-policy/v2":
        raise ValueError("finite-sample receipts require a v2 statistics policy")
    if len(blocks) < statistics_policy.min_timing_blocks:
        raise ValueError("finite-sample evidence has fewer blocks than its statistics policy")
    assert statistics_policy.improvement_margin is not None
    assert statistics_policy.null_win_probability is not None
    assert statistics_policy.betting_fraction is not None
    if statistics_policy.betting_fraction != 1.0:
        raise ValueError("paired-sign-eprocess/v1 requires the pre-registered all-in bet")
    required = minimum_sign_eprocess_blocks(
        per_look_alpha,
        null_win_probability=float(statistics_policy.null_win_probability),
    )
    if statistics_policy.min_timing_blocks < required:
        raise ValueError(
            f"min_timing_blocks ({statistics_policy.min_timing_blocks}) cannot resolve alpha={per_look_alpha:.12g}; "
            f"at least {required} pre-registered paired blocks are required"
        )
    candidate = [float(item[0]) for item in blocks]
    incumbent = [float(item[1]) for item in blocks]
    reference = [float(item[2]) for item in blocks]
    if any(not math.isfinite(value) or value <= 0 for item in blocks for value in item):
        raise ValueError("finite-sample timing blocks must contain positive finite values")
    required_speedup = 1.0 / (1.0 - float(statistics_policy.improvement_margin))
    wins = sum((incumbent_ms / candidate_ms) + 1e-12 >= required_speedup for candidate_ms, incumbent_ms, _ in blocks)
    non_wins = len(blocks) - wins
    all_wins = non_wins == 0
    log_e_value = len(blocks) * math.log(1.0 / float(statistics_policy.null_win_probability)) if all_wins else 0.0
    p_value_bound = math.exp(-log_e_value) if all_wins else 1.0
    speedup_incumbent = _geometric_mean_ratio(incumbent, candidate)
    speedup_reference = _geometric_mean_ratio(reference, candidate)
    quartile = max(1, len(reference) // 4)
    drift = abs(statistics.median(reference[-quartile:]) / statistics.median(reference[:quartile]) - 1.0)
    return KernelDerivedStatisticsReceipt(
        statistics_policy_id=statistics_policy.policy_id,
        raw_report_digest=raw_report_digest,
        raw_blocks_digest=_canonical_digest(
            [
                {"block": index, "candidate_ms": item[0], "incumbent_ms": item[1], "reference_ms": item[2]}
                for index, item in enumerate(blocks)
            ]
        ),
        schedule_seed_material_digest=_canonical_digest({"schedule_seed_material": schedule_seed_material}),
        sample_count=len(blocks),
        improvement_margin=statistics_policy.improvement_margin,
        null_win_probability=statistics_policy.null_win_probability,
        betting_fraction=statistics_policy.betting_fraction,
        candidate_wins=wins,
        non_wins=non_wins,
        terminal_e_value_zeroed=not all_wins,
        log_terminal_e_value=log_e_value,
        p_value_bound=p_value_bound,
        per_look_alpha=per_look_alpha,
        finite_sample_gate_passed=p_value_bound <= per_look_alpha,
        candidate_median_ms=statistics.median(candidate),
        incumbent_median_ms=statistics.median(incumbent),
        reference_median_ms=statistics.median(reference),
        speedup_vs_incumbent=speedup_incumbent,
        speedup_vs_reference=speedup_reference,
        relative_improvement=1.0 - (1.0 / speedup_incumbent),
        candidate_p95_ms=_percentile(candidate, 0.95),
        incumbent_p95_ms=_percentile(incumbent, 0.95),
        environment_drift_ratio=drift,
        all_case_no_regression_passed=all_case_no_regression_passed,
    )


__all__ = [
    "KernelDerivedStatisticsReceipt",
    "derive_finite_sample_receipt",
    "minimum_sign_eprocess_blocks",
]
