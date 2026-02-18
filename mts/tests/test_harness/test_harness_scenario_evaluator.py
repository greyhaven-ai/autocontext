"""Tests for ScenarioEvaluator — adapter bridging ScenarioInterface to Evaluator protocol."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits, EvaluationResult


class FakeResult:
    def __init__(self, score: float, errors: list[str] | None = None) -> None:
        self.score = score
        self.summary = "test"
        self.replay: list[dict[str, Any]] = []
        self.metrics: dict[str, float] = {"score": score}
        self.validation_errors = errors or []
        self.passed_validation = len(self.validation_errors) == 0


class FakeReplay:
    def __init__(self) -> None:
        self.scenario = "test"
        self.seed = 0
        self.narrative = "replay"
        self.timeline: list[dict[str, Any]] = []

    def model_dump(self) -> dict[str, Any]:
        return {"scenario": self.scenario, "seed": self.seed}


@dataclass
class FakeExecutionOutput:
    result: FakeResult
    replay: FakeReplay


class FakeScenario:
    name = "test_scenario"

    def execute_match(self, strategy: Mapping[str, Any], seed: int) -> FakeResult:
        return FakeResult(score=float(strategy.get("score", 0.5)))


class FakeSupervisor:
    def __init__(self, score: float = 0.75) -> None:
        self._score = score
        self.calls: list[tuple[Any, Any]] = []

    def run(self, scenario: Any, payload: Any) -> FakeExecutionOutput:
        self.calls.append((scenario, payload))
        return FakeExecutionOutput(result=FakeResult(score=self._score), replay=FakeReplay())


class TestScenarioEvaluator:
    def test_implements_evaluator_protocol(self) -> None:
        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor())
        assert hasattr(evaluator, "evaluate")

    def test_evaluate_returns_evaluation_result(self) -> None:
        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor(score=0.8))
        result = evaluator.evaluate({"score": 0.8}, seed=42, limits=EvaluationLimits())
        assert isinstance(result, EvaluationResult)
        assert result.score == 0.8

    def test_evaluate_passes_strategy_and_seed(self) -> None:
        supervisor = FakeSupervisor()
        evaluator = ScenarioEvaluator(FakeScenario(), supervisor)
        evaluator.evaluate({"score": 0.5}, seed=99, limits=EvaluationLimits())
        assert len(supervisor.calls) == 1
        _, payload = supervisor.calls[0]
        assert payload.seed == 99

    def test_evaluate_maps_limits(self) -> None:
        supervisor = FakeSupervisor()
        evaluator = ScenarioEvaluator(FakeScenario(), supervisor)
        limits = EvaluationLimits(timeout_seconds=30.0, max_memory_mb=1024)
        evaluator.evaluate({}, seed=1, limits=limits)
        _, payload = supervisor.calls[0]
        assert payload.limits.timeout_seconds == 30.0
        assert payload.limits.max_memory_mb == 1024

    def test_evaluate_captures_errors(self) -> None:
        class ErrorSupervisor:
            def run(self, scenario: Any, payload: Any) -> FakeExecutionOutput:
                return FakeExecutionOutput(
                    result=FakeResult(score=0.0, errors=["invalid param"]),
                    replay=FakeReplay(),
                )
        evaluator = ScenarioEvaluator(FakeScenario(), ErrorSupervisor())
        result = evaluator.evaluate({}, seed=1, limits=EvaluationLimits())
        assert result.errors == ["invalid param"]
        assert result.passed is False

    def test_evaluate_captures_replay_data(self) -> None:
        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor())
        result = evaluator.evaluate({}, seed=1, limits=EvaluationLimits())
        assert "scenario" in result.replay_data

    def test_works_with_evaluation_runner(self) -> None:
        from mts.harness.evaluation.runner import EvaluationRunner
        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor(score=0.7))
        runner = EvaluationRunner(evaluator=evaluator)
        summary = runner.run(
            candidate={"score": 0.7}, seed_base=0, trials=3,
            limits=EvaluationLimits(), challenger_elo=1000.0,
        )
        assert summary.mean_score == pytest.approx(0.7)
        assert len(summary.results) == 3
