"""tests for curate's eligibility guardrails."""

from __future__ import annotations

from typing import Any

from autocontext.ambient.charter import CharterTarget
from autocontext.ambient.eligibility import (
    EVALUATIVE_ROLES,
    assess,
    is_evaluative_target,
    to_training_record,
)
from autocontext.ambient.trace_store import TraceRecord


def _target(**overrides: Any) -> CharterTarget:
    base: dict[str, Any] = {
        "name": "competitor-local",
        "kind": "role",
        "selector": "competitor",
        "base_model": "qwen2.5-3b",
        "min_dataset_records": 10,
        "eval_suite": "anchor-v1",
    }
    base.update(overrides)
    return CharterTarget(**base)


def _trace(**payload_overrides: Any) -> TraceRecord:
    payload: dict[str, Any] = {
        "run_id": "run_a",
        "scenario": "grid_ctf",
        "generation_index": 3,
        "role": "competitor",
        "content": "the strategy",
        "status": "completed",
        "mean_score": 0.5,
        "best_score": 0.9,
        "gate_decision": "advance",
        "created_at": "t",
    }
    produced_by = payload_overrides.pop("produced_by", "frontier")
    kind = payload_overrides.pop("kind", "agent_output")
    payload.update(payload_overrides)
    return TraceRecord(7, "autocontext-outputs:native", kind, payload, produced_by, 0, "t")


def test_frontier_completed_matching_trace_is_eligible() -> None:
    decision = assess(_trace(), _target())
    assert decision.eligible is True
    assert decision.reason == "ok"


def test_finetune_provenance_is_quarantined() -> None:
    decision = assess(_trace(produced_by="finetune:lineage-3"), _target())
    assert decision.eligible is False
    assert decision.reason == "quarantined_provenance"


def test_non_output_kind_is_rejected() -> None:
    decision = assess(_trace(kind="generation"), _target())
    assert decision.reason == "wrong_kind"


def test_failed_generation_is_rejected() -> None:
    decision = assess(_trace(status="failed"), _target())
    assert decision.reason == "not_completed"


def test_role_selector_mismatch_is_rejected() -> None:
    decision = assess(_trace(role="analyst"), _target())
    assert decision.reason == "selector_mismatch"


def test_task_family_selector_matches_scenario() -> None:
    target = _target(kind="task_family", selector="grid_ctf")
    assert assess(_trace(), target).eligible is True
    assert assess(_trace(scenario="othello"), target).reason == "selector_mismatch"


def test_task_family_non_competitor_role_is_rejected() -> None:
    # a task-family dataset is a generator dataset: only competitor output is
    # the generator signal, so a matching-scenario analyst trace is refused
    target = _target(kind="task_family", selector="grid_ctf")
    decision = assess(_trace(role="analyst"), target)
    assert decision.eligible is False
    assert decision.reason == "non_competitor_role"


def test_task_family_coach_role_is_rejected() -> None:
    # coach text would otherwise sidestep the evaluative-role refusal, which
    # only guards kind="role" targets
    target = _target(kind="task_family", selector="grid_ctf")
    decision = assess(_trace(role="coach"), target)
    assert decision.eligible is False
    assert decision.reason == "non_competitor_role"


def test_task_family_competitor_role_is_eligible() -> None:
    target = _target(kind="task_family", selector="grid_ctf")
    assert assess(_trace(role="competitor"), target).eligible is True


def test_role_target_still_accepts_its_own_role() -> None:
    # the competitor-only rule is task-family-scoped: a role-kind target keeps
    # training on its own role's traces
    target = _target(kind="role", selector="analyst")
    assert assess(_trace(role="analyst"), target).eligible is True


def test_scoped_role_selector_accepts_matching_role_and_scenario() -> None:
    target = _target(name="competitor-grid_ctf", selector="competitor@grid_ctf")
    assert assess(_trace(), target).eligible is True


def test_scoped_role_selector_rejects_wrong_scenario() -> None:
    target = _target(name="competitor-grid_ctf", selector="competitor@grid_ctf")
    decision = assess(_trace(scenario="othello"), target)
    assert decision.eligible is False
    assert decision.reason == "selector_mismatch"


def test_scoped_role_selector_rejects_wrong_role() -> None:
    target = _target(name="competitor-grid_ctf", selector="competitor@grid_ctf")
    decision = assess(_trace(role="analyst"), target)
    assert decision.eligible is False
    assert decision.reason == "selector_mismatch"


def test_scoped_evaluative_role_is_flagged() -> None:
    # the role part decides: a curator@grid_ctf target is evaluative and must
    # be refused just like a bare curator target
    assert is_evaluative_target(_target(name="curator-grid_ctf", selector="curator@grid_ctf")) is True


def test_empty_content_is_rejected() -> None:
    decision = assess(_trace(content=""), _target())
    assert decision.reason == "missing_content"


def test_evaluative_role_targets_are_flagged() -> None:
    assert EVALUATIVE_ROLES == frozenset({"judge", "curator", "coach"})
    assert is_evaluative_target(_target(selector="curator")) is True
    assert is_evaluative_target(_target(selector="competitor")) is False
    # task-family targets are generator datasets regardless of name
    assert is_evaluative_target(_target(kind="task_family", selector="curator")) is False


def test_training_record_matches_autoresearch_schema() -> None:
    record = to_training_record(_trace())
    assert record["run_id"] == "run_a"
    assert record["scenario"] == "grid_ctf"
    assert record["strategy"] == "the strategy"
    assert record["score"] == 0.9
    assert record["context"]["role"] == "competitor"
    assert record["context"]["gate_decision"] == "advance"
    assert record["context"]["trace_record_id"] == 7
    assert record["context"]["produced_by"] == "frontier"
