"""Multi-step improvement loop for agent tasks.

Orchestrates: generate -> judge -> revise -> judge -> ... -> done.
Stops when quality_threshold is met or max_rounds is exhausted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult

logger = logging.getLogger(__name__)


_PARSE_FAILURE_MARKERS = frozenset({
    "no parseable score found",
    "missing JUDGE_RESULT markers",
    "invalid JSON",
    "Failed to parse judge response",
})


def _is_parse_failure(score: float, reasoning: str) -> bool:
    """Detect whether a judge result is a parse failure rather than a real score."""
    if score > 0.0:
        return False
    return any(marker in reasoning for marker in _PARSE_FAILURE_MARKERS)


@dataclass(slots=True)
class RoundResult:
    """Result from a single improvement round."""

    round_number: int
    output: str
    score: float
    reasoning: str
    dimension_scores: dict[str, float] = field(default_factory=dict)
    is_revision: bool = False
    judge_failed: bool = False


@dataclass(slots=True)
class ImprovementResult:
    """Result from the full improvement loop."""

    rounds: list[RoundResult]
    best_output: str
    best_score: float
    best_round: int
    total_rounds: int
    met_threshold: bool
    judge_failures: int = 0

    @property
    def improved(self) -> bool:
        """Whether the final score is higher than the initial score."""
        if len(self.rounds) < 2:
            return False
        valid = [r for r in self.rounds if not r.judge_failed]
        if len(valid) < 2:
            return False
        return valid[-1].score > valid[0].score


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
        """Run the improvement loop.

        Resilient to judge parse failures: failed rounds are logged but
        don't count toward max_rounds, and the last good feedback is
        carried forward for revision prompts.
        """
        rounds: list[RoundResult] = []
        current_output = initial_output
        best_output = initial_output
        best_score = 0.0
        best_round = 1
        judge_failures = 0
        last_good_result = None  # Carry forward for revision on judge failure
        consecutive_failures = 0
        max_consecutive_failures = 3  # Safety valve

        for round_num in range(1, self.max_rounds + 1):
            logger.info("improvement loop round %d/%d", round_num, self.max_rounds)

            result = self.task.evaluate_output(
                current_output,
                state,
                reference_context=reference_context,
                required_concepts=required_concepts,
                calibration_examples=calibration_examples,
            )

            failed = _is_parse_failure(result.score, result.reasoning)

            round_result = RoundResult(
                round_number=round_num,
                output=current_output,
                score=result.score,
                reasoning=result.reasoning,
                dimension_scores=result.dimension_scores,
                is_revision=round_num > 1,
                judge_failed=failed,
            )
            rounds.append(round_result)

            if failed:
                judge_failures += 1
                consecutive_failures += 1
                logger.warning(
                    "round %d: judge parse failure (%s), not counting toward score",
                    round_num, result.reasoning[:80],
                )
                if consecutive_failures >= max_consecutive_failures:
                    logger.error(
                        "aborting: %d consecutive judge failures", consecutive_failures,
                    )
                    break
                # Use last good feedback for revision if available
                if round_num < self.max_rounds:
                    if last_good_result is not None:
                        logger.info("using feedback from round %d for revision", last_good_result.round_number)
                        # Find the matching RoundResult to build a revision result
                        revised = self.task.revise_output(
                            current_output,
                            AgentTaskResult(
                                score=last_good_result.score,
                                reasoning=last_good_result.reasoning,
                                dimension_scores=last_good_result.dimension_scores,
                            ),
                            state,
                        )
                    else:
                        # No prior feedback — skip revision, just re-judge next round
                        logger.info("no prior feedback available, retrying judge next round")
                        continue
                    if revised != current_output:
                        current_output = revised
                continue

            # Successful judge evaluation
            consecutive_failures = 0
            last_good_result = round_result

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
                    judge_failures=judge_failures,
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
            judge_failures=judge_failures,
        )
