from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mts.execution.elo import update_elo
from mts.execution.supervisor import ExecutionInput, ExecutionOutput, ExecutionSupervisor
from mts.scenarios.base import ExecutionLimits, ScenarioInterface


@dataclass(slots=True)
class TournamentSummary:
    mean_score: float
    best_score: float
    wins: int
    losses: int
    elo_after: float
    outputs: list[ExecutionOutput]


class TournamentRunner:
    def __init__(self, supervisor: ExecutionSupervisor, opponent_elo: float = 1000.0):
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
        outputs: list[ExecutionOutput] = []
        elo = challenger_elo
        wins = 0
        losses = 0
        scores: list[float] = []
        for offset in range(matches):
            payload = ExecutionInput(strategy=strategy, seed=seed_base + offset, limits=limits)
            output = self.supervisor.run(scenario, payload)
            outputs.append(output)
            score = output.result.score
            scores.append(score)
            actual = 1.0 if score >= 0.55 else 0.0
            wins += int(actual == 1.0)
            losses += int(actual == 0.0)
            elo = update_elo(elo, self.opponent_elo, actual)
            if on_match:
                on_match(offset, score)
        mean_score = sum(scores) / len(scores)
        return TournamentSummary(
            mean_score=mean_score,
            best_score=max(scores),
            wins=wins,
            losses=losses,
            elo_after=elo,
            outputs=outputs,
        )
