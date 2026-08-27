"""Outcome-gated immutable context bundles."""

from typing import TYPE_CHECKING, Any

from autocontext.context_bundles.assembly import (
    CONSTRUCTION_BOUND_ROUTING_FIELDS,
    DEFERRED_ROUTING_FIELDS,
    LIVE_CONTEXT_ROUTING_FIELDS,
    build_candidate_bundle,
    build_legacy_baseline,
    bundle_mutations,
    bundle_routing_config,
    bundle_text,
    bundle_tool_context,
    evaluator_epoch_for,
    routing_snapshot,
    validate_bundle_promotion_contract,
)
from autocontext.context_bundles.comparison import evaluate_matched_trials
from autocontext.context_bundles.diff import (
    BundleManifestChange,
    ContextBundleManifestDiff,
    context_bundle_manifest_diff,
)
from autocontext.context_bundles.false_promotion import (
    CampaignFalsePromotionController,
    CampaignFalsePromotionPolicy,
    CampaignFalsePromotionResult,
    CampaignFixtureUnit,
    CandidateFixtureReservation,
    CandidateRiskReservation,
    FalsePromotionMethod,
    FalsePromotionStatus,
)
from autocontext.context_bundles.models import (
    BundleComponent,
    BundleLifecycle,
    ComparisonDecision,
    ComparisonResult,
    ComponentKind,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    PromotionArtifact,
    TrialLane,
    canonical_json,
    stable_digest,
)
from autocontext.context_bundles.promotion import (
    ContextBundleEvaluationOutcome,
    ContextBundleEvaluationUnit,
    ContextBundleEvaluator,
    ContextBundleLifecycleAudit,
    ContextBundlePromotionAudit,
    ContextBundlePromotionCoordinator,
    ContextBundlePromotionResult,
    PromotionAuditOutcome,
)
from autocontext.context_bundles.store import CandidateRecord, ContextBundleStore

if TYPE_CHECKING:
    from autocontext.context_bundles.runtime_evaluator import (
        RuntimeContextBundleEvaluator,
        RuntimeContextFixture,
        build_runtime_context_bundle_evaluator,
        materialize_runtime_fixture,
        runtime_fixture_digest,
    )


def __getattr__(name: str) -> Any:
    if name in {
        "RuntimeContextBundleEvaluator",
        "RuntimeContextFixture",
        "build_runtime_context_bundle_evaluator",
        "materialize_runtime_fixture",
        "runtime_fixture_digest",
    }:
        from autocontext.context_bundles.runtime_evaluator import (
            RuntimeContextBundleEvaluator,
            RuntimeContextFixture,
            build_runtime_context_bundle_evaluator,
            materialize_runtime_fixture,
            runtime_fixture_digest,
        )

        return {
            "RuntimeContextBundleEvaluator": RuntimeContextBundleEvaluator,
            "RuntimeContextFixture": RuntimeContextFixture,
            "build_runtime_context_bundle_evaluator": build_runtime_context_bundle_evaluator,
            "materialize_runtime_fixture": materialize_runtime_fixture,
            "runtime_fixture_digest": runtime_fixture_digest,
        }[name]
    raise AttributeError(name)


__all__ = [
    "BundleComponent",
    "BundleLifecycle",
    "BundleManifestChange",
    "CandidateRecord",
    "CandidateFixtureReservation",
    "CandidateRiskReservation",
    "ComparisonDecision",
    "ComparisonResult",
    "ComponentKind",
    "ConfirmationPolicy",
    "CampaignFalsePromotionController",
    "CampaignFalsePromotionPolicy",
    "CampaignFalsePromotionResult",
    "CampaignFixtureUnit",
    "ContextBundle",
    "ContextBundleManifestDiff",
    "ContextBundleStore",
    "FalsePromotionStatus",
    "FalsePromotionMethod",
    "ContextBundleEvaluationOutcome",
    "ContextBundleEvaluationUnit",
    "ContextBundleEvaluator",
    "ContextBundleLifecycleAudit",
    "ContextBundlePromotionCoordinator",
    "ContextBundlePromotionAudit",
    "ContextBundlePromotionResult",
    "PromotionAuditOutcome",
    "MatchedTrial",
    "PromotionArtifact",
    "TrialLane",
    "CONSTRUCTION_BOUND_ROUTING_FIELDS",
    "DEFERRED_ROUTING_FIELDS",
    "LIVE_CONTEXT_ROUTING_FIELDS",
    "canonical_json",
    "context_bundle_manifest_diff",
    "build_candidate_bundle",
    "build_legacy_baseline",
    "bundle_mutations",
    "bundle_routing_config",
    "bundle_text",
    "bundle_tool_context",
    "evaluate_matched_trials",
    "evaluator_epoch_for",
    "routing_snapshot",
    "stable_digest",
    "validate_bundle_promotion_contract",
    "RuntimeContextBundleEvaluator",
    "RuntimeContextFixture",
    "build_runtime_context_bundle_evaluator",
    "materialize_runtime_fixture",
    "runtime_fixture_digest",
]
