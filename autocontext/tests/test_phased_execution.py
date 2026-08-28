"""Tests for AC-244: split agent-task scaffolding and execution into separate budgets.

Covers: PhaseBudget, PhaseResult, PhasedExecutionPlan, PhasedExecutionResult,
PhaseTimer, PhasedRunner, split_budget utility.
"""

from __future__ import annotations

import threading
import time

# ===========================================================================
# PhaseBudget
# ===========================================================================


class TestPhaseBudget:
    def test_construction(self) -> None:
        from autocontext.execution.phased_execution import PhaseBudget

        b = PhaseBudget(phase_name="scaffolding", budget_seconds=120.0)
        assert b.phase_name == "scaffolding"
        assert b.budget_seconds == 120.0

    def test_defaults(self) -> None:
        from autocontext.execution.phased_execution import PhaseBudget

        b = PhaseBudget(phase_name="execution", budget_seconds=60.0)
        assert b.phase_name == "execution"


# ===========================================================================
# PhaseResult
# ===========================================================================


class TestPhaseResult:
    def test_construction(self) -> None:
        from autocontext.execution.phased_execution import PhaseResult

        r = PhaseResult(
            phase_name="scaffolding",
            status="completed",
            duration_seconds=45.5,
            budget_seconds=120.0,
            budget_remaining_seconds=74.5,
            error=None,
            outputs={"scenario_class": "GridCTF"},
        )
        assert r.status == "completed"
        assert r.budget_remaining_seconds == 74.5

    def test_roundtrip(self) -> None:
        from autocontext.execution.phased_execution import PhaseResult

        r = PhaseResult(
            phase_name="execution",
            status="timeout",
            duration_seconds=60.0,
            budget_seconds=60.0,
            budget_remaining_seconds=0.0,
            error="Execution phase exceeded 60s budget",
            outputs={},
        )
        d = r.to_dict()
        restored = PhaseResult.from_dict(d)
        assert restored.status == "timeout"
        assert restored.error == "Execution phase exceeded 60s budget"


# ===========================================================================
# PhasedExecutionPlan
# ===========================================================================


class TestPhasedExecutionPlan:
    def test_construction(self) -> None:
        from autocontext.execution.phased_execution import PhaseBudget, PhasedExecutionPlan

        plan = PhasedExecutionPlan(
            phases=[
                PhaseBudget(phase_name="scaffolding", budget_seconds=120.0),
                PhaseBudget(phase_name="execution", budget_seconds=180.0),
            ],
            total_budget_seconds=300.0,
            allow_rollover=True,
        )
        assert len(plan.phases) == 2
        assert plan.total_budget_seconds == 300.0
        assert plan.allow_rollover is True

    def test_phase_names(self) -> None:
        from autocontext.execution.phased_execution import PhaseBudget, PhasedExecutionPlan

        plan = PhasedExecutionPlan(
            phases=[
                PhaseBudget(phase_name="scaffolding", budget_seconds=60.0),
                PhaseBudget(phase_name="execution", budget_seconds=60.0),
            ],
            total_budget_seconds=120.0,
        )
        assert [p.phase_name for p in plan.phases] == ["scaffolding", "execution"]


# ===========================================================================
# PhasedExecutionResult
# ===========================================================================


class TestPhasedExecutionResult:
    def test_all_completed(self) -> None:
        from autocontext.execution.phased_execution import PhasedExecutionResult, PhaseResult

        result = PhasedExecutionResult(
            phase_results=[
                PhaseResult(
                    phase_name="scaffolding", status="completed",
                    duration_seconds=30.0, budget_seconds=60.0,
                    budget_remaining_seconds=30.0, error=None, outputs={},
                ),
                PhaseResult(
                    phase_name="execution", status="completed",
                    duration_seconds=50.0, budget_seconds=60.0,
                    budget_remaining_seconds=10.0, error=None, outputs={},
                ),
            ],
            total_duration_seconds=80.0,
        )
        assert result.all_completed is True
        assert result.failed_phase is None
        assert result.completed_phases == 2

    def test_failed_phase(self) -> None:
        from autocontext.execution.phased_execution import PhasedExecutionResult, PhaseResult

        result = PhasedExecutionResult(
            phase_results=[
                PhaseResult(
                    phase_name="scaffolding", status="timeout",
                    duration_seconds=120.0, budget_seconds=120.0,
                    budget_remaining_seconds=0.0,
                    error="Scaffolding exceeded budget", outputs={},
                ),
            ],
            total_duration_seconds=120.0,
        )
        assert result.all_completed is False
        assert result.failed_phase == "scaffolding"
        assert result.completed_phases == 0

    def test_roundtrip(self) -> None:
        from autocontext.execution.phased_execution import PhasedExecutionResult, PhaseResult

        result = PhasedExecutionResult(
            phase_results=[
                PhaseResult(
                    phase_name="scaffolding", status="completed",
                    duration_seconds=10.0, budget_seconds=60.0,
                    budget_remaining_seconds=50.0, error=None, outputs={"key": "val"},
                ),
            ],
            total_duration_seconds=10.0,
        )
        d = result.to_dict()
        restored = PhasedExecutionResult.from_dict(d)
        assert restored.all_completed is True
        assert len(restored.phase_results) == 1


# ===========================================================================
# PhaseTimer
# ===========================================================================


class TestPhaseTimer:
    def test_start_and_elapsed(self) -> None:
        from autocontext.execution.phased_execution import PhaseTimer

        timer = PhaseTimer(budget_seconds=10.0)
        timer.start()
        time.sleep(0.01)
        assert timer.elapsed() > 0
        assert timer.elapsed() < 1.0

    def test_remaining(self) -> None:
        from autocontext.execution.phased_execution import PhaseTimer

        timer = PhaseTimer(budget_seconds=10.0)
        timer.start()
        assert timer.remaining() > 9.0
        assert timer.remaining() <= 10.0

    def test_is_expired_false(self) -> None:
        from autocontext.execution.phased_execution import PhaseTimer

        timer = PhaseTimer(budget_seconds=100.0)
        timer.start()
        assert timer.is_expired() is False

    def test_is_expired_true(self) -> None:
        from autocontext.execution.phased_execution import PhaseTimer

        timer = PhaseTimer(budget_seconds=0.0)
        timer.start()
        time.sleep(0.01)
        assert timer.is_expired() is True

    def test_unlimited_budget(self) -> None:
        """Budget of 0 means unlimited (never expires by convention in some contexts).
        But PhaseTimer with budget_seconds=0 should track elapsed correctly."""
        from autocontext.execution.phased_execution import PhaseTimer

        timer = PhaseTimer(budget_seconds=0.0)
        timer.start()
        assert timer.elapsed() >= 0.0

    def test_stop(self) -> None:
        from autocontext.execution.phased_execution import PhaseTimer

        timer = PhaseTimer(budget_seconds=10.0)
        timer.start()
        time.sleep(0.01)
        timer.stop()
        elapsed_at_stop = timer.elapsed()
        time.sleep(0.01)
        # Elapsed should not increase after stop
        assert timer.elapsed() == elapsed_at_stop


# ===========================================================================
# split_budget
# ===========================================================================


class TestSplitBudget:
    def test_even_split(self) -> None:
        from autocontext.execution.phased_execution import split_budget

        plan = split_budget(
            total_seconds=300.0,
            phase_names=["scaffolding", "execution"],
        )
        assert len(plan.phases) == 2
        assert plan.phases[0].budget_seconds == 150.0
        assert plan.phases[1].budget_seconds == 150.0

    def test_custom_ratios(self) -> None:
        from autocontext.execution.phased_execution import split_budget

        plan = split_budget(
            total_seconds=300.0,
            phase_names=["scaffolding", "execution"],
            ratios=[0.4, 0.6],
        )
        assert plan.phases[0].budget_seconds == 120.0
        assert plan.phases[1].budget_seconds == 180.0

    def test_with_rollover(self) -> None:
        from autocontext.execution.phased_execution import split_budget

        plan = split_budget(
            total_seconds=300.0,
            phase_names=["scaffolding", "execution"],
            allow_rollover=True,
        )
        assert plan.allow_rollover is True

    def test_three_phases(self) -> None:
        from autocontext.execution.phased_execution import split_budget

        plan = split_budget(
            total_seconds=300.0,
            phase_names=["design", "codegen", "execution"],
            ratios=[0.3, 0.2, 0.5],
        )
        assert plan.phases[0].budget_seconds == 90.0
        assert plan.phases[1].budget_seconds == 60.0
        assert plan.phases[2].budget_seconds == 150.0


# ===========================================================================
# PhasedRunner
# ===========================================================================


class TestPhasedRunner:
    def test_run_phase_completes(self) -> None:
        from autocontext.execution.phased_execution import (
            PhaseBudget,
            PhasedRunner,
        )

        runner = PhasedRunner()
        budget = PhaseBudget(phase_name="scaffolding", budget_seconds=10.0)

        def scaffolding_fn() -> dict:
            return {"scenario_name": "test_scenario"}

        result = runner.run_phase(budget, scaffolding_fn)
        assert result.status == "completed"
        assert result.outputs == {"scenario_name": "test_scenario"}
        assert result.duration_seconds >= 0
        assert result.budget_remaining_seconds > 0
        assert result.error is None

    def test_run_phase_timeout(self) -> None:
        from autocontext.execution.phased_execution import (
            PhaseBudget,
            PhasedRunner,
        )

        runner = PhasedRunner()
        budget = PhaseBudget(phase_name="scaffolding", budget_seconds=0.05)
        workers: list[threading.Thread] = []

        def slow_fn() -> dict:
            workers.append(threading.current_thread())
            time.sleep(0.1)
            return {"done": True}

        started_at = time.monotonic()
        try:
            result = runner.run_phase(budget, slow_fn)
            elapsed = time.monotonic() - started_at
        finally:
            for worker in workers:
                worker.join(timeout=1.0)
        assert result.status == "timeout"
        assert result.error is not None
        assert "timeout" in result.error.lower() or "budget" in result.error.lower()
        assert elapsed < 0.15
        assert workers and not workers[0].is_alive()

    def test_run_phase_failure(self) -> None:
        from autocontext.execution.phased_execution import (
            PhaseBudget,
            PhasedRunner,
        )

        runner = PhasedRunner()
        budget = PhaseBudget(phase_name="scaffolding", budget_seconds=10.0)

        def failing_fn() -> dict:
            raise ValueError("Design failed: invalid spec")

        result = runner.run_phase(budget, failing_fn)
        assert result.status == "failed"
        assert result.error is not None
        assert "invalid spec" in result.error

    def test_run_all_completes(self) -> None:
        from autocontext.execution.phased_execution import PhasedRunner, split_budget

        plan = split_budget(300.0, ["scaffolding", "execution"])
        runner = PhasedRunner()

        phase_fns = {
            "scaffolding": lambda: {"scenario": "grid_ctf"},
            "execution": lambda: {"score": 0.85},
        }

        result = runner.run_all(plan, phase_fns)
        assert result.all_completed is True
        assert result.completed_phases == 2
        assert result.phase_results[0].outputs == {"scenario": "grid_ctf"}
        assert result.phase_results[1].outputs == {"score": 0.85}

    def test_run_all_first_phase_fails_skips_rest(self) -> None:
        from autocontext.execution.phased_execution import PhasedRunner, split_budget

        plan = split_budget(300.0, ["scaffolding", "execution"])
        runner = PhasedRunner()

        def fail_scaffolding() -> dict:
            raise RuntimeError("Codegen failed")

        phase_fns = {
            "scaffolding": fail_scaffolding,
            "execution": lambda: {"score": 0.85},
        }

        result = runner.run_all(plan, phase_fns)
        assert result.all_completed is False
        assert result.failed_phase == "scaffolding"
        # Execution phase should be skipped
        exec_result = next(r for r in result.phase_results if r.phase_name == "execution")
        assert exec_result.status == "skipped"

    def test_rollover_gives_extra_time(self) -> None:
        from autocontext.execution.phased_execution import PhasedRunner, split_budget

        plan = split_budget(
            total_seconds=10.0,
            phase_names=["scaffolding", "execution"],
            ratios=[0.5, 0.5],
            allow_rollover=True,
        )
        runner = PhasedRunner()

        # Scaffolding finishes instantly, saving ~5s
        phase_fns = {
            "scaffolding": lambda: {"fast": True},
            "execution": lambda: {"done": True},
        }

        result = runner.run_all(plan, phase_fns)
        assert result.all_completed is True
        # Execution phase should have received rolled-over budget
        exec_result = next(r for r in result.phase_results if r.phase_name == "execution")
        assert exec_result.budget_seconds > 5.0  # Got rollover from scaffolding

    def test_persist_partial_outputs(self) -> None:
        """Successful scaffolding outputs should be accessible even if execution fails."""
        from autocontext.execution.phased_execution import PhasedRunner, split_budget

        plan = split_budget(300.0, ["scaffolding", "execution"])
        runner = PhasedRunner()

        phase_fns = {
            "scaffolding": lambda: {"scenario_class": "TestScenario", "spec": {"name": "test"}},
            "execution": lambda: (_ for _ in ()).throw(RuntimeError("Match engine crashed")),
        }

        result = runner.run_all(plan, phase_fns)
        scaffolding_result = next(r for r in result.phase_results if r.phase_name == "scaffolding")
        exec_result = next(r for r in result.phase_results if r.phase_name == "execution")

        # Scaffolding succeeded — outputs preserved
        assert scaffolding_result.status == "completed"
        assert scaffolding_result.outputs["scenario_class"] == "TestScenario"
        # Execution failed
        assert exec_result.status == "failed"

    def test_phase_specific_error_reporting(self) -> None:
        """Error messages should clearly identify which phase failed."""
        from autocontext.execution.phased_execution import PhasedRunner, split_budget

        plan = split_budget(0.1, ["scaffolding", "execution"], ratios=[0.5, 0.5])
        runner = PhasedRunner()
        workers: list[threading.Thread] = []

        def slow_scaffolding() -> dict:
            workers.append(threading.current_thread())
            time.sleep(0.1)
            return {}

        try:
            result = runner.run_all(plan, {"scaffolding": slow_scaffolding, "execution": lambda: {}})
        finally:
            for worker in workers:
                worker.join(timeout=1.0)
        scaffolding = next(r for r in result.phase_results if r.phase_name == "scaffolding")
        assert scaffolding.status == "timeout"
        assert "scaffolding" in scaffolding.error.lower()
        assert workers and not workers[0].is_alive()
