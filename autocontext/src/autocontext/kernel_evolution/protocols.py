"""Host-owned protocol controls for precision-safe kernel promotion."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

PrecisionProfileName = Literal["strict-fp32-v1", "relaxed-precision-v1"]
PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]
Probability = Annotated[FiniteFloat, Field(gt=0, lt=0.5)]


class _ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


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
        if set(self.inputs.required_slices) != {"train", "holdout"}:
            raise ValueError("named precision profiles require train and holdout slices")
        if (
            self.reference.tf32_allowed
            or not self.reference.deterministic_algorithms
            or not self.enforcement.require_every_correctness_slice
            or not self.enforcement.require_every_case_no_regression
            or not self.enforcement.require_paired_aggregate_performance
            or not self.enforcement.candidate_controls_protected
        ):
            raise ValueError("named precision profiles require all protected reference and promotion controls")
        if self.profile_name == "strict-fp32-v1":
            required_shapes = {"non-tile-square", "rectangular"}
            required_layouts = {"contiguous", "transposed"}
            required_values = {"signed", "small", "large", "cancellation", "dynamic-range"}
            if self.numerical.minimum_input_precision != "float32" or self.numerical.input_downcast_allowed:
                raise ValueError("strict-fp32-v1 forbids input downcasts")
            if (
                not required_shapes <= set(self.inputs.required_shape_classes)
                or not required_layouts <= set(self.inputs.required_layouts)
                or not required_values <= set(self.inputs.required_value_classes)
            ):
                raise ValueError("strict-fp32-v1 requires varied shape, layout, and value classes")
        elif self.numerical.minimum_input_precision != "float16" or not self.numerical.input_downcast_allowed:
            raise ValueError("relaxed-precision-v1 must explicitly allow FP16 input downcasts")
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
