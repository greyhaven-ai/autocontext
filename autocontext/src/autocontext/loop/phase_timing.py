"""Timing helpers shared by phased generation execution."""

from __future__ import annotations

import time
from typing import Any

from autocontext.execution.phased_execution import PhaseBudget, PhaseResult


def phase_exhausted(start_time: float, budget: PhaseBudget | None, *, current_time: float | None = None) -> bool:
    if budget is None:
        return False
    return _elapsed_seconds(start_time, current_time) >= budget.budget_seconds


def build_phase_result(
    *,
    budget: PhaseBudget,
    phase_start_time: float,
    status: str,
    error: str | None = None,
    outputs: dict[str, Any] | None = None,
    current_time: float | None = None,
) -> PhaseResult:
    elapsed = _elapsed_seconds(phase_start_time, current_time)
    remaining = max(0.0, budget.budget_seconds - elapsed)
    return PhaseResult(
        phase_name=budget.phase_name,
        status=status,
        duration_seconds=round(elapsed, 3),
        budget_seconds=round(budget.budget_seconds, 3),
        budget_remaining_seconds=round(remaining, 3),
        error=error,
        outputs=outputs or {},
    )


def _elapsed_seconds(start_time: float, current_time: float | None) -> float:
    return max(0.0, (time.monotonic() if current_time is None else current_time) - start_time)


__all__ = ["build_phase_result", "phase_exhausted"]
