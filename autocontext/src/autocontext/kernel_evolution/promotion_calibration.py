"""Deterministic calibration for the finite-sample kernel promotion policy."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from autocontext.kernel_evolution.finite_sample import _all_win_p_value_bound, minimum_sign_eprocess_blocks
from autocontext.kernel_evolution.protocols import MAX_FINITE_SAMPLE_BLOCKS, KernelDecisionPolicy

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class _CalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class KernelCalibrationScenarioResult(_CalibrationModel):
    name: Literal["null", "heavy-tail", "drift", "autocorrelation", "heteroskedasticity"]
    dependence_model: str
    trials: int = Field(ge=1)
    false_promotions: int = Field(ge=0)
    observed_familywise_error: Annotated[FiniteFloat, Field(ge=0, le=1)]
    simulation_upper_tolerance: Annotated[FiniteFloat, Field(gt=0, lt=1)]
    passed_calibration: bool

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.false_promotions > self.trials:
            raise ValueError("calibration false promotions cannot exceed trials")
        expected = self.false_promotions / self.trials
        if abs(float(self.observed_familywise_error) - expected) > 1e-15:
            raise ValueError("calibration error rate disagrees with its counts")
        return self


class KernelCalibrationReport(_CalibrationModel):
    """Policy-bound theorem statement plus deterministic stress simulations."""

    schema_version: Literal["autocontext.kernel-promotion-calibration/v1"] = (
        "autocontext.kernel-promotion-calibration/v1"
    )
    method: Literal["paired-sign-eprocess/v1"] = "paired-sign-eprocess/v1"
    theorem: Literal["conditional-sign-supermartingale-plus-bonferroni/v1"] = (
        "conditional-sign-supermartingale-plus-bonferroni/v1"
    )
    assumption: Literal["conditional-threshold-win-probability-lte-half/v1"] = (
        "conditional-threshold-win-probability-lte-half/v1"
    )
    decision_policy_id: Digest
    simulation_seed_digest: Digest
    block_count: int = Field(ge=2, le=MAX_FINITE_SAMPLE_BLOCKS)
    proposal_cap: int = Field(ge=1)
    familywise_alpha: Annotated[FiniteFloat, Field(gt=0, lt=0.5)]
    per_look_alpha: Annotated[FiniteFloat, Field(gt=0, lt=0.5)]
    exact_per_look_bound: Annotated[FiniteFloat, Field(gt=0, le=1)]
    exact_familywise_bound: Annotated[FiniteFloat, Field(gt=0, le=1)]
    scenarios: tuple[KernelCalibrationScenarioResult, ...]

    @model_validator(mode="after")
    def validate_guarantee(self) -> Self:
        if {item.name for item in self.scenarios} != {
            "null",
            "heavy-tail",
            "drift",
            "autocorrelation",
            "heteroskedasticity",
        }:
            raise ValueError("calibration must cover every required timing-noise scenario")
        expected_per_look_alpha = float(self.familywise_alpha) / self.proposal_cap
        if float(self.per_look_alpha) != expected_per_look_alpha:
            raise ValueError("calibration per-look alpha disagrees with its Bonferroni budget")
        expected_per_look_bound = _all_win_p_value_bound(0.5, self.block_count)
        expected_familywise_bound = min(1.0, self.proposal_cap * expected_per_look_bound)
        if float(self.exact_per_look_bound) != expected_per_look_bound:
            raise ValueError("calibration per-look bound disagrees with its fixed sign design")
        if float(self.exact_familywise_bound) != expected_familywise_bound:
            raise ValueError("calibration familywise bound disagrees with its proposal budget")
        if self.exact_per_look_bound > self.per_look_alpha:
            raise ValueError("finite-sample per-look bound exceeds its alpha allocation")
        if self.exact_familywise_bound > self.familywise_alpha:
            raise ValueError("finite-sample familywise bound exceeds the configured budget")
        if any(
            item.passed_calibration
            != (
                float(item.observed_familywise_error)
                <= float(self.familywise_alpha) + float(item.simulation_upper_tolerance)
            )
            for item in self.scenarios
        ):
            raise ValueError("calibration pass flags disagree with observed error and tolerance")
        if any(not item.passed_calibration for item in self.scenarios):
            raise ValueError("one or more timing-noise scenarios failed calibration")
        return self

    @property
    def report_id(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _noise_null(rng: random.Random, blocks: int) -> list[float]:
    return [rng.gauss(0.0, 1.0) for _ in range(blocks)]


def _noise_heavy_tail(rng: random.Random, blocks: int) -> list[float]:
    values = []
    for _ in range(blocks):
        magnitude = rng.paretovariate(1.5) - 1.0
        values.append(magnitude if rng.random() < 0.5 else -magnitude)
    return values


def _noise_drift(rng: random.Random, blocks: int) -> list[float]:
    # Shared monotone clock drift is canceled by the paired block. Its changing
    # residual scale remains, while a fresh symmetric sign preserves the
    # receipt's conditional null assumption.
    return [
        (1.0 if rng.random() < 0.5 else -1.0)
        * (0.5 + 1.2 * block / max(1, blocks - 1) + abs(rng.gauss(0.0, 0.25)))
        for block in range(blocks)
    ]


def _noise_autocorrelation(rng: random.Random, blocks: int) -> list[float]:
    # Magnitudes remain strongly autocorrelated across inferential blocks. A
    # fresh symmetric sign per block is exactly the weaker conditional-sign
    # assumption; neither values nor magnitudes are treated as independent.
    state = abs(rng.gauss(0.0, 1.0))
    values = []
    for _ in range(blocks):
        state = 0.85 * state + math.sqrt(1.0 - 0.85**2) * abs(rng.gauss(0.0, 1.0))
        values.append(state if rng.random() < 0.5 else -state)
    return values


def _noise_heteroskedastic(rng: random.Random, blocks: int) -> list[float]:
    return [rng.gauss(0.0, 0.25 + 2.0 * block / max(1, blocks - 1)) for block in range(blocks)]


_SCENARIOS: tuple[tuple[str, str, Callable[[random.Random, int], list[float]]], ...] = (
    ("null", "independent symmetric block noise", _noise_null),
    ("heavy-tail", "independent symmetric Pareto block noise", _noise_heavy_tail),
    ("drift", "paired shared drift with symmetric changing-scale residuals", _noise_drift),
    ("autocorrelation", "AR(1) magnitudes across blocks with conditional symmetric signs", _noise_autocorrelation),
    ("heteroskedasticity", "independent symmetric blocks with increasing variance", _noise_heteroskedastic),
)


def calibrate_kernel_promotion(
    policy: KernelDecisionPolicy,
    *,
    trials: int = 4_096,
    seed_material: str = "autocontext-ac1004-calibration-v1",
) -> KernelCalibrationReport:
    """Stress the exact configured block/proposal design with fixed seeds."""

    statistics_policy = policy.statistics
    sequential = policy.sequential_testing
    if (
        policy.schema_version != "autocontext.kernel-decision-policy/v2"
        or statistics_policy.method != "paired-sign-eprocess/v1"
        or sequential is None
        or statistics_policy.null_win_probability is None
    ):
        raise ValueError("calibration requires a complete v2 finite-sample decision policy")
    if trials < 1_000:
        raise ValueError("calibration requires at least 1000 repeated campaigns")
    blocks = statistics_policy.min_timing_blocks
    required = minimum_sign_eprocess_blocks(
        sequential.per_proposal_alpha,
        null_win_probability=float(statistics_policy.null_win_probability),
    )
    if blocks < required:
        raise ValueError("configured block count cannot resolve the per-look alpha")
    exact_per_look = _all_win_p_value_bound(float(statistics_policy.null_win_probability), blocks)
    exact_familywise = min(1.0, sequential.proposal_cap * exact_per_look)
    scenario_results: list[KernelCalibrationScenarioResult] = []
    for name, dependence_model, sample_noise in _SCENARIOS:
        seed = hashlib.sha256(f"{seed_material}:{name}".encode()).digest()
        rng = random.Random(int.from_bytes(seed[:8], "big"))
        false_promotions = 0
        for _ in range(trials):
            promoted = False
            for _proposal in range(sequential.proposal_cap):
                if all(value >= 0.0 for value in sample_noise(rng, blocks)):
                    promoted = True
                    break
            false_promotions += int(promoted)
        observed = false_promotions / trials
        # A deterministic three-sigma Monte Carlo allowance, with a small
        # floor for rare events, makes this a stress diagnostic rather than a
        # second and noisy source of the theorem claim.
        standard_error = math.sqrt(max(exact_familywise * (1.0 - exact_familywise), 1.0 / trials) / trials)
        tolerance = min(0.49, max(0.01, 3.0 * standard_error))
        scenario_results.append(
            KernelCalibrationScenarioResult(
                name=name,  # type: ignore[arg-type]
                dependence_model=dependence_model,
                trials=trials,
                false_promotions=false_promotions,
                observed_familywise_error=observed,
                simulation_upper_tolerance=tolerance,
                passed_calibration=observed <= float(sequential.familywise_alpha) + tolerance,
            )
        )
    return KernelCalibrationReport(
        decision_policy_id=policy.policy_id,
        simulation_seed_digest=f"sha256:{hashlib.sha256(seed_material.encode()).hexdigest()}",
        block_count=blocks,
        proposal_cap=sequential.proposal_cap,
        familywise_alpha=sequential.familywise_alpha,
        per_look_alpha=sequential.per_proposal_alpha,
        exact_per_look_bound=exact_per_look,
        exact_familywise_bound=exact_familywise,
        scenarios=tuple(scenario_results),
    )


__all__ = [
    "KernelCalibrationReport",
    "KernelCalibrationScenarioResult",
    "calibrate_kernel_promotion",
]
