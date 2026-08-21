"""Configuration contract for the kernel benchmark evaluator."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KernelBenchmarkEvaluatorConfig:
    problem_id: str
    timeout_seconds: float = 630.0
    min_timing_blocks: int = 5
    bootstrap_samples: int = 1_000
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
        if self.bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be at least 100")
        if self.max_feedback_chars < 128:
            raise ValueError("max_feedback_chars must be at least 128")
        if self.max_gpu_memory_bytes is not None and self.max_gpu_memory_bytes < 1:
            raise ValueError("max_gpu_memory_bytes must be positive")


__all__ = ["KernelBenchmarkEvaluatorConfig"]
