"""Evaluator-epoch promotion operation (AC-885 Slice C2).

Decide via calibration tolerance + the autonomy dial whether to activate a candidate epoch, record
the promotion + human-decision metadata, and clear the quarantine on the promoted epoch's prior
scores. Pure: the caller supplies the CalibrationReport (from run_judge_calibration). The trigger
(CLI/stage) and the promote.py generalization are deferred. See docs/internal/ac-885-slice-c2-promotion-design.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from autocontext.ambient.charter import Charter
from autocontext.ambient.policy import decide
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRecord, EvaluatorEpochRegistry
from autocontext.execution.rubric_calibration import AlignmentTolerance, CalibrationReport


def _default_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ReviewerDecision:
    outcome: Literal["approved", "rejected"]
    reviewed_by: str
    reviewed_at: str


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    outcome: Literal["activated", "pending_review", "rejected", "blocked", "noop"]
    reason: str
    record: EvaluatorEpochRecord | None


class _QuarantineClearer(Protocol):
    def clear_quarantine_for_epoch(self, scenario: str, epoch_id: str) -> int: ...


def _promotion_metadata(
    report: CalibrationReport | None,
    *,
    requires_review: bool,
    decision: ReviewerDecision | None,
    previous_active: str,
    now: str,
) -> dict[str, Any]:
    alignment = report.alignment if report is not None else None
    variance = report.variance if report is not None else None
    return {
        "source_patch": None,
        "calibration_anchors": report.num_anchors if report is not None else 0,
        "calibration_anchor_ids": report.anchor_ids if report is not None else [],
        "alignment_delta": (
            {
                "mean_absolute_error": alignment.mean_absolute_error,
                "bias": alignment.bias,
                "correlation": alignment.correlation,
            }
            if alignment is not None
            else None
        ),
        "variance_delta": ({"variance": variance.variance, "std_dev": variance.std_dev} if variance is not None else None),
        "requires_review": requires_review,
        "decision": (
            {"reviewed_by": decision.reviewed_by, "reviewed_at": decision.reviewed_at, "outcome": decision.outcome}
            if decision is not None
            else None
        ),
        "promoted_at": now,
        "previous_active": previous_active,
    }


def promote_evaluator_epoch(
    registry: EvaluatorEpochRegistry,
    scenario: str,
    candidate_epoch_id: str,
    *,
    target_name: str,
    calibration_report: CalibrationReport | None,
    tolerance: AlignmentTolerance,
    charter: Charter,
    reviewer_decision: ReviewerDecision | None = None,
    sqlite: _QuarantineClearer | None = None,
    now_fn: Callable[[], str] = _default_now,
) -> PromotionOutcome:
    # ``scenario`` keys the registry/sqlite; ``target_name`` names the charter target the autonomy
    # policy is looked up by. They are deliberately separate: a valid charter forbids a target name
    # colliding with a registered scenario, so passing ``scenario`` into ``decide`` would KeyError.
    candidate = registry.load(scenario, candidate_epoch_id)
    if candidate is None:
        return PromotionOutcome("noop", "candidate missing", candidate)
    if candidate.activation_state == "active":
        # Already active: a prior promotion may have activated the epoch but crashed before clearing
        # its quarantine. Reconcile the clear so a retry is not a dead noop that strands rows.
        if sqlite is not None:
            sqlite.clear_quarantine_for_epoch(scenario, candidate_epoch_id)
            return PromotionOutcome("activated", "already active; quarantine reconciled", candidate)
        return PromotionOutcome("noop", "already active", candidate)

    now = now_fn()
    # Only trust a calibration report whose lineage matches THIS candidate: same evaluator epoch and
    # same domain. A report tagged another epoch (or domain) must contribute no calibration evidence,
    # so full autonomy cannot activate an uncalibrated evaluator off a passing but unrelated report.
    valid_report = (
        calibration_report
        if (
            calibration_report is not None
            and calibration_report.evaluator_epoch == candidate_epoch_id
            and calibration_report.domain == scenario
        )
        else None
    )
    calibration_passes = valid_report is not None and bool(tolerance.check(valid_report.alignment).get("passes"))
    incumbent = registry.active_for(scenario)
    previous_active = incumbent.epoch_id if incumbent is not None else ""
    policy = decide(charter, "promote_epoch", target_name)

    def _activate(decision: ReviewerDecision | None, requires_review: bool) -> PromotionOutcome:
        meta = _promotion_metadata(
            valid_report,
            requires_review=requires_review,
            decision=decision,
            previous_active=previous_active,
            now=now,
        )
        registry.promote(scenario, candidate_epoch_id, promotion=meta)
        if sqlite is not None:
            sqlite.clear_quarantine_for_epoch(scenario, candidate_epoch_id)
        return PromotionOutcome("activated", "promoted", registry.load(scenario, candidate_epoch_id))

    if reviewer_decision is not None:
        if reviewer_decision.outcome == "approved":
            return _activate(reviewer_decision, requires_review=policy.requires_approval)
        # rejected: record the decision, do not activate
        meta = _promotion_metadata(
            valid_report,
            requires_review=policy.requires_approval,
            decision=reviewer_decision,
            previous_active=previous_active,
            now=now,
        )
        registry.stamp_promotion(scenario, candidate_epoch_id, promotion=meta)
        return PromotionOutcome("rejected", "reviewer rejected", registry.load(scenario, candidate_epoch_id))

    if policy.requires_approval:
        meta = _promotion_metadata(valid_report, requires_review=True, decision=None, previous_active=previous_active, now=now)
        registry.stamp_promotion(scenario, candidate_epoch_id, promotion=meta)
        return PromotionOutcome("pending_review", policy.reason, registry.load(scenario, candidate_epoch_id))

    # autonomy full: decide on calibration
    if calibration_passes:
        return _activate(None, requires_review=False)
    return PromotionOutcome("blocked", "calibration did not pass tolerance", candidate)
