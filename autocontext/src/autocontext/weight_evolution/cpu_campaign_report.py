"""Pure aggregation of a complete, explicitly missing-aware CPU campaign roster."""

from __future__ import annotations

import json
import statistics
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

if TYPE_CHECKING:
    from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignSpec

_METERS = ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")
_POLICIES = ("adaptive-v1", "fixed-order-v1", "seeded-random-v1")
_STATUSES = ("completed", "failed", "pending", "not-started")
_CONDITIONS = ("A", "C", "W", "CW", "D")
_CONTRAST_CONDITIONS = ("C", "W", "CW", "D")
_CONFIDENCE_Z = 1.959963984540054
_FAMILY_SIZE = 8


class _CampaignRow(FrozenModel):
    run_id: Identifier
    task_id: Identifier
    repeat_id: Identifier
    policy: Literal["adaptive-v1", "fixed-order-v1", "seeded-random-v1"]
    inference_seed: int = Field(ge=0, le=2**32 - 1)
    order_seed: int = Field(ge=0, le=2**32 - 1)
    study_digest: Digest
    status: Literal["completed", "failed", "pending", "not-started"]
    reserved_capacity: dict[str, int]
    charged_usage: dict[str, int] | None
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    summary_semantic_digest: Digest | None = None
    confirmation: Literal["confirmed", "vetoed"] | None = None
    confirmation_scores: dict[str, float] | None = None
    audit_scores: dict[str, float] | None = None

    @model_validator(mode="after")
    def consistent_evidence(self) -> Self:
        for value in (self.reserved_capacity, self.charged_usage):
            if value is not None and (set(value) != set(_METERS) or any(amount < 0 for amount in value.values())):
                raise ValueError("campaign cost must name every nonnegative usage meter")
        if self.charged_usage is not None and any(
            self.charged_usage[name] > self.reserved_capacity[name] for name in _METERS
        ):
            raise ValueError("campaign charged usage exceeds its reserved capacity")
        for scores in (self.confirmation_scores, self.audit_scores):
            if scores is not None and (set(scores) != set(_CONDITIONS)
                                       or any(not 0.0 <= score <= 1.0 for score in scores.values())):
                raise ValueError("campaign scores require finite A/C/W/CW/D accuracies")
        if self.status == "completed":
            if (self.confirmation is None or self.confirmation_scores is None
                    or self.summary_semantic_digest is None or self.charged_usage is None or self.error_type is not None):
                raise ValueError("completed campaign row lacks its verified search evidence")
            if (self.confirmation == "confirmed") != (self.audit_scores is not None):
                raise ValueError("audit scores must correspond to confirmed completed searches")
        elif any(value is not None for value in (self.confirmation, self.confirmation_scores,
                                                 self.audit_scores, self.summary_semantic_digest)):
            raise ValueError("unfinished or failed campaign rows cannot claim completed search scores")
        if self.status == "not-started" and (self.error_type is not None or (
            self.charged_usage is not None and any(self.charged_usage.values())
        )):
            raise ValueError("unstarted campaign rows cannot claim charged work or failure")
        return self


def _counts(rows: list[_CampaignRow]) -> dict[str, int]:
    return {"scheduled": len(rows), **{status: sum(row.status == status for row in rows) for status in _STATUSES},
            "confirmed": sum(row.confirmation == "confirmed" for row in rows),
            "vetoed": sum(row.confirmation == "vetoed" for row in rows)}


def _sum_usage(values: list[dict[str, int]]) -> dict[str, int]:
    return {name: sum(value[name] for value in values) for name in _METERS}


def _costs(rows: list[_CampaignRow]) -> dict[str, Any]:
    unknown = [row.run_id for row in rows if row.status != "not-started" and row.charged_usage is None]
    return {
        "scheduled_capacity": _sum_usage([row.reserved_capacity for row in rows]),
        "attempted_capacity": _sum_usage([row.reserved_capacity for row in rows if row.status != "not-started"]),
        "failed_or_pending_reserved_capacity": _sum_usage([
            row.reserved_capacity for row in rows if row.status in {"failed", "pending"}
        ]),
        "known_charged_usage": _sum_usage([row.charged_usage for row in rows if row.charged_usage is not None]),
        "unknown_charged_usage_run_ids": unknown,
        "charged_usage_complete": not unknown,
        "reservation_rule": "failed-or-uncertain-run-capacity-remains-charged-without-refund",
        "scope": "declared-capacity-and-journal-charges-not-timing-or-memory-telemetry",
    }


def _contrast(rows: list[_CampaignRow], control: str, condition: str, *, all_completed: bool) -> dict[str, Any]:
    roster: dict[tuple[str, str, str], _CampaignRow] = {(row.task_id, row.repeat_id, row.policy): row for row in rows}
    task_ids = list(dict.fromkeys(row.task_id for row in rows))
    tasks: list[dict[str, Any]] = []
    observed_pairs = 0
    scheduled_pairs = 0
    for task_id in task_ids:
        pairs = []
        effects = []
        repeat_ids = list(dict.fromkeys(row.repeat_id for row in rows if row.task_id == task_id))
        for repeat_id in repeat_ids:
            adaptive = roster[task_id, repeat_id, "adaptive-v1"]
            baseline = roster[task_id, repeat_id, control]
            available = adaptive.status == baseline.status == "completed"
            delta = None
            if available:
                assert adaptive.confirmation_scores is not None and baseline.confirmation_scores is not None
                delta = adaptive.confirmation_scores[condition] - baseline.confirmation_scores[condition]
                effects.append(delta)
                observed_pairs += 1
            scheduled_pairs += 1
            pairs.append({"repeat_id": repeat_id, "adaptive_run_id": adaptive.run_id,
                          "control_run_id": baseline.run_id, "adaptive_status": adaptive.status,
                          "control_status": baseline.status, "difference": delta})
        tasks.append({"task_id": task_id, "scheduled_pairs": len(pairs), "observed_pairs": len(effects),
                      "complete": len(effects) == len(pairs),
                      "descriptive_mean_observed_pairs": statistics.fmean(effects) if effects else None,
                      "pairs": pairs})
    eligible = all_completed and len(tasks) >= 2
    mean = lower = upper = None
    if all_completed:
        means = [float(task["descriptive_mean_observed_pairs"]) for task in tasks]
        mean, lower, upper = paired_confidence_interval(means, _CONFIDENCE_Z, max_looks=_FAMILY_SIZE)
    return {"adaptive_policy": "adaptive-v1", "control_policy": control, "condition": condition,
            "score_role": "confirmation", "scheduled_pairs": scheduled_pairs, "observed_pairs": observed_pairs,
            "task_details": tasks, "task_count": len(tasks), "independent_unit": "task-mean-over-paired-repeats",
            "mean": mean, "lower": lower, "upper": upper, "interval_available": eligible,
            "interval_unavailable_reason": None if eligible else (
                "incomplete-campaign-schedule" if not all_completed else "fewer-than-two-tasks"),
            "confidence_z": _CONFIDENCE_Z, "family_contrasts": _FAMILY_SIZE,
            "method": "paired-student-t-bonferroni-eight-prespecified-contrasts",
            "scope": "small-constructed-cpu-tasks-model-based-interval-not-benchmark-generalization"}


def build_cpu_campaign_report(
    spec: CpuCampaignSpec, rows: list[dict[str, Any]], *, runtime_digest: str,
) -> dict[str, Any]:
    """Aggregate verified rows without dispatch, filesystem access or repair."""
    # The coordinator verifies files and receipts before calling this pure
    # function. Revalidate its structured inputs instead of trusting callers.
    from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignSpec

    spec = CpuCampaignSpec.model_validate_json(json.dumps(spec.to_dict(), allow_nan=False))
    TypeAdapter(Digest).validate_python(runtime_digest)
    parsed = [_CampaignRow.model_validate_json(json.dumps(row, allow_nan=False)) for row in rows]
    by_id = {row.run_id: row for row in parsed}
    if len(by_id) != len(parsed):
        raise ValueError("campaign report repeats a run ID")
    schedule: list[dict[str, Any]] = [{"run_id": f"{task.task_id}.{repeat.repeat_id}.{policy}", "task_id": task.task_id,
                 "repeat_id": repeat.repeat_id, "policy": policy, "inference_seed": repeat.inference_seed,
                 "order_seed": repeat.search_order_seed if policy == "seeded-random-v1" else 0}
                for task in spec.tasks for repeat in spec.repeats for policy in spec.policies]
    if set(by_id) != {run["run_id"] for run in schedule}:
        raise ValueError("campaign report must retain every scheduled run, including missing results")
    ordered = []
    for run in schedule:
        row = by_id[run["run_id"]]
        if any(getattr(row, name) != run[name]
               for name in ("task_id", "repeat_id", "policy", "inference_seed", "order_seed")):
            raise ValueError("campaign report row differs from the pinned run schedule")
        ordered.append(row)
    capacities = _sum_usage([row.reserved_capacity for row in ordered])
    if any(capacities[name] > getattr(spec.budget, f"max_{name}") for name in _METERS):
        raise ValueError("campaign row capacities exceed the pinned campaign budget")
    for task in spec.tasks:
        task_capacities = [row.reserved_capacity for row in ordered if row.task_id == task.task_id]
        if any(capacity != task_capacities[0] for capacity in task_capacities):
            raise ValueError("task policies and repeats require equal reserved capacities")
    counts = _counts(ordered)
    all_completed = counts["completed"] == counts["scheduled"]
    coverage = {}
    for policy in _POLICIES:
        policy_rows = [row for row in ordered if row.policy == policy]
        policy_counts = _counts(policy_rows)
        coverage[policy] = {**policy_counts, "denominator": policy_counts["scheduled"],
                            "completed_fraction": policy_counts["completed"] / policy_counts["scheduled"],
                            "confirmed_fraction": policy_counts["confirmed"] / policy_counts["scheduled"],
                            "vetoed_fraction": policy_counts["vetoed"] / policy_counts["scheduled"],
                            "costs": _costs(policy_rows)}
    report = {
        "schema_version": 1, "report_kind": "cpu_search_campaign", "campaign_digest": spec.digest,
        "runtime_digest": runtime_digest, "reporting_rule": spec.reporting_rule,
        "model_execution_performed": False, "evidence_model_execution": "actual-cpu-inference",
        "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
        "status_counts": counts, "policy_coverage": coverage, "costs": _costs(ordered),
        "rows": [row.to_dict() for row in ordered],
        "confirmation_contrasts": {
            f"adaptive-minus-{control}:{condition}": _contrast(ordered, control, condition, all_completed=all_completed)
            for control in _POLICIES[1:] for condition in _CONTRAST_CONDITIONS
        },
        "audit_coverage": {"scheduled": len(ordered), "available": sum(row.audit_scores is not None for row in ordered),
                           "withheld_after_veto": counts["vetoed"], "conditional_on_confirmation": True,
                           "overall_quality_aggregate": None},
        "scope": "small-synthetic-cpu-campaign-no-benchmark-or-generalization-claim",
    }
    report["semantic_digest"] = canonical_digest(report)
    return report
