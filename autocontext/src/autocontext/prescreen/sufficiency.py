"""Phase 0 sufficiency report (design 'Phases and gates'): eligible data per (judge identity, scenario family)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.prescreen.rounds import LedgerRound, eligible_rounds

PHASE1_MIN_ELIGIBLE = 300
PHASE1_MIN_VALUE_CEILING = 0.15


@dataclass(frozen=True)
class SufficiencyRow:
    judge_identity: str | None
    scenario_family: str
    epochs: int
    tasks: int
    loops: int
    judged_rounds: int
    parse_failures: int
    pass_rate: float | None
    eligible_rounds: int
    eligible_failing_rounds: int
    value_ceiling: float
    meets_phase1: bool


def sufficiency_report(rounds: Sequence[LedgerRound]) -> list[SufficiencyRow]:
    """One row per (judge identity, family), the certification unit.

    Eligibility is decided on whole loops, then counted under each round's own judge identity. Rows with no identity
    (no serving spec) form their own cell, which is reported but never eligible. `epochs` counts the distinct evaluator
    epochs pooled in the cell (for information), and `tasks` the distinct task groups (scenario name, else loop id).
    """
    judged: dict[tuple[str | None, str], list[LedgerRound]] = defaultdict(list)
    for r in rounds:
        judged[(r.judge_identity, r.scenario_family)].append(r)
    eligible: dict[tuple[str | None, str], list[LedgerRound]] = defaultdict(list)
    for e in eligible_rounds(rounds):
        eligible[(e.round.judge_identity, e.round.scenario_family)].append(e.round)
    rows: list[SufficiencyRow] = []
    for key in sorted(judged, key=lambda k: (k[1], k[0] or "")):
        group = judged[key]
        parsed = [r for r in group if not r.judge_failed]
        failing = sum(not r.passed for r in eligible[key])
        ceiling = failing / len(group)
        rows.append(
            SufficiencyRow(
                judge_identity=key[0],
                scenario_family=key[1],
                epochs=len({r.evaluator_epoch for r in group if r.evaluator_epoch is not None}),
                tasks=len({r.scenario_name or r.loop_id for r in group}),
                loops=len({r.loop_id for r in group}),
                judged_rounds=len(group),
                parse_failures=len(group) - len(parsed),
                pass_rate=sum(r.passed for r in parsed) / len(parsed) if parsed else None,
                eligible_rounds=len(eligible[key]),
                eligible_failing_rounds=failing,
                value_ceiling=ceiling,
                meets_phase1=len(eligible[key]) >= PHASE1_MIN_ELIGIBLE and ceiling >= PHASE1_MIN_VALUE_CEILING,
            )
        )
    return rows


def write_json_report(out_dir: Path, stem: str, payload: dict[str, Any]) -> Path:
    """Write an aggregate-only report as <stem>-<UTC timestamp>.json and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path
