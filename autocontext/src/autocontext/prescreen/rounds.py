"""Ledger rows as typed rounds, and the design's skip-eligibility rule (Decision 3) applied to recorded loops."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LedgerRound:
    row_id: int
    loop_id: str
    run_id: str | None
    scenario_name: str
    scenario_family: str
    round_number: int
    max_rounds: int
    quality_threshold: float
    evaluator_epoch: str | None
    task_prompt: str
    output: str
    required_concepts: tuple[str, ...]
    score: float
    passed: bool
    judge_failed: bool
    dimension_scores: dict[str, float]
    previous_round_number: int | None
    previous_score: float | None
    previous_dimension_scores: dict[str, float]
    created_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> LedgerRound:
        return cls(
            row_id=int(row["id"]),
            loop_id=str(row["loop_id"]),
            run_id=row["run_id"],
            scenario_name=str(row["scenario_name"]),
            scenario_family=str(row["scenario_family"]),
            round_number=int(row["round_number"]),
            max_rounds=int(row["max_rounds"]),
            quality_threshold=float(row["quality_threshold"]),
            evaluator_epoch=row["evaluator_epoch"],
            task_prompt=str(row["task_prompt"]),
            output=str(row["output"]),
            required_concepts=tuple(json.loads(row["required_concepts_json"] or "[]")),
            score=float(row["score"]),
            passed=bool(row["passed"]),
            judge_failed=bool(row["judge_failed"]),
            dimension_scores=json.loads(row["dimension_scores_json"] or "{}"),
            previous_round_number=row["previous_round_number"],
            previous_score=row["previous_score"],
            previous_dimension_scores=json.loads(row["previous_dimension_scores_json"] or "{}"),
            created_at=str(row["created_at"]),
        )


@dataclass(frozen=True)
class EligibleRound:
    round: LedgerRound
    previous: LedgerRound


def eligible_rounds(rounds: Iterable[LedgerRound]) -> list[EligibleRound]:
    """Rounds the pre-screen could have skipped (design Decision 3), in row order.

    Eligible means: round 2 or later; the round just before it in the same loop has a row, parsed and did not pass
    (a round after a pass is a confirmation); it is not the last allowed round; its own verdict parsed; and its
    evaluator epoch is known. Loops are judged whole, so an epoch change inside a loop does not hide a round.
    """
    ordered = sorted(rounds, key=lambda r: r.row_id)
    by_loop: dict[str, dict[int, LedgerRound]] = defaultdict(dict)
    for r in ordered:
        by_loop[r.loop_id][r.round_number] = r
    eligible: list[EligibleRound] = []
    for r in ordered:
        previous = by_loop[r.loop_id].get(r.round_number - 1)
        if (
            r.round_number >= 2
            and previous is not None
            and not previous.judge_failed
            and not previous.passed
            and r.round_number < r.max_rounds
            and not r.judge_failed
            and r.evaluator_epoch is not None
        ):
            eligible.append(EligibleRound(r, previous))
    return eligible
