"""TournamentEvalAdapter — EvaluationRunner-backed tournament producing TournamentSummary."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mts.execution.supervisor import ExecutionInput, ExecutionOutput, ExecutionSupervisor
from mts.execution.tournament import TournamentSummary
from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits as HarnessLimits
from mts.harness.evaluation.types import EvaluationResult
from mts.scenarios.base import ExecutionLimits, ScenarioInterface


class TournamentEvalAdapter:
    """Wraps EvaluationRunner to produce TournamentSummary for backward compatibility."""

    def __init__(self, supervisor: ExecutionSupervisor, opponent_elo: float = 1000.0) -> None:
        self.supervisor = supervisor
        self.opponent_elo = opponent_elo

    def run(
        self,
        *,
        scenario: ScenarioInterface,
        strategy: dict[str, Any],
        seed_base: int,
        matches: int,
        limits: ExecutionLimits,
        challenger_elo: float,
        on_match: Callable[[int, float], None] | None = None,
    ) -> TournamentSummary:
        # Collect ExecutionOutputs for backward compat
        outputs: list[ExecutionOutput] = []
        for offset in range(matches):
            payload = ExecutionInput(strategy=strategy, seed=seed_base + offset, limits=limits)
            output = self.supervisor.run(scenario, payload)
            outputs.append(output)

        # Use EvaluationRunner for scoring + Elo
        evaluator = ScenarioEvaluator(scenario, self.supervisor)
        harness_limits = HarnessLimits(
            timeout_seconds=limits.timeout_seconds,
            max_memory_mb=limits.max_memory_mb,
            network_access=limits.network_access,
        )

        def _on_result(idx: int, result: EvaluationResult) -> None:
            if on_match:
                on_match(idx, result.score)

        runner = EvaluationRunner(evaluator, opponent_elo=self.opponent_elo)
        summary = runner.run(
            candidate=strategy,
            seed_base=seed_base,
            trials=matches,
            limits=harness_limits,
            challenger_elo=challenger_elo,
            on_result=_on_result,
        )

        return TournamentSummary(
            mean_score=summary.mean_score,
            best_score=summary.best_score,
            wins=summary.wins,
            losses=summary.losses,
            elo_after=summary.elo_after,
            outputs=outputs,
        )
