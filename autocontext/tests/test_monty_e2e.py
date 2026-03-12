"""End-to-end integration tests for MontyExecutor with real scenario logic."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autocontext.execution.executors.monty import MontyExecutor
from autocontext.scenarios.base import ExecutionLimits, Result


class FakeScenario:
    """Minimal scenario for e2e testing."""

    name = "fake_e2e"

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "terminal": False, "timeline": [], "resource_density": 0.5}

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
            "timeline": [{"event": "turn_complete", "turn": 1, "score": round(min(1.0, score), 4)}],
            "metrics": {"aggression": agg, "defense": defense},
        }

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        score = float(state.get("score", 0.0))
        return Result(
            score=score,
            winner="challenger" if score >= 0.5 else "incumbent",
            summary=f"Fake e2e score {score:.4f}",
            replay=state.get("timeline", []),
            metrics=state.get("metrics", {}),
            validation_errors=[],
        )

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "Fake e2e replay."


def _simulate_monty_execution(
    scenario: FakeScenario,
    strategy: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    """Simulate what the Monty eval script would do, for building mock chain."""
    state = scenario.initial_state(seed=seed)
    valid, reason = scenario.validate_actions(state, "challenger", strategy)
    if not valid:
        return {
            "score": 0.0, "winner": "incumbent",
            "summary": "strategy rejected during validation",
            "replay": [{"event": "validation_failed", "reason": reason}],
            "metrics": {"valid": 0.0},
            "validation_errors": [reason],
        }
    next_state = scenario.step(state, strategy)
    result = scenario.get_result(next_state)
    return result.model_dump()


def _build_monty_mock(scenario: FakeScenario, strategy: dict[str, Any], seed: int) -> MagicMock:
    """Build a mock Monty that simulates the external function call chain."""
    # Determine the external call sequence the eval script would make
    calls: list[tuple[str, tuple[Any, ...]]] = []
    state = scenario.initial_state(seed=seed)
    calls.append(("initial_state", (seed,)))

    calls.append(("validate_actions", (state, strategy)))
    valid, reason = scenario.validate_actions(state, "challenger", strategy)

    if valid:
        calls.append(("step", (state, strategy)))
        next_state = scenario.step(state, strategy)
        calls.append(("is_terminal", (next_state,)))
        calls.append(("get_result", (next_state,)))

    final_result = _simulate_monty_execution(scenario, strategy, seed)

    complete = MagicMock(spec=[])  # spec=[] so hasattr(complete, "function_name") is False
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


class TestMontyE2EValidStrategy:
    def test_valid_strategy_scores_correctly(self) -> None:
        scenario = FakeScenario()
        strategy = {"aggression": 0.8, "defense": 0.5}
        mock = _build_monty_mock(scenario, strategy, seed=42)

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, replay = executor.execute(
                scenario=scenario,
                strategy=strategy,
                seed=42,
                limits=ExecutionLimits(),
            )

        expected_score = 0.8 * 0.6 + 0.5 * 0.2 + 0.1  # 0.68
        assert result.score == pytest.approx(expected_score, abs=0.01)
        assert result.winner == "challenger"
        assert replay.scenario == "fake_e2e"

    def test_high_aggression_scores_high(self) -> None:
        scenario = FakeScenario()
        strategy = {"aggression": 1.0, "defense": 1.0}
        mock = _build_monty_mock(scenario, strategy, seed=1)

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, _ = executor.execute(
                scenario=scenario,
                strategy=strategy,
                seed=1,
                limits=ExecutionLimits(),
            )

        assert result.score >= 0.8

    def test_different_seeds_produce_same_score_for_same_strategy(self) -> None:
        """Deterministic scenario: same strategy = same score regardless of seed."""
        scenario = FakeScenario()
        strategy = {"aggression": 0.5, "defense": 0.5}

        results = []
        for seed in [1, 2, 3]:
            mock = _build_monty_mock(scenario, strategy, seed=seed)
            executor = MontyExecutor()
            with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
                result, _ = executor.execute(
                    scenario=scenario,
                    strategy=strategy,
                    seed=seed,
                    limits=ExecutionLimits(),
                )
            results.append(result.score)

        assert results[0] == results[1] == results[2]


class TestMontyE2EInvalidStrategy:
    def test_missing_field_returns_zero_score(self) -> None:
        scenario = FakeScenario()
        strategy: dict[str, Any] = {"defense": 0.5}  # missing aggression
        mock = _build_monty_mock(scenario, strategy, seed=42)

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, _ = executor.execute(
                scenario=scenario,
                strategy=strategy,
                seed=42,
                limits=ExecutionLimits(),
            )

        assert result.score == 0.0
        assert "missing aggression" in result.validation_errors

    def test_invalid_value_returns_zero_score(self) -> None:
        scenario = FakeScenario()
        strategy = {"aggression": 5.0}  # out of range
        mock = _build_monty_mock(scenario, strategy, seed=42)

        executor = MontyExecutor()
        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result, _ = executor.execute(
                scenario=scenario,
                strategy=strategy,
                seed=42,
                limits=ExecutionLimits(),
            )

        assert result.score == 0.0


class TestMontyE2EWithSupervisor:
    def test_works_through_execution_supervisor(self) -> None:
        """MontyExecutor integrates with ExecutionSupervisor end-to-end."""
        from autocontext.execution.supervisor import ExecutionInput, ExecutionSupervisor

        scenario = FakeScenario()
        strategy = {"aggression": 0.7, "defense": 0.3}
        mock = _build_monty_mock(scenario, strategy, seed=99)

        executor = MontyExecutor()
        supervisor = ExecutionSupervisor(executor=executor)
        payload = ExecutionInput(
            strategy=strategy,
            seed=99,
            limits=ExecutionLimits(),
        )

        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            output = supervisor.run(scenario, payload)

        assert output.result.score > 0
        assert output.replay.scenario == "fake_e2e"

    def test_works_through_scenario_evaluator(self) -> None:
        """MontyExecutor integrates through the full harness evaluation path."""
        from autocontext.execution.supervisor import ExecutionSupervisor
        from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
        from autocontext.harness.evaluation.types import EvaluationLimits as HarnessLimits

        scenario = FakeScenario()
        strategy = {"aggression": 0.6, "defense": 0.4}
        mock = _build_monty_mock(scenario, strategy, seed=77)

        executor = MontyExecutor()
        supervisor = ExecutionSupervisor(executor=executor)
        evaluator = ScenarioEvaluator(scenario, supervisor)

        with patch("autocontext.execution.executors.monty._create_monty", return_value=mock):
            result = evaluator.evaluate(
                candidate=strategy,
                seed=77,
                limits=HarnessLimits(),
            )

        assert result.score > 0
        assert result.passed is True
