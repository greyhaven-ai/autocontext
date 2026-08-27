from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from autocontext.context_bundles.evaluation_control import ContextEvaluationControl, evaluate_matched_pair
from autocontext.context_bundles.evaluator_plan import (
    bind_evaluator_plan,
    build_evaluator_plan,
    evaluator_plan_binding,
    require_live_evaluator_plan,
)
from autocontext.context_bundles.false_promotion import (
    CampaignFalsePromotionController,
    CampaignFalsePromotionResult,
)
from autocontext.context_bundles.models import (
    BundleLifecycle,
    ComparisonDecision,
    ComparisonResult,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    PromotionArtifact,
    TrialLane,
    stable_digest,
)
from autocontext.context_bundles.risk_terminalization import (
    confirmation_blocks_ready,
    recover_stale_confirmed_candidate,
    terminalize_stale_candidate,
)
from autocontext.context_bundles.store import ContextBundleStore


@dataclass(frozen=True, slots=True)
class ContextBundleEvaluationUnit:
    fixture: str
    fixture_digest: str
    seed: int
    lane: TrialLane

    def __post_init__(self) -> None:
        if not self.fixture.strip() or not self.fixture_digest.strip():
            raise ValueError("context bundle evaluation fixtures require names and digests")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("context bundle evaluation seed must be an integer")


@dataclass(frozen=True, slots=True)
class ContextBundleEvaluationOutcome:
    score: float
    valid: bool = True


class ContextBundleEvaluator(Protocol):
    def evaluate(
        self,
        bundle: ContextBundle,
        unit: ContextBundleEvaluationUnit,
    ) -> ContextBundleEvaluationOutcome: ...


PromotionAuditOutcome = Literal["advisory", "review_required", "safe_pause_recommended"]


class ContextBundlePromotionAudit(Protocol):
    def review_pre_promotion(
        self,
        candidate: ContextBundle,
        comparison: ComparisonResult,
        trials: tuple[MatchedTrial, ...],
        *,
        cancellation_event: threading.Event | None = None,
    ) -> PromotionAuditOutcome | None: ...


class ContextBundleLifecycleAudit(Protocol):
    def review_checkpoint(
        self,
        checkpoint: Literal["pre_promotion", "inconclusive_gate", "integrity_alert", "final_completion"],
        evidence: Mapping[str, Any],
        *,
        cancellation_event: threading.Event | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ContextBundlePromotionResult:
    candidate_digest: str
    comparison: ComparisonResult
    promotion: PromotionArtifact | None
    evaluated_pairs: int
    audit_policy_outcome: PromotionAuditOutcome | None = None
    false_promotion_result: CampaignFalsePromotionResult | None = None


class ContextBundlePromotionCoordinator:
    def __init__(
        self,
        store: ContextBundleStore,
        evaluator: ContextBundleEvaluator,
        units: tuple[ContextBundleEvaluationUnit, ...],
        *,
        cohort: str,
        policy: ConfirmationPolicy | None = None,
        audit_checkpoint: ContextBundlePromotionAudit | None = None,
        lifecycle_auditor: ContextBundleLifecycleAudit | None = None,
        cancellation_event: threading.Event | None = None,
        false_promotion_controller: CampaignFalsePromotionController | None = None,
        campaign_id: str | None = None,
    ) -> None:
        if not cohort.strip():
            raise ValueError("context bundle promotion cohort is required")
        if not units:
            raise ValueError("context bundle promotion requires matched evaluation units")
        if false_promotion_controller is not None and (campaign_id is None or not campaign_id.strip()):
            raise ValueError("campaign_id is required when false-promotion control is enabled")
        pair_identities = {(unit.fixture_digest, unit.seed) for unit in units}
        if len(pair_identities) != len(units):
            raise ValueError("context bundle promotion units must have unique fixture/seed identities")
        self.store = store
        self.evaluator = evaluator
        self.units = units
        self.cohort = cohort
        self.policy = policy or ConfirmationPolicy()
        self.audit_checkpoint = audit_checkpoint
        self.lifecycle_auditor = lifecycle_auditor
        self.cancellation_event = cancellation_event
        self.false_promotion_controller = false_promotion_controller
        self.campaign_id = campaign_id

    def evaluate_candidate(
        self,
        scenario: str,
        digest: str,
        *,
        deadline: float | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> ContextBundlePromotionResult:
        control = ContextEvaluationControl(deadline, cancellation_check, self.cancellation_event)
        control.check()
        candidate = self.store.load_bundle(scenario, digest)
        incumbent = self.store.load_bundle(scenario, candidate.parent_digest) if candidate.parent_digest is not None else None
        if incumbent is None:
            raise ValueError("context bundle candidate must have an incumbent parent")
        stale = recover_stale_confirmed_candidate(self, scenario, candidate, incumbent)
        if stale is not None:
            return stale
        effective_policy = self.policy
        if self.false_promotion_controller is not None:
            assert self.campaign_id is not None
            effective_policy, _ = self.false_promotion_controller.reserve_confirmation_policy(
                self.campaign_id,
                candidate,
                self.policy,
            )
        ordered_units = tuple(
            unit
            for lane in (TrialLane.SCREEN, TrialLane.CONFIRMATION, TrialLane.HELDOUT)
            for unit in self.units
            if unit.lane == lane
        )
        if self.false_promotion_controller is not None:
            assert self.campaign_id is not None
            self.false_promotion_controller.reserve_fixture_plan(
                self.campaign_id,
                candidate,
                self.cohort,
                tuple((unit.lane, unit.fixture_digest, unit.seed) for unit in ordered_units),
                effective_policy,
            )
        evaluator_plan_digest = bind_evaluator_plan(
            self.store,
            scenario,
            digest,
            build_evaluator_plan(
                self.evaluator,
                candidate,
                incumbent,
                ordered_units,
                cohort=self.cohort,
                policy=effective_policy,
            ),
        )
        existing = self.store.matched_trials(scenario, digest)
        planned = {(unit.fixture_digest, unit.seed): unit for unit in ordered_units}
        for trial in existing:
            unit = planned.get((trial.fixture_digest, trial.seed))
            if unit is None or unit.fixture != trial.fixture or unit.lane != trial.lane or trial.cohort != self.cohort:
                raise ValueError("persisted matched evidence does not match the configured evaluation plan")
        completed = {(trial.fixture_digest, trial.seed) for trial in existing}
        record = self.store.candidate(scenario, digest)
        comparison: ComparisonResult | None = None
        if existing:
            if record.lifecycle in {BundleLifecycle.CONFIRMED, BundleLifecycle.REJECTED, BundleLifecycle.ACTIVE}:
                self._require_evaluator_plan(scenario, candidate, incumbent, evaluator_plan_digest)
                if record.lifecycle in {BundleLifecycle.CONFIRMED, BundleLifecycle.REJECTED}:
                    self.store.migrate_terminal_matched_evidence(
                        scenario,
                        digest,
                        policy=effective_policy,
                    )
                comparison = self.store.replay_matched_trials(
                    scenario,
                    digest,
                    policy=effective_policy,
                    evaluator_plan_digest=evaluator_plan_digest,
                )
            else:
                self._require_evaluator_plan(scenario, candidate, incumbent, evaluator_plan_digest)
                comparison = self.store.record_matched_trials(
                    scenario,
                    digest,
                    [],
                    policy=effective_policy,
                    evaluator_plan_digest=evaluator_plan_digest,
                )
        evaluated_pairs = 0
        if comparison is not None:
            if comparison.decision in {ComparisonDecision.REJECTED, ComparisonDecision.INCONCLUSIVE}:
                return self._terminal_result(
                    scenario,
                    candidate,
                    incumbent,
                    comparison,
                    evaluated_pairs,
                    evaluator_plan_digest,
                )
            if comparison.decision == ComparisonDecision.CONFIRMED:
                if record.lifecycle == BundleLifecycle.ACTIVE:
                    self._require_evaluator_plan(scenario, candidate, incumbent, evaluator_plan_digest)
                    recovered = self.store.promote(
                        scenario,
                        candidate.digest,
                        cohort=self.cohort,
                        rationale=comparison.reason,
                        evaluator_plan_digest=evaluator_plan_digest,
                    )
                    return ContextBundlePromotionResult(
                        candidate.digest,
                        comparison,
                        recovered,
                        evaluated_pairs,
                    )
                return self._complete_promotion(
                    scenario,
                    candidate,
                    incumbent,
                    comparison,
                    effective_policy,
                    evaluated_pairs,
                    evaluator_plan_digest,
                )

        for pair_index, unit in enumerate(ordered_units):
            if (unit.fixture_digest, unit.seed) in completed:
                continue
            if unit.lane == TrialLane.SCREEN and comparison is not None:
                if comparison.decision != ComparisonDecision.NEEDS_SCREEN:
                    continue
            elif unit.lane == TrialLane.CONFIRMATION:
                if comparison is None or comparison.decision == ComparisonDecision.NEEDS_SCREEN:
                    continue
                if comparison.decision == ComparisonDecision.NEEDS_HELDOUT and confirmation_blocks_ready(
                    self.false_promotion_controller,
                    self.store.matched_trials(scenario, digest),
                    effective_policy,
                ):
                    continue
                if comparison.decision not in {
                    ComparisonDecision.NEEDS_CONFIRMATION,
                    ComparisonDecision.NEEDS_HELDOUT,
                }:
                    continue
            elif unit.lane == TrialLane.HELDOUT:
                if comparison is None or comparison.decision != ComparisonDecision.NEEDS_HELDOUT:
                    continue
                if not confirmation_blocks_ready(
                    self.false_promotion_controller,
                    self.store.matched_trials(scenario, digest),
                    effective_policy,
                ):
                    continue
            try:
                trial = self._evaluate_pair(
                    scenario,
                    candidate,
                    incumbent,
                    unit,
                    pair_index,
                    evaluator_plan_digest,
                    control,
                )
            except Exception as exc:
                self._review_lifecycle_checkpoint(
                    "integrity_alert",
                    {
                        "scenario": scenario,
                        "candidate": candidate.to_dict(),
                        "incumbent_digest": incumbent.digest,
                        "cohort": self.cohort,
                        "evaluation_unit": {
                            "fixture": unit.fixture,
                            "fixture_digest": unit.fixture_digest,
                            "seed": unit.seed,
                            "lane": unit.lane.value,
                        },
                        "failure_type": type(exc).__name__,
                        "failure_detail": str(exc),
                    },
                )
                raise
            self._require_evaluator_plan(scenario, candidate, incumbent, evaluator_plan_digest)
            comparison = self.store.record_matched_trials(
                scenario,
                digest,
                [trial],
                policy=effective_policy,
                evaluator_plan_digest=evaluator_plan_digest,
            )
            completed.add((unit.fixture_digest, unit.seed))
            evaluated_pairs += 1
            if comparison.decision in {ComparisonDecision.REJECTED, ComparisonDecision.INCONCLUSIVE}:
                return self._terminal_result(
                    scenario,
                    candidate,
                    incumbent,
                    comparison,
                    evaluated_pairs,
                    evaluator_plan_digest,
                )
            if comparison.decision == ComparisonDecision.CONFIRMED:
                return self._complete_promotion(
                    scenario,
                    candidate,
                    incumbent,
                    comparison,
                    effective_policy,
                    evaluated_pairs,
                    evaluator_plan_digest,
                )
        if comparison is None:
            raise ValueError("context bundle promotion plan did not contain a usable evaluation unit")
        return ContextBundlePromotionResult(digest, comparison, None, evaluated_pairs)

    def _terminal_result(
        self,
        scenario: str,
        candidate: ContextBundle,
        incumbent: ContextBundle,
        comparison: ComparisonResult,
        evaluated_pairs: int,
        evaluator_plan_digest: str,
    ) -> ContextBundlePromotionResult:
        trials = tuple(self.store.matched_trials(scenario, candidate.digest))
        self._persist_negative_result(
            scenario,
            candidate,
            comparison,
            trials,
            evaluator_plan_digest=evaluator_plan_digest,
        )
        audit_outcome: PromotionAuditOutcome | None = None
        if comparison.decision == ComparisonDecision.INCONCLUSIVE:
            audit_outcome = self._review_lifecycle_checkpoint(
                "inconclusive_gate",
                self._audit_evidence(scenario, candidate, incumbent, comparison, trials),
            )
        if self.false_promotion_controller is not None:
            assert self.campaign_id is not None
            self.false_promotion_controller.record_terminal_decision(
                self.campaign_id,
                candidate,
                comparison,
            )
        record = self.store.candidate(scenario, candidate.digest)
        if comparison.decision == ComparisonDecision.INCONCLUSIVE:
            if record.lifecycle not in {BundleLifecycle.SCREENED, BundleLifecycle.REJECTED}:
                raise ValueError("inconclusive candidate has an invalid resumable lifecycle")
            if record.lifecycle == BundleLifecycle.SCREENED:
                self.store.reject(
                    scenario,
                    candidate.digest,
                    rationale=f"terminal inconclusive evaluation: {comparison.reason}",
                )
        return ContextBundlePromotionResult(
            candidate.digest,
            comparison,
            None,
            evaluated_pairs,
            audit_policy_outcome=audit_outcome,
        )

    def _complete_promotion(
        self,
        scenario: str,
        candidate: ContextBundle,
        incumbent: ContextBundle,
        comparison: ComparisonResult,
        policy: ConfirmationPolicy,
        evaluated_pairs: int,
        evaluator_plan_digest: str,
    ) -> ContextBundlePromotionResult:
        trials = tuple(self.store.matched_trials(scenario, candidate.digest))
        stale = terminalize_stale_candidate(
            self,
            scenario,
            candidate,
            comparison,
            trials,
            evaluated_pairs,
            evaluator_plan_digest,
        )
        if stale is not None:
            return stale
        audit_outcome: PromotionAuditOutcome | None = None
        if self.lifecycle_auditor is not None:
            audit_outcome = self._review_lifecycle_checkpoint(
                "pre_promotion",
                self._audit_evidence(scenario, candidate, incumbent, comparison, trials),
            )
        elif self.audit_checkpoint is not None:
            audit_outcome = self.audit_checkpoint.review_pre_promotion(
                candidate,
                comparison,
                trials,
                cancellation_event=self.cancellation_event,
            )
        if audit_outcome in {"review_required", "safe_pause_recommended"}:
            return ContextBundlePromotionResult(
                candidate.digest,
                comparison,
                None,
                evaluated_pairs,
                audit_policy_outcome=audit_outcome,
            )
        false_promotion_result: CampaignFalsePromotionResult | None = None
        if self.false_promotion_controller is not None:
            assert self.campaign_id is not None
            false_promotion_result = self.false_promotion_controller.authorize_promotion(
                self.campaign_id,
                candidate,
                comparison,
                trials,
                policy,
            )
            if not false_promotion_result.authorized:
                self._persist_negative_result(
                    scenario,
                    candidate,
                    comparison,
                    trials,
                    false_promotion_result=false_promotion_result,
                    evaluator_plan_digest=evaluator_plan_digest,
                )
                record = self.store.candidate(scenario, candidate.digest)
                if record.lifecycle == BundleLifecycle.CONFIRMED:
                    reservation = false_promotion_result.reservation
                    reservation_digest = stable_digest(reservation.to_dict())
                    self.store.reject(
                        scenario,
                        candidate.digest,
                        rationale=(
                            f"false-promotion gate blocked: {false_promotion_result.reason}; "
                            f"reservation_sha256={reservation_digest}; "
                            f"evidence_sha256={reservation.evidence_digest}"
                        ),
                    )
                elif record.lifecycle != BundleLifecycle.REJECTED:
                    raise ValueError("false-promotion block cannot terminalize the candidate lifecycle")
                return ContextBundlePromotionResult(
                    candidate.digest,
                    comparison,
                    None,
                    evaluated_pairs,
                    audit_policy_outcome=audit_outcome,
                    false_promotion_result=false_promotion_result,
                )
        self._persist_verified_causal_attribution(scenario, candidate, incumbent)
        stale = terminalize_stale_candidate(
            self,
            scenario,
            candidate,
            comparison,
            trials,
            evaluated_pairs,
            evaluator_plan_digest,
        )
        if stale is not None:
            return stale
        self._require_evaluator_plan(scenario, candidate, incumbent, evaluator_plan_digest)
        promotion = self.store.promote(
            scenario,
            candidate.digest,
            cohort=self.cohort,
            rationale=comparison.reason,
            evaluator_plan_digest=evaluator_plan_digest,
        )
        return ContextBundlePromotionResult(
            candidate.digest,
            comparison,
            promotion,
            evaluated_pairs,
            audit_policy_outcome=audit_outcome,
            false_promotion_result=false_promotion_result,
        )

    def _audit_evidence(
        self,
        scenario: str,
        candidate: ContextBundle,
        incumbent: ContextBundle,
        comparison: ComparisonResult,
        trials: tuple[MatchedTrial, ...],
    ) -> dict[str, Any]:
        return {
            "scenario": scenario,
            "candidate": candidate.to_dict(),
            "incumbent_digest": incumbent.digest,
            "cohort": self.cohort,
            "comparison": comparison.to_dict(),
            "trials": [trial.to_dict() for trial in trials],
        }

    def _review_lifecycle_checkpoint(
        self,
        checkpoint: Literal["pre_promotion", "inconclusive_gate", "integrity_alert", "final_completion"],
        evidence: Mapping[str, Any],
    ) -> PromotionAuditOutcome | None:
        if self.lifecycle_auditor is None:
            return None
        try:
            audit = self.lifecycle_auditor.review_checkpoint(
                checkpoint,
                evidence,
                cancellation_event=self.cancellation_event,
            )
        except Exception:
            return "safe_pause_recommended"
        if audit is None:
            return None
        if getattr(audit, "status", "completed") != "completed":
            return "safe_pause_recommended"
        outcome = getattr(audit, "policy_outcome", None)
        if outcome in {"advisory", "review_required", "safe_pause_recommended"}:
            return cast(PromotionAuditOutcome, outcome)
        return None

    def _persist_negative_result(
        self,
        scenario: str,
        candidate: ContextBundle,
        comparison: ComparisonResult,
        trials: tuple[MatchedTrial, ...],
        *,
        false_promotion_result: CampaignFalsePromotionResult | None = None,
        evaluator_plan_digest: str,
        terminal_reason: str | None = None,
    ) -> None:
        from autocontext.analytics.negative_result_ledger import build_negative_result_ledger

        record = self.store.candidate(scenario, candidate.digest)
        evidence_uri, evidence_digest, policy_digest = self.store.matched_evidence_binding(
            scenario,
            candidate.digest,
        )
        blocked = false_promotion_result is not None and not false_promotion_result.authorized
        if (
            false_promotion_result is not None
            and not false_promotion_result.authorized
            and false_promotion_result.reservation.evidence_digest is None
        ):
            raise ValueError("blocked false-promotion reservation is missing its evidence digest")
        event_type = (
            "candidate_rejected"
            if blocked or terminal_reason is not None or comparison.decision == ComparisonDecision.REJECTED
            else "evaluation_failed"
        )
        decision_identity = (
            "stale-incumbent"
            if terminal_reason is not None
            else "false-promotion-blocked"
            if blocked
            else comparison.decision.value
        )
        result_id = f"context-bundle-{candidate.digest}-{decision_identity}"
        failure_kind = (
            "verification_failed"
            if blocked
            or terminal_reason is not None
            or comparison.decision == ComparisonDecision.INCONCLUSIVE
            or "validity" in comparison.reason
            else "score_regression"
        )
        negative_reason = (
            false_promotion_result.reason if false_promotion_result is not None else terminal_reason or comparison.reason
        )
        evidence_refs = [
            {
                "uri": f"{evidence_uri}#sha256={evidence_digest}",
                "summary": (
                    f"Persisted matched candidate/incumbent evaluation pairs; confirmation_policy_sha256={policy_digest}."
                ),
            }
        ]
        if blocked and false_promotion_result is not None:
            reservation = false_promotion_result.reservation
            reservation_digest = stable_digest(reservation.to_dict())
            if self.false_promotion_controller is None or self.campaign_id is None:
                raise RuntimeError("false-promotion evidence has no campaign controller")
            artifact_path, artifact_digest = self.false_promotion_controller.reservation_evidence_binding(
                self.campaign_id,
                candidate.digest,
            )
            evidence_refs.append(
                {
                    "uri": f"{artifact_path.as_posix()}#sha256={artifact_digest}",
                    "summary": (
                        f"Campaign reservation status={reservation.status}; "
                        f"reservation_sha256={reservation_digest}; "
                        f"allocated_alpha={reservation.allocated_alpha}; "
                        f"trial_evidence_sha256={reservation.evidence_digest}."
                    ),
                }
            )
        plan_uri, plan_digest = evaluator_plan_binding(
            self.store,
            scenario,
            candidate.digest,
        )
        if plan_digest != evaluator_plan_digest:
            raise ValueError("negative result evaluator plan digest mismatch")
        evidence_refs.append(
            {
                "uri": f"{plan_uri}#sha256={plan_digest}",
                "summary": "Immutable score, validity, routing, executor, and fixture plan.",
            }
        )
        ledger = build_negative_result_ledger(
            run_id=f"context-bundle:{candidate.digest}",
            events=[
                {
                    "event_type": event_type,
                    "event_id": result_id,
                    "timestamp": record.created_at,
                    "branch_id": candidate.digest,
                    "payload": {
                        "result_id": result_id,
                        "failure_kind": failure_kind,
                        "disposition": "caution",
                        "reason": negative_reason,
                        "score_delta": comparison.mean_effect,
                        "evaluated_seeds": [str(trial.seed) for trial in trials],
                        "evaluated_probes": [trial.fixture for trial in trials],
                        "applicability_scope": "exact_bundle",
                        "evidence_refs": evidence_refs,
                    },
                }
            ],
            scenario_name=scenario,
            context_bundle_digest=candidate.digest,
            evaluator_epoch=candidate.evaluator_epoch,
            generated_at=record.created_at,
            trial_cohort=self.cohort,
        )
        self.store.record_negative_result(
            scenario,
            candidate.digest,
            ledger.model_dump(mode="json"),
        )

    def _evaluate_pair(
        self,
        scenario: str,
        candidate: ContextBundle,
        incumbent: ContextBundle,
        unit: ContextBundleEvaluationUnit,
        pair_index: int,
        evaluator_plan_digest: str,
        control: ContextEvaluationControl,
    ) -> MatchedTrial:
        return evaluate_matched_pair(
            control,
            self.evaluator,
            candidate,
            incumbent,
            unit,
            pair_index=pair_index,
            cohort=self.cohort,
            require_plan=lambda: self._require_evaluator_plan(
                scenario,
                candidate,
                incumbent,
                evaluator_plan_digest,
            ),
        )

    def _require_evaluator_plan(
        self,
        scenario: str,
        candidate: ContextBundle,
        incumbent: ContextBundle,
        plan_digest: str,
    ) -> None:
        require_live_evaluator_plan(
            self.store,
            scenario,
            candidate,
            incumbent,
            plan_digest,
            self.evaluator,
        )

    def _persist_verified_causal_attribution(
        self,
        scenario: str,
        candidate: ContextBundle,
        incumbent: ContextBundle,
    ) -> None:
        from autocontext.analytics.manifest_attribution import (
            ControlledAttributionTrial,
            attribute_manifest_verified_trials,
        )
        from autocontext.context_bundles.diff import context_bundle_manifest_diff

        manifest_diff = context_bundle_manifest_diff(candidate, incumbent)
        if len(manifest_diff.changes) != 1:
            return
        change = manifest_diff.changes[0]
        if change.tested_component_digest is None or change.comparison_component_digest is not None:
            return
        component = next(
            (
                item
                for item in candidate.components
                if item.kind.value == change.component_kind
                and item.key == change.component_key
                and item.digest == change.tested_component_digest
            ),
            None,
        )
        if component is None:
            raise ValueError("verified manifest diff target is absent from the candidate")
        heldout = [trial for trial in self.store.matched_trials(scenario, candidate.digest) if trial.lane == TrialLane.HELDOUT]
        source_trials = [
            ControlledAttributionTrial(
                trial_id=trial.pair_key,
                component_kind=component.kind.value,
                component_key=component.key,
                component_digest=component.digest,
                tested_bundle_digest=candidate.digest,
                comparison_bundle_digest=incumbent.digest,
                evaluator_epoch=candidate.evaluator_epoch,
                trial_cohort=trial.cohort,
                fixture_digest=trial.fixture_digest,
                seed=trial.seed,
                evidence_level="causal_ablation",
                with_component_score=trial.candidate_score,
                without_component_score=trial.incumbent_score,
                token_cost=len(component.content.encode("utf-8")),
                tested_at=f"evaluator:{candidate.evaluator_epoch}",
            )
            for trial in heldout
        ]
        if not source_trials:
            return
        verified = attribute_manifest_verified_trials(
            source_trials,
            evaluator_epoch=candidate.evaluator_epoch,
            bundle_manifests={candidate.digest: candidate, incumbent.digest: incumbent},
        )
        self.store.record_manifest_verified_attribution(
            scenario,
            candidate.digest,
            {
                "schema_version": 1,
                "candidate_digest": candidate.digest,
                "comparison_digest": incumbent.digest,
                "manifest_diff": manifest_diff.to_dict(),
                "source_trials": [trial.to_dict() for trial in source_trials],
                "attributions": [record.to_dict() for record in verified],
            },
        )


__all__ = [
    "ContextBundleEvaluationOutcome",
    "ContextBundleEvaluationUnit",
    "ContextBundleEvaluator",
    "ContextBundlePromotionCoordinator",
    "ContextBundlePromotionAudit",
    "ContextBundleLifecycleAudit",
    "ContextBundlePromotionResult",
    "PromotionAuditOutcome",
]
