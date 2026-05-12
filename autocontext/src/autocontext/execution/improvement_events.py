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
    - `revision_done`: round, output -- carries the output content the loop
      is about to evaluate. For round 1 this is the seed; for round N>1 it
      is the result of task.revise_output() at the end of round N-1.
      Lets consumers salvage near-miss outputs from verifier-vetoed rounds
      (AC-753).
    - `judge_done`: round, score
    - `verifier_done`: round, verifier_ok, verifier_exit_code
    - `round_summary`: round, effective_score
    - `final`: best_score, best_round, total_rounds, met_threshold
    """

    # NB: field order is part of the public contract for positional construction.
    # Existing callers may construct events positionally as
    # `ImprovementLoopEvent("judge_done", 1, 0.95)`; new fields go at the END so
    # they don't shift the meaning of trailing positional arguments. AC-753 added
    # `output` and intentionally appends it after the older fields.
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
    output: str | None = None
