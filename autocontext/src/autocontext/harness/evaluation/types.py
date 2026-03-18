"""Evaluation types — domain-agnostic result and summary containers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class EvaluationLimits:
    timeout_seconds: float = 10.0
    max_memory_mb: int = 512
    network_access: bool = False


@dataclass(slots=True, frozen=True)
class EvaluationResult:
    score: float
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    replay_data: dict[str, Any] = field(default_factory=dict)
    dimension_scores: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class EvaluationSummary:
    mean_score: float
    best_score: float
    wins: int
    losses: int
    elo_after: float
    results: list[EvaluationResult]
    dimension_means: dict[str, float] = field(default_factory=dict)
    best_dimensions: dict[str, float] = field(default_factory=dict)
    dimension_trajectory: list[dict[str, float]] = field(default_factory=list)
    dimension_specs: list[dict[str, Any]] = field(default_factory=list)
    dimension_regressions: list[dict[str, Any]] = field(default_factory=list)
    self_play_summary: dict[str, Any] = field(default_factory=dict)
