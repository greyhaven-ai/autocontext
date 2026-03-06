"""Multi-step improvement loop for agent tasks.

Orchestrates: generate -> judge -> revise -> judge -> ... -> done.
Stops when quality_threshold is met or max_rounds is exhausted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from mts.execution.output_cleaner import clean_revision_output
from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult

logger = logging.getLogger(__name__)

TerminationReason = Literal[
    "threshold_met", "max_rounds", "plateau_stall", "unchanged_output", "consecutive_failures",
]

PLATEAU_EPSILON = 0.01
PLATEAU_PATIENCE = 2
NEAR_THRESHOLD_MARGIN = 0.02

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
    termination_reason: TerminationReason = "max_rounds"
    dimension_trajectory: dict[str, list[float]] = field(default_factory=dict)
    total_internal_retries: int = 0

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
        min_rounds: int = 1,
        max_score_delta: float = 0.5,
        cap_score_jumps: bool = False,
    ) -> None:
        self.task = task
        self.max_rounds = max(1, max_rounds)
        self.quality_threshold = quality_threshold
        self.min_rounds = max(1, min_rounds)
        self.max_score_delta = max_score_delta
        self.cap_score_jumps = cap_score_jumps

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
        total_internal_retries = 0
        last_good_result = None  # Carry forward for revision on judge failure
        consecutive_failures = 0
        max_consecutive_failures = 3  # Safety valve
        termination_reason: TerminationReason = "max_rounds"
        dimension_trajectory: dict[str, list[float]] = {}
        threshold_met_round: int | None = None

        # Plateau detection state
        prev_valid_score: float | None = None
        plateau_count = 0

        for round_num in range(1, self.max_rounds + 1):
            logger.info("improvement loop round %d/%d", round_num, self.max_rounds)

            result = self.task.evaluate_output(
                current_output,
                state,
                reference_context=reference_context,
                required_concepts=required_concepts,
                calibration_examples=calibration_examples,
            )
            total_internal_retries += result.internal_retries

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
                threshold_met_round = None  # Reset stability tracking on parse failure
                logger.warning(
                    "round %d: judge parse failure (%s), not counting toward score",
                    round_num, result.reasoning[:80],
                )
                if consecutive_failures >= max_consecutive_failures:
                    logger.error(
                        "aborting: %d consecutive judge failures", consecutive_failures,
                    )
                    termination_reason = "consecutive_failures"
                    break
                # Use last good feedback for revision if available
                if round_num < self.max_rounds:
                    if last_good_result is not None:
                        logger.info("using feedback from round %d for revision", last_good_result.round_number)
                        revised = self.task.revise_output(
                            current_output,
                            AgentTaskResult(
                                score=last_good_result.score,
                                reasoning=last_good_result.reasoning,
                                dimension_scores=last_good_result.dimension_scores,
                            ),
                            state,
                        )
                        revised = clean_revision_output(revised)
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

            # Build dimension trajectory from valid rounds
            for dim, dim_score in result.dimension_scores.items():
                if dim not in dimension_trajectory:
                    dimension_trajectory[dim] = []
                dimension_trajectory[dim].append(dim_score)

            effective_score = result.score

            # Max score delta warning + optional cap
            if prev_valid_score is not None:
                delta = abs(result.score - prev_valid_score)
                if delta > self.max_score_delta:
                    logger.warning(
                        "Score jump of %.3f exceeds max_score_delta %.3f "
                        "(round %d: %.3f -> %.3f)",
                        delta, self.max_score_delta,
                        round_num, prev_valid_score, result.score,
                    )
                    if self.cap_score_jumps:
                        effective_score = max(0.0, (
                            prev_valid_score + self.max_score_delta
                            if result.score > prev_valid_score
                            else prev_valid_score - self.max_score_delta
                        ))

            # Reference verification hook — apply score penalty if facts unverified
            if effective_score > 0:
                verify_result = self.task.verify_facts(current_output, state)
                if verify_result is not None and not verify_result.get("verified", True):
                    issues = verify_result.get("issues", [])
                    if issues:
                        annotation = " | Fact-check issues: " + "; ".join(issues)
                        round_result.reasoning += annotation
                    effective_score = max(0.0, effective_score * 0.9)
                    round_result.score = effective_score

            if effective_score > best_score:
                best_score = effective_score
                best_output = current_output
                best_round = round_num

            logger.info(
                "round %d score: %.2f (best: %.2f at round %d)",
                round_num, effective_score, best_score, best_round,
            )

            # Plateau detection (only after min_rounds satisfied)
            if prev_valid_score is not None and abs(result.score - prev_valid_score) < PLATEAU_EPSILON:
                plateau_count += 1
                if plateau_count >= PLATEAU_PATIENCE and round_num >= self.min_rounds:
                    termination_reason = "plateau_stall"
                    break
            else:
                plateau_count = 0
            prev_valid_score = result.score

            if effective_score >= self.quality_threshold and round_num >= self.min_rounds:
                near_threshold = effective_score < self.quality_threshold + NEAR_THRESHOLD_MARGIN

                if threshold_met_round is not None:
                    # Threshold was met on a previous round too — confirmed stable
                    logger.info("quality threshold %.2f confirmed stable at round %d", self.quality_threshold, round_num)
                    return ImprovementResult(
                        rounds=rounds,
                        best_output=best_output,
                        best_score=best_score,
                        best_round=best_round,
                        total_rounds=round_num,
                        met_threshold=True,
                        judge_failures=judge_failures,
                        termination_reason="threshold_met",
                        dimension_trajectory=dimension_trajectory,
                        total_internal_retries=total_internal_retries,
                    )

                if near_threshold and round_num < self.max_rounds:
                    # Score barely meets threshold — continue to confirm stability
                    logger.info(
                        "score %.3f barely meets threshold %.2f at round %d, continuing to confirm",
                        effective_score, self.quality_threshold, round_num,
                    )
                    threshold_met_round = round_num
                else:
                    # Clearly above threshold — stop immediately
                    logger.info("quality threshold %.2f met at round %d", self.quality_threshold, round_num)
                    return ImprovementResult(
                        rounds=rounds,
                        best_output=best_output,
                        best_score=best_score,
                        best_round=best_round,
                        total_rounds=round_num,
                        met_threshold=True,
                        judge_failures=judge_failures,
                        termination_reason="threshold_met",
                        dimension_trajectory=dimension_trajectory,
                        total_internal_retries=total_internal_retries,
                    )
            else:
                # Score dropped below threshold after previously meeting it
                threshold_met_round = None

            if round_num < self.max_rounds:
                revised = self.task.revise_output(current_output, result, state)
                revised = clean_revision_output(revised)
                if revised == current_output:
                    logger.info("revise_output returned unchanged output, stopping")
                    termination_reason = "unchanged_output"
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
            termination_reason=termination_reason,
            dimension_trajectory=dimension_trajectory,
            total_internal_retries=total_internal_retries,
        )
