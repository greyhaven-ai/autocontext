"""Multi-step improvement loop for agent tasks.

Orchestrates: generate -> judge -> revise -> judge -> ... -> done.
Stops when quality_threshold is met or max_rounds is exhausted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from mts.scenarios.agent_task import AgentTaskInterface

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RoundResult:
    """Result from a single improvement round."""

    round_number: int
    output: str
    score: float
    reasoning: str
    dimension_scores: dict[str, float] = field(default_factory=dict)
    is_revision: bool = False


@dataclass(slots=True)
class ImprovementResult:
    """Result from the full improvement loop."""

    rounds: list[RoundResult]
    best_output: str
    best_score: float
    best_round: int
    total_rounds: int
    met_threshold: bool

    @property
    def improved(self) -> bool:
        """Whether the final score is higher than the initial score."""
        if len(self.rounds) < 2:
            return False
        return self.rounds[-1].score > self.rounds[0].score


class ImprovementLoop:
    """Orchestrates multi-round improvement of agent task outputs.

    Each round:
    1. Evaluate current output with the judge
    2. If score >= threshold or max rounds reached, stop
    3. Call task.revise_output() with judge feedback
    4. Repeat with revised output
    """

    def __init__(
        self,
        task: AgentTaskInterface,
        max_rounds: int = 5,
        quality_threshold: float = 0.9,
    ) -> None:
        self.task = task
        self.max_rounds = max(1, max_rounds)
        self.quality_threshold = quality_threshold

    def run(
        self,
        initial_output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
    ) -> ImprovementResult:
        """Run the improvement loop."""
        rounds: list[RoundResult] = []
        current_output = initial_output
        best_output = initial_output
        best_score = 0.0
        best_round = 1

        for round_num in range(1, self.max_rounds + 1):
            logger.info("improvement loop round %d/%d", round_num, self.max_rounds)

            result = self.task.evaluate_output(
                current_output,
                state,
                reference_context=reference_context,
                required_concepts=required_concepts,
                calibration_examples=calibration_examples,
            )

            round_result = RoundResult(
                round_number=round_num,
                output=current_output,
                score=result.score,
                reasoning=result.reasoning,
                dimension_scores=result.dimension_scores,
                is_revision=round_num > 1,
            )
            rounds.append(round_result)

            if result.score > best_score:
                best_score = result.score
                best_output = current_output
                best_round = round_num

            logger.info(
                "round %d score: %.2f (best: %.2f at round %d)",
                round_num, result.score, best_score, best_round,
            )

            if result.score >= self.quality_threshold:
                logger.info("quality threshold %.2f met at round %d", self.quality_threshold, round_num)
                return ImprovementResult(
                    rounds=rounds,
                    best_output=best_output,
                    best_score=best_score,
                    best_round=best_round,
                    total_rounds=round_num,
                    met_threshold=True,
                )

            if round_num < self.max_rounds:
                revised = self.task.revise_output(current_output, result, state)
                if revised == current_output:
                    logger.info("revise_output returned unchanged output, stopping")
                    break
                current_output = revised

        return ImprovementResult(
            rounds=rounds,
            best_output=best_output,
            best_score=best_score,
            best_round=best_round,
            total_rounds=len(rounds),
            met_threshold=False,
        )
