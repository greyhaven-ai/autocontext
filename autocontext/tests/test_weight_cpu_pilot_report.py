"""Constructed scorer records exercise reports, not model execution evidence."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_control import _schedule
from autocontext.weight_evolution.cpu_control_tasks import _task_payloads
from autocontext.weight_evolution.cpu_pilot import _plan
from autocontext.weight_evolution.cpu_pilot_bundle import build_cpu_pilot_bundle, load_cpu_pilot_bundle, pilot_schedule
from autocontext.weight_evolution.cpu_pilot_report import build_cpu_pilot_report
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec

_CONDITIONS = ("A", "C", "W", "CW", "D")


def score(child: CpuSearchSpec, role: str, outputs: list[str]) -> dict[str, Any]:
    cases = [{"case_id": case.case_id, "output": output, "correct": output == case.expected,
              "result_digest": canonical_digest([case.case_id, output])}
             for case, output in zip((c for c in child.cases if c.role == role), outputs, strict=True)]
    return {"accuracy": sum(c["correct"] for c in cases) / len(cases), "cases": cases}


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
    bundle = tmp_path_factory.mktemp("pilot-report") / "bundle"
    build_cpu_pilot_bundle(bundle)
    spec, control, templates, _ = load_cpu_pilot_bundle(bundle)
    schedule = pilot_schedule(control, templates)
    children = {row["run_id"]: child for row, child in _schedule(control, templates)}
    expected = {task.task_id: _task_payloads(task_id=task.task_id, generation_seed=task.generation_seed,
        family=task.family, cases_per_role=8)[1].expected_behavior for task in control.tasks}
    prefix = []
    ref_scores = {}
    for row in schedule[:72]:
        child = children[row["run_id"]]
        behavior = expected[row["task_id"]]
        selection = {"synthetic_selection": row["run_id"]}
        selected = None if row["family"] == "correct-baseline-v1" else behavior.designated_weight_option_ids[0]
        reports = {}
        for role in ("practice", "confirmation"):
            gold = [c.expected for c in child.cases if c.role == role]
            baseline = gold if row["family"] == "correct-baseline-v1" else ["zero"] * len(gold)
            weight = (baseline if selected is None else
                      ["one" if c == "zero" else "zero" for c in gold]
                      if role == "confirmation" and row["family"] == "practice-shortcut-v1" else gold)
            scores = {c: score(child, role, weight if c in {"W", "CW"} else baseline) for c in _CONDITIONS}
            reports[role] = payload(role, scores, child, canonical_digest(selection), spec.native_runtime_digest)
            if role == "practice":
                options = {"A": score(child, role, baseline)}
                for option in child.options:
                    outputs = (gold if selected else ["zero"] * len(gold)) if (
                        option.option_id in behavior.designated_weight_option_ids) else baseline
                    options[option.option_id] = score(child, role, outputs)
                ref_scores[f"{row['task_id']}.{row['repeat_id']}"] = options
        summary = {"selection_digest": canonical_digest(selection), "semantic_digest": canonical_digest(row),
                   "selected_options": {"C": None, "W": selected, "D": None},
                   "confirmation": {"status": "vetoed", "report_digest": canonical_digest(reports["confirmation"])}}
        prefix.append({"row": row, "claim": {"claimed_capacity": row["reserved_capacity"]},
            "result": {"verified_usage": row["reserved_capacity"]}, "published": True, "outcome_published": True,
            "outcome": {"status": "completed", "summary": summary, "observation_coverage": [], "parent_error_type": None},
            "details": {"selection": selection, **reports, "audit": None}, "reference": None})
    plan = _plan(spec, control, templates, prefix)
    assert plan is not None
    for row, reference in zip(schedule[72:], plan[0].references, strict=True):
        prefix.append({"row": row, "claim": {"claimed_capacity": row["reserved_capacity"]},
            "result": {"verified_usage": row["reserved_capacity"]}, "published": True, "outcome_published": True,
            "outcome": {"status": "completed", "summary": {"scores": ref_scores[row["run_id"]]},
                        "observation_coverage": [], "parent_error_type": None},
            "details": None, "reference": reference.to_dict()})
    return spec, control, templates, schedule, prefix


def report(context: tuple[Any, ...], prefix: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return build_cpu_pilot_report(*context[:4], context[4] if prefix is None else prefix)


def test_complete_report_has_equal_budget_family_comparisons_and_144_checks(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    assert result["evidence_integrity_verified"] and result["campaign_completed"] and result["acceptance_passed"]
    assert result["status_counts"]["search"]["completed"] == 72
    assert result["status_counts"]["reference"]["completed"] == 24
    assert result["calibration"]["matches"] == 144
    assert result["calibration"]["mismatches"] == result["calibration"]["unavailable_or_nondesignated"] == 0
    assert len(result["practice_gap_rows"]) == 72
    for family in result["family_results"].values():
        assert family["task_count"] == 4
        assert family["audit_coverage"]["available"] == 0
        assert family["audit_coverage"]["withheld_after_veto"] == 24
        assert all(row["mean"] == 0 for row in family["confirmation_contrasts"].values())
    assert not result["family_pooling_performed"] and not result["promotion_eligible"]
    assert result["gpu_execution_deferred"] and not result["historical_failure_cause_recovered"]


@pytest.mark.parametrize("status", ["failed", "uncertain"])
def test_partial_failure_preserves_capacity_and_unknowns(full_context: tuple[Any, ...], status: str) -> None:
    prefix = copy.deepcopy(full_context[4])
    prefix[0]["outcome"].update(status=status, summary=None)
    prefix[0]["result"]["verified_usage"] = None
    prefix[0]["details"] = None
    for entry in prefix[72:]:
        entry["outcome"].update(status="blocked", summary=None)
        entry["claim"]["claimed_capacity"] = dict.fromkeys(entry["row"]["reserved_capacity"], 0)
        entry["result"]["verified_usage"] = None
        entry["reference"] = None
    result = report(full_context, prefix)
    assert result["evidence_integrity_verified"] and not result["campaign_completed"] and not result["acceptance_passed"]
    assert result["status_counts"]["reference"]["blocked"] == 24
    assert result["unknown_usage_rows"] == {"search": 1, "reference": 0}
    assert all(value == 0 for value in result["charged_capacity"]["reference"].values())
    assert result["charged_capacity"]["search"]["proposals"] == 648
    assert result["calibration"]["unavailable_or_nondesignated"] == 73
    assert result["practice_gap_rows"][0]["mechanisms"]["W"]["selected_gap_to_best_practice"] is None
    family = result["family_results"]["transfer-repair-v1"]
    assert all(row["mean"] is None for row in family["confirmation_contrasts"].values())


def test_unstarted_and_pending_publication_do_not_pass(full_context: tuple[Any, ...]) -> None:
    empty = report(full_context, [])
    assert empty["status_counts"]["search"]["not-started"] == 72
    assert empty["status_counts"]["reference"]["not-started"] == 24
    assert empty["calibration"]["unavailable_or_nondesignated"] == 144
    prefix = copy.deepcopy(full_context[4])
    prefix[-1]["published"] = False
    result = report(full_context, prefix)
    assert not result["campaign_completed"] and not result["acceptance_passed"]
    assert result["pending_results"] == 1


def test_calibration_mismatch_is_retained_not_filtered(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    confirmation = prefix[0]["details"]["confirmation"]
    confirmation["conditions"]["W"] = copy.deepcopy(confirmation["conditions"]["A"])
    confirmation["contrasts"]["W-A"] = 0.0
    confirmation["contrasts"]["CW-W"] = 0.5
    prefix[0]["outcome"]["summary"]["confirmation"]["report_digest"] = canonical_digest(confirmation)
    result = report(full_context, prefix)
    assert result["campaign_completed"] and not result["acceptance_passed"]
    assert result["calibration"]["mismatches"] == 1 and len(result["rows"]) == 96


def test_reference_requires_same_outputs_not_only_same_accuracy(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    scores = prefix[72]["outcome"]["summary"]["scores"]["A"]
    for case in scores["cases"]:
        case["output"] = "one" if case["output"] == "zero" else "zero"
        case["correct"] = not case["correct"]
    with pytest.raises(ValueError, match="practice behavior"):
        report(full_context, prefix)


@pytest.mark.parametrize("change", ["schedule", "selection", "role", "case-count", "reference-missing-option"])
def test_drift_and_incomplete_scores_are_rejected(full_context: tuple[Any, ...], change: str) -> None:
    context = copy.deepcopy(full_context)
    prefix = context[4]
    if change == "schedule":
        context[3][0]["family"] = "correct-baseline-v1"
    elif change == "selection":
        prefix[0]["details"]["selection"]["extra"] = True
    elif change == "role":
        prefix[0]["details"]["practice"]["role"] = "confirmation"
    elif change == "case-count":
        prefix[0]["details"]["practice"]["conditions"]["A"]["cases"].pop()
    else:
        prefix[72]["outcome"]["summary"]["scores"].pop("A")
    with pytest.raises(ValueError):
        report(context)
