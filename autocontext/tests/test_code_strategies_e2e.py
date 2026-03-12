"""End-to-end integration tests for code strategies through executor pipeline."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.execution.executors.monty import MontyExecutor
from autocontext.scenarios.base import ExecutionLimits, Observation, Result


class FakeCodeScenario:
    """Minimal scenario for code strategy e2e testing."""

    name = "fake_code_e2e"

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "terminal": False, "resource_density": 0.5}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(
            narrative="A tactical scenario with moderate resources.",
            state=dict(state),
            constraints=["aggression must be 0-1"],
        )

    def validate_actions(self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]) -> tuple[bool, str]:
        if "aggression" not in actions:
            return False, "missing aggression"
        agg = float(actions["aggression"])
        if not 0 <= agg <= 1:
            return False, "aggression must be 0-1"
        return True, "ok"

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        agg = float(actions.get("aggression", 0.5))
        defense = float(actions.get("defense", 0.5))
        score = agg * 0.6 + defense * 0.2 + 0.1
        return {
            **dict(state),
            "terminal": True,
            "score": round(min(1.0, score), 4),
            "timeline": [{"event": "done", "score": round(min(1.0, score), 4)}],
            "metrics": {"aggression": agg, "defense": defense},
        }

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        score = float(state.get("score", 0.0))
        return Result(
            score=score,
            winner="challenger" if score >= 0.5 else "incumbent",
            summary=f"Code e2e score {score:.4f}",
            replay=state.get("timeline", []),
            metrics=state.get("metrics", {}),
            validation_errors=[],
        )

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "Code e2e replay."


def _simulate_code_execution(
    scenario: FakeCodeScenario,
    agent_code: str,
    seed: int,
) -> dict[str, Any]:
    """Simulate what the code strategy eval script would do, for building mock chain."""
    state = scenario.initial_state(seed=seed)
    # Agent code would assign to result — simulate with fixed values
    actions = {"aggression": 0.7, "defense": 0.5}
    valid, reason = scenario.validate_actions(state, "challenger", actions)
    if not valid:
        return {
            "score": 0.0, "winner": "incumbent",
            "summary": "code strategy produced invalid actions",
            "replay": [{"event": "validation_failed", "reason": reason}],
            "metrics": {"valid": 0.0},
            "validation_errors": [reason],
        }
    next_state = scenario.step(state, actions)
    result = scenario.get_result(next_state)
    return result.model_dump()


def _build_code_monty_mock(scenario: FakeCodeScenario, seed: int) -> MagicMock:
    """Build mock Monty for code strategy execution."""
    state = scenario.initial_state(seed=seed)
    actions = {"aggression": 0.7, "defense": 0.5}

    calls: list[tuple[str, tuple[Any, ...]]] = [
        ("initial_state", (seed,)),
        ("get_observation", (state,)),
        ("validate_actions", (state, actions)),
        ("step", (state, actions)),
        ("is_terminal", (scenario.step(state, actions),)),
        ("get_result", (scenario.step(state, actions),)),
    ]

    final_result = _simulate_code_execution(scenario, "", seed)
    complete = MagicMock(spec=[])
    complete.output = final_result

    snapshots: list[MagicMock] = []
    for fn_name, args in calls:
        snap = MagicMock()
        snap.function_name = fn_name
        snap.args = args
        snapshots.append(snap)

    for i, snap in enumerate(snapshots):
        if i + 1 < len(snapshots):
            snap.resume.return_value = snapshots[i + 1]
        else:
            snap.resume.return_value = complete

    monty = MagicMock()
    monty.start.return_value = snapshots[0] if snapshots else complete
    return monty


def _build_error_monty_mock() -> MagicMock:
    """Build a mock Monty that raises on start (simulates code error)."""
    monty = MagicMock()
    monty.start.side_effect = RuntimeError("NameError: name 'undefined_var' is not defined")
    return monty


class TestCodeStrategyE2EValid:
    def test_code_strategy_through_monty_executor(self) -> None:
        scenario = FakeCodeScenario()
        mock = _build_code_monty_mock(scenario, seed=42)

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, replay = executor.execute_code_strategy(
                scenario=scenario,
                code="result = {'aggression': 0.7, 'defense': 0.5}",
                seed=42,
                limits=ExecutionLimits(),
            )

        assert result.score > 0
        assert result.winner == "challenger"
        assert replay.scenario == "fake_code_e2e"

    def test_code_strategy_through_local_executor(self) -> None:
        from autocontext.execution.executors.local import LocalExecutor

        scenario = FakeCodeScenario()
        mock = _build_code_monty_mock(scenario, seed=42)

        executor = LocalExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, replay = executor.execute(
                scenario=scenario,
                strategy={"__code__": "result = {'aggression': 0.7, 'defense': 0.5}"},
                seed=42,
                limits=ExecutionLimits(),
            )

        assert result.score > 0
        assert replay.scenario == "fake_code_e2e"

    def test_code_strategy_via_execute_code_key(self) -> None:
        scenario = FakeCodeScenario()
        mock = _build_code_monty_mock(scenario, seed=42)

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, _ = executor.execute(
                scenario=scenario,
                strategy={"__code__": "result = {'aggression': 0.7, 'defense': 0.5}"},
                seed=42,
                limits=ExecutionLimits(),
            )

        assert result.score > 0


class TestCodeStrategyE2EInvalid:
    def test_runtime_error_returns_zero(self) -> None:
        scenario = FakeCodeScenario()
        mock = _build_error_monty_mock()

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, replay = executor.execute_code_strategy(
                scenario=scenario,
                code="undefined_var + 1",
                seed=42,
                limits=ExecutionLimits(),
            )

        assert result.score == 0.0
        assert len(result.validation_errors) > 0
        assert "code_error" in replay.timeline[0]["event"]

    def test_runtime_error_via_code_key(self) -> None:
        """__code__ detection + runtime error still returns zero."""
        scenario = FakeCodeScenario()
        mock = _build_error_monty_mock()

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, _ = executor.execute(
                scenario=scenario,
                strategy={"__code__": "undefined_var"},
                seed=42,
                limits=ExecutionLimits(),
            )

        assert result.score == 0.0


class TestCodeStrategyE2EWithSupervisor:
    def test_code_strategy_through_execution_supervisor(self) -> None:
        from autocontext.execution.supervisor import ExecutionInput, ExecutionSupervisor

        scenario = FakeCodeScenario()
        mock = _build_code_monty_mock(scenario, seed=99)

        executor = MontyExecutor()
        supervisor = ExecutionSupervisor(executor=executor)
        payload = ExecutionInput(
            strategy={"__code__": "result = {'aggression': 0.7, 'defense': 0.5}"},
            seed=99,
            limits=ExecutionLimits(),
        )

        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            output = supervisor.run(scenario, payload)

        assert output.result.score > 0
        assert output.replay.scenario == "fake_code_e2e"

    def test_code_strategy_through_scenario_evaluator(self) -> None:
        from autocontext.execution.supervisor import ExecutionSupervisor
        from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
        from autocontext.harness.evaluation.types import EvaluationLimits as HarnessLimits

        scenario = FakeCodeScenario()
        mock = _build_code_monty_mock(scenario, seed=77)

        executor = MontyExecutor()
        supervisor = ExecutionSupervisor(executor=executor)
        evaluator = ScenarioEvaluator(scenario, supervisor)

        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result = evaluator.evaluate(
                candidate={"__code__": "result = {'aggression': 0.7, 'defense': 0.5}"},
                seed=77,
                limits=HarnessLimits(),
            )

        assert result.score > 0
        assert result.passed is True
