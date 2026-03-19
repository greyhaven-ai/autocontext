"""EvaluationRunner — generic N-trial evaluation with Elo scoring."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from autocontext.harness.evaluation.protocol import Evaluator
from autocontext.harness.evaluation.types import EvaluationLimits, EvaluationResult, EvaluationSummary
from autocontext.harness.scoring.backends import TrialResult, get_backend


def _comparative_score(candidate_score: float, opponent_score: float) -> float:
    return max(0.0, min(1.0, 0.5 + ((candidate_score - opponent_score) / 2.0)))


class EvaluationRunner:
    def __init__(
        self,
        evaluator: Evaluator,
        opponent_elo: float = 1000.0,
        win_threshold: float = 0.55,
        scoring_backend: str = "elo",
    ) -> None:
        self._evaluator = evaluator
        self._opponent_elo = opponent_elo
        self._win_threshold = win_threshold
        self._backend = get_backend(scoring_backend)

    def run(
        self,
        *,
        candidate: Mapping[str, Any],
        seed_base: int,
        trials: int,
        limits: EvaluationLimits,
        challenger_elo: float,
        challenger_uncertainty: float | None = None,
        opponent_pool: Sequence[Mapping[str, Any]] | None = None,
        on_result: Callable[[int, EvaluationResult], None] | None = None,
    ) -> EvaluationSummary:
        results: list[EvaluationResult] = []
        elo = challenger_elo
        rating_uncertainty = challenger_uncertainty
        self_play_elo = challenger_elo
        self_play_uncertainty = challenger_uncertainty
        wins = 0
        losses = 0
        scores: list[float] = []
        self_play_scores: list[float] = []
        baseline_matches = 0
        self_play_matches = 0
        self_play_wins = 0
        self_play_losses = 0
        dimension_totals: dict[str, float] = defaultdict(float)
        dimension_counts: dict[str, int] = defaultdict(int)
        dimension_trajectory: list[dict[str, float]] = []
        dimension_specs: list[dict[str, Any]] = []

        for offset in range(trials):
            seed = seed_base + offset
            result = self._evaluator.evaluate(candidate, seed, limits)
            actual: float

            opponent_entry = (
                opponent_pool[offset]
                if opponent_pool is not None and offset < len(opponent_pool)
                else {}
            )
            source = (
                str(opponent_entry.get("source"))
                if isinstance(opponent_entry, Mapping) and isinstance(opponent_entry.get("source"), str)
                else "baseline"
            )
            metadata = dict(result.metadata)

            if source == "self_play":
                opponent_strategy = opponent_entry.get("strategy")
                if isinstance(opponent_strategy, Mapping):
                    opponent_result = self._evaluator.evaluate(opponent_strategy, seed, limits)
                    candidate_raw_score = result.score
                    opponent_raw_score = opponent_result.score
                    if candidate_raw_score > opponent_raw_score:
                        actual = 1.0
                        self_play_wins += 1
                    elif candidate_raw_score < opponent_raw_score:
                        actual = 0.0
                        self_play_losses += 1
                    else:
                        actual = 0.5
                    effective_score = _comparative_score(candidate_raw_score, opponent_raw_score)
                    metadata["self_play"] = {
                        "opponent_generation": opponent_entry.get("generation"),
                        "opponent_elo": opponent_entry.get("elo", self._opponent_elo),
                        "candidate_raw_score": candidate_raw_score,
                        "opponent_raw_score": opponent_raw_score,
                        "effective_score": effective_score,
                        "outcome": actual,
                    }
                    metadata["match_source"] = "self_play"
                    result = replace(result, score=effective_score, metadata=metadata)
                    opponent_elo = (
                        float(opponent_entry["elo"])
                        if isinstance(opponent_entry.get("elo"), (int, float))
                        else self._opponent_elo
                    )
                    self_play_update = self._backend.update(
                        self_play_elo,
                        [
                            TrialResult(
                                score=effective_score,
                                seed=seed,
                                opponent_rating=opponent_elo,
                                metadata=metadata,
                            ),
                        ],
                        uncertainty=self_play_uncertainty,
                    )
                    self_play_elo = self_play_update.rating_after
                    self_play_uncertainty = self_play_update.uncertainty_after
                    self_play_matches += 1
                    self_play_scores.append(effective_score)
                else:
                    source = "baseline"

            if source != "self_play":
                actual = 1.0 if result.score >= self._win_threshold else 0.0
                metadata["match_source"] = "baseline"
                result = replace(result, metadata=metadata)
                baseline_matches += 1
                update = self._backend.update(
                    elo,
                    [
                        TrialResult(
                            score=result.score,
                            seed=seed,
                            opponent_rating=self._opponent_elo,
                            metadata=metadata,
                        ),
                    ],
                    uncertainty=rating_uncertainty,
                )
                elo = update.rating_after
                rating_uncertainty = update.uncertainty_after

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
            wins += int(actual == 1.0)
            losses += int(actual == 0.0)
            if on_result:
                on_result(offset, result)

        best_result = max(results, key=lambda r: r.score) if results else None
        dimension_means = {
            name: round(total / dimension_counts[name], 6)
            for name, total in dimension_totals.items()
            if dimension_counts[name] > 0
        }
        self_play_summary: dict[str, Any] = {}
        if opponent_pool is not None:
            self_play_summary = {
                "baseline_matches": baseline_matches,
                "self_play_matches": self_play_matches,
                "observed_weight": round(self_play_matches / trials, 6) if trials > 0 else 0.0,
            }
            if self_play_matches > 0:
                self_play_summary.update({
                    "self_play_mean_score": round(sum(self_play_scores) / self_play_matches, 6),
                    "self_play_elo_after": round(self_play_elo, 6),
                    "self_play_wins": self_play_wins,
                    "self_play_losses": self_play_losses,
                    "self_play_uncertainty_after": (
                        round(self_play_uncertainty, 6)
                        if self_play_uncertainty is not None
                        else None
                    ),
                })

        return EvaluationSummary(
            mean_score=sum(scores) / len(scores) if scores else 0.0,
            best_score=max(scores) if scores else 0.0,
            wins=wins,
            losses=losses,
            elo_after=elo,
            results=results,
            scoring_backend=self._backend.name,
            uncertainty_after=rating_uncertainty,
            dimension_means=dimension_means,
            best_dimensions=dict(best_result.dimension_scores) if best_result is not None else {},
            dimension_trajectory=dimension_trajectory,
            dimension_specs=dimension_specs,
            self_play_summary=self_play_summary,
        )
