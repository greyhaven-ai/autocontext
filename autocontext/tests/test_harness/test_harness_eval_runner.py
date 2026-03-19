"""Tests for autocontext.harness.evaluation.runner — N-trial evaluation runner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from autocontext.harness.evaluation.runner import EvaluationRunner
from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult


class _FixedEvaluator:
    """Returns a fixed score for all evaluations."""

    def __init__(self, score: float) -> None:
        self._score = score

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        return EvaluationResult(score=self._score)


class _SeedEvaluator:
    """Returns score based on seed for varied results."""

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        return EvaluationResult(score=seed / 100.0)


class _ErrorEvaluator:
    """Raises an exception."""

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        raise RuntimeError("evaluation failed")


class _DimensionalEvaluator:
    def __init__(self) -> None:
        self._results = [
            EvaluationResult(
                score=0.70,
                dimension_scores={"control": 0.8, "tempo": 0.6},
                metadata={"dimension_specs": [{"name": "control"}, {"name": "tempo"}]},
            ),
            EvaluationResult(
                score=0.75,
                dimension_scores={"control": 0.9, "tempo": 0.5},
                metadata={"dimension_specs": [{"name": "control"}, {"name": "tempo"}]},
            ),
        ]

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        return self._results[seed]


class _StrategyEvaluator:
    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        return EvaluationResult(score=float(candidate["score"]))


class TestEvaluationRunner:
    def test_runner_single_trial(self) -> None:
        runner = EvaluationRunner(evaluator=_FixedEvaluator(0.7))
        summary = runner.run(
            candidate={"strategy": "test"},
            seed_base=0,
            trials=1,
            limits=EvaluationLimits(),
            challenger_elo=1000.0,
        )
        assert summary.mean_score == pytest.approx(0.7)
        assert summary.best_score == pytest.approx(0.7)
        assert len(summary.results) == 1

    def test_runner_multiple_trials(self) -> None:
        runner = EvaluationRunner(evaluator=_FixedEvaluator(0.6))
        summary = runner.run(
            candidate={}, seed_base=0, trials=5, limits=EvaluationLimits(), challenger_elo=1000.0
        )
        assert summary.mean_score == pytest.approx(0.6)
        assert len(summary.results) == 5
        assert summary.wins + summary.losses == 5

    def test_runner_elo_updates_on_win(self) -> None:
        runner = EvaluationRunner(evaluator=_FixedEvaluator(0.8), win_threshold=0.55)
        summary = runner.run(
            candidate={}, seed_base=0, trials=3, limits=EvaluationLimits(), challenger_elo=1000.0
        )
        assert summary.elo_after > 1000.0

    def test_runner_elo_updates_on_loss(self) -> None:
        runner = EvaluationRunner(evaluator=_FixedEvaluator(0.3), win_threshold=0.55)
        summary = runner.run(
            candidate={}, seed_base=0, trials=3, limits=EvaluationLimits(), challenger_elo=1000.0
        )
        assert summary.elo_after < 1000.0

    def test_runner_custom_win_threshold(self) -> None:
        # Score 0.6 with threshold 0.7 → loss
        runner = EvaluationRunner(evaluator=_FixedEvaluator(0.6), win_threshold=0.7)
        summary = runner.run(
            candidate={}, seed_base=0, trials=3, limits=EvaluationLimits(), challenger_elo=1000.0
        )
        assert summary.wins == 0
        assert summary.losses == 3

    def test_runner_mean_score_calculated(self) -> None:
        # Seeds: 0→0.0, 1→0.01, 2→0.02 → mean=0.01
        runner = EvaluationRunner(evaluator=_SeedEvaluator())
        summary = runner.run(
            candidate={}, seed_base=0, trials=3, limits=EvaluationLimits(), challenger_elo=1000.0
        )
        expected_mean = (0.0 + 0.01 + 0.02) / 3
        assert summary.mean_score == pytest.approx(expected_mean)

    def test_runner_best_score_is_max(self) -> None:
        # Seeds: 10→0.1, 11→0.11, 12→0.12 → best=0.12
        runner = EvaluationRunner(evaluator=_SeedEvaluator())
        summary = runner.run(
            candidate={}, seed_base=10, trials=3, limits=EvaluationLimits(), challenger_elo=1000.0
        )
        assert summary.best_score == pytest.approx(0.12)

    def test_runner_on_result_callback(self) -> None:
        callback_log: list[tuple[int, float]] = []

        def on_result(trial_idx: int, result: EvaluationResult) -> None:
            callback_log.append((trial_idx, result.score))

        runner = EvaluationRunner(evaluator=_FixedEvaluator(0.5))
        runner.run(
            candidate={},
            seed_base=0,
            trials=3,
            limits=EvaluationLimits(),
            challenger_elo=1000.0,
            on_result=on_result,
        )
        assert len(callback_log) == 3
        assert callback_log[0] == (0, 0.5)
        assert callback_log[2] == (2, 0.5)

    def test_runner_collects_all_results(self) -> None:
        runner = EvaluationRunner(evaluator=_FixedEvaluator(0.75))
        summary = runner.run(
            candidate={}, seed_base=0, trials=4, limits=EvaluationLimits(), challenger_elo=1000.0
        )
        assert len(summary.results) == 4
        assert all(r.score == 0.75 for r in summary.results)

    def test_runner_handles_evaluator_error(self) -> None:
        runner = EvaluationRunner(evaluator=_ErrorEvaluator())
        with pytest.raises(RuntimeError, match="evaluation failed"):
            runner.run(
                candidate={}, seed_base=0, trials=1, limits=EvaluationLimits(), challenger_elo=1000.0
            )

    def test_runner_summarizes_dimension_scores(self) -> None:
        runner = EvaluationRunner(evaluator=_DimensionalEvaluator())
        summary = runner.run(
            candidate={},
            seed_base=0,
            trials=2,
            limits=EvaluationLimits(),
            challenger_elo=1000.0,
        )
        assert summary.dimension_means == {"control": pytest.approx(0.85), "tempo": pytest.approx(0.55)}
        assert summary.best_dimensions == {"control": 0.9, "tempo": 0.5}
        assert len(summary.dimension_trajectory) == 2

    def test_runner_applies_self_play_schedule(self) -> None:
        runner = EvaluationRunner(evaluator=_StrategyEvaluator())
        summary = runner.run(
            candidate={"score": 0.7},
            seed_base=0,
            trials=2,
            limits=EvaluationLimits(),
            challenger_elo=1000.0,
            opponent_pool=[
                {"source": "baseline"},
                {"source": "self_play", "strategy": {"score": 0.9}, "generation": 1, "elo": 1100.0},
            ],
        )

        assert summary.self_play_summary["baseline_matches"] == 1
        assert summary.self_play_summary["self_play_matches"] == 1
        assert summary.results[1].metadata["match_source"] == "self_play"
        assert summary.results[1].metadata["self_play"]["opponent_generation"] == 1
        assert summary.results[1].score == pytest.approx(0.4)
        assert summary.mean_score == pytest.approx((0.7 + 0.4) / 2)

    def test_runner_uses_selected_scoring_backend(self) -> None:
        runner = EvaluationRunner(evaluator=_FixedEvaluator(0.8), scoring_backend="glicko")
        summary = runner.run(
            candidate={},
            seed_base=0,
            trials=3,
            limits=EvaluationLimits(),
            challenger_elo=1500.0,
            challenger_uncertainty=350.0,
        )
        assert summary.scoring_backend == "glicko"
        assert summary.uncertainty_after is not None
