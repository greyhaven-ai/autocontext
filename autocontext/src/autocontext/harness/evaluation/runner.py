"""EvaluationRunner — generic N-trial evaluation with Elo scoring."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from autocontext.harness.evaluation.protocol import Evaluator
from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult, EvaluationSummary
from autocontext.harness.scoring.elo import update_elo


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
        dimension_totals: dict[str, float] = defaultdict(float)
        dimension_counts: dict[str, int] = defaultdict(int)
        dimension_trajectory: list[dict[str, float]] = []
        dimension_specs: list[dict[str, Any]] = []

        for offset in range(trials):
            result = self._evaluator.evaluate(candidate, seed_base + offset, limits)
            results.append(result)
            scores.append(result.score)
            if result.dimension_scores:
                dimension_trajectory.append(dict(result.dimension_scores))
                for name, value in result.dimension_scores.items():
                    dimension_totals[name] += value
                    dimension_counts[name] += 1
            if not dimension_specs:
                raw_specs = result.metadata.get("dimension_specs")
                if isinstance(raw_specs, list):
                    dimension_specs = [spec for spec in raw_specs if isinstance(spec, dict)]
            actual = 1.0 if result.score >= self._win_threshold else 0.0
            wins += int(actual == 1.0)
            losses += int(actual == 0.0)
            elo = update_elo(elo, self._opponent_elo, actual)
            if on_result:
                on_result(offset, result)

        best_result = max(results, key=lambda r: r.score) if results else None
        dimension_means = {
            name: round(total / dimension_counts[name], 6)
            for name, total in dimension_totals.items()
            if dimension_counts[name] > 0
        }

        return EvaluationSummary(
            mean_score=sum(scores) / len(scores) if scores else 0.0,
            best_score=max(scores) if scores else 0.0,
            wins=wins,
            losses=losses,
            elo_after=elo,
            results=results,
            dimension_means=dimension_means,
            best_dimensions=dict(best_result.dimension_scores) if best_result is not None else {},
            dimension_trajectory=dimension_trajectory,
            dimension_specs=dimension_specs,
        )
