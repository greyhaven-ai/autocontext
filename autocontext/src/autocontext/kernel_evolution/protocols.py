"""Host-owned protocol controls for precision-safe kernel promotion."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

PrecisionProfileName = Literal["strict-fp32-v1", "relaxed-precision-v1"]
PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]
Probability = Annotated[FiniteFloat, Field(gt=0, lt=0.5)]


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


def _policy_digest(model: BaseModel) -> str:
    payload = json.dumps(
        model.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class KernelStatisticsPolicy(_ProtocolModel):
    """Host-owned derivation policy for benchmark observations.

    Version 1 is the historical empirical percentile bootstrap.  Version 2 is
    the finite-sample production policy: each pre-registered paired block is a
    success only when it clears the complete improvement margin, and a fixed
    sign e-test supplies the per-look error bound.  The v2 guarantee assumes
    the conditional probability of a threshold success is at most one half
    under the null; it does not assume Gaussian timing noise or independent
    block magnitudes.
    """

    schema_version: Literal[
        "autocontext.kernel-statistics-policy/v1",
        "autocontext.kernel-statistics-policy/v2",
    ] = "autocontext.kernel-statistics-policy/v1"
    method: Literal[
        "paired-percentile-bootstrap/v1",
        "paired-sign-eprocess/v1",
    ] = "paired-percentile-bootstrap/v1"
    bootstrap_samples: int | None = Field(default=None, ge=1, exclude_if=lambda value: value is None)
    seed_derivation: Literal[
        "sha256-baseline-hardware-protocol/v1",
        "sha256-plan-commitment-block-schedule/v1",
    ] = "sha256-baseline-hardware-protocol/v1"
    min_timing_blocks: int = Field(ge=2)
    require_resource_telemetry: bool
    max_gpu_memory_bytes: int | None = Field(default=None, ge=1)
    block_definition: Literal["balanced-interleaved-paired-block/v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    dependence_assumption: Literal["conditional-threshold-win-probability-lte-half/v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    null_win_probability: Annotated[FiniteFloat, Field(gt=0, le=0.5)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    betting_fraction: Annotated[FiniteFloat, Field(gt=0, le=1)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    improvement_margin: Annotated[FiniteFloat, Field(ge=0, lt=1)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_method_version(self) -> Self:
        finite_fields = (
            self.block_definition,
            self.dependence_assumption,
            self.null_win_probability,
            self.betting_fraction,
            self.improvement_margin,
        )
        if self.schema_version == "autocontext.kernel-statistics-policy/v1":
            if self.method != "paired-percentile-bootstrap/v1" or self.bootstrap_samples is None:
                raise ValueError("v1 statistics policy requires the paired percentile bootstrap")
            if any(value is not None for value in finite_fields):
                raise ValueError("v1 statistics policy cannot contain finite-sample fields")
            if self.seed_derivation != "sha256-baseline-hardware-protocol/v1":
                raise ValueError("v1 statistics policy has an invalid seed derivation")
            return self
        if self.method != "paired-sign-eprocess/v1" or self.bootstrap_samples is not None:
            raise ValueError("v2 statistics policy requires the paired sign e-process without bootstrap samples")
        if any(value is None for value in finite_fields):
            raise ValueError("v2 statistics policy requires the complete finite-sample contract")
        if self.seed_derivation != "sha256-plan-commitment-block-schedule/v1":
            raise ValueError("v2 statistics policy has an invalid schedule seed derivation")
        if self.null_win_probability != 0.5 or self.betting_fraction != 1.0:
            raise ValueError("paired-sign-eprocess/v1 requires p0=0.5 and the pre-registered all-in bet")
        return self

    @property
    def policy_id(self) -> str:
        return _policy_digest(self)


class KernelNumericalSemantics(_ProtocolModel):
    """Numerical behavior a candidate must preserve."""

    input_dtype: Literal["float32"] = "float32"
    minimum_input_precision: Literal["float32", "float16"]
    accumulation_dtype: Literal["float32"] = "float32"
    output_dtype: Literal["float32"] = "float32"
    input_downcast_allowed: bool

    @model_validator(mode="after")
    def validate_downcast_policy(self) -> Self:
        if self.input_downcast_allowed != (self.minimum_input_precision == "float16"):
            raise ValueError("input_downcast_allowed must agree with minimum_input_precision")
        return self


class KernelReferenceSemantics(_ProtocolModel):
    """Pinned reference implementation settings."""

    implementation: str
    precision: Literal["float32"] = "float32"
    tf32_allowed: bool = False
    deterministic_algorithms: bool = True

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if not self.implementation.strip():
            raise ValueError("reference implementation must not be empty")
        return self


class KernelInputDistribution(_ProtocolModel):
    """Public family constraints; exact holdout cases remain worker-private."""

    family: str
    required_shape_classes: tuple[str, ...]
    required_layouts: tuple[str, ...]
    required_value_classes: tuple[str, ...]
    required_slices: tuple[str, ...] = ("train", "holdout")

    @model_validator(mode="after")
    def validate_non_empty_classes(self) -> Self:
        if not self.family.strip():
            raise ValueError("input distribution family must not be empty")
        for name, values in (
            ("required_shape_classes", self.required_shape_classes),
            ("required_layouts", self.required_layouts),
            ("required_value_classes", self.required_value_classes),
            ("required_slices", self.required_slices),
        ):
            if not values or any(not value.strip() for value in values) or len(set(values)) != len(values):
                raise ValueError(f"{name} must contain unique, non-empty values")
        return self


class KernelEnforcementPolicy(_ProtocolModel):
    """Promotion gates owned by the harness rather than generated code."""

    require_every_correctness_slice: bool = True
    require_every_case_no_regression: bool = True
    require_paired_aggregate_performance: bool = True
    candidate_controls_protected: bool = True
    minimum_case_speedup_vs_incumbent: PositiveFiniteFloat = 0.98


class KernelProtocolSemantics(_ProtocolModel):
    """Named semantics bound into protocol and compatibility identity."""

    profile_name: PrecisionProfileName
    numerical: KernelNumericalSemantics
    reference: KernelReferenceSemantics
    inputs: KernelInputDistribution
    enforcement: KernelEnforcementPolicy = Field(default_factory=KernelEnforcementPolicy)

    @model_validator(mode="after")
    def validate_named_profile(self) -> Self:
        common = {
            "reference": {
                "implementation": "torch.matmul",
                "precision": "float32",
                "tf32_allowed": False,
                "deterministic_algorithms": True,
            },
            "enforcement": {
                "require_every_correctness_slice": True,
                "require_every_case_no_regression": True,
                "require_paired_aggregate_performance": True,
                "candidate_controls_protected": True,
                "minimum_case_speedup_vs_incumbent": 0.98,
            },
        }
        profile_specific = {
            "strict-fp32-v1": {
                "numerical": {
                    "input_dtype": "float32",
                    "minimum_input_precision": "float32",
                    "accumulation_dtype": "float32",
                    "output_dtype": "float32",
                    "input_downcast_allowed": False,
                },
                "inputs": {
                    "family": "matmul-generalization-v1",
                    "required_shape_classes": ("non-tile-square", "rectangular"),
                    "required_layouts": ("contiguous", "transposed"),
                    "required_value_classes": ("signed", "small", "large", "cancellation", "dynamic-range"),
                    "required_slices": ("train", "holdout"),
                },
            },
            "relaxed-precision-v1": {
                "numerical": {
                    "input_dtype": "float32",
                    "minimum_input_precision": "float16",
                    "accumulation_dtype": "float32",
                    "output_dtype": "float32",
                    "input_downcast_allowed": True,
                },
                "inputs": {
                    "family": "matmul-fixed-square-legacy-v1",
                    "required_shape_classes": ("tile-aligned-square",),
                    "required_layouts": ("contiguous",),
                    "required_value_classes": ("positive-unit",),
                    "required_slices": ("train", "holdout"),
                },
            },
        }[self.profile_name]
        if self.numerical.model_dump(mode="python") != profile_specific["numerical"]:
            raise ValueError(f"{self.profile_name} numerical semantics must match the canonical named profile")
        if self.reference.model_dump(mode="python") != common["reference"]:
            raise ValueError(f"{self.profile_name} reference semantics must match the canonical named profile")
        if self.inputs.model_dump(mode="python") != profile_specific["inputs"]:
            raise ValueError(f"{self.profile_name} input semantics must match the canonical named profile")
        if self.enforcement.model_dump(mode="python") != common["enforcement"]:
            raise ValueError(f"{self.profile_name} enforcement semantics must match the canonical named profile")
        return self


class KernelSequentialTestingPolicy(_ProtocolModel):
    """Bonferroni alpha spending for a bounded recursive search."""

    method: Literal["bonferroni"] = "bonferroni"
    proposal_cap: int = Field(default=10, ge=1, le=10_000)
    familywise_alpha: Probability = 0.05

    @property
    def per_proposal_alpha(self) -> float:
        return float(self.familywise_alpha) / self.proposal_cap

    @property
    def confidence_level(self) -> float:
        return 1.0 - self.per_proposal_alpha


class KernelMeasurementDesign(_ProtocolModel):
    """Receipt-bound timing-block construction and dependence assumption."""

    schema_version: Literal["autocontext.kernel-measurement-design/v1"] = (
        "autocontext.kernel-measurement-design/v1"
    )
    block_definition: Literal["balanced-interleaved-paired-block/v1"] = (
        "balanced-interleaved-paired-block/v1"
    )
    schedule_seed_derivation: Literal["sha256-plan-commitment-block-schedule/v1"] = (
        "sha256-plan-commitment-block-schedule/v1"
    )
    dependence_assumption: Literal["conditional-threshold-win-probability-lte-half/v1"] = (
        "conditional-threshold-win-probability-lte-half/v1"
    )
    fixed_block_count: int = Field(ge=2)
    early_stopping_allowed: Literal[False] = False
    order_balanced: Literal[True] = True


class KernelDecisionPolicy(_ProtocolModel):
    """Complete deterministic policy used to accept or reject an attempt."""

    schema_version: Literal[
        "autocontext.kernel-decision-policy/v1",
        "autocontext.kernel-decision-policy/v2",
    ] = "autocontext.kernel-decision-policy/v1"
    evidence_family_version: Literal["autocontext.kernel-evidence-family/v4"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    statistics: KernelStatisticsPolicy
    require_confirmation: bool
    min_relative_improvement: Annotated[FiniteFloat, Field(ge=0, lt=1)]
    require_confidence: bool
    max_p95_regression: Annotated[FiniteFloat, Field(ge=0, lt=1)]
    max_environment_drift: Annotated[FiniteFloat, Field(ge=0, lt=1)]
    max_peak_memory_fraction: Annotated[FiniteFloat, Field(ge=0, lt=1)]
    target_reference_speedup: PositiveFiniteFloat
    sequential_testing: KernelSequentialTestingPolicy | None = None

    @model_validator(mode="after")
    def validate_evidence_family(self) -> Self:
        statistics_v2 = self.statistics.schema_version == "autocontext.kernel-statistics-policy/v2"
        if self.schema_version == "autocontext.kernel-decision-policy/v2":
            if not statistics_v2 or self.evidence_family_version != "autocontext.kernel-evidence-family/v4":
                raise ValueError("v2 decision policy requires the complete v4 finite-sample evidence family")
            if self.sequential_testing is None:
                raise ValueError("v2 decision policy requires a bounded sequential-testing policy")
            if not self.require_confidence:
                raise ValueError("v2 decision policy cannot disable finite-sample evidence")
            if self.statistics.improvement_margin != self.min_relative_improvement:
                raise ValueError("finite-sample improvement margin must match the decision threshold")
        elif statistics_v2 or self.evidence_family_version is not None:
            raise ValueError("v1 decision policy cannot contain v4 finite-sample evidence fields")
        return self

    @property
    def policy_id(self) -> str:
        return _policy_digest(self)


class KernelSequentialEvidence(_ProtocolModel):
    """Persisted alpha-spending receipt for one evaluated proposal."""

    method: Literal["bonferroni"] = "bonferroni"
    proposal_index: int = Field(ge=1)
    proposal_cap: int = Field(ge=1)
    familywise_alpha: Probability
    per_proposal_alpha: Probability
    cumulative_alpha_spent: Probability
    confidence_level: Annotated[FiniteFloat, Field(gt=0.5, lt=1)]

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        expected = float(self.familywise_alpha) / self.proposal_cap
        if self.proposal_index > self.proposal_cap:
            raise ValueError("proposal_index exceeds proposal_cap")
        if abs(float(self.per_proposal_alpha) - expected) > 1e-15:
            raise ValueError("per_proposal_alpha disagrees with Bonferroni policy")
        if abs(float(self.cumulative_alpha_spent) - expected * self.proposal_index) > 1e-15:
            raise ValueError("cumulative_alpha_spent disagrees with proposal index")
        if abs(float(self.confidence_level) - (1.0 - expected)) > 1e-15:
            raise ValueError("confidence_level disagrees with per-proposal alpha")
        return self


STRICT_FP32_SEMANTICS = KernelProtocolSemantics(
    profile_name="strict-fp32-v1",
    numerical=KernelNumericalSemantics(
        minimum_input_precision="float32",
        input_downcast_allowed=False,
    ),
    reference=KernelReferenceSemantics(implementation="torch.matmul"),
    inputs=KernelInputDistribution(
        family="matmul-generalization-v1",
        required_shape_classes=("non-tile-square", "rectangular"),
        required_layouts=("contiguous", "transposed"),
        required_value_classes=("signed", "small", "large", "cancellation", "dynamic-range"),
    ),
)

RELAXED_PRECISION_SEMANTICS = KernelProtocolSemantics(
    profile_name="relaxed-precision-v1",
    numerical=KernelNumericalSemantics(
        minimum_input_precision="float16",
        input_downcast_allowed=True,
    ),
    reference=KernelReferenceSemantics(implementation="torch.matmul"),
    inputs=KernelInputDistribution(
        family="matmul-fixed-square-legacy-v1",
        required_shape_classes=("tile-aligned-square",),
        required_layouts=("contiguous",),
        required_value_classes=("positive-unit",),
        required_slices=("train", "holdout"),
    ),
)
