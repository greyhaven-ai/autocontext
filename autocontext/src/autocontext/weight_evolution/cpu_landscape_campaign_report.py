"""Missing-aware, family-stratified summaries of verified measured landscape campaign evidence.

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
from autocontext.weight_evolution.cpu_campaign_report import _CampaignRow, _costs, _counts
from autocontext.weight_evolution.cpu_control_report import _coverage, _paired_description
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticReference
from autocontext.weight_evolution.cpu_diagnostic_report import (
    _reference_metrics,
    _ReferenceRow,
    _RetainedReport,
    _score_identity,
)
from autocontext.weight_evolution.cpu_landscape_campaign_models import CpuLandscapeCampaignSpec
from autocontext.weight_evolution.cpu_landscape_control_models import (
    LANDSCAPE_CONTROL_FAMILIES as CONTROL_FAMILIES,
)
from autocontext.weight_evolution.cpu_landscape_control_models import (
    CpuLandscapeControlCampaignSpec,
)
from autocontext.weight_evolution.cpu_landscape_control_oracle import expected_landscape_control_run
from autocontext.weight_evolution.cpu_landscape_control_tasks import landscape_control_task_payloads
from autocontext.weight_evolution.cpu_search_models import CpuSearchMechanism, CpuSearchSpec
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore
from autocontext.weight_evolution.session import _same

_STATUSES = ("completed", "failed", "uncertain", "blocked", "not-started")
_MECHANISMS: tuple[CpuSearchMechanism, ...] = ("C", "W", "D")


def _zero() -> dict[str, int]:
    return dict.fromkeys(METERS, 0)


def _rows(schedule: list[dict[str, Any]], prefix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(schedule) != 48 or len(prefix) > len(schedule):
        raise ValueError("landscape campaign report requires the fixed forty-eight-slot roster")
    rows = []
    for index, row in enumerate(schedule):
        entry = prefix[index] if index < len(prefix) else None
        if entry is not None:
            _same(entry["row"], row, "landscape campaign report prefix differs from the schedule")
        outcome = entry["outcome"] if entry else None
        status = outcome["status"] if outcome else "not-started"
        if status not in _STATUSES or (row["stage"] == "search" and status == "blocked"):
            raise ValueError("landscape campaign report has an invalid slot status")
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
    for index, row in enumerate(rows[:36]):
        source = {key: row[key] for key in ("run_id", "task_id", "repeat_id", "policy", "inference_seed",
                                          "order_seed", "study_digest", "reserved_capacity")}
        # The old descriptive helper calls unresolved work "pending". The
        # authoritative landscape campaign roster above retains its explicit uncertain state.
        source.update(status="pending" if row["status"] == "uncertain" else row["status"],
                      charged_usage=row["verified_usage"], error_type=None,
                      confirmation=None, confirmation_scores=None, audit_scores=None)
        if row["status"] == "completed":
            summary, details = row["summary"], prefix[index]["details"]
            if not isinstance(summary, dict) or not isinstance(details, dict):
                raise ValueError("completed landscape campaign search is missing its verified score projections")
            practice = _RetainedReport.model_validate_json(json.dumps(details["practice"]))
            confirmation = _RetainedReport.model_validate_json(json.dumps(details["confirmation"]))
            roles = [("practice", practice), ("confirmation", confirmation)]
            if details["audit"] is not None:
                audit = details["audit"]
                if audit.get("role") != "audit":
                    raise ValueError("landscape campaign audit projection changed its role")
                # The practice-reference parser deliberately excludes audit.
                # Validate its shared score structure after checking the real
                # role; this read-only copy never enters a reference or search.
                parsed_audit = _RetainedReport.model_validate_json(json.dumps({**audit, "role": "confirmation"}))
                roles.append(("confirmation", parsed_audit))
            for role, scores in roles:
                if (scores.role != role or scores.study_digest != row["study_digest"]
                        or scores.runtime_digest != runtime or scores.selection_digest != summary["selection_digest"]
                        or scores.policy.name != row["policy"] or scores.policy.order_seed != row["order_seed"]):
                    raise ValueError("landscape campaign score projection changed its role, selection or source identity")
            if canonical_digest(details["selection"]) != summary["selection_digest"]:
                raise ValueError("landscape campaign selection projection differs from its frozen selection")
            gate = summary["confirmation"]
            if gate["report_digest"] != canonical_digest(details["confirmation"]):
                raise ValueError("landscape campaign confirmation projection differs from its gate")
            source.update(summary_semantic_digest=summary["semantic_digest"], confirmation=gate["status"],
                          confirmation_scores={key: score.accuracy for key, score in confirmation.conditions.items()},
                          audit_scores=({key: score["accuracy"] for key, score in details["audit"]["conditions"].items()}
                                        if details["audit"] is not None else None))
        projected.append(source)
    return projected


def _references(rows: list[dict[str, Any]], prefix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references = []
    for index, row in enumerate(rows[36:], 36):
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
    for index, row in enumerate(rows[:36]):
        reference_id = f"{row['task_id']}.{row['repeat_id']}"
        reference = indexed[reference_id]
        ref_row = next(r for r in rows[36:] if r["run_id"] == reference_id)
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
                            raise ValueError("landscape campaign reference disagrees with the frozen selected practice behavior")
                    maximum = reference["metrics"]["mechanisms"][mechanism]["best_declared_practice_accuracy"]
                    gap = maximum - selected_score
                    if gap < 0:
                        raise ValueError("landscape campaign selected practice score exceeds its exhaustive reference")
            mechanisms[mechanism] = {"selected_option_id": selected, "selection_available": completed,
                "practice_baseline_accuracy": baseline, "practice_selected_accuracy": selected_score,
                "confirmation_gain": confirmation_gain, "reference_available": available,
                "best_declared_practice_accuracy": maximum, "selected_gap_to_best_practice": gap}
        results.append({"run_id": row["run_id"], "task_id": row["task_id"], "repeat_id": row["repeat_id"],
                        "policy": row["policy"], "family": row["family"], "status": row["status"],
                        "reference_status": reference["status"], "mechanisms": mechanisms})
    return results


def _family_gaps(control: CpuLandscapeControlCampaignSpec, gaps: list[dict[str, Any]]) -> dict[str, Any]:
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



def _expectations(control: CpuLandscapeControlCampaignSpec,
                  templates: dict[str, CpuSearchSpec]) -> dict[str, dict[str, Any]]:
    """Recompute the independent oracle; no observed score feeds this path."""
    expected = {}
    for task in control.tasks:
        _, provenance, _ = landscape_control_task_payloads(
            task_id=task.task_id, generation_seed=task.generation_seed, family=task.family,
            instance_index=task.instance_index)
        for repeat in control.repeats:
            for policy in control.policies:
                run_id = f"{task.task_id}.{repeat.repeat_id}.{policy}"
                expected[run_id] = expected_landscape_control_run(templates[task.task_id], provenance,
                    inference_seed=repeat.inference_seed, policy=policy,
                    order_seed=repeat.search_order_seed if policy == "seeded-random-v1" else 0)
    return expected


def _score(value: dict[str, Any]) -> dict[str, Any]:
    # Counts alone cannot establish agreement with the predeclared oracle.
    # Native request/result digests differ, but ordered case/output/correct
    # triples must match exactly, including for unselected practice options.
    cases = [{"case_id": case["case_id"],
              "prediction": case["prediction"] if "prediction" in case else case["output"],
              "correct": case["correct"]} for case in value["cases"]]
    return {"correct": sum(case["correct"] for case in cases), "total": len(cases),
            "accuracy": value["accuracy"], "cases": cases}


def _calibration(rows: list[dict[str, Any]], prefix: list[dict[str, Any]],
                 expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(row: dict[str, Any], name: str, wanted: Any, observed: Any, available: bool) -> None:
        checks.append({"run_id": row["run_id"], "family": row["family"], "check": name,
                       "expected": wanted, "observed": observed if available else None,
                       "matches": observed == wanted if available else None})

    for index, row in enumerate(rows[:36]):
        oracle = expected[row["run_id"]]
        available = row["status"] == "completed"
        summary = row["summary"] if available else {}
        details = prefix[index]["details"] if available else {}
        check(row, "selected-options", oracle["selected_options"], summary.get("selected_options"), available)
        check(row, "practice-histories", oracle["histories"], summary.get("histories"), available)
        roles = ("practice", "confirmation", "audit") if oracle["audit_status"] == "completed" else (
            "practice", "confirmation")
        for role in roles:
            scores = details.get(role) if available else None
            for condition in ("A", "C", "W", "CW", "D"):
                observed = _score(scores["conditions"][condition]) if isinstance(scores, dict) else None
                check(row, f"{role}:{condition}", _score(oracle["scores"][role][condition]),
                      observed, available and scores is not None)
        gate = summary.get("confirmation")
        observed_gate = ({key: gate[key] for key in ("outcomes", "status", "minimum_gain")}
                         if isinstance(gate, dict) else None)
        check(row, "joint-confirmation", oracle["confirmation"], observed_gate, available)
        check(row, "audit-status", oracle["audit_status"], summary.get("audit_status"), available)
    # References remain practice-only and become visible only after every search
    # has terminated. This checks all declared options, not just the winners.
    for row in rows[36:]:
        oracle = expected[f"{row['run_id']}.adaptive-v1"]
        available = row["status"] == "completed"
        scores = row["summary"]["scores"] if available else {}
        for option_id, wanted in oracle["reference_scores"].items():
            check(row, f"reference-practice:{option_id}", _score(wanted),
                  _score(scores[option_id]) if option_id in scores else None, available and option_id in scores)
    return {"scope": "direct-logit-landscape-calibration-with-complete-practice-trajectories",
            "scheduled_checks": len(checks), "checks": checks,
            "matches": sum(c["matches"] is True for c in checks),
            "mismatches": sum(c["matches"] is False for c in checks),
            "unavailable_or_nondesignated": sum(c["matches"] is None for c in checks),
            "unselected_heldout_options_evaluated": False,
            "expectations_used_for_policy_selection": False, "outcome_based_task_filtering_performed": False}


def _practice_curves(rows: list[dict[str, Any]], templates: dict[str, CpuSearchSpec]) -> list[dict[str, Any]]:
    """Describe the saved baseline/trial prefixes, without any additional probe.

    A point's case-evaluation count excludes restoration, composition and held-out
    work. The report's five-meter totals retain those costs separately. Repeats
    and option orders remain nested within each constructed task instance.
    """
    curves: list[dict[str, Any]] = []
    for row in rows[:36]:
        available = row["status"] == "completed"
        spec = templates[row["task_id"]]
        cohort_size = sum(case.role == "practice" for case in spec.cases)
        histories = row["summary"].get("histories") if available else None
        if available and (not isinstance(histories, dict) or set(histories) != set(_MECHANISMS)):
            raise ValueError("completed landscape search lacks complete practice histories")
        for mechanism in _MECHANISMS:
            points: list[dict[str, Any]] | None = None
            if available:
                assert histories is not None
                values = histories[mechanism]
                if not isinstance(values, list) or len(values) != spec.rounds + 1:
                    raise ValueError("landscape history has the wrong bounded trial count")
                history = [CpuPracticeScore.model_validate(value) for value in values]
                option_ids = {option.option_id for option in spec.options_for(mechanism)}
                if (history[0].option_id is not None
                        or any(value.option_id not in option_ids for value in history[1:])
                        or len({value.option_id for value in history}) != len(history)
                        or any(value.total != cohort_size for value in history)):
                    raise ValueError("landscape history changes the baseline, options or complete practice cohort")
                best = history[0]
                points = []
                for index, value in enumerate(history):
                    if value.correct > best.correct:
                        best = value
                    points.append({"trial": index, "option_id": value.option_id,
                        "correct": value.correct, "total": value.total,
                        "best_option_id": best.option_id, "best_correct": best.correct,
                        "best_accuracy": best.correct / best.total,
                        "proposals": index, "practice_case_evaluations": (index + 1) * cohort_size})
                if best.option_id != row["summary"]["selected_options"][mechanism]:
                    raise ValueError("landscape history does not select the frozen earliest best option")
            curves.append({"run_id": row["run_id"], "task_id": row["task_id"], "family": row["family"],
                "instance_index": row["instance_index"], "repeat_id": row["repeat_id"], "policy": row["policy"],
                "mechanism": mechanism, "status": row["status"], "available": available, "points": points,
                "cost_scope": "practice-baseline-and-proposal-probes-only",
                "additional_inference_performed": False})
    return curves


def _landscape_discrimination(control: CpuLandscapeControlCampaignSpec, expected: dict[str, dict[str, Any]],
                              gaps: list[dict[str, Any]]) -> dict[str, Any]:
    """Check the planted W wins, losses and nulls without requiring an aggregate win.

    Every declared task/repeat is retained. The sign requirements are fixed by
    family; measured behavior cannot select the comparison roster or oracle.
    Exhaustive scores are diagnostic only and never alter an earlier selection.
    """
    indexed = {row["run_id"]: row for row in gaps}
    pairs: list[dict[str, Any]] = []
    signs = {"informative-improvement-v1": 1, "misleading-improvement-v1": -1, "neutral-v1": 0}
    for task in control.tasks:
        for repeat in control.repeats:
            run_ids = {policy: f"{task.task_id}.{repeat.repeat_id}.{policy}" for policy in control.policies}
            oracles = {policy: expected[run_id] for policy, run_id in run_ids.items()}
            adaptive, fixed = (oracles[policy] for policy in ("adaptive-v1", "fixed-order-v1"))
            wanted = {
                "practice_adaptive_minus_fixed": adaptive["scores"]["practice"]["W"]["accuracy"]
                    - fixed["scores"]["practice"]["W"]["accuracy"],
                "confirmation_adaptive_minus_fixed": adaptive["scores"]["confirmation"]["W"]["accuracy"]
                    - fixed["scores"]["confirmation"]["W"]["accuracy"],
                "practice_gaps": {policy: oracle["reference_practice"]["W"]["best_accuracy"]
                    - oracle["scores"]["practice"]["W"]["accuracy"] for policy, oracle in oracles.items()},
            }
            sign = signs[task.family]
            effect = wanted["practice_adaptive_minus_fixed"]
            heldout_effect = wanted["confirmation_adaptive_minus_fixed"]
            design_valid = ((effect > 0 and heldout_effect > 0) if sign == 1 else
                            (effect < 0 and heldout_effect < 0) if sign == -1 else
                            effect == 0 and heldout_effect == 0 and all(v == 0 for v in wanted["practice_gaps"].values()))
            if sign == 1:
                design_valid = design_valid and wanted["practice_gaps"]["adaptive-v1"] == 0 \
                    and wanted["practice_gaps"]["fixed-order-v1"] > 0
            elif sign == -1:
                design_valid = design_valid and wanted["practice_gaps"]["adaptive-v1"] > 0 \
                    and wanted["practice_gaps"]["fixed-order-v1"] == 0
            observed_rows = {policy: indexed[run_id]["mechanisms"]["W"] for policy, run_id in run_ids.items()}
            available = all(row["selection_available"] and row["reference_available"] for row in observed_rows.values())
            observed = None
            if available:
                a, f = (observed_rows[policy] for policy in ("adaptive-v1", "fixed-order-v1"))
                observed = {
                    "practice_adaptive_minus_fixed": a["practice_selected_accuracy"] - f["practice_selected_accuracy"],
                    # All policies share the same baseline within the paired task/repeat.
                    "confirmation_adaptive_minus_fixed": a["confirmation_gain"] - f["confirmation_gain"],
                    "practice_gaps": {policy: value["selected_gap_to_best_practice"]
                                      for policy, value in observed_rows.items()},
                }
            pairs.append({"task_id": task.task_id, "family": task.family, "instance_index": task.instance_index,
                "repeat_id": repeat.repeat_id, "mechanism": "W", "expected_sign": sign,
                "declared_design_valid": design_valid, "expected": wanted, "observed": observed,
                "matches": observed == wanted if available else None})
    return {"scope": "constructed-weight-search-controls-not-generalization-or-aggregate-adaptive-superiority",
        "scheduled_pairs": len(pairs), "declared_design_valid": len(pairs) == 12
            and all(pair["declared_design_valid"] for pair in pairs),
        "verified_pairs": sum(pair["matches"] is True for pair in pairs),
        "mismatches": sum(pair["matches"] is False for pair in pairs),
        "unavailable": sum(pair["matches"] is None for pair in pairs),
        "expected_adaptive_wins": sum(pair["expected_sign"] == 1 for pair in pairs),
        "expected_adaptive_losses": sum(pair["expected_sign"] == -1 for pair in pairs),
        "expected_null_pairs": sum(pair["expected_sign"] == 0 for pair in pairs),
        "expected_nonzero_adaptive_or_fixed_regrets": sum(
            pair["expected"]["practice_gaps"][policy] > 0 for pair in pairs
            for policy in ("adaptive-v1", "fixed-order-v1")),
        "pairs": pairs, "aggregation_unit": "constructed-task-instance-with-nested-repeats",
        "aggregate_adaptive_win_required": False, "references_used_for_selection": False}


def _audit_routing(rows: list[dict[str, Any]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    routing = []
    for row in rows[:36]:
        wanted = expected[row["run_id"]]["audit_status"]
        available = row["status"] == "completed"
        audit = [value for value in (row["observation_coverage"] or []) if value["phase"] == "audit"]
        processes = [value["process_observation"] for value in audit if value["process_observation"] is not None]
        actual = row["summary"]["audit_status"] if available else None
        verified = (actual == wanted and (
            len(audit) == 5 and all(value["kind"] == "inference" and value["outcome_present"] for value in audit)
            and len(processes) == 5 and all(p["dispatched"] is True and p["leader_reaped"] is True for p in processes)
            if wanted == "completed" else actual == wanted and not audit)) if available else None
        routing.append({"run_id": row["run_id"], "family": row["family"], "expected_audit_status": wanted,
                        "audit_status": actual, "audit_claims": len(audit), "observed_processes": len(processes),
                        "reaped_processes": sum(p["leader_reaped"] is True for p in processes), "matches": verified})
    return {"scheduled": len(routing), "verified": sum(r["matches"] is True for r in routing),
            "unavailable": sum(r["matches"] is None for r in routing),
            "mismatches": sum(r["matches"] is False for r in routing),
            "positive_count": sum(r["expected_audit_status"] == "completed" for r in routing),
            "veto_count": sum(r["expected_audit_status"] == "withheld" for r in routing), "rows": routing}


def _process_coverage(rows: list[dict[str, Any]], templates: dict[str, CpuSearchSpec]) -> dict[str, Any]:
    """All-veto controls still require actual CPU observations for every probe."""
    coverage = []
    phases = ("practice", "restoration", "confirmation", "audit", "reference")
    for row in rows:
        spec = templates[row["task_id"]]
        expected = dict.fromkeys(phases, 0)
        if row["stage"] == "search":
            expected.update(practice=3 * (spec.rounds + 1) + 1, restoration=3, confirmation=5)
            if row["summary"] is not None and row["summary"].get("audit_status") == "completed":
                expected["audit"] = 5
        else:
            expected["reference"] = 1 + len(spec.options)
        observations = [o for o in (row["observation_coverage"] or []) if o["kind"] == "inference"]
        observed = {phase: sum(o["phase"] == phase for o in observations) for phase in phases}
        available = row["status"] == "completed"
        matches = None
        if available:
            matches = (observed == expected and sum(observed.values()) == len(observations)
                and all(o["outcome_present"] is True and isinstance(o["process_observation"], dict)
                        and o["process_observation"].get("dispatched") is True
                        and o["process_observation"].get("leader_reaped") is True for o in observations))
        coverage.append({"run_id": row["run_id"], "stage": row["stage"], "status": row["status"],
            "expected_phase_batches": expected, "observed_phase_batches": observed,
            "reaped_inference_batches": sum(isinstance(o["process_observation"], dict)
                and o["process_observation"].get("leader_reaped") is True for o in observations),
            "matches": matches})
    return {"scheduled_rows": len(coverage), "verified_rows": sum(c["matches"] is True for c in coverage),
        "mismatches": sum(c["matches"] is False for c in coverage),
        "unavailable": sum(c["matches"] is None for c in coverage),
        "expected_inference_batches": sum(sum(c["expected_phase_batches"].values()) for c in coverage),
        "observed_inference_batches": sum(sum(c["observed_phase_batches"].values()) for c in coverage),
        "reaped_inference_batches": sum(c["reaped_inference_batches"] for c in coverage),
        "rows": coverage, "scope": "completed-batch-dispatch-outcome-and-leader-reaping"}


def _families(control: CpuLandscapeControlCampaignSpec, sources: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = [_CampaignRow.model_validate_json(json.dumps(row)) for row in sources]
    results: dict[str, Any] = {}
    for family in CONTROL_FAMILIES:
        task_ids = [task.task_id for task in control.tasks if task.family == family]
        family_rows = [row for row in ordered if row.task_id in task_ids]
        results[family] = {"task_ids": task_ids, "task_count": len(task_ids), "status_counts": _counts(family_rows),
            "costs": _costs(family_rows),
            "policy_coverage": {policy: _coverage([row for row in family_rows if row.policy == policy])
                                for policy in control.policies},
            "confirmation_contrasts": {f"adaptive-minus-{policy}:{condition}": _paired_description(
                family_rows, task_ids=task_ids, repeat_ids=[r.repeat_id for r in control.repeats],
                control=policy, condition=condition) for policy in control.policies[1:]
                for condition in ("C", "W", "CW", "D")},
            "audit_coverage": {"scheduled": len(family_rows),
                "available": sum(row.audit_scores is not None for row in family_rows),
                "withheld_after_veto": sum(row.confirmation == "vetoed" for row in family_rows),
                "conditional_on_confirmation": True, "overall_quality_aggregate": None}}
    return results


def build_cpu_landscape_campaign_report(spec: CpuLandscapeCampaignSpec, control: CpuLandscapeControlCampaignSpec,
                                    templates: dict[str, CpuSearchSpec], schedule: list[dict[str, Any]],
                                    prefix: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe a verified prefix without selecting tasks, policies or held-out options."""
    if set(templates) != {task.task_id for task in control.tasks} or canonical_digest(schedule) != spec.schedule_digest:
        raise ValueError("landscape campaign report changed its pinned task roster or schedule")
    rows = _rows(schedule, prefix)
    sources = _source_rows(rows, prefix, spec.native_runtime_digest)
    references = _references(rows, prefix)
    gaps = _gaps(rows, prefix, references)
    expected = _expectations(control, templates)
    calibration = _calibration(rows, prefix, expected)
    routing = _audit_routing(rows, expected)
    curves = _practice_curves(rows, templates)
    discrimination = _landscape_discrimination(control, expected, gaps)
    processes = _process_coverage(rows, templates)
    completed = len(prefix) == 48 and all(r["status"] == "completed" and r["result_published"] for r in rows)
    counts = {stage: {status: sum(r["stage"] == stage and r["status"] == status for r in rows) for status in _STATUSES}
              for stage in ("search", "reference")}
    report = {"schema_version": 1, "report_kind": "measured-cpu-landscape-campaign", "campaign_digest": spec.digest,
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
        "acceptance_passed": completed and calibration["matches"] == calibration["scheduled_checks"]
            and routing["verified"] == 36 and discrimination["declared_design_valid"]
            and discrimination["verified_pairs"] == 12 and processes["verified_rows"] == 48,
        "family_results": _families(control, sources),
        "reference_rows": references, "practice_gap_rows": gaps, "family_practice_gaps": _family_gaps(control, gaps),
        "measurements": _measurements(rows), "calibration": calibration, "audit_routing": routing,
        "practice_prefix_curves": curves, "landscape_discrimination": discrimination,
        "process_coverage": processes,
        "ledger_digest": canonical_digest(prefix), "gpu_execution_deferred": True, "promotion_eligible": False,
        "benchmark_result_claimed": False, "historical_failure_cause_recovered": False,
        "family_pooling_performed": False, "inferential_statistics_computed": False,
        "scope": "constructed-search-landscapes-not-generalization-or-qwen-reproduction"}
    return {**report, "semantic_digest": canonical_digest(report)}
