"""curate's eligibility guardrails: quarantine, asymmetric trainability, selector match.

These are the mechanical enforcement points for two charter guardrails
(spec: docs/internal/ambient-trainer-design.md, "Stage: Curate"): provenance
quarantine (fine-tune-produced records never feed the next lineage's
training set; v1 quarantines all non-frontier provenance) and asymmetric
trainability (datasets for evaluative roles need externally anchored
labels, which v1 does not have, so evaluative-role targets are refused).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autocontext.ambient.charter import CharterTarget
from autocontext.ambient.trace_store import TraceRecord

EVALUATIVE_ROLES = frozenset({"judge", "curator", "coach"})


@dataclass(slots=True)
class EligibilityDecision:
    eligible: bool
    reason: str


def split_role_selector(selector: str) -> tuple[str, str | None]:
    """Split a role selector in the role@scenario binding notation.

    A bare role ("competitor") selects that role in every scenario; a
    scoped binding ("competitor@grid_ctf") selects the role only in the
    named scenario. This is the same notation the interview wizard and the
    registry role bindings use, so selectors written in either form work.
    """
    role, _, scenario = selector.partition("@")
    return role, scenario or None


def is_evaluative_target(target: CharterTarget) -> bool:
    if target.kind != "role":
        return False
    role, _ = split_role_selector(target.selector)
    return role in EVALUATIVE_ROLES


def assess(trace: TraceRecord, target: CharterTarget) -> EligibilityDecision:
    if trace.produced_by != "frontier":
        return EligibilityDecision(eligible=False, reason="quarantined_provenance")
    if trace.kind != "agent_output":
        return EligibilityDecision(eligible=False, reason="wrong_kind")
    if trace.payload.get("status") != "completed":
        return EligibilityDecision(eligible=False, reason="not_completed")
    if target.kind == "role":
        role, scenario = split_role_selector(target.selector)
        if trace.payload.get("role") != role:
            return EligibilityDecision(eligible=False, reason="selector_mismatch")
        if scenario is not None and trace.payload.get("scenario") != scenario:
            return EligibilityDecision(eligible=False, reason="selector_mismatch")
    if target.kind == "task_family":
        if trace.payload.get("scenario") != target.selector:
            return EligibilityDecision(eligible=False, reason="selector_mismatch")
        # a task-family dataset is a generator dataset, and only competitor
        # output is the generator signal; other roles' text would sidestep the
        # evaluative-role refusal (which only guards kind="role" targets) and
        # desynchronize dataset stats from the advisor's competitor-only rationale
        if trace.payload.get("role") != "competitor":
            return EligibilityDecision(eligible=False, reason="non_competitor_role")
    if not trace.payload.get("content"):
        return EligibilityDecision(eligible=False, reason="missing_content")
    return EligibilityDecision(eligible=True, reason="ok")


def to_training_record(trace: TraceRecord) -> dict[str, Any]:
    """Shape one eligible trace as an autoresearch training record.

    Matches the JSONL schema consumed by training/autoresearch/prepare.py
    (run_id, scenario, strategy, score, context) so plan 4's train stage can
    feed these files to the existing curate_records + backend pipeline
    unchanged.
    """
    payload = trace.payload
    best_score = payload.get("best_score")
    score = float(best_score) if best_score is not None else 0.0
    return {
        "run_id": payload.get("run_id", ""),
        "scenario": payload.get("scenario", ""),
        "strategy": payload.get("content", ""),
        "score": score,
        "context": {
            "generation_index": payload.get("generation_index"),
            "role": payload.get("role"),
            "gate_decision": payload.get("gate_decision"),
            "trace_record_id": trace.record_id,
            "produced_by": trace.produced_by,
        },
    }
