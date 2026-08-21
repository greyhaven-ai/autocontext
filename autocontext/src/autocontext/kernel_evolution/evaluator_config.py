"""Configuration contract for the kernel benchmark evaluator."""

from __future__ import annotations

import math
from dataclasses import dataclass

from autocontext.kernel_evolution.promotion_statistics import minimum_bootstrap_samples
from autocontext.kernel_evolution.protocols import KernelStatisticsPolicy


@dataclass(frozen=True, slots=True)
class KernelBenchmarkEvaluatorConfig:
    problem_id: str
    timeout_seconds: float = 630.0
    min_timing_blocks: int = 5
    bootstrap_samples: int = 2_000
    max_feedback_chars: int = 4_000
    require_resource_telemetry: bool = False
    max_gpu_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.problem_id.strip():
            raise ValueError("problem_id must not be empty")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.min_timing_blocks < 2:
            raise ValueError("min_timing_blocks must be at least 2")
        minimum_nominal_samples = minimum_bootstrap_samples(0.05)
        if self.bootstrap_samples < minimum_nominal_samples:
            raise ValueError(f"bootstrap_samples must be at least {minimum_nominal_samples}")
        if self.max_feedback_chars < 128:
            raise ValueError("max_feedback_chars must be at least 128")
        if self.max_gpu_memory_bytes is not None and self.max_gpu_memory_bytes < 1:
            raise ValueError("max_gpu_memory_bytes must be positive")

    def validate_confidence_resolution(self, alpha: float) -> None:
        """Fail closed when the configured resampling cannot resolve ``alpha``."""
        required = minimum_bootstrap_samples(alpha)
        if self.bootstrap_samples < required:
            raise ValueError(
                f"bootstrap_samples ({self.bootstrap_samples}) cannot resolve alpha={alpha:.12g}; "
                f"at least {required} samples are required"
            )

    @property
    def statistics_policy(self) -> KernelStatisticsPolicy:
        """Canonical receipt for every derived benchmark observation."""
        return KernelStatisticsPolicy(
            bootstrap_samples=self.bootstrap_samples,
            min_timing_blocks=self.min_timing_blocks,
            require_resource_telemetry=self.require_resource_telemetry,
            max_gpu_memory_bytes=self.max_gpu_memory_bytes,
        )


__all__ = ["KernelBenchmarkEvaluatorConfig"]
