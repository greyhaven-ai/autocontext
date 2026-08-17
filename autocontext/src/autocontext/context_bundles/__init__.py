"""Outcome-gated immutable context bundles."""

from autocontext.context_bundles.assembly import (
    build_candidate_bundle,
    build_legacy_baseline,
    bundle_mutations,
    bundle_text,
    bundle_tool_context,
    evaluator_epoch_for,
    routing_snapshot,
)
from autocontext.context_bundles.comparison import evaluate_matched_trials
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
from autocontext.context_bundles.store import CandidateRecord, ContextBundleStore

__all__ = [
    "BundleComponent",
    "BundleLifecycle",
    "CandidateRecord",
    "ComparisonDecision",
    "ComparisonResult",
    "ComponentKind",
    "ConfirmationPolicy",
    "ContextBundle",
    "ContextBundleStore",
    "MatchedTrial",
    "PromotionArtifact",
    "TrialLane",
    "canonical_json",
    "build_candidate_bundle",
    "build_legacy_baseline",
    "bundle_mutations",
    "bundle_text",
    "bundle_tool_context",
    "evaluate_matched_trials",
    "evaluator_epoch_for",
    "routing_snapshot",
    "stable_digest",
]
