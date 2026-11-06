"""Synthetic verified projections test reporting; these are not worker evidence."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_robustness_campaign import _plan
from autocontext.weight_evolution.cpu_robustness_campaign_bundle import (
    build_cpu_robustness_campaign_bundle,
    load_cpu_robustness_campaign_bundle,
    robustness_campaign_schedule,
)
from autocontext.weight_evolution.cpu_robustness_campaign_report import _expectations, build_cpu_robustness_campaign_report
from autocontext.weight_evolution.cpu_robustness_control_tasks import robustness_control_schedule
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
    bundle = tmp_path_factory.mktemp("robustness-campaign-report") / "bundle"
    build_cpu_robustness_campaign_bundle(bundle)
    spec, control, templates, _ = load_cpu_robustness_campaign_bundle(bundle)
    schedule = robustness_campaign_schedule(control, templates)
    children = {row["run_id"]: child for row, child in robustness_control_schedule(control, templates)}
    expected = _expectations(control, templates)
    prefix: list[dict[str, Any]] = []
    ref_scores = {}
    for row in schedule[:45]:
        child, oracle = children[row["run_id"]], expected[row["run_id"]]
        selection = {"synthetic_selection": row["run_id"]}
        reports = {role: payload(role, {key: retained_score(value) for key, value in scores.items()}, child,
                   canonical_digest(selection), spec.native_runtime_digest)
                   for role, scores in oracle["scores"].items()}
        summary = {"selection_digest": canonical_digest(selection), "semantic_digest": canonical_digest(row),
                   "selected_options": oracle["selected_options"], "histories": copy.deepcopy(oracle["histories"]),
                   "audit_status": oracle["audit_status"],
                   "confirmation": {**oracle["confirmation"], "report_digest": canonical_digest(reports["confirmation"])}}
        process_rows = observations({"practice": 3 * (child.rounds + 1) + 1, "restoration": 3, "confirmation": 5})
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
    for row, reference in zip(schedule[45:], plan[0].references, strict=True):
        prefix.append({"row": row, "claim": {"claimed_capacity": row["reserved_capacity"]},
            "result": {"verified_usage": row["reserved_capacity"]}, "published": True, "outcome_published": True,
            "outcome": {"status": "completed", "summary": {"scores": ref_scores[row["run_id"]]},
                        "observation_coverage": observations({"reference": 19}), "parent_error_type": None},
            "details": None, "reference": reference.to_dict()})
    return spec, control, templates, schedule, prefix


def report(context: tuple[Any, ...], prefix: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return build_cpu_robustness_campaign_report(*context[:4], context[4] if prefix is None else prefix)


def test_full_report_requires_observed_calibrations_invariants_and_processes(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    assert result["evidence_integrity_verified"] and result["campaign_completed"] and result["acceptance_passed"]
    assert result["status_counts"]["search"]["completed"] == 45
    assert result["status_counts"]["reference"]["completed"] == 15
    assert result["calibration"]["matches"] == result["calibration"]["scheduled_checks"] == 915
    assert result["calibration"]["mismatches"] == result["calibration"]["unavailable_or_nondesignated"] == 0
    assert result["audit_routing"]["verified"] == result["audit_routing"]["veto_count"] == 45
    assert result["audit_routing"]["positive_count"] == 0
    checks = result["robustness_checks"]
    assert checks["matches"] == checks["scheduled_checks"] == 90
    assert checks["mismatches"] == checks["unavailable"] == 0
    assert not checks["aggregate_adaptive_win_required"] and not checks["references_used_for_selection"]
    assert not checks["option_id_renaming_invariance_claimed"]
    assert len({(row["check"], row["identity"]) for row in checks["checks"]}) == 90
    expected_counts = {"reference-score-tables": 6, "seeded-random-declaration-order": 6,
        "full-budget-zero-practice-regret": 9, "short-full-budget-history-prefix": 9,
        "neutral-weight-null": 15, "neutral-context-decoding-anchors": 45}
    assert {name: sum(row["check"] == name for row in checks["checks"]) for name in expected_counts} == expected_counts
    assert result["gpu_execution_deferred"] and not result["promotion_eligible"] and not result["benchmark_result_claimed"]


def test_offline_oracle_is_broader_than_and_separate_from_measured_evidence(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    offline = result["offline_expectations"]
    assert offline["scope"] == "finite-construction-expectations-not-measured-evidence"
    assert offline["verified"] and not offline["measured_evidence"] and not offline["used_for_policy_selection"]
    assert not offline["population_generalization_claimed"]
    assert offline["legal_order_count"] == 90 and offline["trajectory_count"] == 6480
    assert offline["budgets"] == [1, 3, 6] and offline["base_task_count"] == 6
    assert offline["policy_seeds"] == [{"policy": policy, "order_seed": seed} for policy, seed in (
        ("adaptive-v1", 0), ("fixed-order-v1", 0), ("seeded-random-v1", 0), ("seeded-random-v1", 1))]
    assert set(offline["invariants"]) == {"score_tables_invariant", "random_order_invariant", "full_budget_zero_regret",
        "budget_prefixes", "neutral_null", "all_passed"}
    assert all(value is True for value in offline["invariants"].values())
    assert len(offline["task_summaries"]) == 6
    assert all(task["trajectory_count"] == 1080 and task["legal_order_count"] == 90
               for task in offline["task_summaries"])
    assert offline["source_oracle_sha256"] == full_context[1].oracle_sha256
    assert len(offline["offline_digest"]) == 64
    assert len(result["rows"]) == 60 and {row["rounds"] for row in result["rows"]} == {3, 6}
    assert {row["order_seed"] for row in result["rows"][:45]} == {0}


def test_variants_are_nested_within_six_base_tasks_without_pooled_sign_claims(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    assert result["base_task_count"] == 6 and result["measured_variant_count"] == 15
    for value in result["family_results"].values():
        assert value["task_count"] == len(value["base_task_ids"]) == 2
        assert value["variant_count"] == len(value["variant_task_ids"]) == 5
        assert value["audit_coverage"]["available"] == 0 and value["audit_coverage"]["withheld_after_veto"] == 15
        assert value["orders_and_budgets_are_nested_conditions"] and value["variant_pooled_mean"] is None
    assert not result["family_pooling_performed"] and not result["variant_pooling_performed"]
    assert not result["inferential_statistics_computed"]
    effects = result["order_effects"]
    assert len(effects) == 6 and all(row["matches"] is True for row in effects)
    signs = {"informative-improvement-v1": 1, "misleading-improvement-v1": -1, "neutral-v1": 0}
    for row in effects:
        difference = row["observed"]["canonical"]["adaptive_minus_fixed"]
        assert (difference > 0) - (difference < 0) == signs[row["family"]]
        assert row["measured_order_count"] == 2 and row["rounds"] == 3
        assert not row["all_legal_orders_measured"] and not row["order_invariant_advantage_required"]
        assert row["aggregation_unit"] == "base-task-with-nested-orders"
    assert any(row["observed"]["canonical"]["adaptive_minus_fixed"]
               != row["observed"]["reverse-lanes"]["adaptive_minus_fixed"] for row in effects)


def test_budget_comparisons_are_first_instance_only_and_full_budget_has_zero_gap(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    effects = result["budget_effects"]
    assert len(effects) == 9
    assert {row["base_task_id"] for row in effects} == {"task-01", "task-03", "task-05"}
    for row in effects:
        assert row["instance_index"] == 1 and row["available"] and row["order_id"] == "canonical"
        assert row["budgets"] == [3, 6] and row["practice_regret"]["6"] == 0.0
        assert row["practice_accuracy"]["6"] >= row["practice_accuracy"]["3"]
        assert row["scope"] == "first-declared-instance-per-family-not-a-general-budget-effect"
    full = [row for row in result["practice_gap_rows"] if row["panel"] == "full-budget"]
    assert len(full) == 9
    assert all(value["selected_gap_to_best_practice"] == 0.0 for row in full for value in row["mechanisms"].values())


def test_practice_prefix_curves_retain_variable_budgets_and_exact_trial_ids(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    curves = result["practice_prefix_curves"]
    assert len(curves) == 135
    assert sum(len(row["points"]) == 4 for row in curves) == 108
    assert sum(len(row["points"]) == 7 for row in curves) == 27
    assert len({(row["run_id"], row["mechanism"]) for row in curves}) == 135
    for row in curves:
        assert row["available"] and row["instance_index"] in {1, 2}
        assert not row["additional_inference_performed"]
        assert row["base_task_id"] in row["run_id"] and row["order_id"] in row["run_id"]
        points = row["points"]
        assert [p["trial"] for p in points] == list(range(row["rounds"] + 1))
        assert [p["proposals"] for p in points] == list(range(row["rounds"] + 1))
        assert [p["practice_case_evaluations"] for p in points] == [8 * (n + 1) for n in range(row["rounds"] + 1)]
        assert points[0]["option_id"] is points[0]["best_option_id"] is None
        assert [p["best_correct"] for p in points] == sorted(p["best_correct"] for p in points)
        gap = next(g for g in result["practice_gap_rows"] if g["run_id"] == row["run_id"])
        assert points[-1]["best_accuracy"] == gap["mechanisms"][row["mechanism"]]["practice_selected_accuracy"]
    neutral = [row for row in curves if row["family"] == "neutral-v1" and row["mechanism"] == "W"]
    assert all(all(point["best_option_id"] is None for point in row["points"]) for row in neutral)


def test_1311_batches_require_observed_processes_and_five_separate_cost_meters(full_context: tuple[Any, ...]) -> None:
    result = report(full_context)
    processes = result["process_coverage"]
    assert processes["scheduled_rows"] == processes["verified_rows"] == 60
    assert processes["mismatches"] == processes["unavailable"] == 0
    assert processes["expected_inference_batches"] == processes["observed_inference_batches"] == 1311
    assert processes["reaped_inference_batches"] == 1311
    schedule = {row["run_id"]: row for row in full_context[3]}
    for row in processes["rows"]:
        wanted = ({"practice": 3 * (schedule[row["run_id"]]["rounds"] + 1) + 1, "restoration": 3,
                   "confirmation": 5, "audit": 0, "reference": 0} if row["stage"] == "search" else
                  {"practice": 0, "restoration": 0, "confirmation": 0, "audit": 0, "reference": 19})
        assert row["expected_phase_batches"] == row["observed_phase_batches"] == wanted
    meters = {"proposals", "materializations", "inference_calls", "input_tokens", "output_tokens"}
    for stage in ("search", "reference"):
        assert set(result["charged_capacity"][stage]) == set(result["completed_verified_usage"][stage]) == meters
    assert result["measurements"]["phases"]["audit"]["observed_inference_claims"] == 0
    assert result["charged_capacity"]["reference"]["inference_calls"] > 0


@pytest.mark.parametrize("status", ["failed", "uncertain"])
def test_partial_failure_retains_reservations_without_imputing_references(full_context: tuple[Any, ...], status: str) -> None:
    prefix = copy.deepcopy(full_context[4])
    prefix[0]["outcome"].update(status=status, summary=None)
    prefix[0]["result"]["verified_usage"] = None
    prefix[0]["details"] = None
    for entry in prefix[45:]:
        entry["outcome"].update(status="blocked", summary=None, observation_coverage=[])
        entry["claim"]["claimed_capacity"] = dict.fromkeys(entry["row"]["reserved_capacity"], 0)
        entry["result"]["verified_usage"] = None
        entry["reference"] = None
    result = report(full_context, prefix)
    assert result["evidence_integrity_verified"] and not result["campaign_completed"] and not result["acceptance_passed"]
    assert result["status_counts"]["reference"]["blocked"] == 15
    assert result["unknown_usage_rows"] == {"search": 1, "reference": 0}
    assert all(value == 0 for value in result["charged_capacity"]["reference"].values())
    assert result["charged_capacity"]["search"]["proposals"] == full_context[0].search_budget.max_proposals
    assert result["calibration"]["unavailable_or_nondesignated"] == 14 + 15 * 19
    assert result["audit_routing"]["unavailable"] == 1 and result["process_coverage"]["unavailable"] == 16
    assert result["robustness_checks"]["unavailable"] > 0
    assert all(row["matches"] is None for row in result["order_effects"])
    assert result["practice_gap_rows"][0]["mechanisms"]["W"]["selected_gap_to_best_practice"] is None
    assert all(row["points"] is None for row in result["practice_prefix_curves"][:3])


def test_unstarted_and_pending_publication_cannot_pass_from_oracle_predictions(full_context: tuple[Any, ...]) -> None:
    empty = report(full_context, [])
    assert empty["status_counts"]["search"]["not-started"] == 45
    assert empty["status_counts"]["reference"]["not-started"] == 15
    assert empty["calibration"]["unavailable_or_nondesignated"] == 915
    assert empty["audit_routing"]["unavailable"] == 45 and empty["process_coverage"]["unavailable"] == 60
    assert empty["robustness_checks"]["unavailable"] == 90 and empty["robustness_checks"]["matches"] == 0
    assert empty["offline_expectations"]["verified"] and not empty["acceptance_passed"]
    prefix = copy.deepcopy(full_context[4])
    prefix[-1]["published"] = False
    result = report(full_context, prefix)
    assert not result["campaign_completed"] and not result["acceptance_passed"] and result["pending_results"] == 1


@pytest.mark.parametrize("index", [0, 36, 45])
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
    assert result["campaign_completed"] and result["calibration"]["matches"] == 915
    assert result["robustness_checks"]["matches"] == 90
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
    summary["histories"]["W"][0]["correct"] += 1
    result = report(full_context, prefix)
    assert summary["selected_options"] == original_selection
    assert result["calibration"]["mismatches"] == 1 and not result["acceptance_passed"]
    mismatch = next(row for row in result["calibration"]["checks"] if row["matches"] is False)
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
        context[3][0]["base_task_id"] = "task-02"
    elif change == "selection":
        prefix[0]["details"]["selection"]["extra"] = True
    elif change == "role":
        prefix[0]["details"]["practice"]["role"] = "confirmation"
    elif change == "case-count":
        prefix[0]["details"]["practice"]["conditions"]["A"]["cases"].pop()
    else:
        prefix[45]["outcome"]["summary"]["scores"].pop("A")
    with pytest.raises(ValueError):
        report(context)


def test_per_case_predictions_are_compared_when_confirmation_accuracy_is_unchanged(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    entry = prefix[0]
    score = entry["details"]["confirmation"]["conditions"]["W"]
    left, right = next((a, b) for a in score["cases"] for b in score["cases"] if a["output"] != b["output"])
    original_accuracy = score["accuracy"]
    left["output"], right["output"] = right["output"], left["output"]
    entry["outcome"]["summary"]["confirmation"]["report_digest"] = canonical_digest(entry["details"]["confirmation"])
    result = report(full_context, prefix)
    assert result["campaign_completed"] and result["calibration"]["mismatches"] == 1
    assert result["robustness_checks"]["matches"] == 90 and not result["acceptance_passed"]
    mismatch = next(row for row in result["calibration"]["checks"] if row["matches"] is False)
    assert mismatch["check"] == "confirmation:W"
    assert mismatch["expected"]["accuracy"] == mismatch["observed"]["accuracy"] == original_accuracy


def test_report_never_dispatches_reselects_or_mutates_verified_inputs(full_context: tuple[Any, ...], monkeypatch: Any) -> None:
    from autocontext.weight_evolution import cpu_execution_search, cpu_measured_runner

    def forbid(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("read-only report attempted model execution")

    monkeypatch.setattr(cpu_execution_search, "run_cpu_execution_search", forbid)
    monkeypatch.setattr(cpu_measured_runner, "run_measured_cpu_batch", forbid)
    before = copy.deepcopy(full_context[4])
    result = report(full_context)
    assert full_context[4] == before
    loss = next(row for row in result["order_effects"] if row["family"] == "misleading-improvement-v1")
    assert loss["observed"]["canonical"]["practice_regret"]["adaptive-v1"] > 0
    assert result["acceptance_passed"] and not result["robustness_checks"]["references_used_for_selection"]


def test_unselected_reference_outputs_are_calibrated_without_reselection(full_context: tuple[Any, ...]) -> None:
    prefix = copy.deepcopy(full_context[4])
    selected = {option for entry in prefix[:3]
                for option in entry["outcome"]["summary"]["selected_options"].values() if option is not None}
    scores = prefix[45]["outcome"]["summary"]["scores"]
    option = next(key for key, value in scores.items() if key not in selected | {"A"}
                  and len({case["output"] for case in value["cases"]}) > 1)
    score = scores[option]
    left, right = next((a, b) for a in score["cases"] for b in score["cases"] if a["output"] != b["output"])
    left["output"], right["output"] = right["output"], left["output"]
    before = [copy.deepcopy(entry["outcome"]["summary"]["selected_options"]) for entry in prefix[:45]]
    result = report(full_context, prefix)
    assert result["calibration"]["mismatches"] == result["robustness_checks"]["mismatches"] == 1
    assert not result["acceptance_passed"]
    mismatch = next(row for row in result["calibration"]["checks"] if row["matches"] is False)
    assert mismatch["check"] == f"reference-practice:{option}"
    check = next(row for row in result["robustness_checks"]["checks"] if row["matches"] is False)
    assert check["check"] == "reference-score-tables" and check["identity"] == "task-01"
    assert [entry["outcome"]["summary"]["selected_options"] for entry in prefix[:45]] == before


@pytest.mark.parametrize("panel,kind", [("order-comparison", "seeded-random-declaration-order"),
                                       ("full-budget", "short-full-budget-history-prefix")])
def test_neutral_probe_order_corruption_is_detected_despite_identical_scores(full_context: tuple[Any, ...],
                                                                          panel: str, kind: str) -> None:
    prefix = copy.deepcopy(full_context[4])
    entry = next(entry for entry in prefix[:45] if entry["row"]["family"] == "neutral-v1"
                 and entry["row"]["panel"] == panel and entry["row"]["policy"] == "seeded-random-v1")
    history = entry["outcome"]["summary"]["histories"]["W"]
    assert history[1]["correct"] == history[2]["correct"]
    history[1], history[2] = history[2], history[1]
    result = report(full_context, prefix)
    assert result["calibration"]["mismatches"] == 1 and not result["acceptance_passed"]
    assert any(row["check"] == kind and row["matches"] is False for row in result["robustness_checks"]["checks"])
    assert all(row["matches"] is True for row in result["robustness_checks"]["checks"]
               if row["check"] == "neutral-weight-null")


@pytest.mark.parametrize("change", ["false-invariant", "missing-invariant", "extra-invariant", "task-invariant",
                                   "duplicate-order", "duplicate-trajectory", "wrong-seeds", "missing-task"])
def test_offline_summary_rejects_incomplete_or_false_invariant_domain(full_context: tuple[Any, ...],
                                                                   monkeypatch: Any, change: str) -> None:
    from autocontext.weight_evolution import cpu_robustness_campaign_report as module

    original = module.offline_robustness_oracle

    def altered(*args: Any) -> dict[str, Any]:
        offline = original(*args)
        if change == "false-invariant":
            offline["invariants"]["budget_prefixes"] = False
        elif change == "missing-invariant":
            offline["invariants"].pop("budget_prefixes")
        elif change == "extra-invariant":
            offline["invariants"]["invented"] = True
        elif change == "task-invariant":
            offline["tasks"][0]["invariants"]["neutral_null"] = False
        elif change == "duplicate-order":
            task = offline["tasks"][0]
            task["orders"] = (task["orders"][0], *task["orders"][:-1])
        elif change == "duplicate-trajectory":
            offline["tasks"][0]["trajectories"][1] = offline["tasks"][0]["trajectories"][0]
        elif change == "wrong-seeds":
            offline["policy_seeds"][-1]["order_seed"] = 2
        else:
            offline["tasks"].pop()
        return offline

    monkeypatch.setattr(module, "offline_robustness_oracle", altered)
    result = report(full_context)
    assert result["calibration"]["matches"] == 915 and result["robustness_checks"]["matches"] == 90
    assert not result["offline_expectations"]["verified"] and not result["acceptance_passed"]
