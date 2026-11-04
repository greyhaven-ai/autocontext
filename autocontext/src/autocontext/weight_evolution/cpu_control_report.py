"""Descriptive family-stratified reports for constructed CPU transfer controls.

Coordinators verify the retained source evidence before calling these pure
functions. Families remain separate; repeats are averaged within task instances
and no confidence interval, pooled winner, promotion, or new gate is introduced.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_report import _CampaignRow, _costs, _counts
from autocontext.weight_evolution.models import Digest

if TYPE_CHECKING:
    from autocontext.weight_evolution.cpu_control_models import CpuControlCampaignSpec, CpuControlTaskProvenance

_FAMILIES = ("transfer-repair-v1", "correct-baseline-v1", "practice-shortcut-v1")
_MECHANISMS = ("C", "W", "D")
_CONDITIONS = ("C", "W", "CW", "D")
_METERS = ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")


def _ordered_campaign_rows(spec: CpuControlCampaignSpec, rows: list[dict[str, Any]]) -> list[_CampaignRow]:
    parsed = [_CampaignRow.model_validate_json(json.dumps(row, allow_nan=False)) for row in rows]
    indexed = {row.run_id: row for row in parsed}
    if len(indexed) != len(parsed):
        raise ValueError("control campaign report repeats a source run")
    expected: list[dict[str, Any]] = [
        {"run_id": f"{task.task_id}.{repeat.repeat_id}.{policy}", "task_id": task.task_id,
         "repeat_id": repeat.repeat_id, "policy": policy, "inference_seed": repeat.inference_seed,
         "order_seed": repeat.search_order_seed if policy == "seeded-random-v1" else 0}
        for task in spec.tasks for repeat in spec.repeats for policy in spec.policies
    ]
    if set(indexed) != {row["run_id"] for row in expected}:
        raise ValueError("control campaign report must retain the complete scheduled roster")
    ordered = []
    for identity in expected:
        row = indexed[identity["run_id"]]
        if any(getattr(row, key) != value for key, value in identity.items()):
            raise ValueError("control campaign row differs from its pinned task, repeat or policy")
        ordered.append(row)
    for meter in _METERS:
        if sum(row.reserved_capacity[meter] for row in ordered) > getattr(spec.budget, f"max_{meter}"):
            raise ValueError("control campaign reservations exceed the pinned complete budget")
    for task in spec.tasks:
        capacities = [row.reserved_capacity for row in ordered if row.task_id == task.task_id]
        if any(capacity != capacities[0] for capacity in capacities):
            raise ValueError("control task policies and repeats require equal reserved capacities")
    return ordered


def _paired_description(
    rows: list[_CampaignRow], *, task_ids: list[str], repeat_ids: list[str], control: str, condition: str,
) -> dict[str, Any]:
    indexed: dict[tuple[str, str, str], _CampaignRow] = {(row.task_id, row.repeat_id, row.policy): row for row in rows}
    family_complete = all(row.status == "completed" for row in rows)
    tasks: list[dict[str, Any]] = []
    observed_pairs = 0
    for task_id in task_ids:
        pairs = []
        effects = []
        for repeat_id in repeat_ids:
            adaptive, baseline = indexed[task_id, repeat_id, "adaptive-v1"], indexed[task_id, repeat_id, control]
            available = adaptive.status == baseline.status == "completed"
            difference = None
            if available:
                assert adaptive.confirmation_scores is not None and baseline.confirmation_scores is not None
                difference = adaptive.confirmation_scores[condition] - baseline.confirmation_scores[condition]
                effects.append(difference)
                observed_pairs += 1
            pairs.append({"repeat_id": repeat_id, "adaptive_run_id": adaptive.run_id,
                          "control_run_id": baseline.run_id, "adaptive_status": adaptive.status,
                          "control_status": baseline.status, "difference": difference})
        tasks.append({"task_id": task_id, "scheduled_pairs": len(repeat_ids), "observed_pairs": len(effects),
                      "complete": len(effects) == len(repeat_ids),
                      "descriptive_mean_observed_pairs": statistics.fmean(effects) if effects else None,
                      "pairs": pairs})
    return {
        "adaptive_policy": "adaptive-v1", "control_policy": control, "condition": condition,
        "score_role": "confirmation", "scheduled_pairs": len(task_ids) * len(repeat_ids),
        "observed_pairs": observed_pairs, "task_count": len(task_ids), "task_details": tasks,
        "mean": statistics.fmean(task["descriptive_mean_observed_pairs"] for task in tasks) if family_complete else None,
        "mean_available": family_complete,
        "mean_unavailable_reason": None if family_complete else "incomplete-family-schedule",
        "aggregation_unit": "task-mean-over-paired-repeats", "inferential_statistics_computed": False,
        "scope": "descriptive-constructed-family-comparison-no-adaptive-advantage-claim",
    }


def _coverage(rows: list[_CampaignRow]) -> dict[str, Any]:
    counts = _counts(rows)
    return {**counts, "denominator": counts["scheduled"],
            "completed_fraction": counts["completed"] / counts["scheduled"],
            "confirmed_fraction": counts["confirmed"] / counts["scheduled"],
            "vetoed_fraction": counts["vetoed"] / counts["scheduled"], "costs": _costs(rows)}


def build_cpu_control_report(
    spec: CpuControlCampaignSpec, rows: list[dict[str, Any]], *, runtime_digest: str,
) -> dict[str, Any]:
    """Retain all searches and describe each predeclared family separately."""
    from autocontext.weight_evolution.cpu_control_models import CpuControlCampaignSpec

    spec = CpuControlCampaignSpec.model_validate_json(json.dumps(spec.to_dict(), allow_nan=False))
    TypeAdapter(Digest).validate_python(runtime_digest)
    ordered = _ordered_campaign_rows(spec, rows)
    family_results = {}
    for family in _FAMILIES:
        task_ids = [task.task_id for task in spec.tasks if task.family == family]
        family_rows = [row for row in ordered if row.task_id in task_ids]
        if not task_ids:
            raise ValueError("control campaign must retain each predeclared family")
        family_results[family] = {
            "task_ids": task_ids, "task_count": len(task_ids), "status_counts": _counts(family_rows),
            "costs": _costs(family_rows),
            "policy_coverage": {policy: _coverage([row for row in family_rows if row.policy == policy])
                                for policy in spec.policies},
            "confirmation_contrasts": {
                f"adaptive-minus-{control}:{condition}": _paired_description(
                    family_rows, task_ids=task_ids, repeat_ids=[repeat.repeat_id for repeat in spec.repeats],
                    control=control, condition=condition,
                ) for control in spec.policies[1:] for condition in _CONDITIONS
            },
            "audit_coverage": {"scheduled": len(family_rows),
                               "available": sum(row.audit_scores is not None for row in family_rows),
                               "withheld_after_veto": sum(row.confirmation == "vetoed" for row in family_rows),
                               "conditional_on_confirmation": True, "overall_quality_aggregate": None},
        }
    report = {
        "schema_version": 1, "report_kind": "cpu_control_campaign", "campaign_digest": spec.digest,
        "runtime_digest": runtime_digest, "reporting_rule": spec.reporting_rule,
        "model_execution_performed": False, "evidence_model_execution": "actual-cpu-inference",
        "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
        "inferential_statistics_computed": False, "family_pooling_performed": False, "confirmation_rule_changed": False,
        "status_counts": _counts(ordered), "costs": _costs(ordered), "rows": [row.to_dict() for row in ordered],
        "family_results": family_results, "scope": "constructed-control-families-no-pooled-winner-or-generalization-claim",
    }
    report["semantic_digest"] = canonical_digest(report)
    return report


def _sealed(report: dict[str, Any]) -> None:
    semantic = {key: value for key, value in report.items() if key not in {"semantic_digest", "ledger_digest"}}
    if report.get("semantic_digest") != canonical_digest(semantic):
        raise ValueError("control transfer input report differs from its semantic digest")
    if "ledger_digest" in report:
        TypeAdapter(Digest).validate_python(report["ledger_digest"])


def _provenances(
    spec: CpuControlCampaignSpec, values: dict[str, Any],
) -> dict[str, CpuControlTaskProvenance]:
    from autocontext.weight_evolution.cpu_control_models import CpuControlTaskProvenance

    if set(values) != {task.task_id for task in spec.tasks}:
        raise ValueError("control transfer report requires every pinned task provenance")
    result = {}
    for task in spec.tasks:
        raw = values[task.task_id]
        payload = raw.to_dict() if isinstance(raw, CpuControlTaskProvenance) else raw
        provenance = CpuControlTaskProvenance.model_validate_json(json.dumps(payload, allow_nan=False))
        if (provenance.task_id, provenance.family, provenance.generation_seed) != (
            task.task_id, task.family, task.generation_seed,
        ):
            raise ValueError("control provenance identity differs from the scheduled task")
        encoded = (json.dumps(provenance.to_dict(), sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        if len(encoded) != task.provenance_size_bytes or hashlib.sha256(encoded).hexdigest() != task.provenance_sha256:
            raise ValueError("control provenance differs from its pinned generator bytes")
        result[task.task_id] = provenance
    return result


def _accuracy(value: Any) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("control diagnostic accuracy must be finite and between zero and one")
    return float(value)


def _count_score(value: Any, total: int) -> None:
    if (not isinstance(value, dict) or type(value.get("correct")) is not int
            or type(value.get("total")) is not int or value["total"] != total or not 0 <= value["correct"] <= total
            or _accuracy(value.get("accuracy")) != value["correct"] / total):
        raise ValueError("control diagnostic score differs from its complete cohort counts")


def _usage(value: Any) -> dict[str, int]:
    if (not isinstance(value, dict) or set(value) != set(_METERS)
            or any(type(amount) is not int or amount < 0 for amount in value.values()) or value["proposals"] != 0):
        raise ValueError("control reference usage must name all meters and zero proposals")
    return value


def _reference_row(row: dict[str, Any], provenance: CpuControlTaskProvenance) -> None:
    status = row.get("status")
    if status not in {"completed", "failed", "pending", "not-started"}:
        raise ValueError("control diagnostic reference has an invalid status")
    _usage(row.get("reserved_capacity"))
    if (status == "failed") != (isinstance(row.get("error_type"), str) and bool(row["error_type"])):
        raise ValueError("control diagnostic reference failure metadata is inconsistent")
    if status != "failed" and row.get("error_type") is not None:
        raise ValueError("control diagnostic nonfailure cannot carry an error type")
    mechanisms = row.get("mechanisms")
    if not isinstance(mechanisms, dict) or set(mechanisms) != set(_MECHANISMS):
        raise ValueError("control diagnostic reference is missing a mechanism")
    if status != "completed":
        if (row.get("baseline_score") is not None or row.get("charged_usage") is not None
                or any(value is not None for value in mechanisms.values())):
            raise ValueError("missing control reference metrics and actual charges must remain unknown")
        return
    if _usage(row.get("charged_usage")) != row["reserved_capacity"]:
        raise ValueError("completed control reference charges differ from its exhaustive reservation")
    total = provenance.cases_per_role
    _count_score(row.get("baseline_score"), total)
    case_ids = row.get("practice_case_ids")
    if not isinstance(case_ids, list) or len(case_ids) != total or len(set(case_ids)) != total:
        raise ValueError("control diagnostic reference does not cover its complete practice cohort")
    all_options: set[str] = set()
    for value in mechanisms.values():
        if not isinstance(value, dict):
            raise ValueError("completed control reference lacks mechanism metrics")
        options = value.get("option_scores")
        if (not isinstance(options, list) or len(options) != value.get("declared_option_count", -1) + 1
                or value.get("evaluated_option_count") != value.get("declared_option_count")
                or value.get("declared_option_count") != 4 or options[0].get("option_id") != "A"):
            raise ValueError("control diagnostic must retain baseline and all four declared options")
        by_id = {}
        for option in options:
            _count_score(option, total)
            option_id = option.get("option_id")
            if not isinstance(option_id, str) or not option_id or option_id in by_id:
                raise ValueError("control diagnostic reference repeats or omits an option identity")
            TypeAdapter(Digest).validate_python(option.get("behavior_digest"))
            by_id[option_id] = option
        if all_options & (set(by_id) - {"A"}):
            raise ValueError("control diagnostic repeats an option across mechanisms")
        all_options.update(set(by_id) - {"A"})
        if any(options[0][key] != row["baseline_score"][key] for key in ("correct", "total", "accuracy")):
            raise ValueError("control diagnostic mechanism baseline changed")
        best = max(option["correct"] for option in options)
        expected_best = [option["option_id"] for option in options if option["correct"] == best]
        if (value.get("practice_total") != total or value.get("best_declared_practice_correct") != best
                or value.get("best_declared_practice_accuracy") != best / total
                or value.get("max_declared_practice_gain") != (best - row["baseline_score"]["correct"]) / total
                or value.get("best_declared_option_ids") != expected_best):
            raise ValueError("control diagnostic optimum differs from its complete practice option scores")
        groups = value.get("output_equivalence_groups")
        if not isinstance(groups, list):
            raise ValueError("control diagnostic is missing its output equivalence groups")
        grouped: list[str] = []
        group_digests = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("option_ids"), list) or not group["option_ids"]:
                raise ValueError("control diagnostic output equivalence group is invalid")
            group_digests.append(group.get("behavior_digest"))
            for option_id in group["option_ids"]:
                if option_id not in by_id or by_id[option_id]["behavior_digest"] != group.get("behavior_digest"):
                    raise ValueError("control diagnostic output groups differ from their option vectors")
                grouped.append(option_id)
        if (len(grouped) != len(by_id) or set(grouped) != set(by_id)
                or len(set(group_digests)) != len(groups) or value.get("unique_output_vector_count") != len(groups)):
            raise ValueError("control diagnostic output groups do not uniquely cover its options")


def _search_row(row: dict[str, Any], source: _CampaignRow, reference: dict[str, Any]) -> None:
    if (row.get("task_id"), row.get("repeat_id"), row.get("policy"), row.get("reference_id")) != (
        source.task_id, source.repeat_id, source.policy, reference["reference_id"],
    ):
        raise ValueError("control diagnostic search differs from its source run identity")
    if (row.get("reference_status") != reference["status"] or row.get("joint_confirmation") != source.confirmation
            or row.get("summary_semantic_digest") != source.summary_semantic_digest
            or row.get("source_audit_status") != ("completed" if source.confirmation == "confirmed" else "withheld")):
        raise ValueError("control diagnostic source outcomes differ from the frozen campaign")
    TypeAdapter(Digest).validate_python(row.get("selection_digest"))
    mechanisms = row.get("mechanisms")
    if not isinstance(mechanisms, dict) or set(mechanisms) != set(_MECHANISMS):
        raise ValueError("control diagnostic search is missing a mechanism")
    assert source.confirmation_scores is not None
    vetoes = []
    thresholds = set()
    for mechanism in _MECHANISMS:
        value = mechanisms[mechanism]
        if not isinstance(value, dict):
            raise ValueError("control diagnostic search mechanism is invalid")
        baseline = _accuracy(value.get("baseline_practice_accuracy"))
        selected = _accuracy(value.get("selected_practice_accuracy"))
        if (type(value.get("baseline_retained")) is not bool
                or value["baseline_retained"] != (value.get("selected_option_id") is None)
                or value.get("practice_gain") != selected - baseline or selected < baseline):
            raise ValueError("control diagnostic selected practice gain or baseline retention is inconsistent")
        threshold = _accuracy(value.get("minimum_confirmation_gain"))
        if threshold <= 0:
            raise ValueError("control diagnostic confirmation minimum gain must remain positive")
        thresholds.add(threshold)
        gain = source.confirmation_scores[mechanism] - source.confirmation_scores["A"]
        passed = gain >= threshold
        if (value.get("confirmation_baseline_accuracy") != source.confirmation_scores["A"]
                or value.get("confirmation_selected_accuracy") != source.confirmation_scores[mechanism]
                or value.get("confirmation_gain") != gain or type(value.get("confirmation_component_passed")) is not bool
                or value["confirmation_component_passed"] != passed
                or type(value.get("practice_gain_failed_confirmation")) is not bool
                or value["practice_gain_failed_confirmation"] != (selected > baseline and not passed)):
            raise ValueError("control diagnostic component outcome differs from original confirmation scores")
        reason = (None if passed else "unchanged-baseline-retained" if value["baseline_retained"] else
                  "selected-confirmation-gain-below-frozen-minimum")
        if value.get("component_veto_reason") != reason:
            raise ValueError("control diagnostic component veto explanation changed")
        if not passed:
            vetoes.append(mechanism)
        available = reference["status"] == "completed"
        if type(value.get("reference_available")) is not bool or value["reference_available"] != available:
            raise ValueError("control diagnostic search reference availability changed")
        if available:
            opportunity = reference["mechanisms"][mechanism]
            best = opportunity["best_declared_practice_accuracy"]
            if (value.get("best_declared_practice_accuracy") != best
                    or value.get("max_declared_practice_gain") != opportunity["max_declared_practice_gain"]
                    or value.get("selected_gap_to_best_practice") != best - selected or best < selected
                    or baseline != reference["baseline_score"]["accuracy"]):
                raise ValueError("control diagnostic practice regret differs from its exhaustive reference")
            scores = {item["option_id"]: item for item in opportunity["option_scores"]}
            selected_id = value.get("selected_option_id") or "A"
            if selected_id not in scores or scores[selected_id]["accuracy"] != selected:
                raise ValueError("control diagnostic selection is absent from its declared practice reference")
        elif any(value.get(key) is not None for key in (
            "best_declared_practice_accuracy", "max_declared_practice_gain", "selected_gap_to_best_practice",
        )):
            raise ValueError("control diagnostic missing-reference gaps must remain null")
    if (len(thresholds) != 1 or row.get("veto_mechanisms") != vetoes
            or (not vetoes) != (source.confirmation == "confirmed")):
        raise ValueError("control diagnostic must preserve the original joint C/W/D gate")
    cw = row.get("selected_CW")
    if (not isinstance(cw, dict) or cw.get("confirmation_accuracy") != source.confirmation_scores["CW"]
            or cw.get("confirmation_gain") != source.confirmation_scores["CW"] - source.confirmation_scores["A"]):
        raise ValueError("control diagnostic selected CW confirmation changed")


def _reference_costs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def total(selected: list[dict[str, Any]], field: str) -> dict[str, int]:
        return {meter: sum(row[field][meter] for row in selected) for meter in _METERS}

    unknown = [row["reference_id"] for row in rows if row["status"] != "not-started" and row["charged_usage"] is None]
    return {
        "scheduled_capacity": total(rows, "reserved_capacity"),
        "attempted_capacity": total([row for row in rows if row["status"] != "not-started"], "reserved_capacity"),
        "failed_or_pending_reserved_capacity": total([
            row for row in rows if row["status"] in {"failed", "pending"}
        ], "reserved_capacity"),
        "known_charged_usage": total([row for row in rows if row["charged_usage"] is not None], "charged_usage"),
        "unknown_charged_usage_reference_ids": unknown, "charged_usage_complete": not unknown,
        "scope": "reference-capacity-separate-from-original-policy-search-budgets",
        "reservation_rule": "failed-or-uncertain-reference-capacity-remains-charged-without-refund",
    }


def _reference_coverage(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"scheduled": len(rows), **{status: sum(row["status"] == status for row in rows)
                                      for status in ("completed", "failed", "pending", "not-started")}}


def _opportunity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for mechanism in _MECHANISMS:
        complete = [row["mechanisms"][mechanism] for row in rows if row["status"] == "completed"]
        result[mechanism] = {
            "scheduled_references": len(rows), "completed_references": len(complete),
            "missing_references": len(rows) - len(complete),
            "positive_declared_practice_improvement": sum(value["max_declared_practice_gain"] > 0 for value in complete),
            "no_declared_practice_improvement": sum(value["max_declared_practice_gain"] == 0 for value in complete),
            "only_one_output_vector": sum(value["unique_output_vector_count"] == 1 for value in complete),
            "mean_unique_output_vectors_if_complete": (
                statistics.fmean(value["unique_output_vector_count"] for value in complete)
                if len(complete) == len(rows) else None
            ),
        }
    return result


def _transfer_policy(rows: list[dict[str, Any]], task_ids: list[str]) -> dict[str, Any]:
    mechanisms = {}
    for mechanism in _MECHANISMS:
        values = [row["mechanisms"][mechanism] for row in rows]
        available = [value for value in values if value["reference_available"]]
        task_details: list[dict[str, Any]] = []
        for task_id in task_ids:
            task_values = [row["mechanisms"][mechanism] for row in rows if row["task_id"] == task_id]
            complete = all(value["reference_available"] for value in task_values)
            task_details.append({
                "task_id": task_id, "repeat_count": len(task_values),
                "mean_practice_gain": statistics.fmean(value["practice_gain"] for value in task_values),
                "mean_confirmation_gain": statistics.fmean(value["confirmation_gain"] for value in task_values),
                "mean_selected_gap_if_references_complete": (
                    statistics.fmean(value["selected_gap_to_best_practice"] for value in task_values) if complete else None
                ),
            })
        reasons = sorted({value["component_veto_reason"] for value in values if value["component_veto_reason"] is not None})
        mechanisms[mechanism] = {
            "scheduled_runs": len(values), "reference_complete_runs": len(available),
            "reference_missing_runs": len(values) - len(available),
            "baseline_retained_runs": sum(value["baseline_retained"] for value in values),
            "positive_practice_gain_runs": sum(value["practice_gain"] > 0 for value in values),
            "confirmation_passed_runs": sum(value["confirmation_component_passed"] for value in values),
            "practice_gain_failed_confirmation_runs": sum(value["practice_gain_failed_confirmation"] for value in values),
            "available_practice_improvement_missed_runs": sum(
                value["max_declared_practice_gain"] > 0 and value["selected_gap_to_best_practice"] > 0 for value in available
            ),
            "component_veto_reasons": {reason: sum(value["component_veto_reason"] == reason for value in values)
                                      for reason in reasons},
            "task_details": task_details,
            "task_mean_practice_gain": statistics.fmean(task["mean_practice_gain"] for task in task_details),
            "task_mean_confirmation_gain": statistics.fmean(task["mean_confirmation_gain"] for task in task_details),
            "task_mean_selected_gap_if_references_complete": (
                statistics.fmean(task["mean_selected_gap_if_references_complete"] for task in task_details)
                if len(available) == len(values) else None
            ),
            "aggregation_unit": "task-mean-over-nested-repeats",
        }
    return {"scheduled_runs": len(rows), "mechanisms": mechanisms}


def _calibration(
    references: list[dict[str, Any]], searches: list[dict[str, Any]], provenances: dict[str, CpuControlTaskProvenance],
) -> dict[str, Any]:
    reference_checks = []
    selected_checks = []
    for row in references:
        expected = provenances[row["task_id"]].expected_behavior
        observed = row["baseline_score"]["accuracy"] if row["status"] == "completed" else None
        designated = []
        option_scores = ({option["option_id"]: option["accuracy"] for option in row["mechanisms"]["W"]["option_scores"]}
                         if row["status"] == "completed" else {})
        for option_id in expected.designated_weight_option_ids:
            if row["status"] == "completed" and option_id not in option_scores:
                raise ValueError("designated control weight option is missing from the exhaustive reference")
            accuracy = option_scores.get(option_id)
            designated.append({"option_id": option_id,
                               "expected_practice_accuracy": expected.designated_weight_accuracy["practice"],
                               "observed_practice_accuracy": accuracy,
                               "matches_expectation": (accuracy == expected.designated_weight_accuracy["practice"]
                                                       if accuracy is not None else None)})
        reference_checks.append({"reference_id": row["reference_id"], "status": row["status"],
                                 "expected_baseline_practice_accuracy": expected.baseline_accuracy["practice"],
                                 "observed_baseline_practice_accuracy": observed,
                                 "baseline_matches_expectation": (observed == expected.baseline_accuracy["practice"]
                                                                  if observed is not None else None),
                                 "designated_weight_options": designated})
    for row in searches:
        expected = provenances[row["task_id"]].expected_behavior
        weight = row["mechanisms"]["W"]
        option_id = weight["selected_option_id"]
        kind = "baseline" if option_id is None else "designated" if option_id in expected.designated_weight_option_ids else None
        accuracy = (expected.baseline_accuracy["confirmation"] if kind == "baseline" else
                    expected.designated_weight_accuracy["confirmation"] if kind == "designated" else None)
        observed = weight["confirmation_selected_accuracy"]
        selected_checks.append({"run_id": row["run_id"], "selected_option_id": option_id, "control_kind": kind,
                                "expected_confirmation_accuracy": accuracy, "observed_confirmation_accuracy": observed,
                                "matches_expectation": observed == accuracy if accuracy is not None else None})
    checks = [row["baseline_matches_expectation"] for row in reference_checks]
    checks.extend(option["matches_expectation"] for row in reference_checks for option in row["designated_weight_options"])
    checks.extend(row["matches_expectation"] for row in selected_checks)
    return {"scope": "analytical-W-calibration-not-joint-gate", "reference_checks": reference_checks,
            "selected_weight_checks": selected_checks, "observed_matches": sum(value is True for value in checks),
            "observed_mismatches": sum(value is False for value in checks),
            "unavailable_or_nondesignated_checks": sum(value is None for value in checks),
            "unselected_heldout_options_evaluated": False}


def build_cpu_control_transfer_report(
    campaign_spec: CpuControlCampaignSpec, campaign_report: dict[str, Any], diagnostic_report: dict[str, Any],
    provenance_by_task: dict[str, Any],
) -> dict[str, Any]:
    """Stratify verified M12-shaped diagnostics without changing any measured outcome."""
    from autocontext.weight_evolution.cpu_control_models import CpuControlCampaignSpec

    spec = CpuControlCampaignSpec.model_validate_json(json.dumps(campaign_spec.to_dict(), allow_nan=False))
    _sealed(campaign_report)
    expected_campaign = build_cpu_control_report(spec, campaign_report["rows"], runtime_digest=campaign_report["runtime_digest"])
    if {key: value for key, value in campaign_report.items() if key != "ledger_digest"} != expected_campaign:
        raise ValueError("control transfer source campaign differs from its rebuilt family report")
    if expected_campaign["status_counts"]["completed"] != expected_campaign["status_counts"]["scheduled"]:
        raise ValueError("control transfer diagnostics require a complete source campaign")
    TypeAdapter(Digest).validate_python(campaign_report.get("ledger_digest"))
    _sealed(diagnostic_report)
    if (diagnostic_report.get("report_kind") != "cpu_control_diagnostic_evidence"
            or diagnostic_report.get("source_campaign_digest") != spec.digest
            or diagnostic_report.get("source_campaign_report_digest") != canonical_digest(campaign_report)
            or diagnostic_report.get("source_campaign_ledger_digest") != campaign_report["ledger_digest"]
            or diagnostic_report.get("runtime_digest") != campaign_report["runtime_digest"]):
        raise ValueError("control transfer diagnostic source identities changed")
    if "policy_summary" in diagnostic_report:
        raise ValueError("control diagnostic evidence must not publish pooled policy quality summaries")
    TypeAdapter(Digest).validate_python(diagnostic_report.get("original_diagnostic_report_digest"))
    for name, expected in (("gpu_execution_deferred", True), ("promotion_eligible", False),
                           ("benchmark_result_claimed", False), ("inferential_statistics_computed", False),
                           ("confirmation_rule_changed", False), ("model_execution_performed", False),
                           ("family_pooling_performed", False)):
        if type(diagnostic_report.get(name)) is not bool or diagnostic_report[name] is not expected:
            raise ValueError("control transfer diagnostic scope or evidence flags changed")
    TypeAdapter(Digest).validate_python(diagnostic_report.get("diagnostic_digest"))
    provenances = _provenances(spec, provenance_by_task)
    reference_ids = [f"{task.task_id}.{repeat.repeat_id}" for task in spec.tasks for repeat in spec.repeats]
    raw_references = diagnostic_report.get("reference_rows")
    raw_searches = diagnostic_report.get("search_rows")
    if not isinstance(raw_references, list) or not isinstance(raw_searches, list):
        raise ValueError("control transfer diagnostics must preserve both reference and search rosters")
    if any(not isinstance(row, dict) for row in [*raw_references, *raw_searches]):
        raise ValueError("control transfer diagnostic rows must be objects")
    references = {row.get("reference_id"): row for row in raw_references}
    sources = {row.run_id: row for row in _ordered_campaign_rows(spec, campaign_report["rows"])}
    searches = {row.get("run_id"): row for row in raw_searches}
    if (len(references) != len(raw_references) or set(references) != set(reference_ids)
            or len(searches) != len(raw_searches) or set(searches) != set(sources)):
        raise ValueError("control transfer diagnostics must preserve every scheduled reference and source run")
    ordered_references = []
    ordered_searches = []
    for task in spec.tasks:
        for repeat in spec.repeats:
            reference = references[f"{task.task_id}.{repeat.repeat_id}"]
            if (reference.get("task_id"), reference.get("repeat_id")) != (task.task_id, repeat.repeat_id):
                raise ValueError("control diagnostic reference identity differs from its family assignment")
            _reference_row(reference, provenances[task.task_id])
            ordered_references.append(reference)
            for policy in spec.policies:
                run_id = f"{task.task_id}.{repeat.repeat_id}.{policy}"
                _search_row(searches[run_id], sources[run_id], reference)
                ordered_searches.append(searches[run_id])
    if (diagnostic_report.get("reference_coverage") != _reference_coverage(ordered_references)
            or diagnostic_report.get("reference_costs") != _reference_costs(ordered_references)):
        raise ValueError("control diagnostic reference coverage or separate cost accounting changed")
    families = {}
    for family in _FAMILIES:
        task_ids = [task.task_id for task in spec.tasks if task.family == family]
        family_references = [row for row in ordered_references if row["task_id"] in task_ids]
        family_searches = [row for row in ordered_searches if row["task_id"] in task_ids]
        families[family] = {
            "task_ids": task_ids, "task_count": len(task_ids), "reference_coverage": _reference_coverage(family_references),
            "reference_costs": _reference_costs(family_references), "reference_opportunity": _opportunity(family_references),
            "policy_summary": {policy: _transfer_policy([row for row in family_searches if row["policy"] == policy], task_ids)
                               for policy in spec.policies},
            "expected_control_behaviors": {task_id: provenances[task_id].expected_behavior.to_dict() for task_id in task_ids},
            "calibration": _calibration(family_references, family_searches, provenances),
        }
    report = {
        "schema_version": 1, "report_kind": "cpu_control_transfer", "campaign_digest": spec.digest,
        "campaign_report_digest": canonical_digest(campaign_report),
        "diagnostic_digest": diagnostic_report["diagnostic_digest"],
        "diagnostic_report_digest": canonical_digest(diagnostic_report),
        "runtime_digest": campaign_report["runtime_digest"],
        "provenance_digests": {task_id: provenance.digest for task_id, provenance in provenances.items()},
        "model_execution_performed": False, "gpu_execution_deferred": True, "promotion_eligible": False,
        "benchmark_result_claimed": False, "inferential_statistics_computed": False,
        "family_pooling_performed": False, "confirmation_rule_changed": False, "family_results": families,
        "scope": "analytical-control-calibration-and-descriptive-transfer-no-pooled-winner-or-unselected-heldout-evaluation",
    }
    report["semantic_digest"] = canonical_digest(report)
    return report
