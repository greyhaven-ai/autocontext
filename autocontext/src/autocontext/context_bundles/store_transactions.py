"""Recovery-safe transactions extracted from the context-bundle store."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.context_bundles.assembly import validate_bundle_promotion_contract
from autocontext.context_bundles.comparison import evaluate_matched_trials
from autocontext.context_bundles.models import (
    BundleLifecycle,
    ComparisonDecision,
    ComparisonResult,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    PromotionArtifact,
    stable_digest,
)
from autocontext.util.json_io import read_json, write_json

if TYPE_CHECKING:
    from autocontext.context_bundles.store import CandidateRecord, ContextBundleStore

logger = logging.getLogger(__name__)


def rollover_evaluator_epoch(
    store: ContextBundleStore,
    scenario: str,
    evaluator_epoch: str,
) -> ContextBundle:
    """Re-anchor unchanged serving context under a fresh evaluator epoch."""

    from autocontext.context_bundles.store import CandidateRecord

    if not evaluator_epoch.strip():
        raise ValueError("context bundle evaluator_epoch is required")
    with store._lock(scenario):  # noqa: SLF001 - transaction implementation
        active = store.active_bundle(scenario)
        if active is None:
            raise ValueError("cannot roll over evaluator epoch without an active context bundle")
        if active.evaluator_epoch == evaluator_epoch:
            return active
        baseline = ContextBundle.create(
            scenario=scenario,
            evaluator_epoch=evaluator_epoch,
            components=active.components,
        )
        validate_bundle_promotion_contract(baseline)
        store.save_bundle(baseline)
        now = datetime.now(UTC).isoformat()
        record_path = store._record_path(scenario, baseline.digest)  # noqa: SLF001
        if record_path.exists():
            baseline_record = CandidateRecord.from_dict(read_json(record_path))
            if baseline_record.parent_digest is not None or baseline_record.evaluator_epoch != evaluator_epoch:
                raise ValueError("evaluator epoch baseline record conflicts with the immutable manifest")
        else:
            baseline_record = CandidateRecord(
                bundle_digest=baseline.digest,
                parent_digest=None,
                evaluator_epoch=evaluator_epoch,
                lifecycle=BundleLifecycle.ACTIVE,
                source_run_id="",
                source_generation=0,
                created_at=now,
                updated_at=now,
                rationale=f"evaluator epoch rollover from {active.evaluator_epoch}",
            )
            write_json(record_path, baseline_record.to_dict())
        write_json(
            store._active_path(scenario),  # noqa: SLF001
            {
                "schema_version": 1,
                "bundle_digest": baseline.digest,
                "evaluator_epoch": evaluator_epoch,
                "promotion_id": None,
                "rollback_target_digest": None,
                "activated_at": now,
                "rationale": f"evaluator epoch rollover from {active.evaluator_epoch}",
            },
        )
        try:
            _set_lifecycle_if_needed(
                store,
                scenario,
                baseline_record,
                BundleLifecycle.ACTIVE,
                f"evaluator epoch rollover from {active.evaluator_epoch}",
            )
            _set_lifecycle_if_needed(
                store,
                scenario,
                store.candidate(scenario, active.digest),
                BundleLifecycle.SUPERSEDED,
                f"evaluator epoch rolled over to {evaluator_epoch}",
            )
        except (OSError, ValueError):
            logger.warning(
                "failed to finalize prior context lifecycle after evaluator epoch rollover",
                exc_info=True,
            )
        store._materialize_active_compatibility(baseline)  # noqa: SLF001
        return baseline


def matched_evidence_binding(
    store: ContextBundleStore,
    scenario: str,
    digest: str,
    *,
    schema_version: int,
) -> tuple[str, str, str]:
    """Return the resolvable URI, semantic digest, and bound policy digest."""

    from autocontext.storage.scenario_paths import normalize_scenario_name_segment

    evidence = store._matched_evidence(scenario, digest)  # noqa: SLF001
    if evidence.confirmation_policy is None or evidence.confirmation_policy_digest is None:
        raise ValueError("matched evidence is not bound to a confirmation policy")
    payload = {
        "schema_version": schema_version,
        "trials": [trial.to_dict() for trial in evidence.trials],
        "confirmation_policy": evidence.confirmation_policy,
        "confirmation_policy_digest": evidence.confirmation_policy_digest,
    }
    relative_path = f"{normalize_scenario_name_segment(scenario)}/context_bundles/candidates/{digest}/matched_trials.json"
    return relative_path, stable_digest(payload), evidence.confirmation_policy_digest


def replay_matched_trials(
    store: ContextBundleStore,
    scenario: str,
    digest: str,
    policy: ConfirmationPolicy,
    *,
    evaluator_plan_digest: str | None = None,
) -> ComparisonResult:
    """Replay persisted evidence without mutating a terminal candidate."""

    with store._lock(scenario):  # noqa: SLF001
        from autocontext.context_bundles.evaluator_plan import (
            require_bound_evaluator_plan,
        )

        require_bound_evaluator_plan(
            store,
            scenario,
            digest,
            evaluator_plan_digest,
        )
        record = store.candidate(scenario, digest)
        evidence = store._matched_evidence(scenario, digest)  # noqa: SLF001
        policy_payload = policy.to_dict()
        policy_digest = stable_digest(policy_payload)
        bound_policy, bound_digest = bound_confirmation_policy(record, evidence)
        if bound_policy is not None and (bound_policy != policy_payload or bound_digest != policy_digest):
            raise ValueError("confirmation policy cannot change while replaying matched evidence")
        comparison = evaluate_matched_trials(
            store.load_bundle(scenario, digest),
            evidence.trials,
            policy=policy,
        )
        if record.comparison is not None and comparison.to_dict() != record.comparison:
            raise ValueError("persisted matched evidence does not reproduce the candidate comparison")
        return comparison


def migrate_terminal_matched_evidence(
    store: ContextBundleStore,
    scenario: str,
    digest: str,
    policy: ConfirmationPolicy,
) -> bool:
    """Atomically bind reproducible schema-v1 terminal evidence to policy v2."""

    with store._lock(scenario):  # noqa: SLF001
        record = store.candidate(scenario, digest)
        evidence = store._matched_evidence(scenario, digest)  # noqa: SLF001
        if not evidence.legacy:
            return False
        if record.lifecycle not in {BundleLifecycle.CONFIRMED, BundleLifecycle.REJECTED}:
            raise ValueError("only terminal schema-v1 matched evidence can be migrated")
        if record.comparison is None:
            raise ValueError("terminal schema-v1 candidate is missing its comparison")
        comparison = evaluate_matched_trials(
            store.load_bundle(scenario, digest),
            evidence.trials,
            policy=policy,
        )
        if comparison.to_dict() != record.comparison:
            raise ValueError("schema-v1 evidence and policy do not reproduce the terminal comparison")
        expected = {
            ComparisonDecision.CONFIRMED: BundleLifecycle.CONFIRMED,
            ComparisonDecision.REJECTED: BundleLifecycle.REJECTED,
        }.get(comparison.decision)
        if expected != record.lifecycle:
            raise ValueError("schema-v1 evidence does not reproduce the terminal lifecycle")
        policy_payload = policy.to_dict()
        store._write_matched_evidence(  # noqa: SLF001
            scenario,
            digest,
            trials=evidence.trials,
            confirmation_policy=policy_payload,
            confirmation_policy_digest=stable_digest(policy_payload),
        )
        return True


def pending_candidates(
    store: ContextBundleStore,
    scenario: str,
    source_run_id: str,
    source_generation: int,
) -> tuple[CandidateRecord, ...]:
    """Return resumable candidates belonging to exactly one generation.

    Proposed candidates may not have started evaluation, screened candidates
    may contain a partially committed matched-evidence envelope, and confirmed
    candidates may be waiting on an audit or serving cutover.  Terminal and
    serving lifecycles are deliberately excluded.
    """

    from autocontext.context_bundles.store import CandidateRecord

    if not source_run_id.strip():
        raise ValueError("pending candidate query requires a source run identity")
    if isinstance(source_generation, bool) or not isinstance(source_generation, int) or source_generation < 0:
        raise ValueError("pending candidate query requires a non-negative generation")
    with store._lock(scenario):  # noqa: SLF001
        records: list[CandidateRecord] = []
        candidate_root = store._root(scenario) / "candidates"  # noqa: SLF001
        for path in sorted(candidate_root.glob("*/record.json")):
            record = CandidateRecord.from_dict(read_json(path))
            if path.parent.name != record.bundle_digest:
                raise ValueError("candidate record path does not match its bundle digest")
            bundle = store.load_bundle(scenario, record.bundle_digest)
            if (
                bundle.digest != record.bundle_digest
                or bundle.parent_digest != record.parent_digest
                or bundle.evaluator_epoch != record.evaluator_epoch
            ):
                raise ValueError("candidate record does not match its immutable bundle")
            # Parse any evidence eagerly so corrupt partial state fails at
            # discovery rather than after the runner has chosen it for resume.
            store._matched_evidence(scenario, record.bundle_digest)  # noqa: SLF001
            if (
                record.source_run_id == source_run_id
                and record.source_generation == source_generation
                and record.lifecycle
                in {
                    BundleLifecycle.PROPOSED,
                    BundleLifecycle.SCREENED,
                    BundleLifecycle.CONFIRMED,
                }
            ):
                records.append(record)
        return tuple(records)


def begin_stale_confirmed_candidate_terminalization(
    store: ContextBundleStore,
    scenario: str,
    digest: str,
    parent_digest: str | None,
    *,
    evaluator_plan_digest: str,
    matched_evidence_digest: str,
) -> dict[str, Any] | None:
    """Linearize a stale decision before writing its external evidence."""

    with store._lock(scenario):  # noqa: SLF001
        path = stale_terminalization_path(store, scenario, digest)
        if path.exists():
            artifact = _validate_stale_terminalization(
                read_json(path),
                digest=digest,
                parent_digest=parent_digest,
                evaluator_plan_digest=evaluator_plan_digest,
                matched_evidence_digest=matched_evidence_digest,
            )
            return artifact
        record = store.candidate(scenario, digest)
        if record.lifecycle != BundleLifecycle.CONFIRMED:
            if record.lifecycle == BundleLifecycle.REJECTED:
                return None
            raise ValueError("stale candidate has an invalid lifecycle")
        pointer = store.active_pointer(scenario)
        active_digest = str(pointer["bundle_digest"]) if pointer is not None else None
        if active_digest in {parent_digest, digest}:
            return None
        artifact = {
            "schema_version": 1,
            "candidate_digest": digest,
            "parent_digest": parent_digest,
            "observed_active_digest": active_digest,
            "evaluator_plan_digest": evaluator_plan_digest,
            "matched_evidence_digest": matched_evidence_digest,
            "reason": f"confirmed candidate incumbent {parent_digest} is stale; active bundle is {active_digest}",
        }
        write_json(path, artifact)
        return artifact


def finalize_stale_confirmed_candidate_terminalization(
    store: ContextBundleStore,
    scenario: str,
    digest: str,
    artifact: dict[str, Any],
) -> CandidateRecord:
    """Finish the lifecycle mirror after every immutable side effect exists."""

    with store._lock(scenario):  # noqa: SLF001
        persisted = read_json(stale_terminalization_path(store, scenario, digest))
        if persisted != artifact:
            raise ValueError("stale candidate terminalization marker changed")
        record = store.candidate(scenario, digest)
        reason = str(artifact["reason"])
        if record.lifecycle == BundleLifecycle.CONFIRMED:
            return store._set_lifecycle(  # noqa: SLF001
                scenario,
                record,
                BundleLifecycle.REJECTED,
                reason,
            )
        if record.lifecycle != BundleLifecycle.REJECTED or record.rationale != reason:
            raise ValueError("stale confirmed candidate has an invalid lifecycle")
        return record


def stale_terminalization_path(store: ContextBundleStore, scenario: str, digest: str) -> Path:
    return store._candidate_dir(scenario, digest) / "stale_terminalization.json"  # noqa: SLF001


def _validate_stale_terminalization(
    artifact: Any,
    *,
    digest: str,
    parent_digest: str | None,
    evaluator_plan_digest: str,
    matched_evidence_digest: str,
) -> dict[str, Any]:
    if not isinstance(artifact, dict) or artifact.get("schema_version") != 1:
        raise ValueError("stale candidate terminalization marker is malformed")
    expected = {
        "candidate_digest": digest,
        "parent_digest": parent_digest,
        "evaluator_plan_digest": evaluator_plan_digest,
        "matched_evidence_digest": matched_evidence_digest,
    }
    if any(artifact.get(key) != value for key, value in expected.items()):
        raise ValueError("stale candidate terminalization marker changed")
    active_digest = artifact.get("observed_active_digest")
    reason = f"confirmed candidate incumbent {parent_digest} is stale; active bundle is {active_digest}"
    if active_digest in {parent_digest, digest} or artifact.get("reason") != reason:
        raise ValueError("stale candidate terminalization marker is not stale")
    return artifact


def promotion_from_pointer(
    store: ContextBundleStore,
    scenario: str,
    pointer: dict[str, Any],
) -> PromotionArtifact:
    promotion_id = pointer.get("promotion_id")
    if not isinstance(promotion_id, str) or not promotion_id:
        raise ValueError("active candidate pointer is missing its promotion artifact identity")
    path = store._root(scenario) / "promotions" / f"{promotion_id}.json"  # noqa: SLF001
    artifact = PromotionArtifact.from_dict(read_json(path))
    if (
        artifact.promotion_id != promotion_id
        or artifact.candidate_digest != pointer.get("bundle_digest")
        or artifact.manifest_diff_digest != pointer.get("manifest_diff_digest")
        or artifact.rollback_target_digest != pointer.get("rollback_target_digest")
        or artifact.evaluator_epoch != pointer.get("evaluator_epoch")
    ):
        raise ValueError("active pointer does not match its durable promotion artifact")
    return artifact


def finalize_active_lifecycles(
    store: ContextBundleStore,
    scenario: str,
    artifact: PromotionArtifact,
    rationale: str,
) -> None:
    try:
        _set_lifecycle_if_needed(
            store,
            scenario,
            store.candidate(scenario, artifact.candidate_digest),
            BundleLifecycle.ACTIVE,
            rationale,
        )
        if artifact.incumbent_digest is not None:
            _set_lifecycle_if_needed(
                store,
                scenario,
                store.candidate(scenario, artifact.incumbent_digest),
                BundleLifecycle.SUPERSEDED,
                f"superseded by {artifact.candidate_digest}",
            )
    except (OSError, ValueError):
        logger.warning(
            "active context pointer committed but lifecycle mirrors require reconciliation",
            exc_info=True,
        )


def _set_lifecycle_if_needed(
    store: ContextBundleStore,
    scenario: str,
    record: Any,
    lifecycle: BundleLifecycle,
    rationale: str,
) -> None:
    if record.lifecycle != lifecycle:
        store._set_lifecycle(scenario, record, lifecycle, rationale)  # noqa: SLF001


def comparison_from_dict(data: dict[str, Any]) -> ComparisonResult:
    return ComparisonResult(
        decision=ComparisonDecision(str(data["decision"])),
        reason=str(data["reason"]),
        screen_pairs=int(data["screen_pairs"]),
        confirmation_pairs=int(data["confirmation_pairs"]),
        heldout_pairs=int(data["heldout_pairs"]),
        mean_effect=(float(data["mean_effect"]) if data.get("mean_effect") is not None else None),
        confidence_low=(float(data["confidence_low"]) if data.get("confidence_low") is not None else None),
        confidence_high=(float(data["confidence_high"]) if data.get("confidence_high") is not None else None),
    )


def parse_matched_trials(values: list[Any]) -> list[MatchedTrial]:
    trials = [MatchedTrial.from_dict(item) for item in values]
    seen: set[str] = set()
    for trial in trials:
        if trial.pair_key in seen:
            raise ValueError("matched trial artifact contains duplicate current pair identity")
        seen.add(trial.pair_key)
    return trials


def bound_confirmation_policy(
    record: CandidateRecord,
    evidence: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    record_payload = record.confirmation_policy
    record_digest = record.confirmation_policy_digest
    if (record_payload is None) != (record_digest is None):
        raise ValueError("candidate record has an incomplete confirmation policy binding")
    if record_payload is not None:
        if stable_digest(record_payload) != record_digest:
            raise ValueError("persisted confirmation policy digest mismatch")
        ConfirmationPolicy.from_dict(record_payload)
    evidence_payload = evidence.confirmation_policy
    evidence_digest = evidence.confirmation_policy_digest
    if (
        record_payload is not None
        and evidence_payload is not None
        and (record_payload != evidence_payload or record_digest != evidence_digest)
    ):
        raise ValueError("matched evidence and candidate record bind different confirmation policies")
    if evidence_payload is not None:
        return evidence_payload, evidence_digest
    return record_payload, record_digest


def confirmation_policy_from_binding(record: CandidateRecord, evidence: Any) -> ConfirmationPolicy:
    payload, digest = bound_confirmation_policy(record, evidence)
    if payload is None or digest is None:
        raise ValueError("confirmed candidate is missing its persisted confirmation policy")
    if stable_digest(payload) != digest:
        raise ValueError("persisted confirmation policy digest mismatch")
    return ConfirmationPolicy.from_dict(payload)


__all__ = [
    "begin_stale_confirmed_candidate_terminalization",
    "bound_confirmation_policy",
    "comparison_from_dict",
    "confirmation_policy_from_binding",
    "finalize_active_lifecycles",
    "finalize_stale_confirmed_candidate_terminalization",
    "matched_evidence_binding",
    "migrate_terminal_matched_evidence",
    "parse_matched_trials",
    "pending_candidates",
    "promotion_from_pointer",
    "replay_matched_trials",
    "rollover_evaluator_epoch",
    "stale_terminalization_path",
]
