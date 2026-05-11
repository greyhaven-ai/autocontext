"""Per-round event value objects emitted by `ImprovementLoop` (AC-752).

These exist so callers (CLI `--ndjson`, dashboards, structured logs) can
stream progress from long-running improvement loops without waiting for
the final result blob.

Design: a single frozen-slots dataclass with all-optional event-specific
fields tagged by an `event` discriminator. Easy to construct, easy to
JSON-serialize via `dataclasses.asdict`, and adding new event kinds is
non-breaking (existing consumers only inspect fields they care about).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImprovementLoopEvent:
    """A single event emitted by `ImprovementLoop.run`.

    The `event` field is the discriminator. Other fields are optional and
    only meaningful for certain event kinds:

    - `round_start`: round
    - `judge_done`: round, score
    - `verifier_done`: round, verifier_ok, verifier_exit_code
    - `round_summary`: round, effective_score
    - `final`: best_score, best_round, total_rounds, met_threshold
    """

    event: str
    round: int | None = None
    score: float | None = None
    effective_score: float | None = None
    verifier_ok: bool | None = None
    verifier_exit_code: int | None = None
    best_score: float | None = None
    best_round: int | None = None
    total_rounds: int | None = None
    met_threshold: bool | None = None
