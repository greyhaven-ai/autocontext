"""Domain-agnostic retry context for backpressure loops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RetryContext:
    attempt: int
    previous_score: float
    best_score_needed: float
    gate_threshold: float
    previous_strategy: dict[str, Any]
    gate_reason: str
