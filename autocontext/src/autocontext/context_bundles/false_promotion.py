"""Campaign-wide false-promotion control for adaptive bundle evaluation (AC-986)."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import NormalDist
from typing import Any, Literal

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.context_bundles.models import (
    ComparisonDecision,
    ComparisonResult,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    TrialLane,
    stable_digest,
)
from autocontext.util.file_lock import advisory_path_lock
from autocontext.util.json_io import read_json, write_json

FALSE_PROMOTION_SCHEMA_VERSION = 1
FalsePromotionStatus = Literal["reserved", "authorized", "rejected", "inconclusive", "blocked"]
FalsePromotionMethod = Literal["cluster_t", "bounded_hoeffding"]


@dataclass(frozen=True, slots=True)
class CampaignFalsePromotionPolicy:
    """A summable alpha-spending policy across every candidate in a campaign.

    Candidate ``i`` receives ``alpha * (1-decay) * decay**i``. The infinite
    sum is bounded by ``familywise_alpha``, so adding candidates adaptively
    cannot reopen or exceed the campaign's false-promotion budget.
    """

    familywise_alpha: float = 0.05
    allocation_decay: float = 0.5
    min_independent_confirmation_blocks: int = 2
    require_disjoint_lane_blocks: bool = True
    robust_method: FalsePromotionMethod = "cluster_t"
    effect_lower_bound: float = -1.0
    effect_upper_bound: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.familywise_alpha) or not 0.0 < self.familywise_alpha < 1.0:
            raise ValueError("familywise_alpha must be finite and between zero and one")
        if not math.isfinite(self.allocation_decay) or not 0.0 < self.allocation_decay < 1.0:
            raise ValueError("allocation_decay must be finite and between zero and one")
        if (
            isinstance(self.min_independent_confirmation_blocks, bool)
            or self.min_independent_confirmation_blocks < 2
        ):
            raise ValueError("min_independent_confirmation_blocks must be an integer of at least two")
        if not isinstance(self.require_disjoint_lane_blocks, bool):
            raise ValueError("require_disjoint_lane_blocks must be a boolean")
        if self.robust_method not in {"cluster_t", "bounded_hoeffding"}:
            raise ValueError("robust_method must be cluster_t or bounded_hoeffding")
        if (
            not math.isfinite(self.effect_lower_bound)
            or not math.isfinite(self.effect_upper_bound)
            or self.effect_lower_bound >= self.effect_upper_bound
        ):
            raise ValueError("effect bounds must be finite and increasing")

    def alpha_for_candidate(self, candidate_index: int) -> float:
        if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or candidate_index < 0:
            raise ValueError("candidate_index must be a non-negative integer")
        allocated = self.familywise_alpha * (1.0 - self.allocation_decay) * self.allocation_decay**candidate_index
        if allocated == 0.0:
            raise ValueError("campaign alpha allocation underflowed; no further promotion can be authorized")
        return allocated

    def to_dict(self) -> dict[str, Any]:
        return {
            "familywise_alpha": self.familywise_alpha,
            "allocation_decay": self.allocation_decay,
            "min_independent_confirmation_blocks": self.min_independent_confirmation_blocks,
            "require_disjoint_lane_blocks": self.require_disjoint_lane_blocks,
            "robust_method": self.robust_method,
            "effect_lower_bound": self.effect_lower_bound,
            "effect_upper_bound": self.effect_upper_bound,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignFalsePromotionPolicy:
        return cls(
            familywise_alpha=float(data["familywise_alpha"]),
            allocation_decay=float(data["allocation_decay"]),
            min_independent_confirmation_blocks=int(data["min_independent_confirmation_blocks"]),
            require_disjoint_lane_blocks=data["require_disjoint_lane_blocks"],
            robust_method=str(data.get("robust_method", "cluster_t")),  # type: ignore[arg-type]
            effect_lower_bound=float(data.get("effect_lower_bound", -1.0)),
            effect_upper_bound=float(data.get("effect_upper_bound", 1.0)),
        )


@dataclass(frozen=True, slots=True)
class CandidateRiskReservation:
    campaign_id: str
    candidate_digest: str
    incumbent_digest: str
    evaluator_epoch: str
    candidate_index: int
    allocated_alpha: float
    required_confidence_z: float
    confirmation_policy_digest: str
    status: FalsePromotionStatus = "reserved"
    reason: str | None = None
    evidence_digest: str | None = None
    independent_confirmation_blocks: int = 0
    independent_heldout_blocks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "candidate_digest": self.candidate_digest,
            "incumbent_digest": self.incumbent_digest,
            "evaluator_epoch": self.evaluator_epoch,
            "candidate_index": self.candidate_index,
            "allocated_alpha": self.allocated_alpha,
            "required_confidence_z": self.required_confidence_z,
            "confirmation_policy_digest": self.confirmation_policy_digest,
            "status": self.status,
            "reason": self.reason,
            "evidence_digest": self.evidence_digest,
            "independent_confirmation_blocks": self.independent_confirmation_blocks,
            "independent_heldout_blocks": self.independent_heldout_blocks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateRiskReservation:
        status = str(data["status"])
        if status not in {"reserved", "authorized", "rejected", "inconclusive", "blocked"}:
            raise ValueError("invalid false-promotion reservation status")
        reservation = cls(
            campaign_id=str(data["campaign_id"]),
            candidate_digest=str(data["candidate_digest"]),
            incumbent_digest=str(data["incumbent_digest"]),
            evaluator_epoch=str(data["evaluator_epoch"]),
            candidate_index=int(data["candidate_index"]),
            allocated_alpha=float(data["allocated_alpha"]),
            required_confidence_z=float(data["required_confidence_z"]),
            confirmation_policy_digest=str(data["confirmation_policy_digest"]),
            status=status,  # type: ignore[arg-type]
            reason=str(data["reason"]) if data.get("reason") is not None else None,
            evidence_digest=str(data["evidence_digest"]) if data.get("evidence_digest") is not None else None,
            independent_confirmation_blocks=int(data.get("independent_confirmation_blocks", 0)),
            independent_heldout_blocks=int(data.get("independent_heldout_blocks", 0)),
        )
        if reservation.candidate_index < 0 or not 0.0 < reservation.allocated_alpha < 1.0:
            raise ValueError("invalid false-promotion reservation allocation")
        if not math.isfinite(reservation.required_confidence_z) or reservation.required_confidence_z <= 0.0:
            raise ValueError("invalid false-promotion reservation confidence threshold")
        return reservation


@dataclass(frozen=True, slots=True)
class CampaignFalsePromotionResult:
    authorized: bool
    reason: str
    reservation: CandidateRiskReservation


class CampaignFalsePromotionController:
    """Persist risk reservations and authorize only dependence-aware evidence."""

    def __init__(self, root: Path, policy: CampaignFalsePromotionPolicy | None = None) -> None:
        self.root = root
        self.policy = policy or CampaignFalsePromotionPolicy()

    def reserve_confirmation_policy(
        self,
        campaign_id: str,
        candidate: ContextBundle,
        base_policy: ConfirmationPolicy,
    ) -> tuple[ConfirmationPolicy, CandidateRiskReservation]:
        incumbent_digest = candidate.parent_digest
        if incumbent_digest is None:
            raise ValueError("false-promotion control requires a candidate incumbent")
        with self._lock(campaign_id):
            reservations = self._load_unlocked(campaign_id)
            existing = next(
                (item for item in reservations if item.candidate_digest == candidate.digest),
                None,
            )
            if existing is None:
                candidate_index = len(reservations)
                allocated_alpha = self.policy.alpha_for_candidate(candidate_index)
                required_confidence_z = round(NormalDist().inv_cdf(1.0 - allocated_alpha / 2.0), 12)
                effective_policy = replace(
                    base_policy,
                    confidence_z=max(base_policy.confidence_z, required_confidence_z),
                )
                existing = CandidateRiskReservation(
                    campaign_id=campaign_id,
                    candidate_digest=candidate.digest,
                    incumbent_digest=incumbent_digest,
                    evaluator_epoch=candidate.evaluator_epoch,
                    candidate_index=candidate_index,
                    allocated_alpha=allocated_alpha,
                    required_confidence_z=required_confidence_z,
                    confirmation_policy_digest=stable_digest(effective_policy.to_dict()),
                )
                reservations.append(existing)
                self._write_unlocked(campaign_id, reservations)
            else:
                self._validate_lineage(existing, candidate, incumbent_digest)
                effective_policy = replace(
                    base_policy,
                    confidence_z=max(base_policy.confidence_z, existing.required_confidence_z),
                )
                if stable_digest(effective_policy.to_dict()) != existing.confirmation_policy_digest:
                    raise ValueError("candidate false-promotion reservation uses a different confirmation policy")
            return effective_policy, existing

    def record_terminal_decision(
        self,
        campaign_id: str,
        candidate: ContextBundle,
        comparison: ComparisonResult,
    ) -> CandidateRiskReservation:
        if comparison.decision not in {ComparisonDecision.REJECTED, ComparisonDecision.INCONCLUSIVE}:
            raise ValueError("only rejected or inconclusive comparisons are terminal without promotion")
        status: FalsePromotionStatus = comparison.decision.value  # type: ignore[assignment]
        return self._update_reservation(
            campaign_id,
            candidate,
            status=status,
            reason=comparison.reason,
        )

    def authorize_promotion(
        self,
        campaign_id: str,
        candidate: ContextBundle,
        comparison: ComparisonResult,
        trials: Sequence[MatchedTrial],
        confirmation_policy: ConfirmationPolicy,
    ) -> CampaignFalsePromotionResult:
        if comparison.decision != ComparisonDecision.CONFIRMED:
            raise ValueError("false-promotion authorization requires a confirmed comparison")
        evidence_digest = stable_digest(
            [trial.to_dict() for trial in sorted(trials, key=lambda item: item.pair_key)]
        )
        with self._lock(campaign_id):
            reservations = self._load_unlocked(campaign_id)
            reservation_index, reservation = self._find_reservation(reservations, candidate.digest)
            self._validate_lineage(reservation, candidate, reservation.incumbent_digest)
            if stable_digest(confirmation_policy.to_dict()) != reservation.confirmation_policy_digest:
                raise ValueError("promotion evidence used a policy different from its risk reservation")
            if reservation.status != "reserved":
                if reservation.evidence_digest != evidence_digest:
                    raise ValueError("terminal false-promotion reservation cannot be rebound to new evidence")
                return CampaignFalsePromotionResult(
                    authorized=reservation.status == "authorized",
                    reason=reservation.reason or "persisted false-promotion decision",
                    reservation=reservation,
                )

            authorized, reason, confirmation_blocks, heldout_blocks = self._evaluate_evidence(
                trials,
                confirmation_policy,
            )
            updated = replace(
                reservation,
                status="authorized" if authorized else "blocked",
                reason=reason,
                evidence_digest=evidence_digest,
                independent_confirmation_blocks=confirmation_blocks,
                independent_heldout_blocks=heldout_blocks,
            )
            reservations[reservation_index] = updated
            self._write_unlocked(campaign_id, reservations)
            return CampaignFalsePromotionResult(authorized=authorized, reason=reason, reservation=updated)

    def reservations(self, campaign_id: str) -> tuple[CandidateRiskReservation, ...]:
        with self._lock(campaign_id):
            return tuple(self._load_unlocked(campaign_id))

    def _evaluate_evidence(
        self,
        trials: Sequence[MatchedTrial],
        confirmation_policy: ConfirmationPolicy,
    ) -> tuple[bool, str, int, int]:
        lane_blocks: dict[TrialLane, dict[str, list[float]]] = {
            lane: defaultdict(list) for lane in TrialLane
        }
        for trial in trials:
            lane_blocks[trial.lane][trial.fixture_digest].append(trial.delta)

        if self.policy.require_disjoint_lane_blocks:
            lane_sets = {lane: set(blocks) for lane, blocks in lane_blocks.items()}
            if (
                lane_sets[TrialLane.SCREEN] & lane_sets[TrialLane.CONFIRMATION]
                or lane_sets[TrialLane.SCREEN] & lane_sets[TrialLane.HELDOUT]
                or lane_sets[TrialLane.CONFIRMATION] & lane_sets[TrialLane.HELDOUT]
            ):
                return False, "dependence blocks overlap across evaluation lanes", 0, 0

        confirmation_effects = _block_means(lane_blocks[TrialLane.CONFIRMATION])
        heldout_effects = _block_means(lane_blocks[TrialLane.HELDOUT])
        required_confirmation_blocks = max(
            confirmation_policy.min_confirmation_pairs,
            self.policy.min_independent_confirmation_blocks,
        )
        if len(confirmation_effects) < required_confirmation_blocks:
            return (
                False,
                "insufficient independent confirmation blocks after non-IID clustering",
                len(confirmation_effects),
                len(heldout_effects),
            )
        if len(heldout_effects) < confirmation_policy.min_heldout_pairs:
            return (
                False,
                "insufficient independent held-out blocks after non-IID clustering",
                len(confirmation_effects),
                len(heldout_effects),
            )
        max_looks = confirmation_policy.max_confirmation_pairs - confirmation_policy.min_confirmation_pairs + 1
        confidence_low: float | None
        if self.policy.robust_method == "bounded_hoeffding":
            all_effects = [effect for blocks in lane_blocks.values() for values in blocks.values() for effect in values]
            if any(
                effect < self.policy.effect_lower_bound or effect > self.policy.effect_upper_bound
                for effect in all_effects
            ):
                return (
                    False,
                    "paired effect falls outside the predeclared robust bounds",
                    len(confirmation_effects),
                    len(heldout_effects),
                )
            family_alpha = math.erfc(confirmation_policy.confidence_z / math.sqrt(2.0))
            look_alpha = family_alpha / max_looks
            width = self.policy.effect_upper_bound - self.policy.effect_lower_bound
            confidence_low = statistics.fmean(confirmation_effects) - width * math.sqrt(
                math.log(1.0 / look_alpha) / (2.0 * len(confirmation_effects))
            )
        else:
            _, confidence_low, _ = paired_confidence_interval(
                confirmation_effects,
                confirmation_policy.confidence_z,
                max_looks=max_looks,
            )
        if confidence_low is None or confidence_low <= confirmation_policy.min_effect:
            return (
                False,
                "campaign-adjusted block confidence interval does not clear the minimum effect",
                len(confirmation_effects),
                len(heldout_effects),
            )
        if statistics.fmean(heldout_effects) <= confirmation_policy.min_effect:
            return (
                False,
                "independent held-out blocks do not clear the minimum effect",
                len(confirmation_effects),
                len(heldout_effects),
            )
        return (
            True,
            "campaign alpha reservation and dependence-aware evidence authorized promotion",
            len(confirmation_effects),
            len(heldout_effects),
        )

    def _update_reservation(
        self,
        campaign_id: str,
        candidate: ContextBundle,
        *,
        status: FalsePromotionStatus,
        reason: str,
    ) -> CandidateRiskReservation:
        with self._lock(campaign_id):
            reservations = self._load_unlocked(campaign_id)
            reservation_index, reservation = self._find_reservation(reservations, candidate.digest)
            self._validate_lineage(reservation, candidate, reservation.incumbent_digest)
            if reservation.status == "authorized":
                raise ValueError("authorized false-promotion reservation cannot be downgraded")
            updated = replace(reservation, status=status, reason=reason)
            reservations[reservation_index] = updated
            self._write_unlocked(campaign_id, reservations)
            return updated

    def _load_unlocked(self, campaign_id: str) -> list[CandidateRiskReservation]:
        path = self._state_path(campaign_id)
        if not path.exists():
            return []
        data = read_json(path)
        if not isinstance(data, dict) or data.get("schema_version") != FALSE_PROMOTION_SCHEMA_VERSION:
            raise ValueError("invalid campaign false-promotion state")
        if data.get("campaign_id") != campaign_id:
            raise ValueError("campaign false-promotion state identity mismatch")
        if data.get("policy") != self.policy.to_dict():
            raise ValueError("campaign false-promotion policy changed after risk was reserved")
        raw_reservations = data.get("reservations")
        if not isinstance(raw_reservations, list):
            raise ValueError("campaign false-promotion reservations must be a list")
        reservations = [CandidateRiskReservation.from_dict(item) for item in raw_reservations]
        expected_digest = data.get("state_digest")
        payload = self._state_payload(campaign_id, reservations)
        if expected_digest != stable_digest(payload):
            raise ValueError("campaign false-promotion state digest mismatch")
        if [item.candidate_index for item in reservations] != list(range(len(reservations))):
            raise ValueError("campaign false-promotion candidate indices are not contiguous")
        for reservation in reservations:
            expected_alpha = self.policy.alpha_for_candidate(reservation.candidate_index)
            if reservation.allocated_alpha != expected_alpha:
                raise ValueError("campaign false-promotion alpha allocation mismatch")
        return reservations

    def _write_unlocked(
        self,
        campaign_id: str,
        reservations: Sequence[CandidateRiskReservation],
    ) -> None:
        payload = self._state_payload(campaign_id, reservations)
        write_json(self._state_path(campaign_id), {**payload, "state_digest": stable_digest(payload)})

    def _state_payload(
        self,
        campaign_id: str,
        reservations: Sequence[CandidateRiskReservation],
    ) -> dict[str, Any]:
        return {
            "schema_version": FALSE_PROMOTION_SCHEMA_VERSION,
            "campaign_id": campaign_id,
            "policy": self.policy.to_dict(),
            "reservations": [reservation.to_dict() for reservation in reservations],
        }

    @contextmanager
    def _lock(self, campaign_id: str) -> Iterator[None]:
        directory = self.root / _safe_segment(campaign_id)
        directory.mkdir(parents=True, exist_ok=True)
        with advisory_path_lock(directory / ".false-promotion.lock"):
            yield

    def _state_path(self, campaign_id: str) -> Path:
        return self.root / _safe_segment(campaign_id) / "false-promotion.json"

    @staticmethod
    def _find_reservation(
        reservations: Sequence[CandidateRiskReservation],
        candidate_digest: str,
    ) -> tuple[int, CandidateRiskReservation]:
        for index, reservation in enumerate(reservations):
            if reservation.candidate_digest == candidate_digest:
                return index, reservation
        raise ValueError("candidate has no durable false-promotion risk reservation")

    @staticmethod
    def _validate_lineage(
        reservation: CandidateRiskReservation,
        candidate: ContextBundle,
        incumbent_digest: str,
    ) -> None:
        if (
            reservation.campaign_id == ""
            or reservation.incumbent_digest != incumbent_digest
            or reservation.evaluator_epoch != candidate.evaluator_epoch
        ):
            raise ValueError("candidate lineage does not match its false-promotion reservation")


def _block_means(blocks: dict[str, list[float]]) -> list[float]:
    return [statistics.fmean(blocks[key]) for key in sorted(blocks)]


def _safe_segment(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("campaign identity must be one non-empty path segment")
    return value


__all__ = [
    "CampaignFalsePromotionController",
    "CampaignFalsePromotionPolicy",
    "CampaignFalsePromotionResult",
    "CandidateRiskReservation",
    "FalsePromotionStatus",
    "FalsePromotionMethod",
]
