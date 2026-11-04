from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_models import (
    CpuCampaignBudget,
    CpuCampaignRepeat,
    CpuCampaignSpec,
    CpuCampaignTask,
)
from autocontext.weight_evolution.cpu_campaign_report import build_cpu_campaign_report

CAPACITY = {"proposals": 9, "materializations": 5, "inference_calls": 130, "input_tokens": 400, "output_tokens": 130}
CHARGED = {"proposals": 9, "materializations": 5, "inference_calls": 130, "input_tokens": 300, "output_tokens": 130}


def campaign(*, tasks: int = 2, repeats: int = 2) -> CpuCampaignSpec:
    return CpuCampaignSpec(
        campaign_id="report-tests", implementation_digest="a" * 64,
        tasks=tuple(CpuCampaignTask(task_id=f"task-{i}", generation_seed=i,
                                   bundle_lock_sha256=f"{i:064x}", bundle_lock_size_bytes=100,
                                   provenance_sha256=f"{i + 100:064x}", provenance_size_bytes=200)
                    for i in range(tasks)),
        repeats=tuple(CpuCampaignRepeat(repeat_id=f"repeat-{i}", inference_seed=i + 40, search_order_seed=i + 80)
                      for i in range(repeats)),
        budget=CpuCampaignBudget(max_runs=tasks * repeats * 3, max_proposals=10000, max_materializations=1000,
                                 max_inference_calls=100000, max_input_tokens=1000000, max_output_tokens=100000),
    )


def completed_rows(spec: CpuCampaignSpec) -> list[dict[str, Any]]:
    rows = []
    for task_index, task in enumerate(spec.tasks):
        for repeat_index, repeat in enumerate(spec.repeats):
            for policy in spec.policies:
                accuracy = 0.4 + 0.02 * repeat_index
                accuracy += 0.1 + 0.1 * task_index if policy == "adaptive-v1" else (
                    0.05 if policy == "seeded-random-v1" else 0.0
                )
                scores = {name: accuracy for name in ("A", "C", "W", "CW", "D")}
                rows.append({"run_id": f"{task.task_id}.{repeat.repeat_id}.{policy}", "task_id": task.task_id,
                             "repeat_id": repeat.repeat_id, "policy": policy,
                             "inference_seed": repeat.inference_seed,
                             "order_seed": repeat.search_order_seed if policy == "seeded-random-v1" else 0,
                             "study_digest": canonical_digest({"task": task_index, "repeat": repeat_index,
                                                                "policy": policy}),
                             "status": "completed", "reserved_capacity": CAPACITY.copy(),
                             "charged_usage": CHARGED.copy(), "summary_semantic_digest": "b" * 64,
                             "confirmation": "confirmed", "confirmation_scores": scores,
                             "audit_scores": scores.copy()})
    return rows


def incomplete(row: dict[str, Any], status: str) -> None:
    row.update(status=status, charged_usage=None, summary_semantic_digest=None,
               confirmation=None, confirmation_scores=None, audit_scores=None)
    if status == "failed":
        row["error_type"] = "CpuSearchBlockedError"


def build(spec: CpuCampaignSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return build_cpu_campaign_report(spec, rows, runtime_digest="c" * 64)


def test_complete_report_pairs_repeats_then_uses_tasks_for_eight_adjusted_intervals() -> None:
    spec = campaign(tasks=3)
    report = build(spec, completed_rows(spec))
    assert report["status_counts"] == {"scheduled": 18, "completed": 18, "failed": 0, "pending": 0,
                                        "not-started": 0, "confirmed": 18, "vetoed": 0}
    assert len(report["confirmation_contrasts"]) == 8
    contrast = report["confirmation_contrasts"]["adaptive-minus-fixed-order-v1:W"]
    expected = paired_confidence_interval([0.1, 0.2, 0.3], 1.959963984540054, max_looks=8)
    assert (contrast["mean"], contrast["lower"], contrast["upper"]) == pytest.approx(expected)
    unadjusted = paired_confidence_interval([0.1, 0.2, 0.3], 1.959963984540054)
    assert contrast["lower"] < unadjusted[1] and contrast["upper"] > unadjusted[2]
    assert contrast["scheduled_pairs"] == contrast["observed_pairs"] == 6
    assert contrast["task_count"] == 3 and contrast["interval_available"] is True
    assert [task["descriptive_mean_observed_pairs"] for task in contrast["task_details"]] == pytest.approx([0.1, 0.2, 0.3])
    assert all(row["family_contrasts"] == 8 for row in report["confirmation_contrasts"].values())


def test_duplicated_repeat_effects_do_not_pretend_to_add_independent_tasks() -> None:
    once, repeated = campaign(repeats=1), campaign(repeats=4)
    first = build(once, completed_rows(once))["confirmation_contrasts"]
    second = build(repeated, completed_rows(repeated))["confirmation_contrasts"]
    for key in first:
        assert first[key]["task_count"] == second[key]["task_count"] == 2
        assert second[key]["observed_pairs"] == 4 * first[key]["observed_pairs"]
        for field in ("mean", "lower", "upper"):
            assert second[key][field] == pytest.approx(first[key][field])


@pytest.mark.parametrize("status", ["failed", "pending", "not-started"])
def test_any_missing_result_suppresses_all_inference_without_zero_imputation(status: str) -> None:
    spec = campaign()
    rows = completed_rows(spec)
    incomplete(rows[-1], status)  # A random-policy row: fixed contrasts still have every pair.
    report = build(spec, rows)
    assert report["status_counts"][status] == 1
    assert report["status_counts"]["completed"] == 11
    assert len(report["rows"]) == 12 and report["rows"][-1]["confirmation_scores"] is None
    for policy in spec.policies:
        assert report["policy_coverage"][policy]["denominator"] == 4
    assert report["policy_coverage"]["seeded-random-v1"]["completed_fraction"] == 0.75
    for contrast in report["confirmation_contrasts"].values():
        assert contrast["mean"] is contrast["lower"] is contrast["upper"] is None
        assert contrast["interval_available"] is False
        assert contrast["interval_unavailable_reason"] == "incomplete-campaign-schedule"
    fixed = report["confirmation_contrasts"]["adaptive-minus-fixed-order-v1:C"]
    random = report["confirmation_contrasts"]["adaptive-minus-seeded-random-v1:C"]
    assert fixed["observed_pairs"] == fixed["scheduled_pairs"] == 4
    assert random["observed_pairs"] == 3 and random["scheduled_pairs"] == 4
    assert random["task_details"][-1]["pairs"][-1]["difference"] is None
    assert report["costs"]["known_charged_usage"]["proposals"] == 11 * CHARGED["proposals"]
    assert report["costs"]["scheduled_capacity"]["proposals"] == 12 * CAPACITY["proposals"]
    expected_unknown = [] if status == "not-started" else [rows[-1]["run_id"]]
    assert report["costs"]["unknown_charged_usage_run_ids"] == expected_unknown
    assert report["costs"]["failed_or_pending_reserved_capacity"] == (
        {name: 0 for name in CAPACITY} if status == "not-started" else CAPACITY
    )
    assert report["costs"]["reservation_rule"] == "failed-or-uncertain-run-capacity-remains-charged-without-refund"


def test_veto_is_a_completed_confirmation_result_with_conditional_audit_only() -> None:
    spec = campaign()
    rows = completed_rows(spec)
    rows[0].update(confirmation="vetoed", audit_scores=None)
    rows[0]["confirmation_scores"]["C"] = 0.0
    report = build(spec, rows)
    assert report["status_counts"]["completed"] == 12
    assert report["status_counts"]["vetoed"] == 1
    assert report["policy_coverage"]["adaptive-v1"]["vetoed_fraction"] == 0.25
    assert report["confirmation_contrasts"]["adaptive-minus-fixed-order-v1:C"]["interval_available"] is True
    assert report["audit_coverage"] == {"scheduled": 12, "available": 11, "withheld_after_veto": 1,
                                        "conditional_on_confirmation": True, "overall_quality_aggregate": None}
    assert report["rows"][0]["audit_scores"] is None


def test_roster_order_is_pinned_inputs_unchanged_and_reporting_never_dispatches_or_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = campaign()
    rows = completed_rows(spec)
    before = copy.deepcopy(rows)
    expected = build(spec, rows)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("report attempted execution or filesystem mutation")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    assert build(spec, list(reversed(rows))) == expected
    assert rows == before
    assert expected["semantic_digest"] == canonical_digest({key: value for key, value in expected.items()
                                                            if key != "semantic_digest"})
    assert expected["model_execution_performed"] is False
    assert expected["gpu_execution_deferred"] is True
    assert expected["promotion_eligible"] is expected["benchmark_result_claimed"] is False


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "inference_seed", "order_seed", "task_id",
                                      "repeat_id", "policy", "study_digest", "score", "bool_score", "missing_score",
                                      "charged", "negative_cost", "missing_meter", "unequal_capacity", "no_summary",
                                      "no_charged", "no_audit", "veto_audit", "failed_score", "unstarted_charge", "extra_field"])
def test_invalid_or_unbound_rows_are_rejected(mutation: str) -> None:
    spec = campaign()
    rows = completed_rows(spec)
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "extra":
        rows.append({**copy.deepcopy(rows[0]), "run_id": "unplanned-run"})
    elif mutation in {"inference_seed", "order_seed"}:
        rows[0][mutation] += 1
    elif mutation in {"task_id", "repeat_id"}:
        rows[0][mutation] = "unplanned"
    elif mutation == "policy":
        rows[0][mutation] = "fixed-order-v1"
    elif mutation == "study_digest":
        rows[0][mutation] = "invalid"
    elif mutation == "score":
        rows[0]["confirmation_scores"]["W"] = float("nan")
    elif mutation == "bool_score":
        rows[0]["confirmation_scores"]["W"] = True
    elif mutation == "missing_score":
        del rows[0]["confirmation_scores"]["W"]
    elif mutation == "charged":
        rows[0]["charged_usage"]["proposals"] += 1
    elif mutation == "negative_cost":
        rows[0]["charged_usage"]["input_tokens"] = -1
    elif mutation == "missing_meter":
        del rows[0]["reserved_capacity"]["proposals"]
    elif mutation == "unequal_capacity":
        rows[0]["reserved_capacity"]["input_tokens"] += 1
    elif mutation == "no_summary":
        rows[0]["summary_semantic_digest"] = None
    elif mutation == "no_charged":
        rows[0]["charged_usage"] = None
    elif mutation == "no_audit":
        rows[0]["audit_scores"] = None
    elif mutation == "veto_audit":
        rows[0]["confirmation"] = "vetoed"
    elif mutation == "failed_score":
        rows[0]["status"] = "failed"
    elif mutation == "unstarted_charge":
        incomplete(rows[0], "not-started")
        rows[0]["charged_usage"] = CHARGED.copy()
    else:
        rows[0]["elapsed_seconds"] = 1.0
    with pytest.raises(ValueError):
        build(spec, rows)


def test_revalidated_policy_and_budget_pins_reject_constructed_invalid_specs() -> None:
    spec = campaign()
    for invalid in (spec.model_copy(update={"confidence_z": 1.0}),
                    spec.model_copy(update={"reporting_rule": "survivors-only"}),
                    spec.model_copy(update={"policies": ("adaptive-v1",)}),
                    spec.model_copy(update={"budget": spec.budget.model_copy(update={"max_inference_calls": 1})})):
        with pytest.raises(ValueError):
            build(invalid, completed_rows(spec))
    with pytest.raises(ValueError):
        build_cpu_campaign_report(spec, completed_rows(spec), runtime_digest="invalid")
