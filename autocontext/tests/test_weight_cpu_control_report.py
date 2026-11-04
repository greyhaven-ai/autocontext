"""Constructed report records exercise aggregation; they are not run evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget, CpuCampaignRepeat
from autocontext.weight_evolution.cpu_control_models import (
    CONTROL_FAMILIES,
    CpuControlCampaignSpec,
    CpuControlCampaignTask,
    CpuControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_control_report import build_cpu_control_report, build_cpu_control_transfer_report
from autocontext.weight_evolution.cpu_control_tasks import _task_payloads
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticCase, CpuDiagnosticReference, CpuDiagnosticSourceRun
from autocontext.weight_evolution.cpu_diagnostic_report import _reference_metrics, _ReferenceRow
from autocontext.weight_evolution.cpu_search_models import CpuSearchPolicy, CpuSearchSpec
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore, choose_policy_option, select_best

_RUNTIME = "c" * 64
_MECHANISMS = ("C", "W", "D")
_CONDITIONS = ("A", "C", "W", "CW", "D")


def campaign_fixture(
    *, tasks_per_family: int = 1, repeats: int = 1,
) -> tuple[CpuControlCampaignSpec, list[dict[str, Any]], dict[str, CpuControlTaskProvenance], dict[str, CpuSearchSpec]]:
    tasks = []
    provenances = {}
    templates = {}
    for family_index, family in enumerate(CONTROL_FAMILIES):
        for task_index in range(tasks_per_family):
            task_id = f"family-{family_index}-task-{task_index}"
            seed = 1000 + family_index * 100 + task_index
            template, provenance, payloads = _task_payloads(
                task_id=task_id, generation_seed=seed, family=family, cases_per_role=4,
            )
            tasks.append(CpuControlCampaignTask(
                task_id=task_id, generation_seed=seed, family=family,
                bundle_lock_sha256=hashlib.sha256(payloads["bundle.lock.json"]).hexdigest(),
                bundle_lock_size_bytes=len(payloads["bundle.lock.json"]),
                provenance_sha256=hashlib.sha256(payloads["task.provenance.json"]).hexdigest(),
                provenance_size_bytes=len(payloads["task.provenance.json"]),
            ))
            provenances[task_id], templates[task_id] = provenance, template
    spec = CpuControlCampaignSpec(
        campaign_id="control-report-fixture", tasks=tuple(tasks), implementation_digest="d" * 64,
        repeats=tuple(CpuCampaignRepeat(repeat_id=f"r{i}", inference_seed=i, search_order_seed=i + 100)
                      for i in range(repeats)),
        budget=CpuCampaignBudget(max_runs=len(tasks) * repeats * 3, max_proposals=10000, max_materializations=1000,
                                 max_inference_calls=100000, max_input_tokens=1000000, max_output_tokens=100000),
    )
    rows = []
    for task in spec.tasks:
        expected = provenances[task.task_id].expected_behavior
        baseline = expected.baseline_accuracy["confirmation"]
        weight = expected.designated_weight_accuracy["confirmation"] if task.family != "correct-baseline-v1" else baseline
        scores = {condition: weight if condition in {"W", "CW"} else baseline for condition in _CONDITIONS}
        for repeat in spec.repeats:
            for policy in spec.policies:
                run_id = f"{task.task_id}.{repeat.repeat_id}.{policy}"
                rows.append({
                    "run_id": run_id, "task_id": task.task_id, "repeat_id": repeat.repeat_id, "policy": policy,
                    "inference_seed": repeat.inference_seed,
                    "order_seed": repeat.search_order_seed if policy == "seeded-random-v1" else 0,
                    "study_digest": canonical_digest([run_id, "study"]), "status": "completed",
                    "reserved_capacity": templates[task.task_id].required_usage.copy(),
                    "charged_usage": templates[task.task_id].required_usage.copy(),
                    "summary_semantic_digest": canonical_digest([run_id, "summary"]),
                    "confirmation": "vetoed", "confirmation_scores": scores.copy(), "audit_scores": None,
                })
    return spec, rows, provenances, templates


def campaign_report(spec: CpuControlCampaignSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    report = build_cpu_control_report(spec, rows, runtime_digest=_RUNTIME)
    report["ledger_digest"] = "e" * 64
    return report


def seal(report: dict[str, Any]) -> None:
    report["semantic_digest"] = canonical_digest({key: value for key, value in report.items()
                                                 if key not in {"semantic_digest", "ledger_digest"}})


def reference_costs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    meters = ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")

    def total(field: str, include: tuple[str, ...]) -> dict[str, int]:
        return {meter: sum(row[field][meter] for row in rows if row["status"] in include) for meter in meters}

    unknown = [row["reference_id"] for row in rows if row["status"] in {"failed", "pending"}]
    return {"scheduled_capacity": total("reserved_capacity", ("completed", "failed", "pending", "not-started")),
            "attempted_capacity": total("reserved_capacity", ("completed", "failed", "pending")),
            "failed_or_pending_reserved_capacity": total("reserved_capacity", ("failed", "pending")),
            "known_charged_usage": total("charged_usage", ("completed",)),
            "unknown_charged_usage_reference_ids": unknown, "charged_usage_complete": not unknown,
            "scope": "reference-capacity-separate-from-original-policy-search-budgets",
            "reservation_rule": "failed-or-uncertain-reference-capacity-remains-charged-without-refund"}


def transfer_fixture() -> tuple[CpuControlCampaignSpec, dict[str, Any], dict[str, Any], dict[str, CpuControlTaskProvenance]]:
    spec, rows, provenances, templates = campaign_fixture()
    source = campaign_report(spec, rows)
    source_rows = {row["run_id"]: row for row in rows}
    references = []
    searches = []
    for task in spec.tasks:
        template = templates[task.task_id]
        provenance = provenances[task.task_id]
        expected = provenance.expected_behavior
        cases = tuple(case for case in template.cases if case.role == "practice")
        gold = tuple(case.expected for case in cases)
        baseline_outputs = gold if task.family == "correct-baseline-v1" else ("zero",) * len(cases)
        patterns = {"A": baseline_outputs}
        for option in template.options:
            patterns[option.option_id] = (gold if task.family != "correct-baseline-v1" else ("zero",) * len(cases)) if (
                option.option_id in expected.designated_weight_option_ids
            ) else baseline_outputs

        def score(outputs: tuple[str, ...], option_id: str, task_id: str = task.task_id,
                  practice_cases: Any = cases) -> dict[str, Any]:
            scored = [{"case_id": case.case_id, "output": output, "correct": output == case.expected,
                       "result_digest": canonical_digest([task_id, option_id, case.case_id, output])}
                      for case, output in zip(practice_cases, outputs, strict=True)]
            return {"accuracy": sum(item["correct"] for item in scored) / len(scored), "cases": scored}

        scores = {option_id: score(outputs, option_id) for option_id, outputs in patterns.items()}
        reference = CpuDiagnosticReference(
            reference_id=f"{task.task_id}.r0", task_id=task.task_id, repeat_id="r0", inference_seed=0,
            source_runs=tuple(CpuDiagnosticSourceRun(
                run_id=f"{task.task_id}.r0.{policy}", policy=policy,
                study_digest=source_rows[f"{task.task_id}.r0.{policy}"]["study_digest"],
                selection_digest=canonical_digest([task.task_id, policy, "selection"]),
                summary_semantic_digest=source_rows[f"{task.task_id}.r0.{policy}"]["summary_semantic_digest"],
            ) for policy in spec.policies),
            model=template.model, candidates=template.candidates, options=template.options,
            baseline_decoding=template.baseline_decoding, limits=template.limits,
            practice=tuple(CpuDiagnosticCase(case_id=case.case_id, prompt=case.prompt) for case in cases),
            practice_cases_digest=canonical_digest([case.to_dict() for case in cases]),
            source_options_digest=canonical_digest([option.to_dict() for option in template.options]),
        )
        raw = {"reference_id": reference.reference_id, "task_id": task.task_id, "repeat_id": "r0", "status": "completed",
               "reserved_capacity": reference.required_usage, "charged_usage": reference.required_usage,
               "scores": scores, "error_type": None}
        metrics = _reference_metrics(reference, _ReferenceRow.model_validate_json(json.dumps(raw)))
        references.append(metrics)
        for pin in reference.source_runs:
            source_row = source_rows[pin.run_id]
            policy = CpuSearchPolicy(name=pin.policy, order_seed=100 if pin.policy == "seeded-random-v1" else 0)
            mechanisms = {}
            for mechanism in _MECHANISMS:
                baseline = scores["A"]["accuracy"]
                history = (CpuPracticeScore(option_id=None, correct=int(baseline * len(cases)), total=len(cases)),)
                curve = []
                best = history[0].correct
                for trial in range(1, 4):
                    decision = choose_policy_option(policy, template.options_for(mechanism), history)  # type: ignore[arg-type]
                    assert decision.option_id is not None
                    correct = sum(item["correct"] for item in scores[decision.option_id]["cases"])
                    history = (*history, CpuPracticeScore(option_id=decision.option_id, correct=correct, total=len(cases)))
                    best = max(best, correct)
                    curve.append({"trial": trial, "option_id": decision.option_id, "correct": correct, "total": len(cases),
                                  "accuracy": correct / len(cases), "best_correct": best, "best_accuracy": best / len(cases),
                                  "gain_from_baseline": best / len(cases) - baseline})
                winner = select_best(history)
                selected = winner.correct / winner.total
                confirmation_gain = source_row["confirmation_scores"][mechanism] - source_row["confirmation_scores"]["A"]
                passed = confirmation_gain >= 0.1
                opportunity = metrics["mechanisms"][mechanism]
                gap = opportunity["best_declared_practice_accuracy"] - selected
                mechanisms[mechanism] = {
                    "selected_option_id": winner.option_id, "baseline_retained": winner.option_id is None,
                    "baseline_practice_accuracy": baseline, "selected_practice_accuracy": selected,
                    "practice_gain": selected - baseline, "trial_count": 3, "practice_curve": curve,
                    "declared_option_count": 4, "searched_option_count": 3, "reference_available": True,
                    "best_declared_practice_accuracy": opportunity["best_declared_practice_accuracy"],
                    "max_declared_practice_gain": opportunity["max_declared_practice_gain"], "selected_gap_to_best_practice": gap,
                    "opportunity_status": "no-declared-practice-improvement" if opportunity["max_declared_practice_gain"] == 0
                    else "declared-practice-best-reached",
                    "confirmation_baseline_accuracy": source_row["confirmation_scores"]["A"],
                    "confirmation_selected_accuracy": source_row["confirmation_scores"][mechanism],
                    "confirmation_gain": confirmation_gain, "minimum_confirmation_gain": 0.1,
                    "confirmation_component_passed": passed,
                    "component_veto_reason": None if passed else "unchanged-baseline-retained" if winner.option_id is None
                    else "selected-confirmation-gain-below-frozen-minimum",
                    "practice_gain_failed_confirmation": selected > baseline and not passed,
                }
            searches.append({
                "run_id": pin.run_id, "reference_id": reference.reference_id, "task_id": task.task_id, "repeat_id": "r0",
                "policy": pin.policy, "reference_status": "completed", "selection_digest": pin.selection_digest,
                "summary_semantic_digest": pin.summary_semantic_digest, "joint_confirmation": "vetoed",
                "veto_mechanisms": [key for key in _MECHANISMS if not mechanisms[key]["confirmation_component_passed"]],
                "source_audit_status": "withheld", "mechanisms": mechanisms,
                "selected_CW": {"practice_accuracy": mechanisms["W"]["selected_practice_accuracy"],
                                "practice_gain": mechanisms["W"]["practice_gain"],
                                "confirmation_accuracy": source_row["confirmation_scores"]["CW"],
                                "confirmation_gain": (source_row["confirmation_scores"]["CW"]
                                                      - source_row["confirmation_scores"]["A"]),
                                "scope": "original-selected-composition-no-exhaustive-CW-optimum"},
            })
    diagnostic = {
        "schema_version": 1, "report_kind": "cpu_control_diagnostic_evidence", "diagnostic_digest": "f" * 64,
        "original_diagnostic_report_digest": "a" * 64, "source_campaign_digest": spec.digest,
        "source_campaign_report_digest": canonical_digest(source), "source_campaign_ledger_digest": source["ledger_digest"],
        "runtime_digest": _RUNTIME, "model_execution_performed": False, "gpu_execution_deferred": True,
        "promotion_eligible": False, "benchmark_result_claimed": False, "inferential_statistics_computed": False,
        "family_pooling_performed": False, "confirmation_rule_changed": False,
        "reference_coverage": {"scheduled": 3, "completed": 3, "failed": 0, "pending": 0, "not-started": 0},
        "reference_costs": reference_costs(references), "reference_rows": references, "search_rows": searches,
    }
    seal(diagnostic)
    return spec, source, diagnostic, provenances


def test_campaign_keeps_families_separate_and_reports_task_means_without_inference() -> None:
    spec, rows, _, _ = campaign_fixture(tasks_per_family=2, repeats=2)
    for row in rows:
        family = next(task.family for task in spec.tasks if task.task_id == row["task_id"])
        task_index = int(row["task_id"][-1])
        effect = {"transfer-repair-v1": (0.25, 0.5), "correct-baseline-v1": (-0.5, -0.25),
                  "practice-shortcut-v1": (0.0, 0.0)}[family][task_index]
        row["confirmation_scores"]["C"] = 0.5 + (effect if row["policy"] == "adaptive-v1" else 0.0)
    report = build_cpu_control_report(spec, rows, runtime_digest=_RUNTIME)
    assert len(report["rows"]) == 36 and report["status_counts"]["vetoed"] == 36
    expected = (0.375, -0.375, 0.0)
    for family, mean in zip(CONTROL_FAMILIES, expected, strict=True):
        value = report["family_results"][family]
        assert value["task_count"] == 2 and value["status_counts"]["scheduled"] == 12
        contrast = value["confirmation_contrasts"]["adaptive-minus-fixed-order-v1:C"]
        assert contrast["mean"] == mean
        assert contrast["task_count"] == 2 and contrast["scheduled_pairs"] == 4
        assert all(len(task["pairs"]) == 2 for task in contrast["task_details"])
        assert not ({"lower", "upper", "p_value"} & set(contrast))
    assert "confirmation_contrasts" not in report and "policy_summary" not in report
    assert report["family_pooling_performed"] is False and report["inferential_statistics_computed"] is False


@pytest.mark.parametrize("status", ["failed", "pending", "not-started"])
def test_missing_campaign_rows_keep_denominators_and_suppress_only_their_family_mean(status: str) -> None:
    spec, rows, _, _ = campaign_fixture(repeats=2)
    rows[0].update(status=status, charged_usage=None, summary_semantic_digest=None, confirmation=None,
                   confirmation_scores=None, audit_scores=None, error_type="blocked" if status == "failed" else None)
    report = build_cpu_control_report(spec, rows, runtime_digest=_RUNTIME)
    first = report["family_results"][CONTROL_FAMILIES[0]]
    assert first["status_counts"][status] == 1 and first["status_counts"]["scheduled"] == 6
    assert first["policy_coverage"]["adaptive-v1"]["denominator"] == 2
    assert all(value["mean"] is None for value in first["confirmation_contrasts"].values())
    unaffected = report["family_results"][CONTROL_FAMILIES[1]]["confirmation_contrasts"]
    assert all(value["mean"] is not None for value in unaffected.values())
    assert len(report["rows"]) == 18


def test_duplicating_nested_repeats_does_not_change_task_count_or_family_mean() -> None:
    outputs = []
    for repeats in (1, 4):
        spec, rows, _, _ = campaign_fixture(tasks_per_family=2, repeats=repeats)
        for row in rows:
            row["confirmation_scores"]["D"] = 0.5 if row["policy"] == "adaptive-v1" else 0.25
        outputs.append(build_cpu_control_report(spec, rows, runtime_digest=_RUNTIME))
    for family in CONTROL_FAMILIES:
        first = outputs[0]["family_results"][family]["confirmation_contrasts"]["adaptive-minus-fixed-order-v1:D"]
        second = outputs[1]["family_results"][family]["confirmation_contrasts"]["adaptive-minus-fixed-order-v1:D"]
        assert first["task_count"] == second["task_count"] == 2
        assert first["mean"] == second["mean"] == 0.25
        assert second["scheduled_pairs"] == 4 * first["scheduled_pairs"]


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "task", "inference-seed", "order-seed", "capacity", "budget"])
def test_campaign_rejects_changed_schedule_and_unmatched_capacities(mutation: str) -> None:
    spec, rows, _, _ = campaign_fixture()
    if mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "missing":
        rows.pop()
    elif mutation == "task":
        rows[0]["task_id"] = "other"
    elif mutation == "inference-seed":
        rows[0]["inference_seed"] += 1
    elif mutation == "order-seed":
        rows[0]["order_seed"] += 1
    elif mutation == "capacity":
        rows[0]["reserved_capacity"]["input_tokens"] += 1
    else:
        raw = spec.to_dict()
        raw["budget"]["max_inference_calls"] = 1
        spec = CpuControlCampaignSpec.model_validate_json(json.dumps(raw))
    with pytest.raises(ValueError):
        build_cpu_control_report(spec, rows, runtime_digest=_RUNTIME)


def test_transfer_reports_known_family_patterns_without_unlocking_joint_audit() -> None:
    report = build_cpu_control_transfer_report(*transfer_fixture())
    repair, null, shortcut = [report["family_results"][family] for family in CONTROL_FAMILIES]
    for family in (repair, null, shortcut):
        assert family["reference_coverage"]["completed"] == 1
        assert family["calibration"]["observed_mismatches"] == 0
        assert family["calibration"]["unselected_heldout_options_evaluated"] is False
    for policy in ("adaptive-v1", "fixed-order-v1", "seeded-random-v1"):
        good = repair["policy_summary"][policy]["mechanisms"]["W"]
        assert good["confirmation_passed_runs"] == 1 and good["task_mean_confirmation_gain"] == 0.5
        assert null["policy_summary"][policy]["mechanisms"]["W"]["baseline_retained_runs"] == 1
        bad = shortcut["policy_summary"][policy]["mechanisms"]["W"]
        assert bad["practice_gain_failed_confirmation_runs"] == 1 and bad["task_mean_confirmation_gain"] == -0.5
        assert bad["component_veto_reasons"] == {"selected-confirmation-gain-below-frozen-minimum": 1}
        assert good["task_mean_selected_gap_if_references_complete"] == 0.0
        assert bad["task_mean_selected_gap_if_references_complete"] == 0.0
    assert report["confirmation_rule_changed"] is False and report["promotion_eligible"] is False
    assert report["family_pooling_performed"] is False


@pytest.mark.parametrize("status", ["failed", "pending", "not-started"])
def test_missing_reference_is_never_zero_imputed_or_pooled_across_control_families(status: str) -> None:
    spec, source, diagnostic, provenances = transfer_fixture()
    first = diagnostic["reference_rows"][0]
    first.update(status=status, charged_usage=None, baseline_score=None, mechanisms={key: None for key in _MECHANISMS},
                 error_type="blocked" if status == "failed" else None)
    for row in diagnostic["search_rows"][:3]:
        row["reference_status"] = status
        for value in row["mechanisms"].values():
            value.update(reference_available=False, best_declared_practice_accuracy=None,
                         max_declared_practice_gain=None, selected_gap_to_best_practice=None,
                         opportunity_status="reference-unavailable")
    diagnostic["reference_coverage"].update(completed=2, **{status: 1})
    diagnostic["reference_costs"] = reference_costs(diagnostic["reference_rows"])
    seal(diagnostic)
    report = build_cpu_control_transfer_report(spec, source, diagnostic, provenances)
    repair = report["family_results"][CONTROL_FAMILIES[0]]
    assert repair["reference_coverage"][status] == 1
    assert repair["reference_opportunity"]["W"]["positive_declared_practice_improvement"] == 0
    assert repair["reference_opportunity"]["W"]["missing_references"] == 1
    assert repair["calibration"]["unavailable_or_nondesignated_checks"] == 3
    for policy in spec.policies:
        value = repair["policy_summary"][policy]["mechanisms"]["W"]
        assert value["task_mean_selected_gap_if_references_complete"] is None
        assert value["task_mean_confirmation_gain"] == 0.5
        assert report["family_results"][CONTROL_FAMILIES[1]]["policy_summary"][policy]["mechanisms"]["W"][
            "task_mean_selected_gap_if_references_complete"] == 0.0


def test_observed_calibration_mismatch_is_reported_instead_of_discarded() -> None:
    spec, source, diagnostic, provenances = transfer_fixture()
    source["rows"][0]["confirmation_scores"].update(W=0.75, CW=0.75)
    source = campaign_report(spec, source["rows"])
    diagnostic["source_campaign_report_digest"] = canonical_digest(source)
    row = diagnostic["search_rows"][0]
    row["mechanisms"]["W"].update(confirmation_selected_accuracy=0.75, confirmation_gain=0.25)
    row["selected_CW"].update(confirmation_accuracy=0.75, confirmation_gain=0.25)
    seal(diagnostic)
    report = build_cpu_control_transfer_report(spec, source, diagnostic, provenances)
    calibration = report["family_results"][CONTROL_FAMILIES[0]]["calibration"]
    assert calibration["observed_mismatches"] == 1
    assert calibration["selected_weight_checks"][0]["matches_expectation"] is False


def test_reports_are_pure_and_deterministic_without_inferential_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    spec, source, diagnostic, provenances = transfer_fixture()
    before = copy.deepcopy((source, diagnostic, provenances))

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("pure control reports cannot execute, access files, or compute inference statistics")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("autocontext.analytics.paired_statistics.paired_confidence_interval", forbidden)
    actual = build_cpu_control_transfer_report(spec, source, diagnostic, provenances)
    assert before == (source, diagnostic, provenances)
    assert actual == build_cpu_control_transfer_report(spec, source, diagnostic, dict(reversed(list(provenances.items()))))
    unsigned = {key: value for key, value in actual.items() if key != "semantic_digest"}
    assert actual["semantic_digest"] == canonical_digest(unsigned)
    assert build_cpu_control_report(spec, list(reversed(source["rows"])), runtime_digest=_RUNTIME) == {
        key: value for key, value in source.items() if key != "ledger_digest"
    }


@pytest.mark.parametrize("mutation", [
    "source-hash", "source-repinned", "provenance-missing", "provenance-pin", "diagnostic-hash", "diagnostic-source",
    "diagnostic-ledger", "pooled-policy-summary", "pooled-flag", "missing-reference", "duplicate-reference",
    "missing-run", "run-family", "summary-pin", "joint-gate", "audit", "confirmation-score", "component-veto",
    "reference-cost", "reference-coverage", "gap", "baseline-retention", "opportunity", "output-group",
])
def test_transfer_rejects_changed_identity_scope_counts_and_measured_relationships(mutation: str) -> None:
    spec, source, diagnostic, provenances = transfer_fixture()
    if mutation == "source-hash":
        source["status_counts"]["vetoed"] -= 1
    elif mutation == "source-repinned":
        source["status_counts"]["vetoed"] -= 1
        seal(source)
        diagnostic["source_campaign_report_digest"] = canonical_digest(source)
    elif mutation == "provenance-missing":
        provenances.pop(spec.tasks[0].task_id)
    elif mutation == "provenance-pin":
        raw = spec.to_dict()
        raw["tasks"][0]["provenance_sha256"] = "0" * 64
        spec = CpuControlCampaignSpec.model_validate_json(json.dumps(raw))
        source = campaign_report(spec, source["rows"])
        diagnostic.update(source_campaign_digest=spec.digest, source_campaign_report_digest=canonical_digest(source))
    elif mutation == "diagnostic-hash":
        diagnostic["scope"] = "changed without repin"
    elif mutation == "diagnostic-source":
        diagnostic["source_campaign_report_digest"] = "0" * 64
    elif mutation == "diagnostic-ledger":
        diagnostic["source_campaign_ledger_digest"] = "0" * 64
    elif mutation == "pooled-policy-summary":
        diagnostic["policy_summary"] = {"adaptive-v1": {"mean_gap": 0.0}}
    elif mutation == "pooled-flag":
        diagnostic["family_pooling_performed"] = True
    elif mutation == "missing-reference":
        diagnostic["reference_rows"].pop()
    elif mutation == "duplicate-reference":
        diagnostic["reference_rows"].append(copy.deepcopy(diagnostic["reference_rows"][0]))
    elif mutation == "missing-run":
        diagnostic["search_rows"].pop()
    elif mutation == "run-family":
        diagnostic["search_rows"][0]["task_id"] = spec.tasks[1].task_id
    elif mutation == "summary-pin":
        diagnostic["search_rows"][0]["summary_semantic_digest"] = "0" * 64
    elif mutation == "joint-gate":
        diagnostic["search_rows"][0]["joint_confirmation"] = "confirmed"
    elif mutation == "audit":
        diagnostic["search_rows"][0]["source_audit_status"] = "completed"
    elif mutation == "confirmation-score":
        diagnostic["search_rows"][0]["mechanisms"]["W"]["confirmation_selected_accuracy"] = 0.0
    elif mutation == "component-veto":
        diagnostic["search_rows"][0]["mechanisms"]["C"]["component_veto_reason"] = None
    elif mutation == "reference-cost":
        diagnostic["reference_costs"]["known_charged_usage"]["materializations"] = 0
    elif mutation == "reference-coverage":
        diagnostic["reference_coverage"]["completed"] = 2
    elif mutation == "gap":
        diagnostic["search_rows"][0]["mechanisms"]["W"]["selected_gap_to_best_practice"] = 0.25
    elif mutation == "baseline-retention":
        diagnostic["search_rows"][0]["mechanisms"]["W"]["baseline_retained"] = True
    elif mutation == "opportunity":
        diagnostic["reference_rows"][0]["mechanisms"]["W"]["max_declared_practice_gain"] = 0.0
    elif mutation == "output-group":
        diagnostic["reference_rows"][0]["mechanisms"]["W"]["unique_output_vector_count"] = 999
    if mutation != "diagnostic-hash":
        seal(diagnostic)
    with pytest.raises(ValueError):
        build_cpu_control_transfer_report(spec, source, diagnostic, provenances)
