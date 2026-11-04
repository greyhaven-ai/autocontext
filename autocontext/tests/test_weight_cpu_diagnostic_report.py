"""Constructed scorer records test diagnostics; these are not campaign evidence."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_models import (
    CpuCampaignBudget,
    CpuCampaignRepeat,
    CpuCampaignSpec,
    CpuCampaignTask,
)
from autocontext.weight_evolution.cpu_campaign_report import build_cpu_campaign_report
from autocontext.weight_evolution.cpu_diagnostic_models import (
    CpuDiagnosticBudget,
    CpuDiagnosticCase,
    CpuDiagnosticReference,
    CpuDiagnosticSourceRun,
    CpuDiagnosticSpec,
)
from autocontext.weight_evolution.cpu_diagnostic_report import build_cpu_diagnostic_report
from autocontext.weight_evolution.cpu_search_models import CpuSearchPolicy, CpuSearchSpec
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore, choose_policy_option, select_best

_BASE = ("zero", "one", "one", "zero")
_THREE = ("zero", "one", "zero", "zero")
_OTHER_THREE = ("zero", "one", "one", "one")
_PERFECT = ("zero", "one", "zero", "one")
_BAD = ("one", "one", "one", "zero")
_GOLD = _PERFECT
_CONDITIONS = ("A", "C", "W", "CW", "D")
_MECHANISMS = ("C", "W", "D")
_RUNTIME = "c" * 64


def score(outputs: tuple[str, ...], role: str, label: str) -> dict[str, Any]:
    cases = [{"case_id": f"{role}-{index + 1:02}", "output": output, "correct": output == expected,
              "result_digest": canonical_digest([label, role, index, output])}
             for index, (output, expected) in enumerate(zip(outputs, _GOLD, strict=True))]
    return {"accuracy": sum(case["correct"] for case in cases) / len(cases), "cases": cases}


def report_payload(
    role: str, scores: dict[str, Any], *, study: str, selection: str, policy: CpuSearchPolicy,
) -> dict[str, Any]:
    labels = {"A": "C-baseline", "C": "C-restored", "W": "W-restored", "CW": "practice-CW", "D": "D-restored"}
    return {
        "schema_version": 1,
        "execution_kind": "adaptive_cpu_inference" if policy.name == "adaptive-v1" else "cpu_search_inference",
        "model_execution_performed": True, "gpu_execution_deferred": True,
        "promotion_eligible": False, "benchmark_result_claimed": False,
        "role": role, "study_digest": study, "runtime_digest": _RUNTIME, "selection_digest": selection,
        "scorer": "exact-label-v1", "policy": policy.to_dict(), "policy_digest": policy.digest,
        "scorer_digest": "d" * 64, "conditions": scores,
        "contrasts": {f"{a}-{b}": scores[a]["accuracy"] - scores[b]["accuracy"]
                      for a, b in (("C", "A"), ("W", "A"), ("CW", "C"), ("CW", "W"), ("D", "A"))},
        "receipt_digests": {condition: canonical_digest([study, f"confirmation-{condition}" if role == "confirmation"
                                                        else labels[condition]]) for condition in _CONDITIONS},
    }


def inputs(scenario: str = "mixed") -> tuple[CpuDiagnosticSpec, dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Use valid tiny-model contracts with deliberately constructed score evidence."""
    root = Path(__file__).resolve().parents[2] / "examples/weight_evolution/cpu_campaign/bundle/tasks"
    templates = [CpuSearchSpec.model_validate_json((root / f"task-{i:02}" / "study.json").read_bytes()) for i in (1, 2)]
    campaign = CpuCampaignSpec(
        campaign_id="diagnostic-report-fixture", implementation_digest="e" * 64,
        tasks=tuple(CpuCampaignTask(task_id=f"task-{i:02}", generation_seed=i,
                                    bundle_lock_sha256=f"{i:064x}", bundle_lock_size_bytes=100,
                                    provenance_sha256=f"{i + 100:064x}", provenance_size_bytes=200) for i in (1, 2)),
        repeats=(CpuCampaignRepeat(repeat_id="r0", inference_seed=0, search_order_seed=17),),
        budget=CpuCampaignBudget(max_runs=6, max_proposals=1000, max_materializations=1000,
                                 max_inference_calls=10000, max_input_tokens=100000, max_output_tokens=10000),
    )
    references = []
    reference_rows = []
    source_rows = []
    details = {}
    for index, template in enumerate(templates, 1):
        task_id, reference_id = f"task-{index:02}", f"task-{index:02}.r0"
        patterns: dict[str, tuple[str, ...]] = {"A": _BASE}
        for mechanism in _MECHANISMS:
            outputs = ((_THREE, _BASE, _BASE, _PERFECT) if mechanism == "C" else
                       (_THREE, _BASE, _BASE, _THREE) if mechanism == "D" else (_BASE,) * 4)
            if scenario == "none":
                outputs = (_BASE,) * 4
            elif scenario == "worse":
                outputs = (_BAD,) * 4
            elif scenario == "ties":
                outputs = (_THREE, _OTHER_THREE, _THREE, _BASE)
            elif scenario == "reached" and mechanism == "C":
                outputs = (_PERFECT, _BASE, _BASE, _BASE)
            for option, vector in zip(template.options_for(mechanism), outputs, strict=True):  # type: ignore[arg-type]
                patterns[option.option_id] = vector
        reference_scores = {option_id: score(vector, "practice", f"reference:{reference_id}:{option_id}")
                            for option_id, vector in patterns.items()}
        source_pins = []
        for policy_name in campaign.policies:
            policy = CpuSearchPolicy(name=policy_name, order_seed=17 if policy_name == "seeded-random-v1" else 0)
            run_id = f"{reference_id}.{policy_name}"
            study = canonical_digest([run_id, "study"])
            selection = canonical_digest([run_id, "selection"])
            histories = {}
            selections = {}
            proposals = {}
            selected_outputs = {"A": _BASE}
            for mechanism in _MECHANISMS:
                history = (CpuPracticeScore(option_id=None, correct=2, total=4),)
                proposals[mechanism] = []
                for _ in range(3):
                    decision = choose_policy_option(policy, template.options_for(mechanism), history)  # type: ignore[arg-type]
                    assert decision.option_id is not None
                    measured = reference_scores[decision.option_id]
                    history = (*history, CpuPracticeScore(option_id=decision.option_id,
                                                         correct=sum(case["correct"] for case in measured["cases"]), total=4))
                    proposals[mechanism].append(decision.to_dict())
                winner = select_best(history)
                histories[mechanism] = [observation.to_dict() for observation in history]
                selections[mechanism] = winner.option_id
                selected_outputs[mechanism] = patterns[winner.option_id or "A"]
            selected_outputs["CW"] = selected_outputs["C"]
            practice_scores = {condition: score(selected_outputs[condition], "practice", f"source:{run_id}:{condition}")
                               for condition in _CONDITIONS}
            confirmation_outputs = {condition: _BASE for condition in _CONDITIONS}
            if selections["C"] is not None:
                confirmation_outputs["C"] = confirmation_outputs["CW"] = _THREE
            if selections["D"] is not None:
                confirmation_outputs["D"] = _BAD
            confirmation_scores = {
                condition: score(confirmation_outputs[condition], "confirmation", f"source:{run_id}:{condition}")
                for condition in _CONDITIONS
            }
            practice = report_payload("practice", practice_scores, study=study, selection=selection, policy=policy)
            confirmation = report_payload("confirmation", confirmation_scores, study=study, selection=selection, policy=policy)
            outcomes = {mechanism: confirmation_scores[mechanism]["accuracy"] - confirmation_scores["A"]["accuracy"] >= 0.1
                        for mechanism in _MECHANISMS}
            gate = {"schema_version": 1, "look": 1, "selection_digest": selection,
                    "report_digest": canonical_digest(confirmation), "minimum_gain": 0.1,
                    "outcomes": outcomes, "status": "confirmed" if all(outcomes.values()) else "vetoed"}
            receipt_digests = {label: canonical_digest([study, label]) for label in (
                "C-baseline", "C-restored", "W-restored", "D-restored", "practice-CW",
                *(f"confirmation-{condition}" for condition in _CONDITIONS),
            )}
            summary = {"schema_version": 1, "execution_kind": practice["execution_kind"], "device": "cpu",
                       "model_execution_performed": True, "gpu_execution_deferred": True, "promotion_eligible": False,
                       "benchmark_result_claimed": False, "study_digest": study, "runtime_digest": _RUNTIME,
                       "policy": policy.to_dict(), "policy_digest": policy.digest, "selection_digest": selection,
                       "histories": histories, "proposal_history": proposals, "search_usage": {},
                       "search_capacity_per_mechanism": {}, "selected_options": selections,
                       "restoration_verified": {mechanism: True for mechanism in _MECHANISMS}, "confirmation": gate,
                       "audit_status": "completed" if gate["status"] == "confirmed" else "withheld",
                       "reserved_usage": template.required_usage, "receipt_digests": receipt_digests,
                       "scope": "constructed-unit-test-scores-not-campaign-evidence"}
            summary["semantic_digest"] = canonical_digest(summary)
            summary["receipt_evidence_digests"], summary["journal_digest"] = {}, "f" * 64
            source_pins.append(CpuDiagnosticSourceRun(run_id=run_id, policy=policy_name, study_digest=study,
                                                      selection_digest=selection,
                                                      summary_semantic_digest=summary["semantic_digest"]))
            source_rows.append({"run_id": run_id, "task_id": task_id, "repeat_id": "r0", "policy": policy_name,
                                "inference_seed": 0, "order_seed": policy.order_seed, "study_digest": study,
                                "status": "completed", "reserved_capacity": template.required_usage,
                                "charged_usage": template.required_usage, "summary_semantic_digest": summary["semantic_digest"],
                                "confirmation": gate["status"],
                                "confirmation_scores": {key: value["accuracy"] for key, value in confirmation_scores.items()},
                                "audit_scores": None})
            details[run_id] = {"summary": summary, "practice": practice, "confirmation": confirmation}
        reference = CpuDiagnosticReference(
            reference_id=reference_id, task_id=task_id, repeat_id="r0", inference_seed=0, source_runs=tuple(source_pins),
            model=template.model, candidates=template.candidates, options=template.options,
            baseline_decoding=template.baseline_decoding, limits=template.limits,
            practice=tuple(CpuDiagnosticCase(case_id=case.case_id, prompt=case.prompt)
                           for case in template.cases if case.role == "practice"),
            practice_cases_digest=canonical_digest([case.to_dict() for case in template.cases if case.role == "practice"]),
            source_options_digest=canonical_digest([option.to_dict() for option in template.options]),
        )
        references.append(reference)
        reference_rows.append({"reference_id": reference_id, "task_id": task_id, "repeat_id": "r0", "status": "completed",
                               "reserved_capacity": reference.required_usage, "charged_usage": reference.required_usage.copy(),
                               "scores": reference_scores, "error_type": None})
    source_report = build_cpu_campaign_report(campaign, source_rows, runtime_digest=_RUNTIME)
    source_report["ledger_digest"] = "a" * 64
    spec = CpuDiagnosticSpec(
        diagnostic_id="report-test", campaign_digest=campaign.digest, runtime_digest=_RUNTIME,
        campaign_report_digest=canonical_digest(source_report), campaign_ledger_digest=source_report["ledger_digest"],
        references=tuple(references), implementation_digest="b" * 64,
        budget=CpuDiagnosticBudget(max_references=len(references), **{
            f"max_{meter}": sum(reference.required_usage[meter] for reference in references)
            for meter in references[0].required_usage
        }),
    )
    return spec, source_report, reference_rows, details


def test_report_distinguishes_absent_opportunity_missed_gain_and_failed_confirmation() -> None:
    report = build_cpu_diagnostic_report(*inputs())
    assert report["reference_coverage"] == {"scheduled": 2, "completed": 2, "failed": 0, "pending": 0, "not-started": 0}
    adaptive = report["search_rows"][0]
    assert adaptive["policy"] == "adaptive-v1"
    context, weights, decoding = (adaptive["mechanisms"][key] for key in _MECHANISMS)
    assert context["max_declared_practice_gain"] == 0.5
    assert context["selected_gap_to_best_practice"] == 0.25
    assert context["opportunity_status"] == "available-practice-improvement-missed"
    assert [point["best_accuracy"] for point in context["practice_curve"]] == [0.75, 0.75, 0.75]
    assert context["confirmation_component_passed"] is True
    assert weights["opportunity_status"] == "no-declared-practice-improvement"
    assert weights["baseline_retained"] is True and weights["selected_gap_to_best_practice"] == 0.0
    assert weights["component_veto_reason"] == "unchanged-baseline-retained"
    assert decoding["practice_gain_failed_confirmation"] is True
    assert decoding["practice_gain"] == 0.25 and decoding["confirmation_gain"] == -0.25
    assert decoding["selected_gap_to_best_practice"] == 0.0
    assert adaptive["joint_confirmation"] == "vetoed" and adaptive["veto_mechanisms"] == ["W", "D"]
    assert adaptive["source_audit_status"] == "withheld"
    assert "optimum" not in adaptive["selected_CW"]
    assert adaptive["selected_CW"]["scope"] == "original-selected-composition-no-exhaustive-CW-optimum"


@pytest.mark.parametrize("scenario", ["none", "worse"])
def test_baseline_remains_in_declared_optimum_when_all_options_fail_to_improve(scenario: str) -> None:
    report = build_cpu_diagnostic_report(*inputs(scenario))
    for row in report["search_rows"]:
        for mechanism in row["mechanisms"].values():
            assert mechanism["baseline_retained"] is True
            assert mechanism["max_declared_practice_gain"] == 0.0
            assert mechanism["selected_gap_to_best_practice"] == 0.0
            assert mechanism["opportunity_status"] == "no-declared-practice-improvement"


def test_reached_best_with_confirmation_gain_does_not_override_other_component_vetoes() -> None:
    report = build_cpu_diagnostic_report(*inputs("reached"))
    adaptive = report["search_rows"][0]
    assert adaptive["mechanisms"]["C"]["opportunity_status"] == "declared-practice-best-reached"
    assert adaptive["mechanisms"]["C"]["confirmation_component_passed"] is True
    assert adaptive["joint_confirmation"] == "vetoed"
    assert report["confirmation_rule_changed"] is False and report["promotion_eligible"] is False


def test_behavior_equivalence_uses_output_vectors_not_accuracy_or_checkpoint_identity() -> None:
    spec, source, rows, details = inputs("ties")
    report = build_cpu_diagnostic_report(spec, source, rows, details)
    for mechanism in _MECHANISMS:
        value = report["reference_rows"][0]["mechanisms"][mechanism]
        assert value["unique_output_vector_count"] == 3
        options = spec.references[0].options_for(mechanism)  # type: ignore[arg-type]
        assert {tuple(group["option_ids"]) for group in value["output_equivalence_groups"]} == {
            ("A", options[3].option_id), (options[0].option_id, options[2].option_id), (options[1].option_id,),
        }
        # Equal top scores have different output vectors; the first measured
        # option remains the winner rather than using reference order to reselect.
        adaptive = report["search_rows"][0]["mechanisms"][mechanism]
        assert adaptive["selected_option_id"] == options[0].option_id


@pytest.mark.parametrize("status", ["failed", "pending", "not-started"])
def test_missing_reference_is_retained_without_zero_scores_or_survivor_mean(status: str) -> None:
    spec, source, rows, details = inputs()
    rows[0].update(status=status, charged_usage=None, scores=None,
                   error_type="ReferenceBlocked" if status == "failed" else None)
    report = build_cpu_diagnostic_report(spec, source, rows, details)
    assert report["reference_coverage"][status] == 1 and len(report["reference_rows"]) == 2
    assert report["reference_rows"][0]["baseline_score"] is None
    assert all(value is None for value in report["reference_rows"][0]["mechanisms"].values())
    for row in report["search_rows"][:3]:
        for mechanism in row["mechanisms"].values():
            assert mechanism["selected_gap_to_best_practice"] is None
            assert mechanism["max_declared_practice_gain"] is None
            assert mechanism["best_declared_practice_accuracy"] is None
            assert mechanism["opportunity_status"] == "reference-unavailable"
        assert row["joint_confirmation"] == "vetoed"
    for value in report["policy_summary"].values():
        assert value["mechanisms"]["C"]["reference_missing_runs"] == 1
        assert value["mechanisms"]["C"]["mean_selected_gap_if_all_references_complete"] is None
    costs = report["reference_costs"]
    assert costs["known_charged_usage"] == spec.references[1].required_usage
    assert costs["unknown_charged_usage_reference_ids"] == ([] if status == "not-started" else [rows[0]["reference_id"]])
    assert costs["failed_or_pending_reserved_capacity"] == (
        {key: 0 for key in spec.references[0].required_usage} if status == "not-started" else spec.references[0].required_usage
    )


def test_reference_reservation_is_shared_once_per_task_repeat_not_once_per_policy() -> None:
    spec, source, rows, details = inputs()
    report = build_cpu_diagnostic_report(spec, source, rows, details)
    assert len(report["reference_rows"]) == 2 and len(report["search_rows"]) == 6
    assert report["reference_costs"]["scheduled_capacity"] == {
        meter: sum(reference.required_usage[meter] for reference in spec.references)
        for meter in spec.references[0].required_usage
    }
    assert report["reference_costs"]["scheduled_capacity"]["proposals"] == 0
    assert report["reference_costs"]["scope"] == "reference-capacity-separate-from-original-policy-search-budgets"


def test_reporting_is_pure_ordered_and_never_computes_new_inference_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    spec, source, rows, details = inputs()
    before = copy.deepcopy((source, rows, details))

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("pure report must not access files, execute models, or compute new inference statistics")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr("autocontext.analytics.paired_statistics.paired_confidence_interval", forbidden)
    report = build_cpu_diagnostic_report(spec, source, rows, details)
    assert report == build_cpu_diagnostic_report(spec, source, list(reversed(rows)), dict(reversed(list(details.items()))))
    assert before == (source, rows, details)
    assert report["inferential_statistics_computed"] is False
    assert report["model_execution_performed"] is False
    assert report["gpu_execution_deferred"] is True
    assert report["promotion_eligible"] is False and report["benchmark_result_claimed"] is False
    semantic = {key: value for key, value in report.items() if key != "semantic_digest"}
    assert report["semantic_digest"] == canonical_digest(semantic)


@pytest.mark.parametrize("mutation", [
    "source-report", "source-ledger", "source-summary", "missing-source", "extra-source", "missing-reference",
    "duplicate-reference", "reference-identity", "reference-capacity", "reference-charge", "failed-score",
    "failed-charge", "missing-option", "extra-cw", "case-order", "accuracy", "integer-correct",
    "unknown-output", "observed-trial-count", "selected-output", "source-practice-role", "source-receipt",
    "source-confirmation-score", "source-selection", "source-gate",
])
def test_inconsistent_or_unpinned_report_inputs_are_rejected(mutation: str) -> None:
    spec, source, rows, details = inputs()
    first_run = spec.references[0].source_runs[0].run_id
    first_option = spec.references[0].options[0].option_id
    if mutation == "source-report":
        source["scope"] = "fabricated"
    elif mutation == "source-ledger":
        source["ledger_digest"] = "0" * 64
    elif mutation == "source-summary":
        details[first_run]["summary"]["scope"] = "fabricated"
    elif mutation == "missing-source":
        del details[first_run]
    elif mutation == "extra-source":
        details["extra"] = details[first_run]
    elif mutation == "missing-reference":
        rows.pop()
    elif mutation == "duplicate-reference":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "reference-identity":
        rows[0]["task_id"] = "other"
    elif mutation == "reference-capacity":
        rows[0]["reserved_capacity"]["input_tokens"] += 1
    elif mutation == "reference-charge":
        rows[0]["charged_usage"]["inference_calls"] -= 1
    elif mutation == "failed-score":
        rows[0].update(status="failed", charged_usage=None, error_type="error")
    elif mutation == "failed-charge":
        rows[0].update(status="failed", scores=None, error_type="error")
    elif mutation == "missing-option":
        del rows[0]["scores"][first_option]
    elif mutation == "extra-cw":
        rows[0]["scores"]["CW"] = copy.deepcopy(rows[0]["scores"]["A"])
    elif mutation == "case-order":
        rows[0]["scores"][first_option]["cases"].reverse()
    elif mutation == "accuracy":
        rows[0]["scores"][first_option]["accuracy"] = 0.1
    elif mutation == "integer-correct":
        rows[0]["scores"][first_option]["cases"][0]["correct"] = 1
    elif mutation == "unknown-output":
        rows[0]["scores"][first_option]["cases"][0]["output"] = "outside"
    elif mutation == "observed-trial-count":
        rows[0]["scores"][first_option] = score(_BASE, "practice", "changed")
    elif mutation == "selected-output":
        rows[0]["scores"][first_option] = score(_OTHER_THREE, "practice", "changed")
    elif mutation == "source-practice-role":
        details[first_run]["practice"]["role"] = "confirmation"
    elif mutation == "source-receipt":
        details[first_run]["practice"]["receipt_digests"]["C"] = "0" * 64
    elif mutation == "source-confirmation-score":
        details[first_run]["confirmation"]["conditions"]["C"]["accuracy"] = 0.0
    elif mutation == "source-selection":
        details[first_run]["summary"]["selected_options"]["C"] = None
    elif mutation == "source-gate":
        details[first_run]["summary"]["confirmation"]["status"] = "confirmed"
    with pytest.raises(ValueError):
        build_cpu_diagnostic_report(spec, source, rows, details)
