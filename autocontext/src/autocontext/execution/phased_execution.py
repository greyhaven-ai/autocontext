"""Phased execution with separate budgets and timeouts (AC-244).

Splits agent-task scaffolding and execution into independent phases,
each with its own time budget, error reporting, and partial output
persistence. Unused budget can optionally roll over to the next phase.

Key types:
- PhaseBudget: time budget for a single phase
- PhaseResult: outcome of a single phase with timing and error detail
- PhasedExecutionPlan: ordered list of phase budgets
- PhasedExecutionResult: aggregate result across all phases
- PhaseTimer: wall-clock timer with budget enforcement
- PhasedRunner: orchestrates phase execution with timeout enforcement
- split_budget(): utility to divide a total budget into phases
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PhaseBudget:
    """Time budget for a single execution phase."""

    phase_name: str
    budget_seconds: float


@dataclass(slots=True)
class PhaseResult:
    """Outcome of a single phase execution."""

    phase_name: str
    status: str  # completed, timeout, failed, skipped
    duration_seconds: float
    budget_seconds: float
    budget_remaining_seconds: float
    error: str | None
    outputs: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_name": self.phase_name,
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "budget_seconds": self.budget_seconds,
            "budget_remaining_seconds": self.budget_remaining_seconds,
            "error": self.error,
            "outputs": self.outputs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhaseResult:
        return cls(
            phase_name=data["phase_name"],
            status=data["status"],
            duration_seconds=data.get("duration_seconds", 0.0),
            budget_seconds=data.get("budget_seconds", 0.0),
            budget_remaining_seconds=data.get("budget_remaining_seconds", 0.0),
            error=data.get("error"),
            outputs=data.get("outputs", {}),
        )


@dataclass(slots=True)
class PhasedExecutionPlan:
    """Ordered list of phase budgets."""

    phases: list[PhaseBudget]
    total_budget_seconds: float
    allow_rollover: bool = False


@dataclass(slots=True)
class PhasedExecutionResult:
    """Aggregate result across all phases."""

    phase_results: list[PhaseResult]
    total_duration_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def all_completed(self) -> bool:
        return all(r.status == "completed" for r in self.phase_results)

    @property
    def failed_phase(self) -> str | None:
        for r in self.phase_results:
            if r.status in ("timeout", "failed"):
                return r.phase_name
        return None

    @property
    def completed_phases(self) -> int:
        return sum(1 for r in self.phase_results if r.status == "completed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_results": [r.to_dict() for r in self.phase_results],
            "total_duration_seconds": self.total_duration_seconds,
            "all_completed": self.all_completed,
            "failed_phase": self.failed_phase,
            "completed_phases": self.completed_phases,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhasedExecutionResult:
        return cls(
            phase_results=[PhaseResult.from_dict(r) for r in data.get("phase_results", [])],
            total_duration_seconds=data.get("total_duration_seconds", 0.0),
            metadata=data.get("metadata", {}),
        )


class PhaseTimer:
    """Wall-clock timer with budget enforcement."""

    def __init__(self, budget_seconds: float) -> None:
        self._budget = budget_seconds
        self._start: float | None = None
        self._stop: float | None = None

    def start(self) -> None:
        self._start = time.monotonic()
        self._stop = None

    def stop(self) -> None:
        self._stop = time.monotonic()

    def elapsed(self) -> float:
        if self._start is None:
            return 0.0
        end = self._stop if self._stop is not None else time.monotonic()
        return end - self._start

    def remaining(self) -> float:
        return max(0.0, self._budget - self.elapsed())

    def is_expired(self) -> bool:
        return self.elapsed() >= self._budget


def split_budget(
    total_seconds: float,
    phase_names: list[str],
    ratios: list[float] | None = None,
    allow_rollover: bool = False,
) -> PhasedExecutionPlan:
    """Split a total time budget into phases.

    If ratios are not provided, budget is split evenly.
    """
    n = len(phase_names)
    if ratios is None:
        ratios = [1.0 / n] * n

    phases = [
        PhaseBudget(
            phase_name=name,
            budget_seconds=round(total_seconds * ratio, 2),
        )
        for name, ratio in zip(phase_names, ratios, strict=False)
    ]

    return PhasedExecutionPlan(
        phases=phases,
        total_budget_seconds=total_seconds,
        allow_rollover=allow_rollover,
    )


class PhasedRunner:
    """Orchestrates phase execution with timeout enforcement."""

    def run_phase(
        self,
        budget: PhaseBudget,
        fn: Callable[[], dict[str, Any]],
    ) -> PhaseResult:
        """Run a single phase with timeout enforcement.

        Uses a daemon thread instead of ``ThreadPoolExecutor`` so the caller
        regains control promptly when the timeout is hit.
        """
        timer = PhaseTimer(budget.budget_seconds)
        timer.start()
        result_queue: Queue[tuple[str, dict[str, Any] | Exception]] = Queue(maxsize=1)

        def _run() -> None:
            try:
                result_queue.put(("completed", fn()))
            except Exception as exc:  # pragma: no cover - exercised via queue consumer
                logger.debug("execution.phased_execution: caught Exception", exc_info=True)
                result_queue.put(("failed", exc))

        worker = threading.Thread(
            target=_run,
            name=f"phase-{budget.phase_name}",
            daemon=True,
        )
        worker.start()

        timeout = budget.budget_seconds if budget.budget_seconds > 0 else None
        try:
            status, payload = result_queue.get(timeout=timeout)
        except Empty:
            timer.stop()
            return PhaseResult(
                phase_name=budget.phase_name,
                status="timeout",
                duration_seconds=round(timer.elapsed(), 3),
                budget_seconds=budget.budget_seconds,
                budget_remaining_seconds=0.0,
                error=f"{budget.phase_name} phase exceeded {budget.budget_seconds}s budget (timeout)",
                outputs={},
            )

        if status == "failed":
            exc = payload
            assert isinstance(exc, Exception)
            timer.stop()
            return PhaseResult(
                phase_name=budget.phase_name,
                status="failed",
                duration_seconds=round(timer.elapsed(), 3),
                budget_seconds=budget.budget_seconds,
                budget_remaining_seconds=round(timer.remaining(), 3),
                error=str(exc),
                outputs={},
            )

        timer.stop()
        outputs = payload if isinstance(payload, dict) else {}
        return PhaseResult(
            phase_name=budget.phase_name,
            status="completed",
            duration_seconds=round(timer.elapsed(), 3),
            budget_seconds=budget.budget_seconds,
            budget_remaining_seconds=round(timer.remaining(), 3),
            error=None,
            outputs=outputs if isinstance(outputs, dict) else {},
        )

    def run_all(
        self,
        plan: PhasedExecutionPlan,
        phase_fns: dict[str, Callable[[], dict[str, Any]]],
    ) -> PhasedExecutionResult:
        """Run all phases in order, stopping on failure."""
        overall_start = time.monotonic()
        results: list[PhaseResult] = []
        rollover_seconds = 0.0
        failed = False

        for phase_budget in plan.phases:
            if failed:
                results.append(PhaseResult(
                    phase_name=phase_budget.phase_name,
                    status="skipped",
                    duration_seconds=0.0,
                    budget_seconds=phase_budget.budget_seconds,
                    budget_remaining_seconds=phase_budget.budget_seconds,
                    error=None,
                    outputs={},
                ))
                continue

            fn = phase_fns.get(phase_budget.phase_name)
            if fn is None:
                results.append(PhaseResult(
                    phase_name=phase_budget.phase_name,
                    status="skipped",
                    duration_seconds=0.0,
                    budget_seconds=phase_budget.budget_seconds,
                    budget_remaining_seconds=phase_budget.budget_seconds,
                    error="No function provided for phase",
                    outputs={},
                ))
                continue

            # Apply rollover
            effective_budget = PhaseBudget(
                phase_name=phase_budget.phase_name,
                budget_seconds=phase_budget.budget_seconds + rollover_seconds,
            )

            result = self.run_phase(effective_budget, fn)
            results.append(result)

            if result.status == "completed" and plan.allow_rollover:
                rollover_seconds = result.budget_remaining_seconds
            else:
                rollover_seconds = 0.0

            if result.status in ("timeout", "failed"):
                failed = True

        total_duration = round(time.monotonic() - overall_start, 3)

        return PhasedExecutionResult(
            phase_results=results,
            total_duration_seconds=total_duration,
        )
