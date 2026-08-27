"""Transactional persistence for immutable context bundles (AC-973)."""

from __future__ import annotations

import ast
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.context_bundles.assembly import validate_bundle_promotion_contract
from autocontext.context_bundles.comparison import evaluate_matched_trials
from autocontext.context_bundles.diff import ContextBundleManifestDiff, context_bundle_manifest_diff
from autocontext.context_bundles.models import (
    BundleLifecycle,
    ComparisonDecision,
    ComparisonResult,
    ComponentKind,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    PromotionArtifact,
    stable_digest,
)
from autocontext.context_bundles.store_transactions import (
    bound_confirmation_policy,
    comparison_from_dict,
    confirmation_policy_from_binding,
    finalize_active_lifecycles,
    matched_evidence_binding,
    migrate_terminal_matched_evidence,
    parse_matched_trials,
    pending_candidates,
    promotion_from_pointer,
    replay_matched_trials,
    rollover_evaluator_epoch,
    stale_terminalization_path,
)
from autocontext.storage.scenario_paths import resolve_scenario_root
from autocontext.util.file_lock import advisory_path_lock
from autocontext.util.json_io import read_json, write_json, write_text_atomic

CANDIDATE_RECORD_SCHEMA_VERSION = 2
MATCHED_EVIDENCE_SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _require_digest(digest: str) -> None:
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("bundle digest must be a 64-character sha256 hex digest")


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    bundle_digest: str
    parent_digest: str | None
    evaluator_epoch: str
    lifecycle: BundleLifecycle
    source_run_id: str
    source_generation: int
    created_at: str
    updated_at: str
    schema_version: int = CANDIDATE_RECORD_SCHEMA_VERSION
    rationale: str = ""
    comparison: dict[str, Any] | None = None
    confirmation_policy: dict[str, Any] | None = None
    confirmation_policy_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bundle_digest": self.bundle_digest,
            "parent_digest": self.parent_digest,
            "evaluator_epoch": self.evaluator_epoch,
            "lifecycle": self.lifecycle.value,
            "source_run_id": self.source_run_id,
            "source_generation": self.source_generation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "rationale": self.rationale,
            "comparison": self.comparison,
            "confirmation_policy": self.confirmation_policy,
            "confirmation_policy_digest": self.confirmation_policy_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateRecord:
        schema_version = int(data.get("schema_version", 1))
        if schema_version not in {1, CANDIDATE_RECORD_SCHEMA_VERSION}:
            raise ValueError(f"unsupported candidate record schema version: {schema_version}")
        if schema_version == CANDIDATE_RECORD_SCHEMA_VERSION:
            missing = {"confirmation_policy", "confirmation_policy_digest"}.difference(data)
            if missing:
                raise ValueError(f"candidate record is missing schema-v2 fields: {', '.join(sorted(missing))}")
        return cls(
            bundle_digest=str(data["bundle_digest"]),
            parent_digest=(str(data["parent_digest"]) if data.get("parent_digest") is not None else None),
            evaluator_epoch=str(data["evaluator_epoch"]),
            lifecycle=BundleLifecycle(str(data["lifecycle"])),
            source_run_id=str(data.get("source_run_id", "")),
            source_generation=int(data.get("source_generation", 0)),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            schema_version=schema_version,
            rationale=str(data.get("rationale", "")),
            comparison=(dict(data["comparison"]) if isinstance(data.get("comparison"), dict) else None),
            confirmation_policy=(
                dict(data["confirmation_policy"]) if isinstance(data.get("confirmation_policy"), dict) else None
            ),
            confirmation_policy_digest=(
                str(data["confirmation_policy_digest"]) if data.get("confirmation_policy_digest") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class _MatchedEvidence:
    trials: list[MatchedTrial]
    confirmation_policy: dict[str, Any] | None
    confirmation_policy_digest: str | None
    legacy: bool = False


class ContextBundleStore:
    """Content-addressed bundle store with one atomic active pointer per scenario.

    Bundle manifests and promotion artifacts are immutable. The only serving
    switch is ``active.json``; it is replaced atomically under a scenario lock,
    so readers can never observe a half-promoted collection of components.
    """

    def __init__(self, knowledge_root: Path) -> None:
        self.knowledge_root = Path(knowledge_root)

    def _root(self, scenario: str) -> Path:
        return resolve_scenario_root(self.knowledge_root, scenario) / "context_bundles"

    def _bundle_path(self, scenario: str, digest: str) -> Path:
        _require_digest(digest)
        return self._root(scenario) / "bundles" / f"{digest}.json"

    def _candidate_dir(self, scenario: str, digest: str) -> Path:
        _require_digest(digest)
        return self._root(scenario) / "candidates" / digest

    def _record_path(self, scenario: str, digest: str) -> Path:
        return self._candidate_dir(scenario, digest) / "record.json"

    def _trials_path(self, scenario: str, digest: str) -> Path:
        return self._candidate_dir(scenario, digest) / "matched_trials.json"

    def _manifest_diff_path(self, scenario: str, digest: str) -> Path:
        return self._candidate_dir(scenario, digest) / "manifest_diff.json"

    def _causal_attribution_path(self, scenario: str, digest: str) -> Path:
        return self._candidate_dir(scenario, digest) / "causal_attribution.json"

    def _negative_result_path(self, scenario: str, digest: str) -> Path:
        return self._candidate_dir(scenario, digest) / "negative_result.json"

    def _active_path(self, scenario: str) -> Path:
        return self._root(scenario) / "active.json"

    @contextmanager
    def _lock(self, scenario: str) -> Iterator[None]:
        lock_path = self._root(scenario) / ".lock"
        with advisory_path_lock(lock_path):
            yield

    def save_bundle(self, bundle: ContextBundle) -> Path:
        """Write a bundle once; refuse any attempted digest collision."""
        path = self._bundle_path(bundle.scenario, bundle.digest)
        if path.exists():
            existing = ContextBundle.from_dict(read_json(path))
            if existing != bundle:
                raise ValueError("immutable bundle path already contains different content")
            self._materialize_runtime(bundle)
            return path
        write_json(path, bundle.to_dict())
        self._materialize_runtime(bundle)
        return path

    def runtime_harness_dir(self, scenario: str, digest: str) -> Path | None:
        """Return the immutable validator directory for a bundle when non-empty."""
        directory = self._root(scenario) / "runtime" / digest / "harness"
        return directory if any(directory.glob("*.py")) else None

    def _materialize_runtime(self, bundle: ContextBundle) -> None:
        """Prepare code surfaces before an active pointer can reference them."""
        from autocontext.context_bundles.models import ComponentKind

        harness_dir = self._root(bundle.scenario) / "runtime" / bundle.digest / "harness"
        for component in bundle.components_of_kind(ComponentKind.HARNESS_VALIDATOR):
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", component.key):
                raise ValueError(f"invalid harness component key: {component.key!r}")
            source = ""
            if component.media_type == "application/json":
                value = json.loads(component.content)
                if isinstance(value, dict):
                    source = str(value.get("code", ""))
            elif component.media_type == "text/x-python":
                source = component.content
            if not source.strip():
                continue
            ast.parse(source)
            target = harness_dir / f"{component.key}.py"
            write_text_atomic(target, source.rstrip() + "\n")

    def load_bundle(self, scenario: str, digest: str) -> ContextBundle:
        bundle = ContextBundle.from_dict(read_json(self._bundle_path(scenario, digest)))
        if bundle.scenario != scenario:
            raise ValueError("bundle scenario does not match its storage namespace")
        return bundle

    def active_pointer(self, scenario: str) -> dict[str, Any] | None:
        path = self._active_path(scenario)
        if not path.exists():
            return None
        data = read_json(path)
        if not isinstance(data, dict):
            raise ValueError("active context bundle pointer must be an object")
        _require_digest(str(data["bundle_digest"]))
        return data

    def active_bundle(self, scenario: str) -> ContextBundle | None:
        pointer = self.active_pointer(scenario)
        if pointer is None:
            return None
        return self.load_bundle(scenario, str(pointer["bundle_digest"]))

    def bootstrap(self, bundle: ContextBundle, *, rationale: str = "legacy active context baseline") -> ContextBundle:
        """Adopt an existing live context as the initial baseline, never a challenger."""
        with self._lock(bundle.scenario):
            active = self.active_bundle(bundle.scenario)
            if active is not None:
                return active
            if bundle.parent_digest is not None:
                raise ValueError("a bootstrap bundle cannot have a parent")
            validate_bundle_promotion_contract(bundle)
            self.save_bundle(bundle)
            now = _now()
            record = CandidateRecord(
                bundle_digest=bundle.digest,
                parent_digest=None,
                evaluator_epoch=bundle.evaluator_epoch,
                lifecycle=BundleLifecycle.ACTIVE,
                source_run_id="",
                source_generation=0,
                created_at=now,
                updated_at=now,
                rationale=rationale,
            )
            write_json(self._record_path(bundle.scenario, bundle.digest), record.to_dict())
            write_json(
                self._active_path(bundle.scenario),
                {
                    "schema_version": 1,
                    "bundle_digest": bundle.digest,
                    "evaluator_epoch": bundle.evaluator_epoch,
                    "promotion_id": None,
                    "rollback_target_digest": None,
                    "activated_at": now,
                    "rationale": rationale,
                },
            )
            self._materialize_active_compatibility(bundle)
            return bundle

    def rollover_evaluator_epoch(self, scenario: str, evaluator_epoch: str) -> ContextBundle:
        return rollover_evaluator_epoch(self, scenario, evaluator_epoch)

    def pending_candidates(self, scenario: str, source_run_id: str, source_generation: int) -> tuple[CandidateRecord, ...]:
        return pending_candidates(self, scenario, source_run_id, source_generation)

    def propose(
        self,
        bundle: ContextBundle,
        *,
        source_run_id: str,
        source_generation: int,
        rationale: str = "",
    ) -> CandidateRecord:
        with self._lock(bundle.scenario):
            pointer = self.active_pointer(bundle.scenario)
            active_digest = str(pointer["bundle_digest"]) if pointer is not None else None
            if bundle.parent_digest != active_digest:
                raise ValueError("candidate parent is stale; it must equal the active bundle digest")
            existing_path = self._record_path(bundle.scenario, bundle.digest)
            if existing_path.exists():
                existing = CandidateRecord.from_dict(read_json(existing_path))
                if existing.source_run_id != source_run_id or existing.source_generation != source_generation:
                    raise ValueError("candidate digest is already bound to a different source generation")
                self.save_bundle(bundle)
                self._persist_manifest_diff(bundle)
                return existing
            self.save_bundle(bundle)
            self._persist_manifest_diff(bundle)
            now = _now()
            record = CandidateRecord(
                bundle_digest=bundle.digest,
                parent_digest=bundle.parent_digest,
                evaluator_epoch=bundle.evaluator_epoch,
                lifecycle=BundleLifecycle.PROPOSED,
                source_run_id=source_run_id,
                source_generation=source_generation,
                created_at=now,
                updated_at=now,
                rationale=rationale,
            )
            write_json(existing_path, record.to_dict())
            return record

    def candidate(self, scenario: str, digest: str) -> CandidateRecord:
        return CandidateRecord.from_dict(read_json(self._record_path(scenario, digest)))

    def matched_trials(self, scenario: str, digest: str) -> list[MatchedTrial]:
        return self._matched_evidence(scenario, digest).trials

    def matched_evidence_binding(self, scenario: str, digest: str) -> tuple[str, str, str]:
        return matched_evidence_binding(
            self,
            scenario,
            digest,
            schema_version=MATCHED_EVIDENCE_SCHEMA_VERSION,
        )

    def record_manifest_verified_attribution(
        self,
        scenario: str,
        digest: str,
        artifact: dict[str, Any],
    ) -> Path:
        """Persist one immutable attribution artifact before serving cutover."""

        path = self._causal_attribution_path(scenario, digest)
        with self._lock(scenario):
            if path.exists():
                if read_json(path) != artifact:
                    raise ValueError("candidate causal attribution artifact is immutable")
                return path
            write_json(path, artifact)
            return path

    def record_negative_result(
        self,
        scenario: str,
        digest: str,
        artifact: dict[str, Any],
    ) -> Path:
        """Persist one immutable, context-bound negative-result ledger."""

        from autocontext.storage.negative_result_ledger_store import negative_result_ledger_path

        path = self._negative_result_path(scenario, digest)
        discoverable_path = negative_result_ledger_path(
            self.knowledge_root,
            scenario,
            f"context-bundle-{digest}",
        )
        with self._lock(scenario):
            if path.exists():
                if read_json(path) != artifact:
                    raise ValueError("candidate negative-result artifact is immutable")
            else:
                write_json(path, artifact)
            if discoverable_path.exists():
                if read_json(discoverable_path) != artifact:
                    raise ValueError("discoverable candidate negative-result artifact is immutable")
            else:
                write_json(discoverable_path, artifact)
            return path

    def _matched_evidence(self, scenario: str, digest: str) -> _MatchedEvidence:
        path = self._trials_path(scenario, digest)
        if not path.exists():
            return _MatchedEvidence(trials=[], confirmation_policy=None, confirmation_policy_digest=None)
        data = read_json(path)
        if isinstance(data, list):
            return _MatchedEvidence(
                trials=parse_matched_trials(data),
                confirmation_policy=None,
                confirmation_policy_digest=None,
                legacy=True,
            )
        if not isinstance(data, dict):
            raise ValueError("matched evidence artifact must be an object or legacy array")
        if data.get("schema_version") != MATCHED_EVIDENCE_SCHEMA_VERSION:
            raise ValueError(f"unsupported matched evidence schema version: {data.get('schema_version')}")
        expected_fields = {
            "schema_version",
            "trials",
            "confirmation_policy",
            "confirmation_policy_digest",
        }
        if set(data) != expected_fields:
            raise ValueError("matched evidence artifact has an invalid schema-v2 shape")
        raw_policy = data.get("confirmation_policy")
        raw_digest = data.get("confirmation_policy_digest")
        if not isinstance(raw_policy, dict) or not isinstance(raw_digest, str):
            raise ValueError("matched evidence artifact is missing its confirmation policy")
        if stable_digest(raw_policy) != raw_digest:
            raise ValueError("matched evidence confirmation policy digest mismatch")
        ConfirmationPolicy.from_dict(raw_policy)
        raw_trials = data.get("trials")
        if not isinstance(raw_trials, list):
            raise ValueError("matched evidence trials must be an array")
        return _MatchedEvidence(
            trials=parse_matched_trials(raw_trials),
            confirmation_policy=dict(raw_policy),
            confirmation_policy_digest=raw_digest,
        )

    def _write_matched_evidence(
        self,
        scenario: str,
        digest: str,
        *,
        trials: list[MatchedTrial],
        confirmation_policy: dict[str, Any],
        confirmation_policy_digest: str,
    ) -> None:
        write_json(
            self._trials_path(scenario, digest),
            {
                "schema_version": MATCHED_EVIDENCE_SCHEMA_VERSION,
                "trials": [trial.to_dict() for trial in trials],
                "confirmation_policy": confirmation_policy,
                "confirmation_policy_digest": confirmation_policy_digest,
            },
        )

    def record_matched_trials(
        self,
        scenario: str,
        digest: str,
        trials: list[MatchedTrial],
        *,
        policy: ConfirmationPolicy | None = None,
        evaluator_plan_digest: str | None = None,
    ) -> ComparisonResult:
        """Merge raw pairs, evaluate them, and advance only the candidate lifecycle."""
        effective_policy = policy or ConfirmationPolicy()
        with self._lock(scenario):
            from autocontext.context_bundles.evaluator_plan import (
                require_bound_evaluator_plan,
            )

            require_bound_evaluator_plan(
                self,
                scenario,
                digest,
                evaluator_plan_digest,
            )
            record = self.candidate(scenario, digest)
            evidence = self._matched_evidence(scenario, digest)
            existing = evidence.trials
            existing_by_key = {trial.pair_key: trial for trial in existing}
            idempotent_recovery_input = all(existing_by_key.get(trial.pair_key) == trial for trial in trials)
            recovering_terminal_legacy_evidence = (
                bool(existing)
                and record.comparison is not None
                and (record.confirmation_policy is None or evidence.legacy)
                and record.lifecycle in {BundleLifecycle.CONFIRMED, BundleLifecycle.REJECTED}
                and idempotent_recovery_input
            )
            if record.lifecycle in {BundleLifecycle.ACTIVE, BundleLifecycle.SUPERSEDED} or (
                record.lifecycle in {BundleLifecycle.CONFIRMED, BundleLifecycle.REJECTED}
                and not recovering_terminal_legacy_evidence
            ):
                raise ValueError(f"cannot add trials to a {record.lifecycle.value} bundle")
            bundle = self.load_bundle(scenario, digest)
            policy_payload = effective_policy.to_dict()
            policy_digest = stable_digest(policy_payload)
            bound_policy, bound_policy_digest = bound_confirmation_policy(record, evidence)
            if bound_policy is not None and (bound_policy != policy_payload or bound_policy_digest != policy_digest):
                raise ValueError("confirmation policy cannot change while collecting matched evidence")
            by_key = dict(existing_by_key)
            for trial in trials:
                previous = by_key.get(trial.pair_key)
                if previous is not None and previous != trial:
                    raise ValueError("matched trial pair cannot be overwritten")
                by_key[trial.pair_key] = trial
            merged = sorted(by_key.values(), key=lambda trial: (trial.lane.value, trial.pair_key))
            comparison = evaluate_matched_trials(bundle, merged, policy=effective_policy)
            if recovering_terminal_legacy_evidence:
                if record.comparison is None:
                    raise ValueError("terminal legacy candidate is missing its comparison")
                recorded_comparison = comparison_from_dict(record.comparison)
                if comparison.to_dict() != recorded_comparison.to_dict():
                    raise ValueError("legacy confirmation policy does not reproduce the terminal comparison")
                expected_terminal_lifecycle = {
                    ComparisonDecision.CONFIRMED: BundleLifecycle.CONFIRMED,
                    ComparisonDecision.REJECTED: BundleLifecycle.REJECTED,
                }.get(comparison.decision)
                if expected_terminal_lifecycle != record.lifecycle:
                    raise ValueError("legacy comparison does not reproduce the terminal lifecycle")
            self._write_matched_evidence(
                scenario,
                digest,
                trials=merged,
                confirmation_policy=policy_payload,
                confirmation_policy_digest=policy_digest,
            )

            lifecycle = record.lifecycle
            if comparison.decision == ComparisonDecision.CONFIRMED:
                lifecycle = BundleLifecycle.CONFIRMED
            elif comparison.decision == ComparisonDecision.REJECTED:
                lifecycle = BundleLifecycle.REJECTED
            elif comparison.decision in {
                ComparisonDecision.NEEDS_CONFIRMATION,
                ComparisonDecision.NEEDS_HELDOUT,
                ComparisonDecision.INCONCLUSIVE,
            }:
                lifecycle = BundleLifecycle.SCREENED
            updated = CandidateRecord(
                bundle_digest=record.bundle_digest,
                parent_digest=record.parent_digest,
                evaluator_epoch=record.evaluator_epoch,
                lifecycle=lifecycle,
                source_run_id=record.source_run_id,
                source_generation=record.source_generation,
                created_at=record.created_at,
                updated_at=_now(),
                rationale=comparison.reason,
                comparison=comparison.to_dict(),
                confirmation_policy=policy_payload,
                confirmation_policy_digest=policy_digest,
            )
            write_json(self._record_path(scenario, digest), updated.to_dict())
            return comparison

    def replay_matched_trials(
        self,
        scenario: str,
        digest: str,
        *,
        policy: ConfirmationPolicy,
        evaluator_plan_digest: str | None = None,
    ) -> ComparisonResult:
        return replay_matched_trials(
            self,
            scenario,
            digest,
            policy,
            evaluator_plan_digest=evaluator_plan_digest,
        )

    def migrate_terminal_matched_evidence(
        self,
        scenario: str,
        digest: str,
        *,
        policy: ConfirmationPolicy,
    ) -> bool:
        return migrate_terminal_matched_evidence(self, scenario, digest, policy)

    def promote(
        self,
        scenario: str,
        digest: str,
        *,
        cohort: str,
        rationale: str,
        evaluator_plan_digest: str | None = None,
    ) -> PromotionArtifact:
        """Atomically switch the active pointer after confirmed matched evidence."""
        if not cohort.strip():
            raise ValueError("promotion cohort is required")
        with self._lock(scenario):
            from autocontext.context_bundles.evaluator_plan import (
                require_bound_evaluator_plan,
            )

            require_bound_evaluator_plan(
                self,
                scenario,
                digest,
                evaluator_plan_digest,
            )
            pointer = self.active_pointer(scenario)
            if pointer is not None and pointer.get("bundle_digest") == digest:
                artifact = promotion_from_pointer(self, scenario, pointer)
                if artifact.cohort != cohort:
                    raise ValueError("recovered context bundle promotion cohort mismatch")
                finalize_active_lifecycles(self, scenario, artifact, rationale)
                return artifact

            record = self.candidate(scenario, digest)
            if record.lifecycle != BundleLifecycle.CONFIRMED or record.comparison is None:
                raise ValueError("only a confirmed context bundle can be promoted")
            if stale_terminalization_path(self, scenario, digest).exists():
                raise ValueError("stale context bundle terminalization is already committed")
            bundle = self.load_bundle(scenario, digest)
            incumbent_digest = str(pointer["bundle_digest"]) if pointer is not None else None
            if incumbent_digest != bundle.parent_digest:
                raise ValueError("active bundle changed after confirmation; candidate must be re-evaluated")
            incumbent_bundle = self.load_bundle(scenario, incumbent_digest) if incumbent_digest is not None else None
            validate_bundle_promotion_contract(bundle, incumbent=incumbent_bundle)
            manifest_diff = self._verified_manifest_diff(bundle, incumbent_bundle)
            evidence = self._matched_evidence(scenario, digest)
            trials = evidence.trials
            if {trial.cohort for trial in trials} != {cohort}:
                raise ValueError("promotion cohort must exactly match every confirmation trial")
            policy = confirmation_policy_from_binding(record, evidence)
            if evidence.legacy:
                raise ValueError("schema-v1 terminal evidence must be migrated before promotion")
            if evidence.confirmation_policy != policy.to_dict() or evidence.confirmation_policy_digest != stable_digest(
                policy.to_dict()
            ):
                raise ValueError("matched evidence and candidate record bind different confirmation policies")
            recorded_comparison = comparison_from_dict(record.comparison)
            comparison = evaluate_matched_trials(bundle, trials, policy=policy)
            if comparison.to_dict() != recorded_comparison.to_dict():
                raise ValueError("persisted matched trials and policy do not reproduce the candidate comparison")
            if comparison.decision != ComparisonDecision.CONFIRMED:
                raise ValueError("replayed candidate evidence is not confirmed")

            now = _now()
            artifact = PromotionArtifact(
                promotion_id=uuid.uuid4().hex,
                candidate_digest=digest,
                incumbent_digest=incumbent_digest,
                rollback_target_digest=incumbent_digest,
                evaluator_epoch=bundle.evaluator_epoch,
                cohort=cohort,
                rationale=rationale,
                comparison=comparison,
                confirmation_policy=policy,
                confirmation_policy_digest=stable_digest(policy.to_dict()),
                manifest_diff_digest=manifest_diff.digest,
                promoted_at=now,
            )
            promotion_path = self._root(scenario) / "promotions" / f"{artifact.promotion_id}.json"
            write_json(promotion_path, artifact.to_dict())

            # This single atomic replace is the serving commit. Bundles are
            # immutable and already durable, so no reader can see mixed state.
            write_json(
                self._active_path(scenario),
                {
                    "schema_version": 1,
                    "bundle_digest": digest,
                    "evaluator_epoch": bundle.evaluator_epoch,
                    "promotion_id": artifact.promotion_id,
                    "rollback_target_digest": incumbent_digest,
                    "manifest_diff_digest": manifest_diff.digest,
                    "activated_at": now,
                    "rationale": rationale,
                },
            )
            self._materialize_active_compatibility(bundle)
            # The pointer is the serving commit. Lifecycle mirrors are repaired
            # best-effort and can be replayed from the durable promotion artifact
            # if the process or filesystem fails after this point.
            finalize_active_lifecycles(self, scenario, artifact, rationale)
            return artifact

    def _persist_manifest_diff(self, bundle: ContextBundle) -> ContextBundleManifestDiff:
        if bundle.parent_digest is None:
            raise ValueError("candidate context bundle must have a parent manifest")
        incumbent = self.load_bundle(bundle.scenario, bundle.parent_digest)
        expected = context_bundle_manifest_diff(bundle, incumbent)
        path = self._manifest_diff_path(bundle.scenario, bundle.digest)
        if path.exists():
            existing = ContextBundleManifestDiff.from_dict(read_json(path))
            if existing != expected:
                raise ValueError("immutable context bundle manifest diff does not match candidate manifests")
            return existing
        write_json(path, expected.to_dict())
        return expected

    def _verified_manifest_diff(
        self,
        bundle: ContextBundle,
        incumbent: ContextBundle | None,
    ) -> ContextBundleManifestDiff:
        if incumbent is None:
            raise ValueError("context bundle promotion requires an incumbent manifest")
        path = self._manifest_diff_path(bundle.scenario, bundle.digest)
        if not path.exists():
            # Candidates created before manifest-diff persistence can be
            # migrated exactly from their immutable parent/tested manifests.
            return self._persist_manifest_diff(bundle)
        persisted = ContextBundleManifestDiff.from_dict(read_json(path))
        expected = context_bundle_manifest_diff(bundle, incumbent)
        if persisted != expected:
            raise ValueError("persisted context bundle manifest diff does not match promotion manifests")
        return persisted

    def rollback(self, scenario: str, *, rationale: str) -> ContextBundle:
        """Atomically restore the active pointer's explicit rollback target."""
        with self._lock(scenario):
            pointer = self.active_pointer(scenario)
            if pointer is None:
                raise ValueError("cannot roll back without an active context bundle")
            current_digest = str(pointer["bundle_digest"])
            target_digest = pointer.get("rollback_target_digest")
            if not isinstance(target_digest, str) or not target_digest:
                raise ValueError("active context bundle has no rollback target")
            target = self.load_bundle(scenario, target_digest)
            validate_bundle_promotion_contract(target)
            now = _now()
            write_json(
                self._active_path(scenario),
                {
                    "schema_version": 1,
                    "bundle_digest": target_digest,
                    "evaluator_epoch": target.evaluator_epoch,
                    "promotion_id": None,
                    "rollback_target_digest": current_digest,
                    "activated_at": now,
                    "rationale": f"rollback: {rationale}",
                },
            )
            self._materialize_active_compatibility(target)
            self._set_lifecycle(
                scenario,
                self.candidate(scenario, current_digest),
                BundleLifecycle.SUPERSEDED,
                f"rolled back: {rationale}",
            )
            self._set_lifecycle(
                scenario,
                self.candidate(scenario, target_digest),
                BundleLifecycle.ACTIVE,
                f"restored by rollback: {rationale}",
            )
            return target

    def _materialize_active_compatibility(self, bundle: ContextBundle) -> None:
        """Refresh legacy read surfaces after the canonical pointer commits.

        Serving resolves the immutable bundle, not these mirrors. Therefore a
        mirror failure cannot expose mixed context and must not roll back an
        already-committed pointer.
        """
        scenario_root = resolve_scenario_root(self.knowledge_root, bundle.scenario)
        for component in bundle.components:
            try:
                if component.kind == ComponentKind.PLAYBOOK and component.key == "playbook":
                    write_text_atomic(scenario_root / "playbook.md", component.content.rstrip() + "\n")
                elif component.kind == ComponentKind.HINTS and component.key == "hints":
                    write_text_atomic(scenario_root / "hints.md", component.content.rstrip() + "\n")
            except OSError:
                # The active pointer remains authoritative and fully usable.
                continue

    def reject(self, scenario: str, digest: str, *, rationale: str) -> CandidateRecord:
        """Reject a candidate without writing the active pointer."""
        with self._lock(scenario):
            record = self.candidate(scenario, digest)
            if record.lifecycle in {BundleLifecycle.ACTIVE, BundleLifecycle.SUPERSEDED}:
                raise ValueError(f"cannot reject a {record.lifecycle.value} bundle")
            return self._set_lifecycle(scenario, record, BundleLifecycle.REJECTED, rationale)

    def _set_lifecycle(
        self,
        scenario: str,
        record: CandidateRecord,
        lifecycle: BundleLifecycle,
        rationale: str,
    ) -> CandidateRecord:
        updated = CandidateRecord(
            bundle_digest=record.bundle_digest,
            parent_digest=record.parent_digest,
            evaluator_epoch=record.evaluator_epoch,
            lifecycle=lifecycle,
            source_run_id=record.source_run_id,
            source_generation=record.source_generation,
            created_at=record.created_at,
            updated_at=_now(),
            rationale=rationale,
            comparison=record.comparison,
            confirmation_policy=record.confirmation_policy,
            confirmation_policy_digest=record.confirmation_policy_digest,
        )
        write_json(self._record_path(scenario, record.bundle_digest), updated.to_dict())
        return updated
