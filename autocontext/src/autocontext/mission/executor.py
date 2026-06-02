"""AC-697 mission step executor (slice 3).

Mirrors ``ts/src/mission/executor.ts`` (AC-412). Bounded step loop:
``run_step`` runs one operator-supplied step (sync or async),
``run_until_done`` keeps running until the verifier passes, the
budget is exhausted, or a step blocks.

Same status-state contract as TS: the mission must be ``active`` to
accept a step; budget is checked before AND after every step so a
single-step burst that consumes the last budget slot still
transitions to ``budget_exhausted``; verifier outcomes that come
back with ``verifierThrew=True`` mark the mission ``verifier_failed``
instead of leaving it half-run.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from autocontext.mission._async_bridge import AsyncContextError, has_running_loop
from autocontext.mission.types import MissionStatus

if TYPE_CHECKING:
    from autocontext.mission.manager import MissionManager


__all__ = [
    "RunStepResult",
    "RunUntilDoneResult",
    "StepExecutor",
    "StepResult",
    "run_step",
    "run_until_done",
]


@dataclass(frozen=True)
class StepResult:
    description: str
    status: Literal["completed", "failed", "blocked"]
    block_reason: str | None = None


@dataclass(frozen=True)
class RunStepResult:
    step_recorded: bool
    budget_exhausted: bool
    blocked: bool
    final_status: MissionStatus | None = None
    error: str | None = None


@dataclass(frozen=True)
class RunUntilDoneResult:
    final_status: MissionStatus
    steps_executed: int
    verifier_passed: bool


StepExecutor = Callable[[str], StepResult | Awaitable[StepResult]]
"""Step executor signature. The TS contract is promise-based
(`Promise<StepResult>`); the Python version accepts both sync and
async callables, mirroring the slice-2 verifier shape."""


def _await_if_needed(value: StepResult | Awaitable[StepResult]) -> StepResult:
    """Drive an awaitable step executor to completion. Same
    sync-to-async bridge as ``MissionManager.verify``.

    PR #1016 review (P2): when called from inside a running event
    loop with an awaitable value, raise ``AsyncContextError`` so the
    caller (``run_step``) knows not to record a failed step. Calling
    ``asyncio.run`` from inside a running loop raises
    ``RuntimeError`` — without this guard the exception would be
    swallowed by the catch-all in ``run_step`` and a spurious failed
    step would be persisted.
    """
    if not inspect.isawaitable(value):
        return value
    if has_running_loop():
        if hasattr(value, "close"):
            value.close()  # avoid an unawaited-coroutine warning
        raise AsyncContextError(
            "run_step received an async executor while a running event loop is "
            "active. Run from sync code, or call the step executor yourself and "
            "pass the resolved StepResult into run_step."
        )
    return asyncio.run(value)  # type: ignore[arg-type]


def run_step(manager: MissionManager, mission_id: str, executor: StepExecutor) -> RunStepResult:
    """Run a single bounded step. Mirrors TS ``runStep``."""
    mission = manager.get(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")

    if mission.status != "active":
        return RunStepResult(
            step_recorded=False,
            budget_exhausted=mission.status == "budget_exhausted",
            blocked=mission.status == "blocked",
            final_status=mission.status,
            error=f"Mission is {mission.status}",
        )

    budget = manager.budget_usage(mission_id)
    if budget.exhausted:
        manager.set_status(mission_id, "budget_exhausted")
        return RunStepResult(
            step_recorded=False,
            budget_exhausted=True,
            blocked=False,
            final_status="budget_exhausted",
        )

    try:
        result = _await_if_needed(executor(mission_id))
    except AsyncContextError:
        # PR #1016 review (P2): propagate the async-context guard so
        # the caller can react. State has not been mutated yet.
        raise
    except Exception as err:  # noqa: BLE001 — executor exceptions become a failing step
        message = str(err)
        step_id = manager.advance(mission_id, f"Error: {message}")
        manager.update_step(step_id, "failed", message)
        return RunStepResult(
            step_recorded=True,
            budget_exhausted=False,
            blocked=False,
            error=message,
        )

    step_id = manager.advance(mission_id, result.description)

    if result.status == "failed":
        manager.update_step(step_id, "failed", result.description)
        return RunStepResult(
            step_recorded=True,
            budget_exhausted=False,
            blocked=False,
            error=result.description,
        )

    if result.status == "blocked":
        block_reason = result.block_reason or result.description
        manager.update_step(step_id, "blocked", block_reason)
        manager.set_status(mission_id, "blocked")
        return RunStepResult(
            step_recorded=True,
            budget_exhausted=False,
            blocked=True,
            final_status="blocked",
            error=block_reason,
        )

    updated_budget = manager.budget_usage(mission_id)
    if updated_budget.exhausted:
        manager.set_status(mission_id, "budget_exhausted")
        return RunStepResult(
            step_recorded=True,
            budget_exhausted=True,
            blocked=False,
            final_status="budget_exhausted",
        )

    return RunStepResult(step_recorded=True, budget_exhausted=False, blocked=False)


def run_until_done(
    manager: MissionManager,
    mission_id: str,
    executor: StepExecutor,
    *,
    max_iterations: int = 100,
) -> RunUntilDoneResult:
    """Run steps + verify until the verifier passes, the budget is
    exhausted, a step blocks, or ``max_iterations`` is hit. Mirrors
    TS ``runUntilDone``."""
    steps_executed = 0
    for _iteration in range(max_iterations):
        mission = manager.get(mission_id)
        if mission is None:
            raise ValueError(f"Mission not found: {mission_id}")
        if mission.status != "active":
            return RunUntilDoneResult(
                final_status=mission.status,
                steps_executed=steps_executed,
                verifier_passed=False,
            )

        step_result = run_step(manager, mission_id, executor)
        if step_result.step_recorded:
            steps_executed += 1

        if step_result.final_status is not None and step_result.final_status != "active":
            return RunUntilDoneResult(
                final_status=step_result.final_status,
                steps_executed=steps_executed,
                verifier_passed=False,
            )

        if step_result.budget_exhausted:
            return RunUntilDoneResult(
                final_status="budget_exhausted",
                steps_executed=steps_executed,
                verifier_passed=False,
            )

        if step_result.blocked:
            return RunUntilDoneResult(
                final_status="blocked",
                steps_executed=steps_executed,
                verifier_passed=False,
            )

        verify_result = manager.verify(mission_id)
        if verify_result.passed:
            return RunUntilDoneResult(
                final_status="completed",
                steps_executed=steps_executed,
                verifier_passed=True,
            )
        if verify_result.metadata.get("verifierThrew") is True:
            manager.set_status(mission_id, "verifier_failed")
            return RunUntilDoneResult(
                final_status="verifier_failed",
                steps_executed=steps_executed,
                verifier_passed=False,
            )

    # Max iterations reached without completion.
    final_mission = manager.get(mission_id)
    final_status: MissionStatus = final_mission.status if final_mission is not None else "active"
    return RunUntilDoneResult(
        final_status=final_status,
        steps_executed=steps_executed,
        verifier_passed=False,
    )
