"""AC-697 mission executor tests (slice 3).

Mirrors the unit-test surface for ``ts/src/mission/executor.ts``.
Covers happy-path step run, mission-not-active short-circuit,
budget exhaustion before AND after step, blocked path, failed
step, executor exception turning into failing step, and the
verifier-pass / verifier-fail / verifier-threw branches in
``run_until_done``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.mission import (
    MissionBudget,
    MissionManager,
    StepResult,
    VerifierResult,
    run_step,
    run_until_done,
)


def _manager(tmp_path: Path) -> MissionManager:
    return MissionManager(str(tmp_path / "m.sqlite3"))


# ---------------------------------------------------------------------------
# run_step happy path + guards
# ---------------------------------------------------------------------------


def test_run_step_records_completed_step(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        result = run_step(
            mgr,
            mid,
            lambda _mid: StepResult(description="did a", status="completed"),
        )
        assert result.step_recorded is True
        steps = mgr.steps(mid)
        assert steps[0].description == "did a"


def test_run_step_rejects_when_mission_not_active(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.pause(mid)
        result = run_step(
            mgr,
            mid,
            lambda _mid: StepResult(description="d", status="completed"),
        )
        assert result.step_recorded is False
        assert result.final_status == "paused"
        assert "Mission is paused" in (result.error or "")


def test_run_step_unknown_mission_raises(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        with pytest.raises(ValueError, match="Mission not found"):
            run_step(
                mgr,
                "mission-nope",
                lambda _mid: StepResult(description="d", status="completed"),
            )


def test_run_step_budget_exhausted_before_execution(tmp_path: Path) -> None:
    """Budget is checked before executing the step; an already-exhausted
    budget transitions the mission to ``budget_exhausted``."""
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g", budget=MissionBudget(max_steps=1))
        mgr.advance(mid, "ate the budget")
        result = run_step(
            mgr,
            mid,
            lambda _mid: StepResult(description="d", status="completed"),
        )
        assert result.budget_exhausted is True
        assert result.final_status == "budget_exhausted"
        assert mgr.get(mid).status == "budget_exhausted"


def test_run_step_budget_exhausted_after_execution(tmp_path: Path) -> None:
    """A step that consumes the last budget slot still records but
    transitions ``budget_exhausted`` for the next iteration."""
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g", budget=MissionBudget(max_steps=1))
        result = run_step(
            mgr,
            mid,
            lambda _mid: StepResult(description="d", status="completed"),
        )
        assert result.step_recorded is True
        assert result.budget_exhausted is True
        assert mgr.get(mid).status == "budget_exhausted"


def test_run_step_failed_step_records_failure(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        result = run_step(
            mgr,
            mid,
            lambda _mid: StepResult(description="failed thing", status="failed"),
        )
        assert result.step_recorded is True
        assert result.error == "failed thing"
        assert mgr.steps(mid)[0].status == "failed"


def test_run_step_blocked_step_transitions_blocked(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        result = run_step(
            mgr,
            mid,
            lambda _mid: StepResult(
                description="needs human",
                status="blocked",
                block_reason="waiting for review",
            ),
        )
        assert result.blocked is True
        assert mgr.get(mid).status == "blocked"
        assert mgr.steps(mid)[0].status == "blocked"
        assert mgr.steps(mid)[0].result == "waiting for review"


def test_run_step_executor_exception_records_failed_step(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")

        def boom(_mid: str) -> StepResult:
            raise RuntimeError("kaboom")

        result = run_step(mgr, mid, boom)
        assert result.step_recorded is True
        assert "kaboom" in (result.error or "")
        steps = mgr.steps(mid)
        assert steps[0].status == "failed"
        assert "kaboom" in steps[0].description


def test_run_step_supports_async_executor(tmp_path: Path) -> None:
    """Parity with the slice-2 async-verifier fix: the executor
    callable can be async too."""
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")

        async def step(_mid: str) -> StepResult:
            return StepResult(description="async did a", status="completed")

        result = run_step(mgr, mid, step)
        assert result.step_recorded is True
        assert mgr.steps(mid)[0].description == "async did a"


def test_run_step_async_inside_running_loop_raises_without_mutating_state(
    tmp_path: Path,
) -> None:
    """PR #1016 review (P2): the sync `run_step` API cannot bridge
    async executors from inside a running event loop because
    `asyncio.run` rejects a nested call. Without the guard the
    catch-all would have persisted a falsely-failed step. The fix
    raises `AsyncContextError` BEFORE recording any step, so the
    caller can react and the mission state stays untouched.
    """
    import asyncio

    from autocontext.mission._async_bridge import AsyncContextError

    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")

        async def driver() -> None:
            async def step(_mid: str) -> StepResult:
                return StepResult(description="never recorded", status="completed")

            with pytest.raises(AsyncContextError):
                run_step(mgr, mid, step)

        asyncio.run(driver())
        # No step was recorded — the guard fired before any state mutation.
        assert mgr.steps(mid) == []
        assert mgr.get(mid).status == "active"


# ---------------------------------------------------------------------------
# run_until_done
# ---------------------------------------------------------------------------


def test_run_until_done_completes_when_verifier_passes(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=True, reason="green"))
        result = run_until_done(
            mgr,
            mid,
            lambda _mid: StepResult(description="d", status="completed"),
            max_iterations=5,
        )
        assert result.final_status == "completed"
        assert result.verifier_passed is True
        assert result.steps_executed == 1


def test_run_until_done_stops_on_budget_exhaustion(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g", budget=MissionBudget(max_steps=2))
        # Verifier never passes so the loop runs until budget runs out.
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=False, reason="not yet"))
        result = run_until_done(
            mgr,
            mid,
            lambda _mid: StepResult(description="d", status="completed"),
            max_iterations=10,
        )
        assert result.final_status == "budget_exhausted"
        assert result.steps_executed == 2


def test_run_until_done_stops_on_blocked(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        result = run_until_done(
            mgr,
            mid,
            lambda _mid: StepResult(
                description="needs human",
                status="blocked",
                block_reason="waiting",
            ),
            max_iterations=3,
        )
        assert result.final_status == "blocked"


def test_run_until_done_marks_verifier_failed_when_verifier_throws(
    tmp_path: Path,
) -> None:
    """A verifier that throws lands as a failing result with
    ``verifierThrew=True``; the loop marks the mission
    ``verifier_failed`` and stops."""
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")

        def boom(_mid: str) -> VerifierResult:
            raise RuntimeError("kaboom")

        mgr.set_verifier(mid, boom)
        result = run_until_done(
            mgr,
            mid,
            lambda _mid: StepResult(description="d", status="completed"),
            max_iterations=3,
        )
        assert result.final_status == "verifier_failed"


def test_run_until_done_caps_at_max_iterations(tmp_path: Path) -> None:
    """If the verifier never passes and nothing else short-circuits,
    the loop returns the live mission status after exactly
    ``max_iterations`` steps."""
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=False, reason="not yet"))
        result = run_until_done(
            mgr,
            mid,
            lambda _mid: StepResult(description="d", status="completed"),
            max_iterations=3,
        )
        assert result.steps_executed == 3
        assert result.final_status == "active"
        assert result.verifier_passed is False
