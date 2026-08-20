"""Manifest-bound causal attribution for context-bundle controlled trials."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from autocontext.analytics.context_attribution import (
    ComponentAttribution,
    ControlledAttributionTrial,
    attribute_controlled_trials,
    reconstruct_causal_credit,
)
from autocontext.context_bundles.diff import context_bundle_manifest_diff
from autocontext.context_bundles.models import ContextBundle
from autocontext.util.models import StrictModel


class ControlledTrialManifestVerification(StrictModel):
    trial_id: str = Field(min_length=1)
    manifest_diff_digest: str = Field(min_length=1)
    manifest_diff: dict[str, Any]


class ManifestVerifiedComponentAttribution(StrictModel):
    attribution: ComponentAttribution
    manifest_diff_digest: str = Field(min_length=1)
    trial_ids: list[str]


def verify_controlled_trial_manifest_diff(
    trial: ControlledAttributionTrial,
    tested_bundle: ContextBundle,
    comparison_bundle: ContextBundle,
) -> ControlledTrialManifestVerification:
    """Bind a causal claim to a recomputed, exact single-component manifest diff."""

    if tested_bundle.digest != trial.tested_bundle_digest:
        raise ValueError("tested bundle manifest does not match the controlled attribution trial")
    if comparison_bundle.digest != trial.comparison_bundle_digest:
        raise ValueError("comparison bundle manifest does not match the controlled attribution trial")
    if tested_bundle.evaluator_epoch != trial.evaluator_epoch or comparison_bundle.evaluator_epoch != trial.evaluator_epoch:
        raise ValueError("controlled attribution manifest evaluator epoch mismatch")
    manifest_diff = context_bundle_manifest_diff(tested_bundle, comparison_bundle)
    matching = [
        change
        for change in manifest_diff.changes
        if change.component_kind == trial.component_kind and change.component_key == trial.component_key
    ]
    if (
        len(matching) != 1
        or matching[0].tested_component_digest != trial.component_digest
        or matching[0].comparison_component_digest is not None
    ):
        raise ValueError("controlled attribution target does not match the exact manifest diff")
    if trial.evidence_level == "causal_ablation" and len(manifest_diff.changes) != 1:
        raise ValueError("causal attribution requires an exact single-component manifest diff")
    return ControlledTrialManifestVerification(
        trial_id=trial.trial_id,
        manifest_diff_digest=manifest_diff.digest,
        manifest_diff=manifest_diff.to_dict(),
    )


def attribute_manifest_verified_trials(
    trials: list[ControlledAttributionTrial],
    *,
    evaluator_epoch: str,
    bundle_manifests: Mapping[str, ContextBundle],
    neutral_effect: float = 0.0,
    high_token_cost: int = 256,
) -> list[ManifestVerifiedComponentAttribution]:
    """Attribute only after every controlled pair is bound to loaded bundle manifests."""

    verifications: dict[str, ControlledTrialManifestVerification] = {}
    for trial in trials:
        try:
            tested = bundle_manifests[trial.tested_bundle_digest]
            comparison = bundle_manifests[trial.comparison_bundle_digest]
        except KeyError as exc:
            raise ValueError(f"missing controlled attribution bundle manifest: {exc.args[0]}") from exc
        verifications[trial.trial_id] = verify_controlled_trial_manifest_diff(trial, tested, comparison)
    attributions = attribute_controlled_trials(
        trials,
        evaluator_epoch=evaluator_epoch,
        neutral_effect=neutral_effect,
        high_token_cost=high_token_cost,
    )
    results: list[ManifestVerifiedComponentAttribution] = []
    for attribution in attributions:
        digests = {verifications[trial_id].manifest_diff_digest for trial_id in attribution.trial_ids}
        if len(digests) != 1:
            raise ValueError("controlled attribution group mixes exact manifest diffs")
        results.append(
            ManifestVerifiedComponentAttribution(
                attribution=attribution,
                manifest_diff_digest=next(iter(digests)),
                trial_ids=attribution.trial_ids,
            )
        )
    return results


def reconstruct_manifest_verified_causal_credit(
    record: ManifestVerifiedComponentAttribution,
    trials: list[ControlledAttributionTrial],
    *,
    bundle_manifests: Mapping[str, ContextBundle],
) -> float:
    """Replay source observations and their exact manifest binding before returning credit."""

    by_id = {trial.trial_id: trial for trial in trials}
    if len(by_id) != len(trials):
        raise ValueError("duplicate attribution trial")
    try:
        referenced = [by_id[trial_id] for trial_id in record.trial_ids]
    except KeyError as exc:
        raise ValueError(f"missing referenced trial: {exc.args[0]}") from exc
    for trial in referenced:
        try:
            verification = verify_controlled_trial_manifest_diff(
                trial,
                bundle_manifests[trial.tested_bundle_digest],
                bundle_manifests[trial.comparison_bundle_digest],
            )
        except KeyError as exc:
            raise ValueError(f"missing controlled attribution bundle manifest: {exc.args[0]}") from exc
        if verification.manifest_diff_digest != record.manifest_diff_digest:
            raise ValueError("controlled attribution manifest-diff binding mismatch")
    return reconstruct_causal_credit(record.attribution, referenced)


__all__ = [
    "ControlledTrialManifestVerification",
    "ManifestVerifiedComponentAttribution",
    "attribute_manifest_verified_trials",
    "reconstruct_manifest_verified_causal_credit",
    "verify_controlled_trial_manifest_diff",
]
