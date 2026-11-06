"""Read-only, missing-aware order/budget robustness summaries of verified CPU evidence.

The coordinator verifies raw child evidence before calling these pure helpers.
Offline construction predictions never become measured outcomes or guide search.
"""

from __future__ import annotations

import json
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign import METERS
from autocontext.weight_evolution.cpu_campaign_report import _CampaignRow, _costs, _counts
from autocontext.weight_evolution.cpu_control_report import _coverage
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticReference
from autocontext.weight_evolution.cpu_diagnostic_report import (
    _reference_metrics,
    _ReferenceRow,
    _RetainedReport,
    _score_identity,
)
from autocontext.weight_evolution.cpu_robustness_campaign_models import CpuRobustnessCampaignSpec
from autocontext.weight_evolution.cpu_robustness_control_models import CpuRobustnessControlCampaignSpec
from autocontext.weight_evolution.cpu_robustness_control_oracle import (
    expected_robustness_control_run,
    offline_robustness_oracle,
)
from autocontext.weight_evolution.cpu_robustness_control_tasks import robustness_control_task_payloads
from autocontext.weight_evolution.cpu_search_models import CpuSearchMechanism, CpuSearchSpec
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore
from autocontext.weight_evolution.session import _same

_STATUSES = ("completed", "failed", "uncertain", "blocked", "not-started")
_MECHANISMS: tuple[CpuSearchMechanism, ...] = ("C", "W", "D")
_DIMENSIONS = ("base_task_id", "order_id", "rounds", "panel", "family", "instance_index")


def _dimensions(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in _DIMENSIONS}


def _zero() -> dict[str, int]:
    return dict.fromkeys(METERS, 0)


def _rows(schedule: list[dict[str, Any]], prefix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(schedule) != 60 or len(prefix) > len(schedule):
        raise ValueError("robustness campaign report requires the fixed sixty-slot roster")
    rows = []
    for index, row in enumerate(schedule):
        entry = prefix[index] if index < len(prefix) else None
        if entry is not None:
            _same(entry["row"], row, "robustness campaign report prefix differs from the schedule")
        outcome = entry["outcome"] if entry else None
        status = outcome["status"] if outcome else "not-started"
        if status not in _STATUSES or (row["stage"] == "search" and status == "blocked"):
            raise ValueError("robustness campaign report has an invalid slot status")
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
    for index, row in enumerate(rows[:45]):
        source = {key: row[key] for key in ("run_id", "task_id", "repeat_id", "policy", "inference_seed",
                                          "order_seed", "study_digest", "reserved_capacity")}
        # The old descriptive helper calls unresolved work "pending". The
        # authoritative robustness campaign roster above retains its explicit uncertain state.
        source.update(status="pending" if row["status"] == "uncertain" else row["status"],
                      charged_usage=row["verified_usage"], error_type=None,
                      confirmation=None, confirmation_scores=None, audit_scores=None)
        if row["status"] == "completed":
            summary, details = row["summary"], prefix[index]["details"]
            if not isinstance(summary, dict) or not isinstance(details, dict):
                raise ValueError("completed robustness campaign search is missing its verified score projections")
            practice = _RetainedReport.model_validate_json(json.dumps(details["practice"]))
            confirmation = _RetainedReport.model_validate_json(json.dumps(details["confirmation"]))
            roles = [("practice", practice), ("confirmation", confirmation)]
            if details["audit"] is not None:
                audit = details["audit"]
                if audit.get("role") != "audit":
                    raise ValueError("robustness campaign audit projection changed its role")
                # The practice-reference parser deliberately excludes audit.
                # Validate its shared score structure after checking the real
                # role; this read-only copy never enters a reference or search.
                parsed_audit = _RetainedReport.model_validate_json(json.dumps({**audit, "role": "confirmation"}))
                roles.append(("confirmation", parsed_audit))
            for role, scores in roles:
                if (scores.role != role or scores.study_digest != row["study_digest"]
                        or scores.runtime_digest != runtime or scores.selection_digest != summary["selection_digest"]
                        or scores.policy.name != row["policy"] or scores.policy.order_seed != row["order_seed"]):
                    raise ValueError("robustness campaign score projection changed its role, selection or source identity")
            if canonical_digest(details["selection"]) != summary["selection_digest"]:
                raise ValueError("robustness campaign selection projection differs from its frozen selection")
            gate = summary["confirmation"]
            if gate["report_digest"] != canonical_digest(details["confirmation"]):
                raise ValueError("robustness campaign confirmation projection differs from its gate")
            source.update(summary_semantic_digest=summary["semantic_digest"], confirmation=gate["status"],
                          confirmation_scores={key: score.accuracy for key, score in confirmation.conditions.items()},
                          audit_scores=({key: score["accuracy"] for key, score in details["audit"]["conditions"].items()}
                                        if details["audit"] is not None else None))
        projected.append(source)
    return projected


def _references(rows: list[dict[str, Any]], prefix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references = []
    for index, row in enumerate(rows[45:], 45):
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
    for index, row in enumerate(rows[:45]):
        reference_id = f"{row['task_id']}.{row['repeat_id']}"
        reference = indexed[reference_id]
        ref_row = next(r for r in rows[45:] if r["run_id"] == reference_id)
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
                            raise ValueError("robustness campaign reference disagrees with the frozen selected practice behavior")
                    maximum = reference["metrics"]["mechanisms"][mechanism]["best_declared_practice_accuracy"]
                    gap = maximum - selected_score
                    if gap < 0:
                        raise ValueError("robustness campaign selected practice score exceeds its exhaustive reference")
            mechanisms[mechanism] = {"selected_option_id": selected, "selection_available": completed,
                "practice_baseline_accuracy": baseline, "practice_selected_accuracy": selected_score,
                "confirmation_gain": confirmation_gain, "reference_available": available,
                "best_declared_practice_accuracy": maximum, "selected_gap_to_best_practice": gap}
        results.append({"run_id": row["run_id"], "task_id": row["task_id"], "repeat_id": row["repeat_id"],
                        "policy": row["policy"], "family": row["family"], "status": row["status"],
                        "reference_status": reference["status"], "mechanisms": mechanisms})
    return results


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

    for index, row in enumerate(rows[:45]):
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
    for row in rows[45:]:
        oracle = expected[f"{row['run_id']}.adaptive-v1"]
        available = row["status"] == "completed"
        scores = row["summary"]["scores"] if available else {}
        for option_id, wanted in oracle["reference_scores"].items():
            check(row, f"reference-practice:{option_id}", _score(wanted),
                  _score(scores[option_id]) if option_id in scores else None, available and option_id in scores)
    return {"scope": "direct-logit-robustness-calibration-with-complete-practice-trajectories",
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
    for row in rows[:45]:
        available = row["status"] == "completed"
        spec = templates[row["task_id"]]
        cohort_size = sum(case.role == "practice" for case in spec.cases)
        histories = row["summary"].get("histories") if available else None
        if available and (not isinstance(histories, dict) or set(histories) != set(_MECHANISMS)):
            raise ValueError("completed robustness search lacks complete practice histories")
        for mechanism in _MECHANISMS:
            points: list[dict[str, Any]] | None = None
            if available:
                assert histories is not None
                values = histories[mechanism]
                if not isinstance(values, list) or len(values) != spec.rounds + 1:
                    raise ValueError("robustness history has the wrong bounded trial count")
                history = [CpuPracticeScore.model_validate(value) for value in values]
                option_ids = {option.option_id for option in spec.options_for(mechanism)}
                if (history[0].option_id is not None
                        or any(value.option_id not in option_ids for value in history[1:])
                        or len({value.option_id for value in history}) != len(history)
                        or any(value.total != cohort_size for value in history)):
                    raise ValueError("robustness history changes the baseline, options or complete practice cohort")
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
                    raise ValueError("robustness history does not select the frozen earliest best option")
            curves.append({"run_id": row["run_id"], "task_id": row["task_id"], "family": row["family"],
                "instance_index": row["instance_index"], "repeat_id": row["repeat_id"], "policy": row["policy"],
                "mechanism": mechanism, "status": row["status"], "available": available, "points": points,
                "cost_scope": "practice-baseline-and-proposal-probes-only",
                "additional_inference_performed": False})
    return curves


def _audit_routing(rows: list[dict[str, Any]], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    routing = []
    for row in rows[:45]:
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


def _provenances(control: CpuRobustnessControlCampaignSpec) -> dict[str, Any]:
    values = {}
    for task in control.tasks:
        _, provenance, _ = robustness_control_task_payloads(
            task_id=task.task_id, base_task_id=task.base_task_id, generation_seed=task.generation_seed,
            family=task.family, instance_index=task.instance_index, order_id=task.order_id,
            rounds=task.rounds, panel=task.panel)
        values[task.task_id] = provenance
    return values


def _expectations(control: CpuRobustnessControlCampaignSpec,
                  templates: dict[str, CpuSearchSpec]) -> dict[str, dict[str, Any]]:
    """Recompute measured-cell expectations without consulting observed outcomes."""
    provenances = _provenances(control)
    return {f"{task.task_id}.{repeat.repeat_id}.{policy}": expected_robustness_control_run(
                templates[task.task_id], provenances[task.task_id], inference_seed=repeat.inference_seed,
                policy=policy, order_seed=repeat.search_order_seed if policy == "seeded-random-v1" else 0)
            for task in control.tasks for repeat in control.repeats for policy in control.policies}


def _offline_expectations(control: CpuRobustnessControlCampaignSpec,
                          templates: dict[str, CpuSearchSpec]) -> dict[str, Any]:
    """Describe the complete finite oracle domain separately from measured cells."""
    offline = offline_robustness_oracle(templates, _provenances(control))
    invariant_names = ("score_tables_invariant", "random_order_invariant", "full_budget_zero_regret",
                       "budget_prefixes", "neutral_null", "all_passed")
    invariants = {name: offline["invariants"].get(name) for name in invariant_names}
    tasks = offline["tasks"]
    base_ids = {task.base_task_id for task in control.tasks}
    seeds = [{"policy": policy, "order_seed": seed} for policy in control.policies
             for seed in ((0, 1) if policy == "seeded-random-v1" else (0,))]
    verified = (offline["scope"] == "finite-construction-expectations-not-measured-evidence"
        and offline["order_count"] == 90 and offline["budgets"] == [1, 3, 6]
        and offline["policy_seeds"] == seeds and offline["trajectory_count"] == 6480
        and len(tasks) == len(base_ids) == 6 and {task["base_task_id"] for task in tasks} == base_ids
        and all(len(task["orders"]) == len({tuple(order) for order in task["orders"]}) == 90
            and len(task["trajectories"]) == 1080
            and len({(row["order_index"], row["rounds"], row["policy"], row["order_seed"])
                     for row in task["trajectories"]}) == 1080
            and set(task["invariants"]) == set(invariant_names)
            and all(value is True for value in task["invariants"].values()) for task in tasks)
        and set(offline["invariants"]) == set(invariant_names)
        and all(value is True for value in invariants.values()))
    summaries = [{"base_task_id": task["base_task_id"], "family": task["family"],
                  "instance_index": task["instance_index"], "legal_order_count": len(task["orders"]),
                  "trajectory_count": len(task["trajectories"]), "invariants": task["invariants"]}
                 for task in tasks]
    return {"scope": offline["scope"], "measured_evidence": False, "legal_order_count": offline["order_count"],
        "trajectory_count": offline["trajectory_count"], "budgets": offline["budgets"],
        "policy_seeds": offline["policy_seeds"], "base_task_count": len(tasks), "invariants": invariants,
        "verified": verified, "task_summaries": summaries, "offline_digest": canonical_digest(offline),
        "source_oracle_sha256": control.oracle_sha256, "used_for_policy_selection": False,
        "population_generalization_claimed": False}


def _robustness_checks(rows: list[dict[str, Any]], prefix: list[dict[str, Any]],
                       gaps: list[dict[str, Any]]) -> dict[str, Any]:
    """Require measured invariants without prescribing order-dependent win signs."""
    searches = {row["run_id"]: row for row in rows[:45]}
    references = {row["run_id"]: row for row in rows[45:]}
    gap_rows = {row["run_id"]: row for row in gaps}
    details = {row["run_id"]: entry["details"] for row, entry in zip(rows, prefix, strict=False)
               if row["stage"] == "search"}
    checks: list[dict[str, Any]] = []

    def check(kind: str, identity: str, run_ids: list[str], observed: Any, available: bool,
              expected: Any = True) -> None:
        checks.append({"check": kind, "identity": identity, "run_ids": run_ids, "expected": expected,
                       "observed": observed if available else None,
                       "matches": observed == expected if available else None})

    for base_id in sorted({row["base_task_id"] for row in rows}):
        refs = [row for row in references.values() if row["base_task_id"] == base_id]
        available = all(row["status"] == "completed" for row in refs)
        tables = [{key: _score(value) for key, value in row["summary"]["scores"].items()} for row in refs] if available else []
        check("reference-score-tables", base_id, [row["run_id"] for row in refs],
              all(table == tables[0] for table in tables) if tables else None, available)
        random_rows = [row for row in searches.values() if row["base_task_id"] == base_id
                       and row["panel"] == "order-comparison" and row["policy"] == "seeded-random-v1"]
        available = len(random_rows) == 2 and all(row["status"] == "completed" for row in random_rows)
        observed = None
        if available:
            first, second = (row["summary"] for row in random_rows)
            observed = (first["histories"] == second["histories"]
                        and first["selected_options"] == second["selected_options"])
        check("seeded-random-declaration-order", base_id, [row["run_id"] for row in random_rows], observed, available)

    for row in searches.values():
        run_id = row["run_id"]
        selected = gap_rows[run_id]["mechanisms"]
        if row["panel"] == "full-budget":
            available = all(value["selection_available"] and value["reference_available"] for value in selected.values())
            check("full-budget-zero-practice-regret", run_id, [run_id],
                  {mechanism: value["selected_gap_to_best_practice"] for mechanism, value in selected.items()},
                  available, {mechanism: 0.0 for mechanism in _MECHANISMS})
            shorter = next(value for value in searches.values() if value["base_task_id"] == row["base_task_id"]
                           and value["order_id"] == "canonical" and value["rounds"] == 3
                           and value["policy"] == row["policy"])
            available = row["status"] == shorter["status"] == "completed"
            observed = None
            if available:
                observed = all(shorter["summary"]["histories"][mechanism]
                               == row["summary"]["histories"][mechanism][:shorter["rounds"] + 1]
                               for mechanism in _MECHANISMS)
            check("short-full-budget-history-prefix", run_id, [shorter["run_id"], run_id], observed, available)
        if row["family"] == "neutral-v1":
            weight = selected["W"]
            available = weight["selection_available"] and weight["reference_available"]
            observed = None
            if available:
                history = row["summary"]["histories"]["W"]
                observed = (weight["practice_selected_accuracy"] == weight["practice_baseline_accuracy"]
                    and weight["confirmation_gain"] == 0.0 and weight["selected_gap_to_best_practice"] == 0.0
                    and weight["selected_option_id"] is None
                    and all(point["correct"] == history[0]["correct"] for point in history))
            check("neutral-weight-null", run_id, [run_id], observed, available)
        available = row["status"] == "completed"
        observed = None
        if available:
            retained = details[run_id]
            history = row["summary"]["histories"]
            observed = all(row["summary"]["selected_options"][mechanism] is None
                and all(point["correct"] == history[mechanism][0]["correct"] for point in history[mechanism])
                and all(_score(retained[role]["conditions"][mechanism]) == _score(retained[role]["conditions"]["A"])
                        for role in ("practice", "confirmation")) for mechanism in ("C", "D"))
        check("neutral-context-decoding-anchors", run_id, [run_id], observed, available)
    if len(checks) != 90 or len({(row["check"], row["identity"]) for row in checks}) != 90:
        raise ValueError("robustness report lost its fixed ninety invariant identities")
    return {"scope": "measured-invariants-with-order-dependent-policy-outcomes", "scheduled_checks": len(checks),
        "matches": sum(row["matches"] is True for row in checks),
        "mismatches": sum(row["matches"] is False for row in checks),
        "unavailable": sum(row["matches"] is None for row in checks), "checks": checks,
        "aggregate_adaptive_win_required": False, "references_used_for_selection": False,
        "option_id_renaming_invariance_claimed": False}


def _order_effects(control: CpuRobustnessControlCampaignSpec, expected: dict[str, dict[str, Any]],
                   gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two measured orders are not the full ninety-order offline population."""
    effects = []
    for base_id in sorted({task.base_task_id for task in control.tasks}):
        selected = [row for row in gaps if row["base_task_id"] == base_id and row["panel"] == "order-comparison"]
        wanted: dict[str, Any] = {}
        observed: dict[str, Any] = {}
        available = len(selected) == 6 and all(row["mechanisms"]["W"]["selection_available"]
            and row["mechanisms"]["W"]["reference_available"] for row in selected)
        for order_id in ("canonical", "reverse-lanes"):
            cells = {row["policy"]: row for row in selected if row["order_id"] == order_id}
            scores = {policy: expected[row["run_id"]]["scores"]["practice"]["W"]["accuracy"]
                      for policy, row in cells.items()}
            wanted[order_id] = {"practice_accuracy": scores,
                "practice_regret": {policy: expected[row["run_id"]]["reference_practice"]["W"]["best_accuracy"]
                                    - scores[policy] for policy, row in cells.items()},
                "adaptive_minus_fixed": scores["adaptive-v1"] - scores["fixed-order-v1"],
                "adaptive_minus_seeded_random": scores["adaptive-v1"] - scores["seeded-random-v1"]}
            if available:
                actual = {policy: row["mechanisms"]["W"]["practice_selected_accuracy"] for policy, row in cells.items()}
                observed[order_id] = {"practice_accuracy": actual,
                    "practice_regret": {policy: row["mechanisms"]["W"]["selected_gap_to_best_practice"]
                                        for policy, row in cells.items()},
                    "adaptive_minus_fixed": actual["adaptive-v1"] - actual["fixed-order-v1"],
                    "adaptive_minus_seeded_random": actual["adaptive-v1"] - actual["seeded-random-v1"]}
        task = next(task for task in control.tasks if task.base_task_id == base_id)
        effects.append({"base_task_id": base_id, "family": task.family, "instance_index": task.instance_index,
            "rounds": 3, "measured_order_count": 2, "expected": wanted, "observed": observed if available else None,
            "matches": wanted == observed if available else None, "aggregation_unit": "base-task-with-nested-orders",
            "all_legal_orders_measured": False, "order_invariant_advantage_required": False})
    return effects


def _budget_effects(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    effects = []
    for longer in (row for row in gaps if row["panel"] == "full-budget"):
        shorter = next(row for row in gaps if row["base_task_id"] == longer["base_task_id"]
                       and row["order_id"] == "canonical" and row["rounds"] == 3 and row["policy"] == longer["policy"])
        available = all(row["mechanisms"]["W"]["selection_available"] and row["mechanisms"]["W"]["reference_available"]
                        for row in (shorter, longer))
        effects.append({"base_task_id": longer["base_task_id"], "family": longer["family"],
            "instance_index": longer["instance_index"], "policy": longer["policy"], "order_id": "canonical",
            "short_run_id": shorter["run_id"], "full_run_id": longer["run_id"], "budgets": [3, 6],
            "available": available, "practice_accuracy": {
                "3": shorter["mechanisms"]["W"]["practice_selected_accuracy"],
                "6": longer["mechanisms"]["W"]["practice_selected_accuracy"]} if available else None,
            "practice_regret": {"3": shorter["mechanisms"]["W"]["selected_gap_to_best_practice"],
                                "6": longer["mechanisms"]["W"]["selected_gap_to_best_practice"]} if available else None,
            "scope": "first-declared-instance-per-family-not-a-general-budget-effect"})
    return effects


def _families(control: CpuRobustnessControlCampaignSpec, sources: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = [_CampaignRow.model_validate_json(json.dumps(row)) for row in sources]
    families = sorted({task.family for task in control.tasks})
    results: dict[str, Any] = {}
    for family in families:
        tasks = [task for task in control.tasks if task.family == family]
        variant_ids = [task.task_id for task in tasks]
        base_ids = sorted({task.base_task_id for task in tasks})
        family_rows = [row for row in ordered if row.task_id in variant_ids]
        results[family] = {"base_task_ids": base_ids, "task_count": len(base_ids), "variant_task_ids": variant_ids,
            "variant_count": len(variant_ids), "status_counts": _counts(family_rows), "costs": _costs(family_rows),
            "policy_coverage": {policy: _coverage([row for row in family_rows if row.policy == policy])
                                for policy in control.policies},
            "audit_coverage": {"scheduled": len(family_rows),
                "available": sum(row.audit_scores is not None for row in family_rows),
                "withheld_after_veto": sum(row.confirmation == "vetoed" for row in family_rows),
                "conditional_on_confirmation": True, "overall_quality_aggregate": None},
            "orders_and_budgets_are_nested_conditions": True, "variant_pooled_mean": None}
    return results


def build_cpu_robustness_campaign_report(spec: CpuRobustnessCampaignSpec, control: CpuRobustnessControlCampaignSpec,
                                        templates: dict[str, CpuSearchSpec], schedule: list[dict[str, Any]],
                                        prefix: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe one verified prefix; predictions cannot supply missing observations."""
    if (set(templates) != {task.task_id for task in control.tasks}
            or canonical_digest(schedule) != spec.schedule_digest or spec.control_campaign_digest != control.digest):
        raise ValueError("robustness campaign report changed its pinned task roster or schedule")
    rows = _rows(schedule, prefix)
    if [row["stage"] for row in rows] != ["search"] * 45 + ["reference"] * 15:
        raise ValueError("robustness campaign report changed its ordered search/reference stages")
    tasks = {task.task_id: task for task in control.tasks}
    for row in rows:
        task = tasks[row["task_id"]]
        if _dimensions(row) != {name: getattr(task, name) for name in _DIMENSIONS}:
            raise ValueError("robustness campaign row changed its base-task or nested variant identity")
    sources = _source_rows(rows, prefix, spec.native_runtime_digest)
    references = _references(rows, prefix)
    gaps = _gaps(rows, prefix, references)
    expected = _expectations(control, templates)
    calibration = _calibration(rows, prefix, expected)
    routing = _audit_routing(rows, expected)
    curves = _practice_curves(rows, templates)
    by_id = {row["run_id"]: row for row in rows}
    for collection in (references, gaps, curves):
        for item in collection:
            source_id = item.get("run_id", item.get("reference_id"))
            item.update(_dimensions(by_id[source_id]))
    robustness = _robustness_checks(rows, prefix, gaps)
    offline = _offline_expectations(control, templates)
    order_effects = _order_effects(control, expected, gaps)
    budget_effects = _budget_effects(gaps)
    processes = _process_coverage(rows, templates)
    completed = len(prefix) == 60 and all(row["status"] == "completed" and row["result_published"] for row in rows)
    counts = {stage: {status: sum(row["stage"] == stage and row["status"] == status for row in rows) for status in _STATUSES}
              for stage in ("search", "reference")}
    report = {"schema_version": 1, "report_kind": "measured-cpu-robustness-campaign", "campaign_digest": spec.digest,
        "rows": rows, "status_counts": counts, "search_budget": spec.search_budget.to_dict(),
        "reference_budget": spec.reference_budget.to_dict(),
        "charged_capacity": {stage: {meter: sum(row["charged_capacity"][meter] for row in rows if row["stage"] == stage)
            for meter in METERS} for stage in ("search", "reference")},
        "completed_verified_usage": {stage: {meter: sum((row["verified_usage"] or _zero())[meter]
            for row in rows if row["stage"] == stage) for meter in METERS} for stage in ("search", "reference")},
        "unknown_usage_rows": {stage: sum(row["stage"] == stage and row["status"] in {"failed", "uncertain"} for row in rows)
                               for stage in ("search", "reference")},
        "pending_results": sum(not entry["published"] for entry in prefix),
        "evidence_integrity_verified": True, "campaign_completed": completed,
        "acceptance_passed": completed and calibration["matches"] == calibration["scheduled_checks"] == 915
            and routing["verified"] == 45 and robustness["matches"] == robustness["scheduled_checks"] == 90
            and offline["verified"] and all(row["matches"] is True for row in order_effects)
            and processes["verified_rows"] == 60,
        "family_results": _families(control, sources), "base_task_count": 6, "measured_variant_count": 15,
        "reference_rows": references, "practice_gap_rows": gaps, "measurements": _measurements(rows),
        "calibration": calibration, "audit_routing": routing, "practice_prefix_curves": curves,
        "robustness_checks": robustness, "offline_expectations": offline,
        "order_effects": order_effects, "budget_effects": budget_effects, "process_coverage": processes,
        "ledger_digest": canonical_digest(prefix), "gpu_execution_deferred": True, "promotion_eligible": False,
        "benchmark_result_claimed": False, "historical_failure_cause_recovered": False,
        "family_pooling_performed": False, "variant_pooling_performed": False, "inferential_statistics_computed": False,
        "scope": "finite-order-budget-robustness-not-generalization-or-qwen-reproduction"}
    return {**report, "semantic_digest": canonical_digest(report)}
