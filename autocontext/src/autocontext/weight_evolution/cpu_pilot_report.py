"""Missing-aware, family-stratified summaries of verified measured pilot evidence.

The coordinator verifies every child and its immutable projections first. These
pure functions reuse the existing control comparison and practice diagnostics;
they neither execute models nor infer observations for missing operations.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign import METERS
from autocontext.weight_evolution.cpu_control_models import CONTROL_FAMILIES, CpuControlCampaignSpec
from autocontext.weight_evolution.cpu_control_report import build_cpu_control_report
from autocontext.weight_evolution.cpu_control_tasks import _task_payloads
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticReference
from autocontext.weight_evolution.cpu_diagnostic_report import (
    _reference_metrics,
    _ReferenceRow,
    _RetainedReport,
    _score_identity,
)
from autocontext.weight_evolution.cpu_pilot_models import CpuPilotSpec
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.session import _same

_STATUSES = ("completed", "failed", "uncertain", "blocked", "not-started")
_MECHANISMS = ("C", "W", "D")


def _zero() -> dict[str, int]:
    return dict.fromkeys(METERS, 0)


def _rows(schedule: list[dict[str, Any]], prefix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(schedule) != 96 or len(prefix) > len(schedule):
        raise ValueError("pilot report requires the fixed ninety-six-slot roster")
    rows = []
    for index, row in enumerate(schedule):
        entry = prefix[index] if index < len(prefix) else None
        if entry is not None:
            _same(entry["row"], row, "pilot report prefix differs from the schedule")
        outcome = entry["outcome"] if entry else None
        status = outcome["status"] if outcome else "not-started"
        if status not in _STATUSES or (row["stage"] == "search" and status == "blocked"):
            raise ValueError("pilot report has an invalid slot status")
        rows.append({**row, "status": status, "result_published": entry["published"] if entry else False,
            "child_evidence_path": f"measured-v2/{'searches' if row['stage'] == 'search' else 'references'}/{row['run_id']}",
            "charged_capacity": entry["claim"]["claimed_capacity"] if entry else _zero(),
            "verified_usage": entry["result"]["verified_usage"] if entry else None,
            "observation_coverage": outcome["observation_coverage"] if outcome else None,
            "parent_error_type": outcome["parent_error_type"] if outcome else None,
            "summary": outcome["summary"] if outcome else None})
    return rows


def _source_rows(rows: list[dict[str, Any]], prefix: list[dict[str, Any]], runtime: str) -> list[dict[str, Any]]:
    projected = []
    for index, row in enumerate(rows[:72]):
        source = {key: row[key] for key in ("run_id", "task_id", "repeat_id", "policy", "inference_seed",
                                          "order_seed", "study_digest", "reserved_capacity")}
        # The old descriptive helper calls unresolved work "pending". The
        # authoritative pilot roster above retains its explicit uncertain state.
        source.update(status="pending" if row["status"] == "uncertain" else row["status"],
                      charged_usage=row["verified_usage"], error_type=None,
                      confirmation=None, confirmation_scores=None, audit_scores=None)
        if row["status"] == "completed":
            summary, details = row["summary"], prefix[index]["details"]
            if not isinstance(summary, dict) or not isinstance(details, dict):
                raise ValueError("completed pilot search is missing its verified score projections")
            practice = _RetainedReport.model_validate_json(json.dumps(details["practice"]))
            confirmation = _RetainedReport.model_validate_json(json.dumps(details["confirmation"]))
            for role, scores in (("practice", practice), ("confirmation", confirmation)):
                if (scores.role != role or scores.study_digest != row["study_digest"]
                        or scores.runtime_digest != runtime or scores.selection_digest != summary["selection_digest"]
                        or scores.policy.name != row["policy"] or scores.policy.order_seed != row["order_seed"]):
                    raise ValueError("pilot score projection changed its role, selection or source identity")
            if canonical_digest(details["selection"]) != summary["selection_digest"]:
                raise ValueError("pilot selection projection differs from its frozen selection")
            gate = summary["confirmation"]
            if gate["report_digest"] != canonical_digest(details["confirmation"]):
                raise ValueError("pilot confirmation projection differs from its gate")
            source.update(summary_semantic_digest=summary["semantic_digest"], confirmation=gate["status"],
                          confirmation_scores={key: score.accuracy for key, score in confirmation.conditions.items()},
                          audit_scores=({key: score["accuracy"] for key, score in details["audit"]["conditions"].items()}
                                        if details["audit"] is not None else None))
        projected.append(source)
    return projected


def _references(rows: list[dict[str, Any]], prefix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references = []
    for index, row in enumerate(rows[72:], 72):
        metrics = None
        if row["status"] == "completed":
            reference = CpuDiagnosticReference.model_validate_json(json.dumps(prefix[index]["reference"]))
            parsed = _ReferenceRow.model_validate_json(json.dumps({
                "reference_id": row["run_id"], "task_id": row["task_id"], "repeat_id": row["repeat_id"],
                "status": "completed", "reserved_capacity": row["reserved_capacity"],
                "charged_usage": row["verified_usage"], "scores": row["summary"]["scores"], "error_type": None}))
            metrics = _reference_metrics(reference, parsed)
        references.append({"reference_id": row["run_id"], "task_id": row["task_id"],
                           "repeat_id": row["repeat_id"], "family": row["family"],
                           "status": row["status"], "metrics": metrics})
    return references


def _gaps(rows: list[dict[str, Any]], prefix: list[dict[str, Any]], references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {row["reference_id"]: row for row in references}
    results = []
    for index, row in enumerate(rows[:72]):
        reference_id = f"{row['task_id']}.{row['repeat_id']}"
        reference = indexed[reference_id]
        ref_row = next(r for r in rows[72:] if r["run_id"] == reference_id)
        mechanisms: dict[str, Any] = {}
        completed = row["status"] == "completed"
        available = completed and reference["status"] == "completed"
        for mechanism in _MECHANISMS:
            selected = row["summary"]["selected_options"][mechanism] if completed else None
            selected_score = baseline = confirmation_gain = maximum = gap = None
            if completed:
                details = prefix[index]["details"]
                practice = _RetainedReport.model_validate_json(json.dumps(details["practice"]))
                confirmation = _RetainedReport.model_validate_json(json.dumps(details["confirmation"]))
                baseline = practice.conditions["A"].accuracy
                selected_score = practice.conditions[mechanism].accuracy
                confirmation_gain = confirmation.conditions[mechanism].accuracy - confirmation.conditions["A"].accuracy
                if available:
                    parsed = _ReferenceRow.model_validate_json(json.dumps({
                        "reference_id": reference_id, "task_id": row["task_id"], "repeat_id": row["repeat_id"],
                        "status": "completed", "reserved_capacity": ref_row["reserved_capacity"],
                        "charged_usage": ref_row["verified_usage"], "scores": ref_row["summary"]["scores"]}))
                    assert parsed.scores is not None
                    for condition, option_id in (("A", "A"), (mechanism, selected or "A")):
                        if _score_identity(practice.conditions[condition]) != _score_identity(parsed.scores[option_id]):
                            raise ValueError("pilot reference disagrees with the frozen selected practice behavior")
                    maximum = reference["metrics"]["mechanisms"][mechanism]["best_declared_practice_accuracy"]
                    gap = maximum - selected_score
                    if gap < 0:
                        raise ValueError("pilot selected practice score exceeds its exhaustive reference")
            mechanisms[mechanism] = {"selected_option_id": selected, "selection_available": completed,
                "practice_baseline_accuracy": baseline, "practice_selected_accuracy": selected_score,
                "confirmation_gain": confirmation_gain, "reference_available": available,
                "best_declared_practice_accuracy": maximum, "selected_gap_to_best_practice": gap}
        results.append({"run_id": row["run_id"], "task_id": row["task_id"], "repeat_id": row["repeat_id"],
                        "policy": row["policy"], "family": row["family"], "status": row["status"],
                        "reference_status": reference["status"], "mechanisms": mechanisms})
    return results


def _calibration(control: CpuControlCampaignSpec, rows: list[dict[str, Any]], prefix: list[dict[str, Any]],
                 references: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {task.task_id: _task_payloads(task_id=task.task_id, generation_seed=task.generation_seed,
        family=task.family, cases_per_role=8)[1].expected_behavior for task in control.tasks}
    checks = []
    for row in references:
        behavior = expected[row["task_id"]]
        metrics = row["metrics"]
        options = ({option["option_id"]: option["accuracy"] for option in metrics["mechanisms"]["W"]["option_scores"]}
                   if metrics is not None else {})
        values = [("baseline-practice", behavior.baseline_accuracy["practice"],
                   metrics["baseline_score"]["accuracy"] if metrics else None)]
        values.extend((f"weight-practice:{option_id}", behavior.designated_weight_accuracy["practice"], options.get(option_id))
                      for option_id in behavior.designated_weight_option_ids)
        for name, wanted, observed in values:
            checks.append({"run_id": row["reference_id"], "family": row["family"], "check": name,
                           "expected": wanted, "observed": observed,
                           "matches": observed == wanted if observed is not None else None})
    for index, row in enumerate(rows[:72]):
        behavior = expected[row["task_id"]]
        selected_observed: float | None = None
        selected_expected: float | None = None
        kind = None
        if row["status"] == "completed":
            selected = row["summary"]["selected_options"]["W"]
            kind = "baseline" if selected is None else "designated" if selected in behavior.designated_weight_option_ids else None
            selected_expected = (behavior.baseline_accuracy["confirmation"] if kind == "baseline" else
                      behavior.designated_weight_accuracy["confirmation"] if kind == "designated" else None)
            selected_observed = prefix[index]["details"]["confirmation"]["conditions"]["W"]["accuracy"]
        checks.append({"run_id": row["run_id"], "family": row["family"], "check": "selected-weight-confirmation",
                       "control_kind": kind, "expected": selected_expected, "observed": selected_observed,
                       "matches": selected_observed == selected_expected
                           if selected_observed is not None and selected_expected is not None else None})
    return {"scope": "analytical-W-calibration-not-joint-gate", "scheduled_checks": 144, "checks": checks,
            "matches": sum(c["matches"] is True for c in checks),
            "mismatches": sum(c["matches"] is False for c in checks),
            "unavailable_or_nondesignated": sum(c["matches"] is None for c in checks),
            "unselected_heldout_options_evaluated": False}


def _family_gaps(control: CpuControlCampaignSpec, gaps: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for family in CONTROL_FAMILIES:
        result[family] = {}
        task_ids = [task.task_id for task in control.tasks if task.family == family]
        for policy in control.policies:
            selected = [r for r in gaps if r["family"] == family and r["policy"] == policy]
            mechanisms = {}
            for mechanism in _MECHANISMS:
                tasks: list[dict[str, Any]] = []
                for task_id in task_ids:
                    values = [r["mechanisms"][mechanism]["selected_gap_to_best_practice"]
                              for r in selected if r["task_id"] == task_id]
                    complete = len(values) == len(control.repeats) and all(v is not None for v in values)
                    tasks.append({"task_id": task_id, "scheduled_repeats": len(control.repeats),
                                  "available_repeats": sum(v is not None for v in values),
                                  "mean": statistics.fmean(values) if complete else None})
                complete = all(t["mean"] is not None for t in tasks)
                mechanisms[mechanism] = {"task_details": tasks,
                    "task_mean_gap": statistics.fmean(t["mean"] for t in tasks) if complete else None,
                    "available": complete, "aggregation_unit": "task-mean-over-nested-repeats"}
            result[family][policy] = mechanisms
    return result


def _measurements(rows: list[dict[str, Any]]) -> dict[str, Any]:
    phases = {}
    for phase in ("practice", "restoration", "confirmation", "audit", "reference"):
        observations = [value for row in rows for value in (row["observation_coverage"] or [])
                        if value["kind"] == "inference" and value["phase"] == phase]
        processes = [value["process_observation"] for value in observations if value["process_observation"] is not None]
        durations = [p["total_seconds"] for p in processes if p["total_seconds"] is not None]
        rss = [p["peak_rss_bytes"] for p in processes if p["peak_rss_bytes"] is not None]
        phases[phase] = {"observed_inference_claims": len(observations), "process_observations": len(processes),
            "missing_process_observations": len(observations) - len(processes),
            "saved_outcomes": sum(value["outcome_present"] for value in observations),
            "dispatched_observations": sum(p["dispatched"] for p in processes),
            "reaped_observations": sum(p["leader_reaped"] is True for p in processes),
            "duration_observations": len(durations), "observed_duration_seconds": sum(durations) if durations else None,
            "rss_observations": len(rss), "max_observed_rss_bytes": max(rss) if rss else None}
    return {"phases": phases, "scope": "retained-process-observations-not-complete-campaign-cost-or-root-cause",
            "unobserved_resources_imputed": False, "linux_address_space_limit_is_not_rss_measurement": True}


def build_cpu_pilot_report(spec: CpuPilotSpec, control: CpuControlCampaignSpec,
                          templates: dict[str, CpuSearchSpec], schedule: list[dict[str, Any]],
                          prefix: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize an independently verified prefix, retaining all missing slots."""
    if set(templates) != {task.task_id for task in control.tasks} or canonical_digest(schedule) != spec.schedule_digest:
        raise ValueError("pilot report changed its pinned task roster or schedule")
    rows = _rows(schedule, prefix)
    sources = _source_rows(rows, prefix, spec.native_runtime_digest)
    control_report = build_cpu_control_report(control, sources, runtime_digest=spec.native_runtime_digest)
    references = _references(rows, prefix)
    gaps = _gaps(rows, prefix, references)
    calibration = _calibration(control, rows, prefix, references)
    completed = len(prefix) == 96 and all(r["status"] == "completed" and r["result_published"] for r in rows)
    counts = {stage: {status: sum(r["stage"] == stage and r["status"] == status for r in rows) for status in _STATUSES}
              for stage in ("search", "reference")}
    report = {"schema_version": 1, "report_kind": "measured-cpu-pilot", "campaign_digest": spec.digest,
        "rows": rows, "status_counts": counts, "search_budget": spec.search_budget.to_dict(),
        "reference_budget": spec.reference_budget.to_dict(),
        "charged_capacity": {stage: {m: sum(r["charged_capacity"][m] for r in rows if r["stage"] == stage)
            for m in METERS} for stage in ("search", "reference")},
        "completed_verified_usage": {stage: {m: sum((r["verified_usage"] or _zero())[m] for r in rows if r["stage"] == stage)
            for m in METERS} for stage in ("search", "reference")},
        "unknown_usage_rows": {stage: sum(r["stage"] == stage and r["status"] in {"failed", "uncertain"} for r in rows)
                               for stage in ("search", "reference")},
        "pending_results": sum(not e["published"] for e in prefix),
        "evidence_integrity_verified": True, "campaign_completed": completed,
        "acceptance_passed": completed and calibration["matches"] == 144,
        "family_results": control_report["family_results"],
        "descriptive_control_projection_digest": control_report["semantic_digest"],
        "reference_rows": references, "practice_gap_rows": gaps, "family_practice_gaps": _family_gaps(control, gaps),
        "measurements": _measurements(rows),
        "calibration": calibration, "ledger_digest": canonical_digest(prefix),
        "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
        "historical_failure_cause_recovered": False, "family_pooling_performed": False,
        "inferential_statistics_computed": False,
        "scope": "measured-constructed-cpu-pilot-not-adaptive-advantage-or-qwen-reproduction"}
    return {**report, "semantic_digest": canonical_digest(report)}
