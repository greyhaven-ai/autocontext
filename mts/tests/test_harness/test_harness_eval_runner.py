"""Tests for mts.harness.evaluation.runner — N-trial evaluation runner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.types import EvaluationLimits, EvaluationResult


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
