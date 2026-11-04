"""Pure descriptive diagnostics over verified searches and practice references.

The coordinator verifies receipts and retained files before calling this module.
This layer checks their pinned identities, coverage, scores, and consistency. It
never executes a model, changes selection, opens held-out data, or computes a
new inferential comparison.
"""

from __future__ import annotations

import json
import statistics
from typing import Any, Literal, Self, TypeVar

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_report import _CampaignRow
from autocontext.weight_evolution.cpu_diagnostic_models import (
    DIAGNOSTIC_METERS,
    CpuDiagnosticReference,
    CpuDiagnosticSourceRun,
    CpuDiagnosticSpec,
)
from autocontext.weight_evolution.cpu_search_models import SEARCH_MECHANISMS, CpuSearchPolicy
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore, select_best
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

_CONDITIONS = ("A", "C", "W", "CW", "D")
_STATUSES = ("completed", "failed", "pending", "not-started")
_T = TypeVar("_T", bound=FrozenModel)


class _Input(FrozenModel):
    @field_validator("schema_version", "look", "model_execution_performed", "gpu_execution_deferred",
                     "promotion_eligible", "benchmark_result_claimed", mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        expected = int if info.field_name in {"schema_version", "look"} else bool
        if type(value) is not expected:
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class _CaseScore(_Input):
    case_id: Identifier = Field(max_length=64)
    output: str = Field(min_length=1, max_length=128)
    correct: bool
    result_digest: Digest


class _Score(_Input):
    accuracy: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    cases: tuple[_CaseScore, ...] = Field(min_length=2, max_length=16)

    @property
    def correct(self) -> int:
        return sum(case.correct for case in self.cases)

    @property
    def behavior(self) -> tuple[str, ...]:
        return tuple(case.output for case in self.cases)

    @property
    def counts(self) -> dict[str, int | float]:
        return {"correct": self.correct, "total": len(self.cases), "accuracy": self.accuracy}

    @model_validator(mode="after")
    def exact_score(self) -> Self:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("diagnostic score repeats a case ID")
        if self.accuracy != self.correct / len(self.cases):
            raise ValueError("diagnostic accuracy differs from its exact-label counts")
        return self


class _ReferenceRow(_Input):
    reference_id: Identifier
    task_id: Identifier
    repeat_id: Identifier
    status: Literal["completed", "failed", "pending", "not-started"]
    reserved_capacity: dict[str, int]
    charged_usage: dict[str, int] | None
    scores: dict[str, _Score] | None
    error_type: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def consistent_evidence(self) -> Self:
        for cost in (self.reserved_capacity, self.charged_usage):
            if cost is not None and (set(cost) != set(DIAGNOSTIC_METERS)
                                     or any(value < 0 for value in cost.values()) or cost["proposals"] != 0):
                raise ValueError("reference usage must name every nonnegative meter and zero proposals")
        if self.charged_usage is not None and any(
            value > self.reserved_capacity[name] for name, value in self.charged_usage.items()
        ):
            raise ValueError("reference charges exceed its reservation")
        if self.status == "completed":
            if self.scores is None or self.charged_usage != self.reserved_capacity or self.error_type is not None:
                raise ValueError("completed reference lacks complete scores and exact exhaustive charges")
        elif self.scores is not None or self.charged_usage is not None:
            raise ValueError("unfinished reference scores and actual charges must remain unknown")
        if (self.status == "failed") != (self.error_type is not None):
            raise ValueError("only a failed reference carries an error type")
        return self


class _Gate(_Input):
    schema_version: Literal[1]
    look: Literal[1]
    selection_digest: Digest
    report_digest: Digest
    minimum_gain: float = Field(gt=0.0, le=1.0, allow_inf_nan=False)
    outcomes: dict[str, bool]
    status: Literal["confirmed", "vetoed"]

    @model_validator(mode="after")
    def joint_gate(self) -> Self:
        if set(self.outcomes) != set(SEARCH_MECHANISMS):
            raise ValueError("source confirmation must retain every C/W/D component")
        if (self.status == "confirmed") != all(self.outcomes.values()):
            raise ValueError("source confirmation status differs from its unchanged joint gate")
        return self


class _RetainedReport(_Input):
    schema_version: Literal[1]
    execution_kind: Literal["adaptive_cpu_inference", "cpu_search_inference"]
    model_execution_performed: Literal[True]
    gpu_execution_deferred: Literal[True]
    promotion_eligible: Literal[False]
    benchmark_result_claimed: Literal[False]
    role: Literal["practice", "confirmation"]
    study_digest: Digest
    runtime_digest: Digest
    selection_digest: Digest
    scorer: Literal["exact-label-v1"]
    policy: CpuSearchPolicy
    policy_digest: Digest
    scorer_digest: Digest
    conditions: dict[str, _Score]
    contrasts: dict[str, float]
    receipt_digests: dict[str, Digest]

    @model_validator(mode="after")
    def consistent_conditions(self) -> Self:
        if set(self.conditions) != set(_CONDITIONS) or set(self.receipt_digests) != set(_CONDITIONS):
            raise ValueError("source report must preserve exactly A/C/W/CW/D")
        if self.policy_digest != self.policy.digest:
            raise ValueError("source report policy digest changed")
        case_ids = tuple(case.case_id for case in self.conditions["A"].cases)
        if any(tuple(case.case_id for case in score.cases) != case_ids for score in self.conditions.values()):
            raise ValueError("source conditions must cover the same ordered cases")
        contrasts = {f"{a}-{b}": self.conditions[a].accuracy - self.conditions[b].accuracy
                     for a, b in (("C", "A"), ("W", "A"), ("CW", "C"), ("CW", "W"), ("D", "A"))}
        if self.contrasts != contrasts:
            raise ValueError("source contrasts differ from retained condition scores")
        return self


def _parse(model: type[_T], value: Any) -> _T:
    return model.model_validate_json(json.dumps(value, allow_nan=False))


def _score_identity(score: _Score) -> tuple[tuple[str, str, bool], ...]:
    # Request IDs differ between reference and source. Native result digests
    # therefore differ too; compare the measured case/output/correct triples.
    return tuple((case.case_id, case.output, case.correct) for case in score.cases)


def _score_cohort(score: _Score, reference: CpuDiagnosticReference, *, practice: bool) -> None:
    if practice and tuple(case.case_id for case in score.cases) != tuple(case.case_id for case in reference.practice):
        raise ValueError("diagnostic score differs from the complete ordered practice cohort")
    if any(case.output not in reference.model.output_tokens for case in score.cases):
        raise ValueError("diagnostic output is outside the source model vocabulary")


def _reference_metrics(reference: CpuDiagnosticReference, row: _ReferenceRow) -> dict[str, Any]:
    if (row.task_id, row.repeat_id) != (reference.task_id, reference.repeat_id):
        raise ValueError("diagnostic reference row differs from its pinned identity")
    if row.reserved_capacity != reference.required_usage:
        raise ValueError("diagnostic reference reservation differs from its full schedule")
    result: dict[str, Any] = {
        "reference_id": row.reference_id, "task_id": row.task_id, "repeat_id": row.repeat_id,
        "status": row.status, "reserved_capacity": row.reserved_capacity, "charged_usage": row.charged_usage,
        "error_type": row.error_type, "reference_record_digest": canonical_digest(row.to_dict()),
        "practice_case_ids": [case.case_id for case in reference.practice], "baseline_score": None,
        "mechanisms": {mechanism: None for mechanism in SEARCH_MECHANISMS},
    }
    if row.scores is None:
        return result
    if set(row.scores) != {"A", *(option.option_id for option in reference.options)}:
        raise ValueError("completed reference must cover baseline and every single-surface option exactly")
    for score in row.scores.values():
        _score_cohort(score, reference, practice=True)
    baseline = row.scores["A"]
    result["baseline_score"] = baseline.counts
    for mechanism in SEARCH_MECHANISMS:
        option_ids = ["A", *(option.option_id for option in reference.options_for(mechanism))]
        best = max(row.scores[option_id].correct for option_id in option_ids)
        groups: dict[tuple[str, ...], list[str]] = {}
        for option_id in option_ids:
            groups.setdefault(row.scores[option_id].behavior, []).append(option_id)
        result["mechanisms"][mechanism] = {
            "declared_option_count": len(option_ids) - 1, "evaluated_option_count": len(option_ids) - 1,
            "best_declared_practice_correct": best, "practice_total": len(baseline.cases),
            "best_declared_practice_accuracy": best / len(baseline.cases),
            "max_declared_practice_gain": (best - baseline.correct) / len(baseline.cases),
            "best_declared_option_ids": [option_id for option_id in option_ids if row.scores[option_id].correct == best],
            "option_scores": [{"option_id": option_id, **row.scores[option_id].counts,
                                "behavior_digest": canonical_digest(row.scores[option_id].behavior)}
                               for option_id in option_ids],
            "unique_output_vector_count": len(groups),
            "output_equivalence_groups": [{"option_ids": ids, "behavior_digest": canonical_digest(vector)}
                                          for vector, ids in groups.items()],
            "equivalence_scope": "same-output-tokens-on-this-ordered-practice-cohort-only",
        }
    return result


def _source_details(
    spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, source: CpuDiagnosticSourceRun,
    row: _CampaignRow, details: dict[str, Any],
) -> tuple[dict[str, Any], _RetainedReport, _RetainedReport, _Gate, dict[str, tuple[CpuPracticeScore, ...]]]:
    if set(details) != {"summary", "practice", "confirmation"}:
        raise ValueError("diagnostic source details require exactly summary, practice and confirmation")
    summary = details["summary"]
    if not isinstance(summary, dict):
        raise ValueError("diagnostic source summary must be an object")
    semantic = {key: value for key, value in summary.items()
                if key not in {"semantic_digest", "receipt_evidence_digests", "journal_digest"}}
    if (summary.get("semantic_digest") != source.summary_semantic_digest
            or canonical_digest(semantic) != source.summary_semantic_digest
            or row.summary_semantic_digest != source.summary_semantic_digest):
        raise ValueError("diagnostic source summary differs from its semantic pin")
    if (summary.get("study_digest") != source.study_digest or row.study_digest != source.study_digest
            or summary.get("selection_digest") != source.selection_digest
            or summary.get("runtime_digest") != spec.runtime_digest):
        raise ValueError("diagnostic source summary identity differs from the frozen plan")
    policy: CpuSearchPolicy = _parse(CpuSearchPolicy, summary.get("policy"))
    if (policy.name != source.policy or policy.name != row.policy or policy.order_seed != row.order_seed
            or summary.get("policy_digest") != policy.digest):
        raise ValueError("diagnostic source policy differs from the pinned search")
    practice: _RetainedReport = _parse(_RetainedReport, details["practice"])
    confirmation: _RetainedReport = _parse(_RetainedReport, details["confirmation"])
    for expected_role, retained in (("practice", practice), ("confirmation", confirmation)):
        if (retained.role != expected_role or retained.study_digest != source.study_digest
                or retained.runtime_digest != spec.runtime_digest or retained.selection_digest != source.selection_digest
                or retained.policy != policy):
            raise ValueError("diagnostic retained report differs from its source identity or role")
        for condition, score in retained.conditions.items():
            _score_cohort(score, reference, practice=expected_role == "practice")
            label = (f"confirmation-{condition}" if expected_role == "confirmation" else
                     {"A": "C-baseline", "C": "C-restored", "W": "W-restored", "CW": "practice-CW", "D": "D-restored"}
                     [condition])
            if summary.get("receipt_digests", {}).get(label) != retained.receipt_digests[condition]:
                raise ValueError("diagnostic source report differs from its pinned receipt identities")
    if {case.case_id for case in practice.conditions["A"].cases} & {
        case.case_id for case in confirmation.conditions["A"].cases
    }:
        raise ValueError("diagnostic source practice and confirmation case IDs overlap")
    if row.confirmation_scores != {key: score.accuracy for key, score in confirmation.conditions.items()}:
        raise ValueError("diagnostic confirmation scores differ from the source campaign")
    gate: _Gate = _parse(_Gate, summary.get("confirmation"))
    if (gate.selection_digest != source.selection_digest or gate.status != row.confirmation
            or gate.report_digest != canonical_digest(details["confirmation"])):
        raise ValueError("diagnostic confirmation gate differs from its source report")
    outcomes = {mechanism: confirmation.conditions[mechanism].accuracy - confirmation.conditions["A"].accuracy
                >= gate.minimum_gain for mechanism in SEARCH_MECHANISMS}
    if outcomes != gate.outcomes:
        raise ValueError("diagnostic confirmation outcomes differ from the unchanged minimum gain")
    if summary.get("audit_status") != ("completed" if gate.status == "confirmed" else "withheld"):
        raise ValueError("diagnostic source audit status changed after confirmation")
    histories = summary.get("histories")
    selections = summary.get("selected_options")
    restorations = summary.get("restoration_verified")
    if (not isinstance(histories, dict) or set(histories) != set(SEARCH_MECHANISMS)
            or not isinstance(selections, dict) or set(selections) != set(SEARCH_MECHANISMS)
            or restorations != {mechanism: True for mechanism in SEARCH_MECHANISMS}
            or any(type(value) is not bool for value in restorations.values())):
        raise ValueError("diagnostic source search histories, selection or restoration are incomplete")
    parsed_histories: dict[str, tuple[CpuPracticeScore, ...]] = {}
    for mechanism in SEARCH_MECHANISMS:
        raw_history = histories[mechanism]
        if not isinstance(raw_history, list):
            raise ValueError("diagnostic source history must be an ordered list")
        history = tuple(_parse(CpuPracticeScore, item) for item in raw_history)
        best = select_best(history)
        option_ids = {option.option_id for option in reference.options_for(mechanism)}
        if (not 2 <= len(history) <= len(option_ids) + 1 or any(item.option_id not in option_ids for item in history[1:])
                or any(item.total != len(reference.practice) for item in history)
                or history[0].correct != practice.conditions["A"].correct):
            raise ValueError("diagnostic source history differs from its declared space or baseline")
        if best.option_id != selections[mechanism] or best.correct != practice.conditions[mechanism].correct:
            raise ValueError("diagnostic source selection differs from its earliest practice winner")
        parsed_histories[mechanism] = history
    if len({len(history) for history in parsed_histories.values()}) != 1:
        raise ValueError("diagnostic source mechanisms do not have equal trial counts")
    return summary, practice, confirmation, gate, parsed_histories


def _search_metrics(
    spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, source: CpuDiagnosticSourceRun,
    source_row: _CampaignRow, details: dict[str, Any], row: _ReferenceRow, reference_metrics: dict[str, Any],
) -> dict[str, Any]:
    summary, practice, confirmation, gate, histories = _source_details(spec, reference, source, source_row, details)
    if row.scores is not None and _score_identity(row.scores["A"]) != _score_identity(practice.conditions["A"]):
        raise ValueError("exhaustive reference baseline differs from source practice outputs")
    mechanisms = {}
    for mechanism in SEARCH_MECHANISMS:
        history = histories[mechanism]
        selected = summary["selected_options"][mechanism]
        baseline = practice.conditions["A"]
        score = practice.conditions[mechanism]
        if row.scores is not None:
            for item in history:
                if item.correct != row.scores[item.option_id or "A"].correct:
                    raise ValueError("exhaustive reference differs from an observed source trial count")
            if _score_identity(row.scores[selected or "A"]) != _score_identity(score):
                raise ValueError("exhaustive reference differs from selected source practice outputs")
        best = baseline.correct
        curve = []
        for trial, observation in enumerate(history[1:], 1):
            best = max(best, observation.correct)
            curve.append({"trial": trial, "option_id": observation.option_id, "correct": observation.correct,
                          "total": observation.total, "accuracy": observation.correct / observation.total,
                          "best_correct": best, "best_accuracy": best / observation.total,
                          "gain_from_baseline": (best - baseline.correct) / observation.total})
        opportunity = reference_metrics["mechanisms"][mechanism]
        maximum = opportunity["best_declared_practice_accuracy"] if opportunity is not None else None
        maximum_gain = opportunity["max_declared_practice_gain"] if opportunity is not None else None
        gap = maximum - score.accuracy if maximum is not None else None
        if gap is not None and gap < 0:
            raise ValueError("source winner exceeds its complete declared practice reference")
        practice_gain = score.accuracy - baseline.accuracy
        mechanisms[mechanism] = {
            "selected_option_id": selected, "baseline_retained": selected is None,
            "baseline_practice_accuracy": baseline.accuracy, "selected_practice_accuracy": score.accuracy,
            "practice_gain": practice_gain, "trial_count": len(curve), "practice_curve": curve,
            "declared_option_count": len(reference.options_for(mechanism)),
            "searched_option_count": len(history) - 1, "reference_available": row.status == "completed",
            "best_declared_practice_accuracy": maximum, "max_declared_practice_gain": maximum_gain,
            "selected_gap_to_best_practice": gap,
            "opportunity_status": ("reference-unavailable" if opportunity is None else
                                   "no-declared-practice-improvement" if maximum_gain == 0 else
                                   "available-practice-improvement-missed" if gap is not None and gap > 0 else
                                   "declared-practice-best-reached"),
            "confirmation_baseline_accuracy": confirmation.conditions["A"].accuracy,
            "confirmation_selected_accuracy": confirmation.conditions[mechanism].accuracy,
            "confirmation_gain": confirmation.conditions[mechanism].accuracy - confirmation.conditions["A"].accuracy,
            "minimum_confirmation_gain": gate.minimum_gain, "confirmation_component_passed": gate.outcomes[mechanism],
            "component_veto_reason": (None if gate.outcomes[mechanism] else
                                      "unchanged-baseline-retained" if selected is None else
                                      "selected-confirmation-gain-below-frozen-minimum"),
            "practice_gain_failed_confirmation": practice_gain > 0 and not gate.outcomes[mechanism],
        }
    return {
        "run_id": source.run_id, "reference_id": reference.reference_id, "task_id": reference.task_id,
        "repeat_id": reference.repeat_id, "policy": source.policy, "reference_status": row.status,
        "selection_digest": source.selection_digest, "summary_semantic_digest": source.summary_semantic_digest,
        "joint_confirmation": gate.status, "veto_mechanisms": [key for key in SEARCH_MECHANISMS if not gate.outcomes[key]],
        "source_audit_status": summary["audit_status"], "mechanisms": mechanisms,
        "selected_CW": {"practice_accuracy": practice.conditions["CW"].accuracy,
                        "practice_gain": practice.conditions["CW"].accuracy - practice.conditions["A"].accuracy,
                        "confirmation_accuracy": confirmation.conditions["CW"].accuracy,
                        "confirmation_gain": confirmation.conditions["CW"].accuracy - confirmation.conditions["A"].accuracy,
                        "scope": "original-selected-composition-no-exhaustive-CW-optimum"},
    }


def _sum_usage(values: list[dict[str, int]]) -> dict[str, int]:
    return {name: sum(value[name] for value in values) for name in DIAGNOSTIC_METERS}


def _policy_summary(spec: CpuDiagnosticSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for policy in spec.policies:
        selected = [row for row in rows if row["policy"] == policy]
        mechanisms = {}
        for mechanism in SEARCH_MECHANISMS:
            values = [row["mechanisms"][mechanism] for row in selected]
            available = [value for value in values if value["reference_available"]]
            mechanisms[mechanism] = {
                "scheduled_runs": len(values), "reference_complete_runs": len(available),
                "reference_missing_runs": len(values) - len(available),
                "baseline_retained_runs": sum(value["baseline_retained"] for value in values),
                "positive_practice_gain_runs": sum(value["practice_gain"] > 0 for value in values),
                "confirmation_passed_runs": sum(value["confirmation_component_passed"] for value in values),
                "practice_gain_failed_confirmation_runs": sum(value["practice_gain_failed_confirmation"] for value in values),
                "no_declared_practice_improvement_runs": sum(value["max_declared_practice_gain"] == 0 for value in available),
                "available_practice_improvement_missed_runs": sum(
                    value["max_declared_practice_gain"] > 0 and value["selected_gap_to_best_practice"] > 0
                    for value in available
                ),
                "mean_selected_gap_if_all_references_complete": (
                    statistics.fmean(value["selected_gap_to_best_practice"] for value in available)
                    if len(available) == len(values) else None
                ),
                "scope": "descriptive-run-counts-and-mean-repeats-are-not-independent-tasks",
            }
        result[policy] = {"scheduled_runs": len(selected), "mechanisms": mechanisms}
    return result


def build_cpu_diagnostic_report(
    spec: CpuDiagnosticSpec, source_campaign_report: dict[str, Any], reference_rows: list[dict[str, Any]],
    search_details: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Describe complete retained evidence, preserving unknown reference results."""
    spec = _parse(CpuDiagnosticSpec, spec.to_dict())
    if (canonical_digest(source_campaign_report) != spec.campaign_report_digest
            or source_campaign_report.get("campaign_digest") != spec.campaign_digest
            or source_campaign_report.get("runtime_digest") != spec.runtime_digest
            or source_campaign_report.get("ledger_digest") != spec.campaign_ledger_digest):
        raise ValueError("diagnostic source campaign differs from its frozen report identity")
    source_semantic = {key: value for key, value in source_campaign_report.items()
                       if key not in {"semantic_digest", "ledger_digest"}}
    if source_campaign_report.get("semantic_digest") != canonical_digest(source_semantic):
        raise ValueError("diagnostic source campaign semantic digest is invalid")
    source_rows: list[_CampaignRow] = [_parse(_CampaignRow, row) for row in source_campaign_report["rows"]]
    sources = {row.run_id: row for row in source_rows}
    expected = {source.run_id for reference in spec.references for source in reference.source_runs}
    if len(sources) != len(source_rows) or set(sources) != expected or set(search_details) != expected:
        raise ValueError("diagnostic must retain the complete source search roster without duplicates")
    if any(row.status != "completed" for row in source_rows):
        raise ValueError("diagnostic source searches must be completed and sealed before reference execution")
    parsed: list[_ReferenceRow] = [_parse(_ReferenceRow, row) for row in reference_rows]
    references = {row.reference_id: row for row in parsed}
    if len(references) != len(parsed) or set(references) != {reference.reference_id for reference in spec.references}:
        raise ValueError("diagnostic must retain every reference, including missing results, without duplicates")
    ordered = [references[reference.reference_id] for reference in spec.references]
    reference_metrics = []
    search_metrics = []
    for reference in spec.references:
        row = references[reference.reference_id]
        metrics = _reference_metrics(reference, row)
        reference_metrics.append(metrics)
        trial_counts: set[int] = set()
        for source in reference.source_runs:
            source_row = sources[source.run_id]
            if (source_row.task_id, source_row.repeat_id, source_row.inference_seed) != (
                reference.task_id, reference.repeat_id, reference.inference_seed
            ):
                raise ValueError("diagnostic source row differs from its paired task/repeat")
            search = _search_metrics(spec, reference, source, source_row, search_details[source.run_id], row, metrics)
            search_metrics.append(search)
            trial_counts.update(value["trial_count"] for value in search["mechanisms"].values())
        if len(trial_counts) != 1:
            raise ValueError("diagnostic source policies do not have equal trial counts")
    unknown = [row.reference_id for row in ordered if row.status != "not-started" and row.charged_usage is None]
    report = {
        "schema_version": 1, "report_kind": "cpu_search_diagnostic", "diagnostic_digest": spec.digest,
        "source_campaign_digest": spec.campaign_digest, "source_campaign_report_digest": spec.campaign_report_digest,
        "source_campaign_ledger_digest": spec.campaign_ledger_digest, "runtime_digest": spec.runtime_digest,
        "model_execution_performed": False, "evidence_model_execution": "verified-cpu-inference",
        "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
        "inferential_statistics_computed": False, "confirmation_rule_changed": False,
        "reference_coverage": {"scheduled": len(ordered),
                               **{status: sum(row.status == status for row in ordered) for status in _STATUSES}},
        "reference_costs": {
            "scheduled_capacity": _sum_usage([row.reserved_capacity for row in ordered]),
            "attempted_capacity": _sum_usage([row.reserved_capacity for row in ordered if row.status != "not-started"]),
            "failed_or_pending_reserved_capacity": _sum_usage([
                row.reserved_capacity for row in ordered if row.status in {"failed", "pending"}
            ]),
            "known_charged_usage": _sum_usage([row.charged_usage for row in ordered if row.charged_usage is not None]),
            "unknown_charged_usage_reference_ids": unknown, "charged_usage_complete": not unknown,
            "scope": "reference-capacity-separate-from-original-policy-search-budgets",
            "reservation_rule": "failed-or-uncertain-reference-capacity-remains-charged-without-refund",
        },
        "reference_rows": reference_metrics, "search_rows": search_metrics,
        "policy_summary": _policy_summary(spec, search_metrics),
        "task_count": len({reference.task_id for reference in spec.references}),
        "reference_sampling_unit": "task-instance-with-nested-repeats",
        "scope": "post-search-descriptive-finite-practice-space-diagnostic-no-reselection-or-held-out-optimum",
    }
    report["semantic_digest"] = canonical_digest(report)
    return report
