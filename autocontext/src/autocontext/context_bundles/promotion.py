"""Live matched evaluation and promotion orchestration for context bundles (AC-984)."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from autocontext.context_bundles.false_promotion import (
    CampaignFalsePromotionController,
    CampaignFalsePromotionResult,
)
from autocontext.context_bundles.models import (
    ComparisonDecision,
    ComparisonResult,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    PromotionArtifact,
    TrialLane,
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
    """Structural contract shared with the campaign checkpoint runner."""

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
    """Adaptively evaluate a proposed bundle and atomically serve it only when confirmed."""

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

    def evaluate_candidate(self, scenario: str, digest: str) -> ContextBundlePromotionResult:
        candidate = self.store.load_bundle(scenario, digest)
        incumbent = self.store.load_bundle(scenario, candidate.parent_digest) if candidate.parent_digest is not None else None
        if incumbent is None:
            raise ValueError("context bundle candidate must have an incumbent parent")
        effective_policy = self.policy
        if self.false_promotion_controller is not None:
            assert self.campaign_id is not None
            effective_policy, _ = self.false_promotion_controller.reserve_confirmation_policy(
                self.campaign_id,
                candidate,
                self.policy,
            )
        comparison: ComparisonResult | None = None
        evaluated_pairs = 0
        for lane in (TrialLane.SCREEN, TrialLane.CONFIRMATION, TrialLane.HELDOUT):
            for unit in (item for item in self.units if item.lane == lane):
                try:
                    trial = self._evaluate_pair(candidate, incumbent, unit, evaluated_pairs)
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
                comparison = self.store.record_matched_trials(
                    scenario,
                    digest,
                    [trial],
                    policy=effective_policy,
                )
                evaluated_pairs += 1
                if comparison.decision in {ComparisonDecision.REJECTED, ComparisonDecision.INCONCLUSIVE}:
                    trials = tuple(self.store.matched_trials(scenario, digest))
                    self._persist_negative_result(scenario, candidate, comparison, trials)
                    terminal_audit_outcome: PromotionAuditOutcome | None = None
                    if comparison.decision == ComparisonDecision.INCONCLUSIVE:
                        terminal_audit_outcome = self._review_lifecycle_checkpoint(
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
                    return ContextBundlePromotionResult(
                        digest,
                        comparison,
                        None,
                        evaluated_pairs,
                        audit_policy_outcome=terminal_audit_outcome,
                    )
                if lane == TrialLane.SCREEN and comparison.decision != ComparisonDecision.NEEDS_SCREEN:
                    break
                if lane == TrialLane.CONFIRMATION and comparison.decision != ComparisonDecision.NEEDS_CONFIRMATION:
                    break
                if lane == TrialLane.HELDOUT and comparison.decision == ComparisonDecision.CONFIRMED:
                    audit_outcome: PromotionAuditOutcome | None = None
                    trials = tuple(self.store.matched_trials(scenario, digest))
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
                            digest,
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
                            effective_policy,
                        )
                        if not false_promotion_result.authorized:
                            return ContextBundlePromotionResult(
                                digest,
                                comparison,
                                None,
                                evaluated_pairs,
                                audit_policy_outcome=audit_outcome,
                                false_promotion_result=false_promotion_result,
                            )
                    self._persist_verified_causal_attribution(
                        scenario,
                        candidate,
                        incumbent,
                    )
                    promotion = self.store.promote(
                        scenario,
                        digest,
                        cohort=self.cohort,
                        rationale=comparison.reason,
                    )
                    return ContextBundlePromotionResult(
                        digest,
                        comparison,
                        promotion,
                        evaluated_pairs,
                        audit_policy_outcome=audit_outcome,
                        false_promotion_result=false_promotion_result,
                    )
        if comparison is None:
            raise ValueError("context bundle promotion plan did not contain a usable evaluation unit")
        return ContextBundlePromotionResult(digest, comparison, None, evaluated_pairs)

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
            # Audits are advisory and may not mutate deterministic promotion
            # decisions when their transport or packet construction fails.
            return None
        if audit is None:
            return None
        if getattr(audit, "status", None) == "canceled":
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
    ) -> None:
        from autocontext.analytics.negative_result_ledger import build_negative_result_ledger

        record = self.store.candidate(scenario, candidate.digest)
        event_type = "candidate_rejected" if comparison.decision == ComparisonDecision.REJECTED else "evaluation_failed"
        result_id = f"context-bundle-{candidate.digest}-{comparison.decision.value}"
        ledger = build_negative_result_ledger(
            run_id=f"context-bundle:{candidate.digest}",
            events=[
                {
                    "event_type": event_type,
                    "event_id": result_id,
                    "timestamp": record.updated_at,
                    "branch_id": candidate.digest,
                    "payload": {
                        "result_id": result_id,
                        "failure_kind": (
                            "score_regression" if comparison.decision == ComparisonDecision.REJECTED else "verification_failed"
                        ),
                        "disposition": "caution",
                        "reason": comparison.reason,
                        "score_delta": comparison.mean_effect,
                        "evaluated_seeds": [str(trial.seed) for trial in trials],
                        "evaluated_probes": [trial.fixture for trial in trials],
                        "applicability_scope": "exact_bundle",
                        "evidence_refs": [
                            {
                                "uri": (f"context-bundles/{scenario}/candidates/{candidate.digest}/matched_trials.json"),
                                "summary": "Persisted matched candidate/incumbent evaluation pairs.",
                            }
                        ],
                    },
                }
            ],
            scenario_name=scenario,
            context_bundle_digest=candidate.digest,
            evaluator_epoch=candidate.evaluator_epoch,
            generated_at=record.updated_at,
            trial_cohort=self.cohort,
        )
        self.store.record_negative_result(
            scenario,
            candidate.digest,
            ledger.model_dump(mode="json"),
        )

    def _evaluate_pair(
        self,
        candidate: ContextBundle,
        incumbent: ContextBundle,
        unit: ContextBundleEvaluationUnit,
        pair_index: int,
    ) -> MatchedTrial:
        # Alternate arm order to avoid systematically favoring the first call
        # when a provider drifts within a matched cohort.
        if pair_index % 2 == 0:
            candidate_result = self.evaluator.evaluate(candidate, unit)
            incumbent_result = self.evaluator.evaluate(incumbent, unit)
        else:
            incumbent_result = self.evaluator.evaluate(incumbent, unit)
            candidate_result = self.evaluator.evaluate(candidate, unit)
        return MatchedTrial(
            candidate_digest=candidate.digest,
            incumbent_digest=incumbent.digest,
            evaluator_epoch=candidate.evaluator_epoch,
            cohort=self.cohort,
            fixture=unit.fixture,
            fixture_digest=unit.fixture_digest,
            seed=unit.seed,
            lane=unit.lane,
            candidate_score=candidate_result.score,
            incumbent_score=incumbent_result.score,
            candidate_valid=candidate_result.valid,
            incumbent_valid=incumbent_result.valid,
        )

    def _persist_verified_causal_attribution(
        self,
        scenario: str,
        candidate: ContextBundle,
        incumbent: ContextBundle,
    ) -> None:
        """Write causal credit only for an exact, verified component addition."""

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
