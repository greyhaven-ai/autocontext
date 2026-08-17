"""Transactional persistence for immutable context bundles (AC-973)."""

from __future__ import annotations

import ast
import fcntl
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.context_bundles.comparison import evaluate_matched_trials
from autocontext.context_bundles.models import (
    BundleLifecycle,
    ComparisonDecision,
    ComparisonResult,
    ComponentKind,
    ConfirmationPolicy,
    ContextBundle,
    MatchedTrial,
    PromotionArtifact,
)
from autocontext.storage.scenario_paths import resolve_scenario_root
from autocontext.util.json_io import read_json, write_json, write_text_atomic


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
    rationale: str = ""
    comparison: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateRecord:
        return cls(
            bundle_digest=str(data["bundle_digest"]),
            parent_digest=(str(data["parent_digest"]) if data.get("parent_digest") is not None else None),
            evaluator_epoch=str(data["evaluator_epoch"]),
            lifecycle=BundleLifecycle(str(data["lifecycle"])),
            source_run_id=str(data.get("source_run_id", "")),
            source_generation=int(data.get("source_generation", 0)),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            rationale=str(data.get("rationale", "")),
            comparison=(dict(data["comparison"]) if isinstance(data.get("comparison"), dict) else None),
        )


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

    def _active_path(self, scenario: str) -> Path:
        return self._root(scenario) / "active.json"

    @contextmanager
    def _lock(self, scenario: str) -> Iterator[None]:
        lock_path = self._root(scenario) / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

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
                self.save_bundle(bundle)
                return existing
            self.save_bundle(bundle)
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
        path = self._trials_path(scenario, digest)
        if not path.exists():
            return []
        data = read_json(path)
        if not isinstance(data, list):
            raise ValueError("matched trial artifact must be an array")
        return [MatchedTrial.from_dict(item) for item in data]

    def record_matched_trials(
        self,
        scenario: str,
        digest: str,
        trials: list[MatchedTrial],
        *,
        policy: ConfirmationPolicy | None = None,
    ) -> ComparisonResult:
        """Merge raw pairs, evaluate them, and advance only the candidate lifecycle."""
        effective_policy = policy or ConfirmationPolicy()
        with self._lock(scenario):
            record = self.candidate(scenario, digest)
            if record.lifecycle in {
                BundleLifecycle.ACTIVE,
                BundleLifecycle.CONFIRMED,
                BundleLifecycle.REJECTED,
                BundleLifecycle.SUPERSEDED,
            }:
                raise ValueError(f"cannot add trials to a {record.lifecycle.value} bundle")
            bundle = self.load_bundle(scenario, digest)
            existing = self.matched_trials(scenario, digest)
            by_key = {trial.pair_key: trial for trial in existing}
            for trial in trials:
                previous = by_key.get(trial.pair_key)
                if previous is not None and previous != trial:
                    raise ValueError("matched trial pair cannot be overwritten")
                by_key[trial.pair_key] = trial
            merged = sorted(by_key.values(), key=lambda trial: (trial.lane.value, trial.pair_key))
            comparison = evaluate_matched_trials(bundle, merged, policy=effective_policy)
            write_json(self._trials_path(scenario, digest), [trial.to_dict() for trial in merged])

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
            )
            write_json(self._record_path(scenario, digest), updated.to_dict())
            return comparison

    def promote(self, scenario: str, digest: str, *, cohort: str, rationale: str) -> PromotionArtifact:
        """Atomically switch the active pointer after confirmed matched evidence."""
        if not cohort.strip():
            raise ValueError("promotion cohort is required")
        with self._lock(scenario):
            record = self.candidate(scenario, digest)
            if record.lifecycle != BundleLifecycle.CONFIRMED or record.comparison is None:
                raise ValueError("only a confirmed context bundle can be promoted")
            bundle = self.load_bundle(scenario, digest)
            pointer = self.active_pointer(scenario)
            incumbent_digest = str(pointer["bundle_digest"]) if pointer is not None else None
            if incumbent_digest != bundle.parent_digest:
                raise ValueError("active bundle changed after confirmation; candidate must be re-evaluated")
            trials = self.matched_trials(scenario, digest)
            if {trial.cohort for trial in trials} != {cohort}:
                raise ValueError("promotion cohort must exactly match every confirmation trial")
            comparison = _comparison_from_dict(record.comparison)
            if comparison.decision != ComparisonDecision.CONFIRMED:
                raise ValueError("candidate record does not contain confirmed comparison evidence")

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
                    "activated_at": now,
                    "rationale": rationale,
                },
            )
            self._materialize_active_compatibility(bundle)
            self._set_lifecycle(scenario, record, BundleLifecycle.ACTIVE, rationale)
            if incumbent_digest is not None:
                incumbent = self.candidate(scenario, incumbent_digest)
                self._set_lifecycle(scenario, incumbent, BundleLifecycle.SUPERSEDED, f"superseded by {digest}")
            return artifact

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
        )
        write_json(self._record_path(scenario, record.bundle_digest), updated.to_dict())
        return updated


def _comparison_from_dict(data: dict[str, Any]) -> ComparisonResult:
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
