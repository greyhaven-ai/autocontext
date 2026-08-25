"""Configuration contract for the kernel-evolution runner."""

from __future__ import annotations

import math
from dataclasses import dataclass

from autocontext.kernel_evolution.models import KernelCandidate
from autocontext.kernel_evolution.protocols import (
    KernelDecisionPolicy,
    KernelSequentialTestingPolicy,
    KernelStatisticsPolicy,
    PrecisionProfileName,
)


@dataclass(frozen=True, slots=True)
class KernelEvolutionConfig:
    problem_id: str
    task_prompt: str
    baseline_source: str
    source_suffix: str = ".py"
    entrypoint: str = "ModelNew"
    min_relative_improvement: float = 0.01
    require_confidence: bool = True
    max_p95_regression: float = 0.02
    max_environment_drift: float = 0.03
    max_peak_memory_fraction: float = 0.80
    target_reference_speedup: float = 2.0
    precision_profile: PrecisionProfileName | None = None
    proposal_cap: int | None = None
    familywise_alpha: float = 0.05

    def __post_init__(self) -> None:
        if not self.problem_id.strip() or not self.task_prompt.strip() or not self.baseline_source.strip():
            raise ValueError("problem_id, task_prompt, and baseline_source must not be empty")
        for name, value in (
            ("min_relative_improvement", self.min_relative_improvement),
            ("max_p95_regression", self.max_p95_regression),
            ("max_environment_drift", self.max_environment_drift),
            ("max_peak_memory_fraction", self.max_peak_memory_fraction),
        ):
            if not math.isfinite(value) or not 0 <= value < 1:
                raise ValueError(f"{name} must be in [0, 1)")
        if not math.isfinite(self.target_reference_speedup) or self.target_reference_speedup <= 0:
            raise ValueError("target_reference_speedup must be positive")
        if self.proposal_cap is not None and not 1 <= self.proposal_cap <= 10_000:
            raise ValueError("proposal_cap must be between 1 and 10000")
        if not math.isfinite(self.familywise_alpha) or not 0 < self.familywise_alpha < 0.5:
            raise ValueError("familywise_alpha must be in (0, 0.5)")
        KernelCandidate(source=self.baseline_source, source_suffix=self.source_suffix, entrypoint=self.entrypoint)

    @property
    def sequential_testing(self) -> KernelSequentialTestingPolicy | None:
        if self.proposal_cap is None:
            return None
        return KernelSequentialTestingPolicy(
            proposal_cap=self.proposal_cap,
            familywise_alpha=self.familywise_alpha,
        )


def decision_policy_from_config(
    config: KernelEvolutionConfig,
    statistics: KernelStatisticsPolicy,
    *,
    require_confirmation: bool,
) -> KernelDecisionPolicy:
    finite_sample = statistics.schema_version == "autocontext.kernel-statistics-policy/v2"
    return KernelDecisionPolicy(
        schema_version="autocontext.kernel-decision-policy/v2" if finite_sample else "autocontext.kernel-decision-policy/v1",
        evidence_family_version="autocontext.kernel-evidence-family/v4" if finite_sample else None,
        statistics=statistics,
        require_confirmation=require_confirmation,
        min_relative_improvement=config.min_relative_improvement,
        require_confidence=config.require_confidence,
        max_p95_regression=config.max_p95_regression,
        max_environment_drift=config.max_environment_drift,
        max_peak_memory_fraction=config.max_peak_memory_fraction,
        target_reference_speedup=config.target_reference_speedup,
        sequential_testing=config.sequential_testing,
    )


__all__ = ["KernelEvolutionConfig", "decision_policy_from_config"]
