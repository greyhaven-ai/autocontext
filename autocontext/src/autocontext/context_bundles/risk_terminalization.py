"""Recovery-safe terminal disposition for confirmed but stale candidates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from autocontext.context_bundles.fixture_reservations import persist_reservation_artifact
from autocontext.context_bundles.models import (
    BundleLifecycle,
    ComparisonDecision,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    TrialLane,
    stable_digest,
)

if TYPE_CHECKING:
    from autocontext.context_bundles.false_promotion import (
        CampaignFalsePromotionController,
        CampaignFalsePromotionResult,
    )
    from autocontext.context_bundles.models import ComparisonResult
    from autocontext.context_bundles.promotion import (
        ContextBundlePromotionCoordinator,
        ContextBundlePromotionResult,
    )


def reject_stale_risk_reservation(
    controller: CampaignFalsePromotionController,
    campaign_id: str,
    candidate: ContextBundle,
    trials: Sequence[MatchedTrial],
    reason: str,
) -> CampaignFalsePromotionResult:
    """Bind a stale-parent rejection to the exact reserved fixture evidence."""

    from autocontext.context_bundles.false_promotion import CampaignFalsePromotionResult

    evidence_digest = stable_digest([trial.to_dict() for trial in sorted(trials, key=lambda item: item.pair_key)])
    with controller._lock(campaign_id):  # noqa: SLF001 - controller transaction
        state = controller._load_unlocked(campaign_id)  # noqa: SLF001
        index, reservation = controller._find_reservation(  # noqa: SLF001
            state.reservations,
            candidate.digest,
        )
        controller._validate_lineage(  # noqa: SLF001
            reservation,
            candidate,
            reservation.incumbent_digest,
        )
        fixture = controller._find_fixture_reservation(  # noqa: SLF001
            state.fixture_reservations,
            candidate.digest,
        )
        controller._validate_trials_match_fixture_reservation(trials, fixture)  # noqa: SLF001
        if reservation.status == "authorized":
            # Statistical authorization was valid at its linearization point
            # and its terminal artifact is immutable. The later stale-parent
            # serving disposition supersedes promotion authority in the store;
            # it does not rewrite history in the campaign risk ledger.
            persist_reservation_artifact(controller.root, campaign_id, reservation, fixture)
            return CampaignFalsePromotionResult(False, reason, reservation)
        if reservation.status != "reserved":
            if reservation.status != "rejected" or reservation.reason != reason or reservation.evidence_digest != evidence_digest:
                raise ValueError("terminal false-promotion reservation cannot change stale disposition")
            persist_reservation_artifact(controller.root, campaign_id, reservation, fixture)
            return CampaignFalsePromotionResult(False, reason, reservation)
        confirmation_blocks = len({trial.fixture_digest for trial in trials if trial.lane == TrialLane.CONFIRMATION})
        heldout_blocks = len({trial.fixture_digest for trial in trials if trial.lane == TrialLane.HELDOUT})
        updated = replace(
            reservation,
            status="rejected",
            reason=reason,
            evidence_digest=evidence_digest,
            independent_confirmation_blocks=confirmation_blocks,
            independent_heldout_blocks=heldout_blocks,
        )
        state.reservations[index] = updated
        controller._write_unlocked(campaign_id, state)  # noqa: SLF001
        persist_reservation_artifact(controller.root, campaign_id, updated, fixture)
        return CampaignFalsePromotionResult(False, reason, updated)


def confirmation_blocks_ready(
    controller: CampaignFalsePromotionController | None,
    trials: list[MatchedTrial],
    policy: ConfirmationPolicy,
) -> bool:
    return controller is None or controller.confirmation_blocks_ready(trials, policy)


def recover_stale_confirmed_candidate(
    coordinator: ContextBundlePromotionCoordinator,
    scenario: str,
    candidate: ContextBundle,
    incumbent: ContextBundle,
) -> ContextBundlePromotionResult | None:
    """Terminalize stale confirmed evidence without consulting new evaluator code."""

    from autocontext.context_bundles.evaluator_plan import evaluator_plan_binding, require_evaluator_plan
    from autocontext.context_bundles.store_transactions import stale_terminalization_path

    record = coordinator.store.candidate(scenario, candidate.digest)
    if record.lifecycle != BundleLifecycle.CONFIRMED:
        return None
    pointer = coordinator.store.active_pointer(scenario)
    if pointer is None:
        raise ValueError("confirmed candidate recovery requires an active bundle pointer")
    active_digest = str(pointer["bundle_digest"])
    marker_exists = stale_terminalization_path(coordinator.store, scenario, candidate.digest).exists()
    if not marker_exists and active_digest in {candidate.digest, candidate.parent_digest}:
        return None
    _, plan_digest = evaluator_plan_binding(coordinator.store, scenario, candidate.digest)
    plan = require_evaluator_plan(coordinator.store, scenario, candidate.digest, plan_digest)
    if (
        plan.get("candidate_digest") != candidate.digest
        or plan.get("incumbent_digest") != incumbent.digest
        or plan.get("cohort") != coordinator.cohort
    ):
        raise ValueError("persisted stale candidate evaluator plan has incompatible lineage")
    raw_policy = plan.get("confirmation_policy")
    if not isinstance(raw_policy, dict) or plan.get("confirmation_policy_digest") != stable_digest(raw_policy):
        raise ValueError("persisted stale candidate evaluator plan has an invalid policy")
    policy = ConfirmationPolicy.from_dict(raw_policy)
    coordinator.store.migrate_terminal_matched_evidence(scenario, candidate.digest, policy=policy)
    comparison = coordinator.store.replay_matched_trials(
        scenario,
        candidate.digest,
        policy=policy,
        evaluator_plan_digest=plan_digest,
    )
    if comparison.decision != ComparisonDecision.CONFIRMED:
        raise ValueError("persisted stale candidate evidence is not confirmed")
    trials = tuple(coordinator.store.matched_trials(scenario, candidate.digest))
    return terminalize_stale_candidate(
        coordinator,
        scenario,
        candidate,
        comparison,
        trials,
        0,
        plan_digest,
    )


def terminalize_stale_candidate(
    coordinator: ContextBundlePromotionCoordinator,
    scenario: str,
    candidate: ContextBundle,
    comparison: ComparisonResult,
    trials: tuple[MatchedTrial, ...],
    evaluated_pairs: int,
    evaluator_plan_digest: str,
) -> ContextBundlePromotionResult | None:
    """Persist evidence before removing a stale confirmed sibling from resume."""

    from autocontext.context_bundles.promotion import ContextBundlePromotionResult
    from autocontext.context_bundles.store_transactions import (
        begin_stale_confirmed_candidate_terminalization,
        finalize_stale_confirmed_candidate_terminalization,
    )

    _, matched_evidence_digest, _ = coordinator.store.matched_evidence_binding(
        scenario,
        candidate.digest,
    )
    marker = begin_stale_confirmed_candidate_terminalization(
        coordinator.store,
        scenario,
        candidate.digest,
        candidate.parent_digest,
        evaluator_plan_digest=evaluator_plan_digest,
        matched_evidence_digest=matched_evidence_digest,
    )
    if marker is None:
        return None
    reason = str(marker["reason"])
    false_result = None
    if coordinator.false_promotion_controller is not None:
        assert coordinator.campaign_id is not None
        false_result = reject_stale_risk_reservation(
            coordinator.false_promotion_controller,
            coordinator.campaign_id,
            candidate,
            trials,
            reason,
        )
    coordinator._persist_negative_result(  # noqa: SLF001
        scenario,
        candidate,
        comparison,
        trials,
        false_promotion_result=false_result,
        evaluator_plan_digest=evaluator_plan_digest,
        terminal_reason=reason,
    )
    finalize_stale_confirmed_candidate_terminalization(
        coordinator.store,
        scenario,
        candidate.digest,
        marker,
    )
    return ContextBundlePromotionResult(
        candidate.digest,
        comparison,
        None,
        evaluated_pairs,
        false_promotion_result=false_result,
    )


__all__ = [
    "confirmation_blocks_ready",
    "recover_stale_confirmed_candidate",
    "reject_stale_risk_reservation",
    "terminalize_stale_candidate",
]
