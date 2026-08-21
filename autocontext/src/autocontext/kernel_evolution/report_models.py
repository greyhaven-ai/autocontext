"""Correctness and performance report models for kernel benchmarks."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

PositiveFiniteFloat = Annotated[FiniteFloat, Field(gt=0)]
NonNegativeFiniteFloat = Annotated[FiniteFloat, Field(ge=0)]


class _ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class KernelCorrectnessSliceReport(_ReportModel):
    """Named train/holdout correctness slice with no hidden case material."""

    name: str
    split: Literal["train", "holdout"]
    cases_run: int = Field(ge=1)
    cases_passed: int = Field(ge=0)
    passed: bool

    @model_validator(mode="after")
    def validate_passed(self) -> Self:
        if self.cases_passed > self.cases_run:
            raise ValueError("slice cases_passed cannot exceed cases_run")
        if self.passed != (self.cases_passed == self.cases_run):
            raise ValueError("slice passed flag disagrees with case counts")
        if not self.name.strip():
            raise ValueError("slice name must not be empty")
        return self


class KernelCorrectnessReport(_ReportModel):
    passed: bool
    tests_run: int = Field(ge=1)
    tests_passed: int = Field(ge=0)
    hidden_tests_run: int = Field(ge=1)
    hidden_tests_passed: int = Field(ge=0)
    max_abs_error: NonNegativeFiniteFloat | None = None
    max_rel_error: NonNegativeFiniteFloat | None = None
    parameter_state_match: bool = True
    input_mutation_detected: bool = False
    failures: list[str] = Field(default_factory=list)
    slices: list[KernelCorrectnessSliceReport] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts_and_pass_flag(self) -> Self:
        if self.tests_passed > self.tests_run:
            raise ValueError("tests_passed cannot exceed tests_run")
        if self.hidden_tests_run > self.tests_run:
            raise ValueError("hidden_tests_run cannot exceed tests_run")
        if self.hidden_tests_passed > self.hidden_tests_run:
            raise ValueError("hidden_tests_passed cannot exceed hidden_tests_run")
        fully_correct = (
            self.tests_passed == self.tests_run
            and self.hidden_tests_passed == self.hidden_tests_run
            and self.parameter_state_match
            and not self.input_mutation_detected
        )
        if self.passed != fully_correct:
            raise ValueError("correctness passed flag disagrees with trial, state, or mutation checks")
        if self.slices:
            if sum(item.cases_run for item in self.slices) != self.tests_run:
                raise ValueError("correctness slice cases must sum to tests_run")
            if sum(item.cases_passed for item in self.slices) != self.tests_passed:
                raise ValueError("correctness slice passes must sum to tests_passed")
            if self.passed != all(item.passed for item in self.slices):
                raise ValueError("correctness passed flag disagrees with slice gates")
        return self


class KernelTimingBlock(_ReportModel):
    """One interleaved, paired measurement block."""

    block: int = Field(ge=0)
    candidate_ms: PositiveFiniteFloat
    incumbent_ms: PositiveFiniteFloat
    reference_ms: PositiveFiniteFloat


class KernelCasePerformanceReport(_ReportModel):
    """Per-case median gate evaluated before aggregate promotion."""

    name: str
    split: Literal["train", "holdout"]
    candidate_median_ms: PositiveFiniteFloat
    incumbent_median_ms: PositiveFiniteFloat
    reference_median_ms: PositiveFiniteFloat
    minimum_speedup_vs_incumbent: PositiveFiniteFloat = 0.98
    passed_no_regression: bool

    @model_validator(mode="after")
    def validate_floor(self) -> Self:
        if not self.name.strip():
            raise ValueError("performance case name must not be empty")
        actual = float(self.incumbent_median_ms) / float(self.candidate_median_ms)
        if self.passed_no_regression != (actual + 1e-12 >= float(self.minimum_speedup_vs_incumbent)):
            raise ValueError("passed_no_regression disagrees with the per-case speedup floor")
        return self


class KernelPerformanceReport(_ReportModel):
    blocks: list[KernelTimingBlock] = Field(min_length=1)
    cases: list[KernelCasePerformanceReport] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_blocks(self) -> Self:
        block_ids = [block.block for block in self.blocks]
        if block_ids != list(range(len(self.blocks))):
            raise ValueError("timing block ids must be unique, ordered, and contiguous from zero")
        return self


__all__ = [
    "KernelCasePerformanceReport",
    "KernelCorrectnessReport",
    "KernelCorrectnessSliceReport",
    "KernelPerformanceReport",
    "KernelTimingBlock",
]
