from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class ProgressSnapshot:
    generation: int
    best_score: float
    best_elo: float
    mean_score: float
    last_advance_generation: int
    stagnation_count: int
    gate_history: list[str]
    top_lessons: list[str]
    blocked_approaches: list[str]
    strategy_summary: dict[str, Any]
    score_trend: list[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProgressSnapshot:
        return cls(**data)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def build_progress_snapshot(
    generation: int,
    best_score: float,
    best_elo: float,
    mean_score: float,
    gate_history: list[str],
    score_history: list[float],
    current_strategy: dict[str, Any],
    lessons: list[str],
    blocked_approaches: list[str] | None = None,
) -> ProgressSnapshot:
    last_advance_generation = 0
    for i, decision in enumerate(gate_history):
        if decision == "advance":
            last_advance_generation = i + 1

    # Count trailing non-'advance' decisions (includes retry + rollback).
    # Note: StagnationDetector.detect() counts only trailing 'rollback' — different metric.
    stagnation_count = 0
    for decision in reversed(gate_history):
        if decision != "advance":
            stagnation_count += 1
        else:
            break

    return ProgressSnapshot(
        generation=generation,
        best_score=best_score,
        best_elo=best_elo,
        mean_score=mean_score,
        last_advance_generation=last_advance_generation,
        stagnation_count=stagnation_count,
        gate_history=list(gate_history),
        top_lessons=lessons[:5],
        blocked_approaches=blocked_approaches or [],  # callers may populate from strategy registry
        strategy_summary=dict(current_strategy),
        score_trend=score_history[-10:],
    )
