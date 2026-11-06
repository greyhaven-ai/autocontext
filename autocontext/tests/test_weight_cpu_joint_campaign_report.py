"""Synthetic verified projections test reporting; these are not worker evidence."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_joint_campaign import _plan
from autocontext.weight_evolution.cpu_joint_campaign_bundle import (
    build_cpu_joint_campaign_bundle,
    joint_campaign_schedule,
    load_cpu_joint_campaign_bundle,
)
from autocontext.weight_evolution.cpu_joint_campaign_report import _expectations, build_cpu_joint_campaign_report
from autocontext.weight_evolution.cpu_joint_control_tasks import joint_control_schedule
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec

_CONDITIONS = ("A", "C", "W", "CW", "D")


def retained_score(oracle: dict[str, Any]) -> dict[str, Any]:
    return {"accuracy": oracle["accuracy"], "cases": [{"case_id": case["case_id"], "output": case["prediction"],
        "correct": case["correct"], "result_digest": canonical_digest([case["case_id"], case["prediction"]])}
        for case in oracle["cases"]]}


def payload(role: str, scores: dict[str, Any], child: CpuSearchSpec, selection: str, runtime: str) -> dict[str, Any]:
    return {"schema_version": 1, "execution_kind": "cpu_search_inference", "model_execution_performed": True,
        "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
        "role": role, "study_digest": child.digest, "runtime_digest": runtime, "selection_digest": selection,
        "scorer": "exact-label-v1", "scorer_digest": "d" * 64, "policy": child.policy.to_dict(),
        "policy_digest": child.policy.digest, "conditions": scores,
        "contrasts": {f"{a}-{b}": scores[a]["accuracy"] - scores[b]["accuracy"]
                      for a, b in (("C", "A"), ("W", "A"), ("CW", "C"), ("CW", "W"), ("D", "A"))},
        "receipt_digests": {condition: canonical_digest([child.digest, role, condition]) for condition in _CONDITIONS}}


@pytest.fixture(scope="module")
def full_context(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, ...]:
    bundle = tmp_path_factory.mktemp("joint-campaign-report") / "bundle"
    build_cpu_joint_campaign_bundle(bundle)
    spec, control, templates, _ = load_cpu_joint_campaign_bundle(bundle)
    schedule = joint_campaign_schedule(control, templates)
    children = {row["run_id"]: child for row, child in joint_control_schedule(control, templates)}
    expected = _expectations(control, templates)
    prefix: list[dict[str, Any]] = []
    ref_scores = {}
    for row in schedule[:18]:
        child, oracle = children[row["run_id"]], expected[row["run_id"]]
        selection = {"synthetic_selection": row["run_id"]}
        reports = {role: payload(role, {key: retained_score(value) for key, value in scores.items()}, child,
                   canonical_digest(selection), spec.native_runtime_digest)
                   for role, scores in oracle["scores"].items()}
        summary = {"selection_digest": canonical_digest(selection), "semantic_digest": canonical_digest(row),
                   "selected_options": oracle["selected_options"], "audit_status": oracle["audit_status"],
                   "confirmation": {**oracle["confirmation"], "report_digest": canonical_digest(reports["confirmation"])}}
        observations = []
        if oracle["audit_status"] == "completed":
            observations = [{"kind": "inference", "phase": "audit", "outcome_present": True,
                "process_observation": {"dispatched": True, "leader_reaped": True,
                                        "total_seconds": 0.25, "peak_rss_bytes": None}} for _ in _CONDITIONS]
        else:
            reports["audit"] = None
        prefix.append({"row": row, "claim": {"claimed_capacity": row["reserved_capacity"]},
            "result": {"verified_usage": row["reserved_capacity"]}, "published": True, "outcome_published": True,
            "outcome": {"status": "completed", "summary": summary,
                        "observation_coverage": observations, "parent_error_type": None},
            "details": {"selection": selection, **reports}, "reference": None})
        ref_scores[f"{row['task_id']}.{row['repeat_id']}"] = {
            key: retained_score(value) for key, value in oracle["reference_scores"].items()}
    plan = _plan(spec, control, templates, prefix)
    assert plan is not None
    for row, reference in zip(schedule[18:], plan[0].references, strict=True):
        prefix.append({"row": row, "claim": {"claimed_capacity": row["reserved_capacity"]},
            "result": {"verified_usage": row["reserved_capacity"]}, "published": True, "outcome_published": True,
            "outcome": {"status": "completed", "summary": {"scores": ref_scores[row["run_id"]]},
                        "observation_coverage": [], "parent_error_type": None},
            "details": None, "reference": reference.to_dict()})
    return spec, control, templates, schedule, prefix


def report(context: tuple[Any, ...], prefix: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return build_cpu_joint_campaign_report(*context[:4], context[4] if prefix is None else prefix)


def test_full_report_requires_all_342_calibrations_and_real_audit_routing(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    assert result["evidence_integrity_verified"] and result["campaign_completed"] and result["acceptance_passed"]
    assert result["status_counts"]["search"]["completed"] == 18
    assert result["status_counts"]["reference"]["completed"] == 6
    assert result["calibration"]["matches"] == result["calibration"]["scheduled_checks"] == 342
    assert result["calibration"]["mismatches"] == result["calibration"]["unavailable_or_nondesignated"] == 0
    assert result["audit_routing"]["verified"] == 18
    assert result["audit_routing"]["positive_count"] == 6 and result["audit_routing"]["veto_count"] == 12
    for family, value in result["family_results"].items():
        assert value["task_count"] == 1
        assert value["audit_coverage"]["available"] == (6 if family == "joint-positive-v1" else 0)
        assert all(row["mean"] == 0 for row in value["confirmation_contrasts"].values())
    assert not result["family_pooling_performed"] and not result["promotion_eligible"]
    assert result["gpu_execution_deferred"] and not result["benchmark_result_claimed"]


@pytest.mark.parametrize("status", ["failed", "uncertain"])
def test_partial_failure_keeps_reservations_and_missing_calibrations(full_context: tuple[Any, ...], status: str) -> None:
    prefix = copy.deepcopy(full_context[4])
    prefix[0]["outcome"].update(status=status, summary=None)
    prefix[0]["result"]["verified_usage"] = None
    prefix[0]["details"] = None
    for entry in prefix[18:]:
        entry["outcome"].update(status="blocked", summary=None)
        entry["claim"]["claimed_capacity"] = dict.fromkeys(entry["row"]["reserved_capacity"], 0)
        entry["result"]["verified_usage"] = None
        entry["reference"] = None
    result = report(full_context, prefix)
    assert result["evidence_integrity_verified"] and not result["campaign_completed"] and not result["acceptance_passed"]
    assert result["status_counts"]["reference"]["blocked"] == 6
    assert result["unknown_usage_rows"] == {"search": 1, "reference": 0}
    assert all(value == 0 for value in result["charged_capacity"]["reference"].values())
    assert result["charged_capacity"]["search"]["proposals"] == full_context[0].search_budget.max_proposals
    assert result["calibration"]["unavailable_or_nondesignated"] == 18 + 6 * 13
    assert result["audit_routing"]["unavailable"] == 1
    assert result["practice_gap_rows"][0]["mechanisms"]["W"]["selected_gap_to_best_practice"] is None


def test_unstarted_and_pending_publication_cannot_pass(full_context: tuple[Any, ...]) -> None:
    empty = report(full_context, [])
    assert empty["status_counts"]["search"]["not-started"] == 18
    assert empty["status_counts"]["reference"]["not-started"] == 6
    assert empty["calibration"]["unavailable_or_nondesignated"] == 342
    assert empty["audit_routing"]["unavailable"] == 18
    prefix = copy.deepcopy(full_context[4])
    prefix[-1]["published"] = False
    result = report(full_context, prefix)
    assert not result["campaign_completed"] and not result["acceptance_passed"]
    assert result["pending_results"] == 1


@pytest.mark.parametrize("change", ["missing-observation", "not-dispatched", "not-reaped", "veto-audit-claim"])
def test_synthetic_audit_success_without_worker_evidence_cannot_pass(full_context: tuple[Any, ...], change: str) -> None:
    prefix = copy.deepcopy(full_context[4])
    observation = prefix[0]["outcome"]["observation_coverage"][0]
    if change == "missing-observation":
        prefix[0]["outcome"]["observation_coverage"].pop()
    elif change == "not-dispatched":
        observation["process_observation"]["dispatched"] = False
    elif change == "not-reaped":
        observation["process_observation"]["leader_reaped"] = False
    else:
        prefix[6]["outcome"]["observation_coverage"].append(observation)
    result = report(full_context, prefix)
    assert result["campaign_completed"] and result["calibration"]["matches"] == 342
    assert not result["acceptance_passed"] and result["audit_routing"]["mismatches"] == 1


def test_calibration_mismatch_is_retained_without_reselecting(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    prefix[0]["outcome"]["summary"]["confirmation"]["outcomes"]["C"] = False
    result = report(full_context, prefix)
    assert result["campaign_completed"] and not result["acceptance_passed"]
    assert result["calibration"]["mismatches"] == 1 and len(result["rows"]) == 24


@pytest.mark.parametrize("change", ["schedule", "selection", "role", "audit-role", "case-count", "reference-missing-option"])
def test_drift_or_incomplete_projections_fail_closed(full_context: tuple[Any, ...], change: str) -> None:
    context = copy.deepcopy(full_context)
    prefix = context[4]
    if change == "schedule":
        context[3][0]["family"] = "joint-correct-baseline-v1"
    elif change == "selection":
        prefix[0]["details"]["selection"]["extra"] = True
    elif change == "role":
        prefix[0]["details"]["practice"]["role"] = "confirmation"
    elif change == "audit-role":
        prefix[0]["details"]["audit"]["role"] = "confirmation"
    elif change == "case-count":
        prefix[0]["details"]["practice"]["conditions"]["A"]["cases"].pop()
    else:
        prefix[18]["outcome"]["summary"]["scores"].pop("A")
    with pytest.raises(ValueError):
        report(context)


def test_oracle_requires_ordered_predictions_not_only_retained_accuracy(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    # Audit has no exhaustive reference. Preserve the reported count/accuracy
    # while swapping two distinct predictions, testing the independent oracle
    # check rather than selected-vs-reference output consistency.
    audit = prefix[0]["details"]["audit"]["conditions"]["C"]
    cases = audit["cases"]
    left, right = next((a, b) for a in cases for b in cases if a["output"] != b["output"])
    original_accuracy = audit["accuracy"]
    original_correct = sum(case["correct"] for case in cases)
    left["output"], right["output"] = right["output"], left["output"]
    result = report(full_context, prefix)
    assert audit["accuracy"] == original_accuracy and sum(case["correct"] for case in cases) == original_correct
    assert result["campaign_completed"] and not result["acceptance_passed"]
    assert result["calibration"]["scheduled_checks"] == 342 and result["calibration"]["mismatches"] == 1
    mismatch = next(check for check in result["calibration"]["checks"] if check["matches"] is False)
    assert mismatch["check"] == "audit:C"
    assert mismatch["expected"]["accuracy"] == mismatch["observed"]["accuracy"]
    assert mismatch["expected"]["cases"] != mismatch["observed"]["cases"]
