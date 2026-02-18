"""EvaluationRunner — generic N-trial evaluation with Elo scoring."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mts.harness.evaluation.protocol import Evaluator
from mts.harness.evaluation.types import EvaluationLimits, EvaluationResult, EvaluationSummary
from mts.harness.scoring.elo import update_elo


class EvaluationRunner:
    def __init__(
        self,
        evaluator: Evaluator,
        opponent_elo: float = 1000.0,
        win_threshold: float = 0.55,
    ) -> None:
        self._evaluator = evaluator
        self._opponent_elo = opponent_elo
        self._win_threshold = win_threshold

    def run(
        self,
        *,
        candidate: Mapping[str, Any],
        seed_base: int,
        trials: int,
        limits: EvaluationLimits,
        challenger_elo: float,
        on_result: Callable[[int, EvaluationResult], None] | None = None,
    ) -> EvaluationSummary:
        results: list[EvaluationResult] = []
        elo = challenger_elo
        wins = 0
        losses = 0
        scores: list[float] = []

        for offset in range(trials):
            result = self._evaluator.evaluate(candidate, seed_base + offset, limits)
            results.append(result)
            scores.append(result.score)
            actual = 1.0 if result.score >= self._win_threshold else 0.0
            wins += int(actual == 1.0)
            losses += int(actual == 0.0)
            elo = update_elo(elo, self._opponent_elo, actual)
            if on_result:
                on_result(offset, result)

        return EvaluationSummary(
            mean_score=sum(scores) / len(scores) if scores else 0.0,
            best_score=max(scores) if scores else 0.0,
            wins=wins,
            losses=losses,
            elo_after=elo,
            results=results,
        )
