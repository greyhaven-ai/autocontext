"""Native checkpoint seals and confirmation over retained CPU tensor evidence.

The old search journal stores only a compatibility projection of a checkpoint
handle. This module makes the verified safetensors candidate authoritative and
rechecks its materializations before relying on a sealed search or confirmation.
The scorer remains a literal fixture; real tensor changes are not inference.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.adaptive import AdaptivePolicy, run_adaptive_search
from autocontext.weight_evolution.adaptive_confirmation import (
    ConfirmationPolicy,
    _verified_search_completion,
    build_confirmation_selection,
    confirm_adaptive_selection,
    load_confirmation_selection,
)
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorSessionAdapter, VerifiedTensorCandidate
from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence
from autocontext.weight_evolution.comparison_models import ArtifactSource, ComparisonPlan
from autocontext.weight_evolution.models import Digest, FrozenModel
from autocontext.weight_evolution.session import _clear_staging, _immutable_json, _read_record, _same
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import CandidateRevision, Mechanism, SessionSpec, SessionUsage


class TensorCandidateArtifact(FrozenModel):
    """The native checkpoint authority, separate from the legacy search carrier."""

    schema_version: Literal[1] = 1
    representation: Literal["verified_safetensors_fixture"] = "verified_safetensors_fixture"
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False
    benchmark_result_claimed: Literal[False] = False
    mechanism: Mechanism
    tensor_edit_performed: bool
    baseline: VerifiedTensorCandidate
    checkpoint: VerifiedTensorCandidate
    registry_digest: Digest
    search_completion_path: str = Field(min_length=1, max_length=1024)
    search_completion_digest: Digest
    spec_digest: Digest
    policy_digest: Digest
    selected_candidate_digest: Digest
    candidate_digest: Digest
    selected_state_digest: Digest
    search_selection_digest: Digest
    session_export_digest: Digest
    compatibility_projection_digest: Digest
    source: ArtifactSource
    reserved_usage: SessionUsage
    observed_usage: SessionUsage
    implementation_digest: Digest

    @field_validator("search_completion_path")
    @classmethod
    def relative_completion(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (path.is_absolute() or any(part in {".", ".."} for part in path.parts)
                or str(path) != value or "\\" in value or path.name != "complete.json"):
            raise ValueError("native seal requires a normalized relative completion path")
        return value

    @model_validator(mode="after")
    def checkpoint_mechanism(self) -> Self:
        if self.baseline.reference.program_digest is not None:
            raise ValueError("native baseline must be the verified unedited checkpoint")
        if self.checkpoint.reference.input_sha256 != self.baseline.reference.output_sha256:
            raise ValueError("native candidate must derive from the pinned baseline bytes")
        if self.checkpoint.contract_digest != self.baseline.contract_digest:
            raise ValueError("native candidate and baseline must share the tensor contract")
        edited = self.mechanism == "W"
        if self.tensor_edit_performed is not edited:
            raise ValueError("native tensor-edit flag differs from the mechanism")
        if edited:
            if self.checkpoint.reference.program_digest is None or self.checkpoint.changed_entries <= 0:
                raise ValueError("W requires a verified nonempty tensor edit")
            if self.checkpoint.reference.output_sha256 == self.baseline.reference.output_sha256:
                raise ValueError("W must change checkpoint bytes")
        elif self.checkpoint.reference != self.baseline.reference:
            raise ValueError("C/D must retain the unedited checkpoint")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def _provenance() -> dict[str, Any]:
    package = Path(__file__).parent
    return {"name": "native-checkpoint-seal-v1", "sources": [
        file_identity(package / name, max_bytes=1024 * 1024) for name in (
            "checkpoint_seal.py", "checkpoint_candidates.py", "tensor_edits.py", "adaptive.py",
            "adaptive_confirmation.py", "session.py", "session_models.py", "comparison_models.py",
            "artifacts.py", "models.py",
        )
    ]}


def _safe_parent(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("native checkpoint paths must not traverse symlinks")
    path.parent.mkdir(parents=True, exist_ok=True)


def _replayed_search(completion_path: Path, *, adapter: TensorSessionAdapter,
                     data: SessionDataBundle) -> dict[str, Any]:
    """Require a retained terminal search before any replay, then verify all bytes."""
    complete = _verified_search_completion(completion_path)["complete"]
    manifest = _read_record(completion_path.parent / "manifest.json")
    spec = SessionSpec.model_validate_json(json.dumps(manifest.get("spec"), allow_nan=False))
    policy = AdaptivePolicy.model_validate_json(json.dumps(manifest.get("policy"), allow_nan=False))
    if spec.mechanism != adapter.delegate.mechanism or spec.adapter_digest != adapter.identity_digest:
        raise ValueError("native seal adapter differs from the frozen search")
    before = adapter.verify_session_materializations(completion_path.parent / "session", data=data)
    if before["status"] != "sealed":
        raise ValueError("native seal requires a retained sealed session before replay")
    replayed = run_adaptive_search(spec=spec, policy=policy, data=data, adapter=adapter,
                                  output_dir=completion_path.parent)
    _same(complete, replayed, "native search replay differs from retained completion")
    after = adapter.verify_session_materializations(completion_path.parent / "session", data=data)
    if after["status"] != "sealed" or after["history_digest"] != replayed["history_digest"]:
        raise ValueError("native checkpoint materializations differ from sealed search history")
    return {"complete": replayed, "spec": spec}


def _native_artifact(completion_path: Path, *, adapter: TensorSessionAdapter, data: SessionDataBundle,
                     output_path: Path) -> TensorCandidateArtifact:
    verified = _replayed_search(completion_path, adapter=adapter, data=data)
    complete, spec = verified["complete"], verified["spec"]
    final = CandidateRevision.model_validate_json(json.dumps(complete["candidate"], allow_nan=False))
    baseline_spec = adapter.registry.parse_handle(spec.baseline.base_checkpoint.content)
    if baseline_spec.digest != adapter.registry.baseline.digest:
        raise ValueError("search baseline handle differs from the native registry baseline")
    baseline = adapter.registry.verify(baseline_spec.handle)
    checkpoint = adapter.registry.verify(final.state.weight_descriptor)
    exported = _read_record(completion_path.parent / "session" / "export.json")
    source = ArtifactSource.model_validate_json(json.dumps(complete["artifact"]["source"], allow_nan=False))
    relative = completion_path.absolute().relative_to(output_path.absolute().parent).as_posix()
    return TensorCandidateArtifact(
        mechanism=spec.mechanism, tensor_edit_performed=spec.mechanism == "W", baseline=baseline,
        checkpoint=checkpoint, registry_digest=adapter.registry.identity_digest, search_completion_path=relative,
        search_completion_digest=canonical_digest(complete), spec_digest=spec.digest, policy_digest=complete["policy_digest"],
        selected_candidate_digest=complete["selected_candidate_digest"], candidate_digest=complete["candidate_digest"],
        selected_state_digest=complete["selected_state_digest"],
        search_selection_digest=complete["selection_receipt_digest"], session_export_digest=canonical_digest(exported),
        compatibility_projection_digest=complete["artifact_digest"], source=source,
        reserved_usage=SessionUsage.model_validate_json(json.dumps(complete["reserved_usage"])),
        observed_usage=SessionUsage.model_validate_json(json.dumps(complete["observed_usage"])),
        implementation_digest=canonical_digest(_provenance()),
    )


def seal_checkpoint_search(completion_path: Path, *, adapter: TensorSessionAdapter, data: SessionDataBundle,
                           output_path: Path) -> TensorCandidateArtifact:
    """Freeze the native authority after independently replaying a sealed search."""
    output_path = output_path.absolute()
    _safe_parent(output_path)
    artifact = _native_artifact(completion_path, adapter=adapter, data=data, output_path=output_path)
    _immutable_json(output_path, artifact.to_dict())
    return artifact


def verify_checkpoint_search_seal(path: Path, *, adapter: TensorSessionAdapter,
                                  data: SessionDataBundle) -> TensorCandidateArtifact:
    """Require existing native evidence; missing or changed bytes are never repaired."""
    path = path.absolute()
    payload = _read_record(path)
    retained = TensorCandidateArtifact.model_validate_json(json.dumps(payload, allow_nan=False))
    completion_path = path.parent / retained.search_completion_path
    actual = _native_artifact(completion_path, adapter=adapter, data=data, output_path=path)
    _same(payload, actual.to_dict(), "native checkpoint seal differs from verified search/checkpoint evidence")
    return actual


def confirm_checkpoint_selection(
    plan: ComparisonPlan, evidence: FixtureEvidence, *, data: SessionDataBundle,
    adapters: tuple[TensorSessionAdapter, ...], native_paths: tuple[Path, ...], selection_path: Path,
    policy: ConfirmationPolicy, output_dir: Path,
) -> dict[str, Any]:
    """Verify native seals before the existing one-look fixture confirmation gate."""
    if len(adapters) != 3 or len(native_paths) != 3:
        raise ValueError("native confirmation requires ordered C/W/D adapters and seals")
    plan = ComparisonPlan.model_validate_json(json.dumps(plan.to_dict(), allow_nan=False))
    evidence = FixtureEvidence.model_validate_json(json.dumps(evidence.to_dict(), allow_nan=False))
    policy = ConfirmationPolicy.model_validate_json(json.dumps(policy.to_dict(), allow_nan=False))
    data = SessionDataBundle.model_validate_json(json.dumps(data.to_dict(), allow_nan=False))
    native = tuple(verify_checkpoint_search_seal(path, adapter=adapter, data=data)
                   for path, adapter in zip(native_paths, adapters, strict=True))
    if tuple(artifact.mechanism for artifact in native) != ("C", "W", "D"):
        raise ValueError("native confirmation seals must be ordered C/W/D")
    if len({artifact.registry_digest for artifact in native}) != 1:
        raise ValueError("native confirmation requires one shared checkpoint registry")
    baseline = adapters[0].registry.parse_handle(plan.artifacts.base_checkpoint.content)
    edited = adapters[1].registry.parse_handle(plan.artifacts.edited_checkpoint.content)
    if (any(artifact.baseline.reference != baseline.reference for artifact in native)
            or native[1].checkpoint.reference != edited.reference):
        raise ValueError("comparison checkpoint projections differ from native baseline/selected tensors")
    completion_paths = tuple(path.absolute().parent / artifact.search_completion_path
                             for path, artifact in zip(native_paths, native, strict=True))
    selection = build_confirmation_selection(data, completion_paths=completion_paths, selection_path=selection_path)
    retained_selection = load_confirmation_selection(selection_path)
    _same(selection.to_dict(), retained_selection.to_dict(), "legacy selection differs from native sealed searches")
    _same(load_small_json(selection_path), selection.to_dict(), "legacy selection JSON differs from native sealed searches")
    for artifact, selected in zip(native, selection.selections, strict=True):
        if (artifact.candidate_digest != selected.candidate_digest
                or artifact.search_completion_digest != selected.completion_digest
                or artifact.compatibility_projection_digest != selected.artifact_digest):
            raise ValueError("native seal differs from all-arm confirmation selection")
    root = output_dir.absolute()
    _safe_parent(root)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".native-confirmation.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock):
        _clear_staging(root)
        existing = {path.name for path in root.iterdir()}
        allowed = {lock.name, "native-claim.json", "comparison", "complete.json"}
        if existing - allowed or ("native-claim.json" not in existing and existing - {lock.name}):
            raise ValueError("native confirmation directory contains unowned evidence")
        if "complete.json" in existing and not (root / "comparison" / "complete.json").exists():
            raise ValueError("completed native confirmation is missing its comparison evidence")
        native_selection = {"schema_version": 1, "execution_kind": "fixture", "data_digest": data.digest,
                            "selection_digest": selection.digest,
                            "native_artifacts": {artifact.mechanism: artifact.digest for artifact in native}}
        claim = {"schema_version": 1, "execution_kind": "fixture", "model_execution_performed": False,
                 "gpu_execution_deferred": True, "native_selection": native_selection,
                 "native_selection_digest": canonical_digest(native_selection), "plan_digest": plan.digest,
                 "evidence_digest": evidence.digest, "policy_digest": policy.digest,
                 "implementation": _provenance()}
        _immutable_json(root / "native-claim.json", claim)
        result = confirm_adaptive_selection(plan, evidence, data=data, selection_path=selection_path, policy=policy,
                                             output_dir=root / "comparison")
        complete = {"schema_version": 1, "native_claim_digest": canonical_digest(claim),
                    "native_selection_digest": canonical_digest(native_selection),
                    "confirmation_digest": canonical_digest(result), "status": result["status"]}
        _immutable_json(root / "complete.json", complete)
        return {**result, "native_selection_digest": complete["native_selection_digest"],
                "native_claim_digest": complete["native_claim_digest"]}
