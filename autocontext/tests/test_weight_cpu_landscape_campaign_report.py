"""Synthetic verified projections test reporting; these are not worker evidence."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_landscape_campaign import _plan
from autocontext.weight_evolution.cpu_landscape_campaign_bundle import (
    build_cpu_landscape_campaign_bundle,
    landscape_campaign_schedule,
    load_cpu_landscape_campaign_bundle,
)
from autocontext.weight_evolution.cpu_landscape_campaign_report import _expectations, build_cpu_landscape_campaign_report
from autocontext.weight_evolution.cpu_landscape_control_tasks import landscape_control_schedule
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec

_CONDITIONS = ("A", "C", "W", "CW", "D")


def observations(phases: dict[str, int]) -> list[dict[str, Any]]:
    return [{"kind": "inference", "phase": phase, "outcome_present": True,
             "process_observation": {"dispatched": True, "leader_reaped": True,
                                     "total_seconds": 0.25, "peak_rss_bytes": None}}
            for phase, count in phases.items() for _ in range(count)]


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
    bundle = tmp_path_factory.mktemp("landscape-campaign-report") / "bundle"
    build_cpu_landscape_campaign_bundle(bundle)
    spec, control, templates, _ = load_cpu_landscape_campaign_bundle(bundle)
    schedule = landscape_campaign_schedule(control, templates)
    children = {row["run_id"]: child for row, child in landscape_control_schedule(control, templates)}
    expected = _expectations(control, templates)
    prefix: list[dict[str, Any]] = []
    ref_scores = {}
    for row in schedule[:36]:
        child, oracle = children[row["run_id"]], expected[row["run_id"]]
        selection = {"synthetic_selection": row["run_id"]}
        reports = {role: payload(role, {key: retained_score(value) for key, value in scores.items()}, child,
                   canonical_digest(selection), spec.native_runtime_digest)
                   for role, scores in oracle["scores"].items()}
        summary = {"selection_digest": canonical_digest(selection), "semantic_digest": canonical_digest(row),
                   "selected_options": oracle["selected_options"], "histories": copy.deepcopy(oracle["histories"]),
                   "audit_status": oracle["audit_status"],
                   "confirmation": {**oracle["confirmation"], "report_digest": canonical_digest(reports["confirmation"])}}
        process_rows = observations({"practice": 13, "restoration": 3, "confirmation": 5})
        assert oracle["audit_status"] == "withheld"
        reports["audit"] = None
        prefix.append({"row": row, "claim": {"claimed_capacity": row["reserved_capacity"]},
            "result": {"verified_usage": row["reserved_capacity"]}, "published": True, "outcome_published": True,
            "outcome": {"status": "completed", "summary": summary,
                        "observation_coverage": process_rows, "parent_error_type": None},
            "details": {"selection": selection, **reports}, "reference": None})
        ref_scores[f"{row['task_id']}.{row['repeat_id']}"] = {
            key: retained_score(value) for key, value in oracle["reference_scores"].items()}
    plan = _plan(spec, control, templates, prefix)
    assert plan is not None
    for row, reference in zip(schedule[36:], plan[0].references, strict=True):
        prefix.append({"row": row, "claim": {"claimed_capacity": row["reserved_capacity"]},
            "result": {"verified_usage": row["reserved_capacity"]}, "published": True, "outcome_published": True,
            "outcome": {"status": "completed", "summary": {"scores": ref_scores[row["run_id"]]},
                        "observation_coverage": observations({"reference": 19}), "parent_error_type": None},
            "details": None, "reference": reference.to_dict()})
    return spec, control, templates, schedule, prefix


def report(context: tuple[Any, ...], prefix: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return build_cpu_landscape_campaign_report(*context[:4], context[4] if prefix is None else prefix)


def test_full_report_requires_732_calibrations_and_all_three_landscape_behaviors(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    assert result["evidence_integrity_verified"] and result["campaign_completed"] and result["acceptance_passed"]
    assert result["status_counts"]["search"]["completed"] == 36
    assert result["status_counts"]["reference"]["completed"] == 12
    assert result["calibration"]["matches"] == result["calibration"]["scheduled_checks"] == 732
    assert result["calibration"]["mismatches"] == result["calibration"]["unavailable_or_nondesignated"] == 0
    assert result["audit_routing"]["verified"] == result["audit_routing"]["veto_count"] == 36
    assert result["audit_routing"]["positive_count"] == 0
    contrast = result["landscape_discrimination"]
    assert contrast["declared_design_valid"]
    assert contrast["scheduled_pairs"] == contrast["verified_pairs"] == 12
    assert contrast["mismatches"] == contrast["unavailable"] == 0
    assert contrast["expected_adaptive_wins"] == contrast["expected_adaptive_losses"] == contrast["expected_null_pairs"] == 4
    assert contrast["expected_nonzero_adaptive_or_fixed_regrets"] == 8
    assert not contrast["aggregate_adaptive_win_required"] and not contrast["references_used_for_selection"]
    means = {"informative-improvement-v1": 0.375, "misleading-improvement-v1": -0.375, "neutral-v1": 0.0}
    for family, value in result["family_results"].items():
        assert value["task_count"] == 2
        assert value["audit_coverage"]["available"] == 0 and value["audit_coverage"]["withheld_after_veto"] == 12
        assert value["confirmation_contrasts"]["adaptive-minus-fixed-order-v1:W"]["mean"] == means[family]
    assert not result["family_pooling_performed"] and not result["inferential_statistics_computed"]
    assert result["gpu_execution_deferred"] and not result["promotion_eligible"] and not result["benchmark_result_claimed"]


def test_practice_prefix_curves_retain_order_and_exclude_additional_execution(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    curves = result["practice_prefix_curves"]
    assert len(curves) == 108
    for row in curves:
        assert row["available"] and row["instance_index"] in {1, 2}
        assert not row["additional_inference_performed"]
        points = row["points"]
        assert [p["trial"] for p in points] == [0, 1, 2, 3]
        assert [p["proposals"] for p in points] == [0, 1, 2, 3]
        assert [p["practice_case_evaluations"] for p in points] == [8, 16, 24, 32]
        assert points[0]["option_id"] is points[0]["best_option_id"] is None
        assert [p["best_correct"] for p in points] == sorted(p["best_correct"] for p in points)
        gap = next(g for g in result["practice_gap_rows"] if g["run_id"] == row["run_id"])
        assert points[-1]["best_accuracy"] == gap["mechanisms"][row["mechanism"]]["practice_selected_accuracy"]
    neutral = [r for r in curves if r["family"] == "neutral-v1" and r["mechanism"] == "W"]
    assert all(all(p["best_option_id"] is None for p in r["points"]) for r in neutral)


def test_all_completed_rows_require_actual_processes_and_keep_all_five_cost_meters(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    processes = result["process_coverage"]
    assert processes["scheduled_rows"] == processes["verified_rows"] == 48
    assert processes["mismatches"] == processes["unavailable"] == 0
    assert processes["expected_inference_batches"] == processes["observed_inference_batches"] == 984
    assert processes["reaped_inference_batches"] == 984
    for row in processes["rows"]:
        wanted = ({"practice": 13, "restoration": 3, "confirmation": 5, "audit": 0, "reference": 0}
                  if row["stage"] == "search" else
                  {"practice": 0, "restoration": 0, "confirmation": 0, "audit": 0, "reference": 19})
        assert row["expected_phase_batches"] == row["observed_phase_batches"] == wanted
    meters = {"proposals", "materializations", "inference_calls", "input_tokens", "output_tokens"}
    for stage in ("search", "reference"):
        assert set(result["charged_capacity"][stage]) == set(result["completed_verified_usage"][stage]) == meters
    assert result["measurements"]["phases"]["audit"]["observed_inference_claims"] == 0


@pytest.mark.parametrize("status", ["failed", "uncertain"])
def test_partial_failure_retains_reservations_and_missing_landscape_results(full_context: tuple[Any, ...], status: str) -> None:
    prefix = copy.deepcopy(full_context[4])
    prefix[0]["outcome"].update(status=status, summary=None)
    prefix[0]["result"]["verified_usage"] = None
    prefix[0]["details"] = None
    for entry in prefix[36:]:
        entry["outcome"].update(status="blocked", summary=None, observation_coverage=[])
        entry["claim"]["claimed_capacity"] = dict.fromkeys(entry["row"]["reserved_capacity"], 0)
        entry["result"]["verified_usage"] = None
        entry["reference"] = None
    result = report(full_context, prefix)
    assert result["evidence_integrity_verified"] and not result["campaign_completed"] and not result["acceptance_passed"]
    assert result["status_counts"]["reference"]["blocked"] == 12
    assert result["unknown_usage_rows"] == {"search": 1, "reference": 0}
    assert all(value == 0 for value in result["charged_capacity"]["reference"].values())
    assert result["charged_capacity"]["search"]["proposals"] == full_context[0].search_budget.max_proposals
    assert result["calibration"]["unavailable_or_nondesignated"] == 14 + 12 * 19
    assert result["audit_routing"]["unavailable"] == 1 and result["process_coverage"]["unavailable"] == 13
    assert result["landscape_discrimination"]["unavailable"] == 12
    assert result["practice_gap_rows"][0]["mechanisms"]["W"]["selected_gap_to_best_practice"] is None
    assert all(row["points"] is None for row in result["practice_prefix_curves"][:3])


def test_unstarted_and_pending_publication_cannot_pass(full_context: tuple[Any, ...]) -> None:
    empty = report(full_context, [])
    assert empty["status_counts"]["search"]["not-started"] == 36
    assert empty["status_counts"]["reference"]["not-started"] == 12
    assert empty["calibration"]["unavailable_or_nondesignated"] == 732
    assert empty["audit_routing"]["unavailable"] == 36
    assert empty["process_coverage"]["unavailable"] == 48
    assert empty["landscape_discrimination"]["declared_design_valid"]
    assert empty["landscape_discrimination"]["verified_pairs"] == 0
    assert not empty["acceptance_passed"]
    prefix = copy.deepcopy(full_context[4])
    prefix[-1]["published"] = False
    result = report(full_context, prefix)
    assert not result["campaign_completed"] and not result["acceptance_passed"] and result["pending_results"] == 1


@pytest.mark.parametrize("index", [0, 36])
@pytest.mark.parametrize("change", ["missing", "not-dispatched", "not-reaped", "missing-outcome", "wrong-phase"])
def test_missing_process_evidence_cannot_pass_with_perfect_scores(full_context: tuple[Any, ...], index: int,
                                                                 change: str) -> None:
    prefix = copy.deepcopy(full_context[4])
    rows = prefix[index]["outcome"]["observation_coverage"]
    if change == "missing":
        rows.pop()
    elif change == "not-dispatched":
        rows[0]["process_observation"]["dispatched"] = False
    elif change == "not-reaped":
        rows[0]["process_observation"]["leader_reaped"] = False
    elif change == "missing-outcome":
        rows[0]["outcome_present"] = False
    else:
        rows[0]["phase"] = "unknown"
    result = report(full_context, prefix)
    assert result["campaign_completed"] and result["calibration"]["matches"] == 732
    assert result["landscape_discrimination"]["verified_pairs"] == 12
    assert not result["acceptance_passed"] and result["process_coverage"]["mismatches"] == 1


def test_veto_cannot_hide_an_audit_launch(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    prefix[0]["outcome"]["observation_coverage"].extend(observations({"audit": 1}))
    result = report(full_context, prefix)
    assert result["audit_routing"]["mismatches"] == result["process_coverage"]["mismatches"] == 1
    assert not result["acceptance_passed"]


def test_forged_nonwinning_history_cannot_hide_behind_correct_final_selection(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    summary = prefix[0]["outcome"]["summary"]
    original_selection = copy.deepcopy(summary["selected_options"])
    history = summary["histories"]["W"]
    history[0]["correct"] += 1
    result = report(full_context, prefix)
    assert summary["selected_options"] == original_selection
    assert result["calibration"]["mismatches"] == 1 and not result["acceptance_passed"]
    mismatch = next(c for c in result["calibration"]["checks"] if c["matches"] is False)
    assert mismatch["check"] == "practice-histories"


@pytest.mark.parametrize("change", ["missing", "short", "duplicate-option", "wrong-total", "wrong-selected"])
def test_malformed_or_inconsistent_histories_fail_closed(full_context: tuple[Any, ...], change: str) -> None:
    prefix = copy.deepcopy(full_context[4])
    summary = prefix[0]["outcome"]["summary"]
    history = summary["histories"]["W"]
    if change == "missing":
        summary.pop("histories")
    elif change == "short":
        history.pop()
    elif change == "duplicate-option":
        history[2]["option_id"] = history[1]["option_id"]
    elif change == "wrong-total":
        history[0]["total"] = 9
    else:
        summary["selected_options"]["W"] = None
    with pytest.raises(ValueError):
        report(full_context, prefix)


@pytest.mark.parametrize("change", ["schedule", "selection", "role", "case-count", "reference-missing-option"])
def test_drift_or_incomplete_projections_fail_closed(full_context: tuple[Any, ...], change: str) -> None:
    context = copy.deepcopy(full_context)
    prefix = context[4]
    if change == "schedule":
        context[3][0]["family"] = "neutral-v1"
    elif change == "selection":
        prefix[0]["details"]["selection"]["extra"] = True
    elif change == "role":
        prefix[0]["details"]["practice"]["role"] = "confirmation"
    elif change == "case-count":
        prefix[0]["details"]["practice"]["conditions"]["A"]["cases"].pop()
    else:
        prefix[36]["outcome"]["summary"]["scores"].pop("A")
    with pytest.raises(ValueError):
        report(context)


def test_oracle_compares_predictions_even_when_confirmation_accuracy_is_unchanged(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    entry = prefix[0]
    score = entry["details"]["confirmation"]["conditions"]["W"]
    left, right = next((a, b) for a in score["cases"] for b in score["cases"] if a["output"] != b["output"])
    original_accuracy = score["accuracy"]
    left["output"], right["output"] = right["output"], left["output"]
    entry["outcome"]["summary"]["confirmation"]["report_digest"] = canonical_digest(entry["details"]["confirmation"])
    result = report(full_context, prefix)
    assert result["campaign_completed"] and result["calibration"]["mismatches"] == 1
    assert result["landscape_discrimination"]["verified_pairs"] == 12 and not result["acceptance_passed"]
    mismatch = next(check for check in result["calibration"]["checks"] if check["matches"] is False)
    assert mismatch["check"] == "confirmation:W"
    assert mismatch["expected"]["accuracy"] == mismatch["observed"]["accuracy"] == original_accuracy


def test_report_never_dispatches_or_uses_reference_to_reselect(full_context: tuple[Any, ...], monkeypatch: Any) -> None:
    from autocontext.weight_evolution import cpu_execution_search, cpu_measured_runner

    def forbid(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("read-only report attempted model execution")

    monkeypatch.setattr(cpu_execution_search, "run_cpu_execution_search", forbid)
    monkeypatch.setattr(cpu_measured_runner, "run_measured_cpu_batch", forbid)
    before = copy.deepcopy(full_context[4])
    result = report(full_context)
    assert full_context[4] == before
    loss = next(row for row in result["landscape_discrimination"]["pairs"] if row["expected_sign"] == -1)
    assert loss["observed"]["practice_gaps"]["adaptive-v1"] > 0
    assert result["acceptance_passed"] and not result["landscape_discrimination"]["references_used_for_selection"]


def test_unselected_reference_outputs_are_calibrated_without_changing_selection(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    selected = {option for entry in prefix[:3]
                for option in entry["outcome"]["summary"]["selected_options"].values() if option is not None}
    scores = prefix[36]["outcome"]["summary"]["scores"]
    option = next(key for key, value in scores.items() if key not in selected | {"A"}
                  and len({case["output"] for case in value["cases"]}) > 1)
    score = scores[option]
    left, right = next((a, b) for a in score["cases"] for b in score["cases"] if a["output"] != b["output"])
    left["output"], right["output"] = right["output"], left["output"]
    before = [copy.deepcopy(entry["outcome"]["summary"]["selected_options"]) for entry in prefix[:36]]
    result = report(full_context, prefix)
    assert result["calibration"]["mismatches"] == 1 and not result["acceptance_passed"]
    mismatch = next(c for c in result["calibration"]["checks"] if c["matches"] is False)
    assert mismatch["check"] == f"reference-practice:{option}"
    assert [entry["outcome"]["summary"]["selected_options"] for entry in prefix[:36]] == before
