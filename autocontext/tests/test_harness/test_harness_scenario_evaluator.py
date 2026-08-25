"""Tests for ScenarioEvaluator — adapter bridging ScenarioInterface to Evaluator protocol."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from autocontext.harness.evaluation.scenario_evaluator import (
    ScenarioEvaluator,
    generation_evaluation_namespace,
)
from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult


class FakeResult:
    def __init__(
        self,
        score: float,
        errors: list[str] | None = None,
        metrics: dict[str, float] | None = None,
    ) -> None:
        self.score = score
        self.summary = "test"
        self.replay: list[dict[str, Any]] = []
        self.metrics: dict[str, float] = metrics or {"score": score}
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

    def scoring_dimensions(self) -> list[dict[str, Any]] | None:
        return None


class FakeSupervisor:
    def __init__(
        self,
        score: float = 0.75,
        metrics: dict[str, float] | None = None,
    ) -> None:
        self._score = score
        self._metrics = metrics
        self.calls: list[tuple[Any, Any]] = []

    def run(self, scenario: Any, payload: Any) -> FakeExecutionOutput:
        self.calls.append((scenario, payload))
        return FakeExecutionOutput(
            result=FakeResult(score=self._score, metrics=self._metrics),
            replay=FakeReplay(),
        )


class FakeDurableSupervisor:
    """Small task-ID cache modeling durable provider-result replay."""

    def __init__(self) -> None:
        self.cache: dict[str, FakeExecutionOutput] = {}
        self.provider_calls = 0
        self.task_ids: list[str] = []

    def run(self, scenario: Any, payload: Any) -> FakeExecutionOutput:
        del scenario
        assert payload.task_id is not None
        self.task_ids.append(payload.task_id)
        cached = self.cache.get(payload.task_id)
        if cached is not None:
            return cached
        self.provider_calls += 1
        output = FakeExecutionOutput(
            result=FakeResult(score=float(self.provider_calls)),
            replay=FakeReplay(),
        )
        self.cache[payload.task_id] = output
        return output


class _DivergentCandidate(Mapping[str, Any]):
    def __init__(self) -> None:
        self.reads = 0

    def __getitem__(self, key: str) -> Any:
        if key != "score":
            raise KeyError(key)
        self.reads += 1
        return 0.25 if self.reads == 1 else 0.75

    def __iter__(self) -> Iterator[str]:
        return iter(("score",))

    def __len__(self) -> int:
        return 1


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

    def test_generation_identity_separates_fresh_runs_and_replays_restart(self) -> None:
        supervisor = FakeDurableSupervisor()
        run_a_namespace = generation_evaluation_namespace("run-a", 2, "tournament:attempt:0")
        run_b_namespace = generation_evaluation_namespace("run-b", 2, "tournament:attempt:0")
        candidate = {"score": 0.5}
        limits = EvaluationLimits()

        first = ScenarioEvaluator(
            FakeScenario(),
            supervisor,
            task_namespace=run_a_namespace,
        ).evaluate(candidate, seed=99, limits=limits)
        independent = ScenarioEvaluator(
            FakeScenario(),
            supervisor,
            task_namespace=run_b_namespace,
        ).evaluate(candidate, seed=99, limits=limits)
        restarted = ScenarioEvaluator(
            FakeScenario(),
            supervisor,
            task_namespace=run_a_namespace,
        ).evaluate(candidate, seed=99, limits=limits)

        assert first.score == 1.0
        assert independent.score == 2.0
        assert restarted.score == first.score
        assert supervisor.provider_calls == 2
        assert supervisor.task_ids[0] != supervisor.task_ids[1]
        assert supervisor.task_ids[0] == supervisor.task_ids[2]

    def test_generation_identity_binds_candidate_and_seed(self) -> None:
        supervisor = FakeSupervisor()
        evaluator = ScenarioEvaluator(
            FakeScenario(),
            supervisor,
            task_namespace=generation_evaluation_namespace("run-a", 2, "probe"),
        )

        evaluator.evaluate({"move": "up"}, seed=10, limits=EvaluationLimits())
        evaluator.evaluate({"move": "down"}, seed=10, limits=EvaluationLimits())
        evaluator.evaluate({"move": "up"}, seed=11, limits=EvaluationLimits())

        task_ids = [payload.task_id for _, payload in supervisor.calls]
        assert all(task_id is not None for task_id in task_ids)
        assert len(set(task_ids)) == 3

    def test_candidate_identity_and_execution_share_one_top_level_snapshot(self) -> None:
        supervisor = FakeSupervisor()
        evaluator = ScenarioEvaluator(
            FakeScenario(),
            supervisor,
            task_namespace=generation_evaluation_namespace("run-a", 2, "snapshot"),
        )
        candidate = _DivergentCandidate()

        evaluator.evaluate(candidate, seed=10, limits=EvaluationLimits())

        payload = supervisor.calls[0][1]
        assert payload.strategy == {"score": 0.25}
        assert payload.task_id == evaluator._task_id({"score": 0.25}, 10, None)
        assert candidate.reads == 1

    def test_candidate_snapshot_detaches_nested_mutation_before_execution(self) -> None:
        candidate = {"policy": {"moves": ["up"]}}

        class MutatingSupervisor(FakeSupervisor):
            def run(self, scenario: Any, payload: Any) -> FakeExecutionOutput:
                candidate["policy"]["moves"][0] = "down"
                return super().run(scenario, payload)

        supervisor = MutatingSupervisor()
        evaluator = ScenarioEvaluator(
            FakeScenario(),
            supervisor,
            task_namespace=generation_evaluation_namespace("run-a", 2, "nested-snapshot"),
        )

        evaluator.evaluate(candidate, seed=10, limits=EvaluationLimits())

        payload = supervisor.calls[0][1]
        expected = {"policy": {"moves": ["up"]}}
        assert payload.strategy == expected
        assert payload.task_id == evaluator._task_id(expected, 10, None)

    def test_strict_semantic_identity_excludes_regenerated_candidate(self) -> None:
        supervisor = FakeSupervisor()
        evaluator = ScenarioEvaluator(
            FakeScenario(),
            supervisor,
            task_namespace=generation_evaluation_namespace("run-a", 2, "context-promotion:arm-a"),
            strict_task_identity=True,
        )

        evaluator.evaluate({"move": "first"}, seed=10, limits=EvaluationLimits())
        evaluator.evaluate({"move": "regenerated"}, seed=10, limits=EvaluationLimits())
        evaluator.evaluate({"move": "first"}, seed=11, limits=EvaluationLimits())

        payloads = [payload for _, payload in supervisor.calls]
        assert payloads[0].task_id == payloads[1].task_id
        assert payloads[0].task_id != payloads[2].task_id
        assert all(payload.strict_task_identity for payload in payloads)

    def test_strict_semantic_identity_requires_namespace(self) -> None:
        with pytest.raises(ValueError, match="requires an evaluation task_namespace"):
            ScenarioEvaluator(FakeScenario(), FakeSupervisor(), strict_task_identity=True)

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

    def test_evaluate_preserves_execution_output(self) -> None:
        """EvaluationResult.metadata contains the full ExecutionOutput."""
        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor(score=0.75))
        result = evaluator.evaluate({"aggression": 0.7}, seed=42, limits=EvaluationLimits())
        assert "execution_output" in result.metadata
        output = result.metadata["execution_output"]
        # Duck-typed check: the stored object must expose .result and .replay
        assert hasattr(output, "result")
        assert hasattr(output, "replay")
        assert output.result.score == result.score

    def test_evaluate_extracts_dimension_scores(self) -> None:
        class DimensionalScenario(FakeScenario):
            def scoring_dimensions(self) -> list[dict[str, Any]] | None:
                return [
                    {"name": "control", "weight": 0.6},
                    {"name": "tempo", "weight": 0.4},
                ]

        evaluator = ScenarioEvaluator(
            DimensionalScenario(),
            FakeSupervisor(
                score=0.75,
                metrics={"control": 0.8, "tempo": 0.7, "other": 1.0},
            ),
        )
        result = evaluator.evaluate({}, seed=1, limits=EvaluationLimits())
        assert result.dimension_scores == {"control": 0.8, "tempo": 0.7}
        assert result.metadata["dimension_specs"][0]["name"] == "control"

    def test_works_with_evaluation_runner(self) -> None:
        from autocontext.harness.evaluation.runner import EvaluationRunner

        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor(score=0.7))
        runner = EvaluationRunner(evaluator=evaluator)
        summary = runner.run(
            candidate={"score": 0.7},
            seed_base=0,
            trials=3,
            limits=EvaluationLimits(),
            challenger_elo=1000.0,
        )
        assert summary.mean_score == pytest.approx(0.7)
        assert len(summary.results) == 3
