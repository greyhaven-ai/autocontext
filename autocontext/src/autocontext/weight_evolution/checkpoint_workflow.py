"""Adaptive tiny-checkpoint edits with native seals and separate fixture scoring.

The legacy comparison/session strings are explicit compatibility projections of
typed checkpoint references. Native seals and independently verified tensor
bytes are authoritative. Literal responses remain fixture evidence, not inference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.adaptive import AdaptivePolicy, run_adaptive_search, validate_equal_search_budgets
from autocontext.weight_evolution.adaptive_confirmation import ConfirmationPolicy, build_confirmation_selection
from autocontext.weight_evolution.adaptive_workflow import AdaptiveArm
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry, TensorCandidateSpec, TensorSessionAdapter
from autocontext.weight_evolution.checkpoint_seal import confirm_checkpoint_selection, seal_checkpoint_search
from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence, _validate_evidence, evaluate_comparison_fixture
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import _verified_bytes
from autocontext.weight_evolution.comparison_models import (
    CheckpointArtifact,
    ComparisonArtifacts,
    ComparisonPlan,
    ContextArtifact,
    DecodingArtifact,
    DevelopmentProvenance,
    build_comparison_plan,
)
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier
from autocontext.weight_evolution.session_data import MAX_SESSION_DATA_BYTES, SessionDataBundle, load_session_data
from autocontext.weight_evolution.session_workflow import _freeze, _publish_or_verify
from autocontext.weight_evolution.tensor_edits import MAX_TINY_CHECKPOINT_BYTES


class CheckpointWorkflow(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    checkpoint_kind: Literal["tiny_cpu_safetensors"] = "tiny_cpu_safetensors"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    workflow_id: Identifier
    data_digest: Digest
    candidates: tuple[TensorCandidateSpec, ...] = Field(min_length=2, max_length=8)
    search_policy: AdaptivePolicy
    confirmation_policy: ConfirmationPolicy
    arms: tuple[AdaptiveArm, ...] = Field(min_length=3, max_length=3)
    confirmation_plan: ComparisonPlan
    confirmation_evidence_digest: Digest
    audit_plan: ComparisonPlan
    audit_evidence_digest: Digest

    @model_validator(mode="after")
    def equal_capacities(self) -> Self:
        validate_equal_search_budgets(tuple(arm.spec for arm in self.arms), self.search_policy)
        if any(arm.spec.data_digest != self.data_digest for arm in self.arms):
            raise ValueError("checkpoint search arms must share the pinned data")
        if (self.confirmation_plan.artifacts != self.audit_plan.artifacts
                or self.confirmation_plan.protocol != self.audit_plan.protocol):
            raise ValueError("confirmation and audit must bind the same candidate artifacts and protocol")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class CheckpointFixtureLock(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    base_sha256: Digest
    base_size_bytes: int = Field(ge=10, le=MAX_TINY_CHECKPOINT_BYTES)
    data_sha256: Digest
    data_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)
    workflow_sha256: Digest
    workflow_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)
    confirmation_sha256: Digest
    confirmation_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)
    audit_sha256: Digest
    audit_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)


def load_checkpoint_workflow(
    directory: Path,
) -> tuple[CheckpointWorkflow, SessionDataBundle, FixtureEvidence, FixtureEvidence]:
    lock = CheckpointFixtureLock.model_validate_json(json.dumps(load_small_json(directory / "fixtures.lock.json")))
    identity = file_identity(directory / "base.safetensors", max_bytes=MAX_TINY_CHECKPOINT_BYTES)
    if identity["sha256"] != lock.base_sha256 or identity["size_bytes"] != lock.base_size_bytes:
        raise ValueError("base checkpoint differs from fixture byte/hash pins")
    data = load_session_data(directory / "data.json", expected_sha256=lock.data_sha256,
                             expected_size_bytes=lock.data_size_bytes)
    workflow = CheckpointWorkflow.model_validate_json(_verified_bytes(
        directory / "workflow.json", lock.workflow_sha256, lock.workflow_size_bytes,
    ))
    confirmation = FixtureEvidence.model_validate_json(_verified_bytes(
        directory / "confirmation.json", lock.confirmation_sha256, lock.confirmation_size_bytes,
    ))
    audit = FixtureEvidence.model_validate_json(_verified_bytes(directory / "audit.json", lock.audit_sha256,
                                                               lock.audit_size_bytes))
    if (data.digest != workflow.data_digest or confirmation.digest != workflow.confirmation_evidence_digest
            or audit.digest != workflow.audit_evidence_digest):
        raise ValueError("checkpoint workflow data or response identities changed")
    if any(candidate.contract.sha256 != lock.base_sha256 or candidate.contract.size_bytes != lock.base_size_bytes
           for candidate in workflow.candidates):
        raise ValueError("tensor candidates differ from the locked base checkpoint")
    return workflow, data, confirmation, audit


def _validate_inputs(workflow: CheckpointWorkflow, data: SessionDataBundle, confirmation: FixtureEvidence,
                     audit: FixtureEvidence, registry: TensorCandidateRegistry) -> tuple[TensorSessionAdapter, ...]:
    registry.verify(registry.baseline.handle)
    _validate_evidence(workflow.confirmation_plan, confirmation)
    _validate_evidence(workflow.audit_plan, audit)
    scorer = SyntheticComparisonScorer()
    required = workflow.search_policy.required_usage
    units = sum(getattr(required, name) for name in (
        "editor_input_bytes", "editor_output_bytes", "probe_input_bytes", "probe_output_bytes"))
    for plan, cases in ((workflow.confirmation_plan, data.confirmation), (workflow.audit_plan, data.final_audit)):
        if plan.cases != cases or plan.development.cases != data.practice or plan.protocol.scorer_digest != scorer.lock_digest:
            raise ValueError("checkpoint evaluation role, development or scorer pin differs")
        if (plan.budgets.max_search_calls_per_mechanism < required.operations
                or plan.budgets.max_search_units_per_mechanism < units):
            raise ValueError("comparison caps cannot cover worst-case checkpoint search reservations")
    adapters = []
    for arm in workflow.arms:
        baseline = arm.spec.baseline
        if (baseline.base_checkpoint.content != registry.baseline.handle
                or baseline.base_checkpoint != workflow.confirmation_plan.artifacts.base_checkpoint
                or baseline.base_context != workflow.confirmation_plan.artifacts.base_context
                or baseline.base_decoding != workflow.confirmation_plan.artifacts.base_decoding
                or arm.spec.scorer_digest != scorer.lock_digest):
            raise ValueError("checkpoint search/evaluation baseline or scorer mismatch")
        adapter = TensorSessionAdapter(arm.adapter(), registry)
        if adapter.identity_digest != arm.spec.adapter_digest:
            raise ValueError("checkpoint adapter source/catalog changed")
        adapters.append(adapter)
    for dataset in ("math500", "gsm8k", "ifeval"):
        scorer.validate_cohort(dataset, tuple(case.problem for role in (data.practice, data.confirmation, data.final_audit)
                                               for case in role if case.dataset == dataset))
    return tuple(adapters)


def validate_checkpoint_workflow(directory: Path) -> dict[str, Any]:
    workflow, data, confirmation, audit = load_checkpoint_workflow(directory)
    # Registry construction is read-only; this directory is never created by validation.
    registry = TensorCandidateRegistry(workflow.candidates, input_path=directory / "base.safetensors",
                                       output_dir=directory / "unused-validation-store")
    _validate_inputs(workflow, data, confirmation, audit, registry)
    return {"workflow_digest": workflow.digest, "checkpoint_kind": workflow.checkpoint_kind,
            "base_sha256": registry.baseline.output_sha256, "candidate_count": len(workflow.candidates),
            "execution_kind": "fixture", "model_execution_performed": False, "gpu_execution_deferred": True,
            "splits": data.split_commitments()}


def run_checkpoint_workflow(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    workflow, data, confirmation, audit = load_checkpoint_workflow(directory)
    root = output_dir.absolute()
    registry = TensorCandidateRegistry(workflow.candidates, input_path=directory / "base.safetensors",
                                       output_dir=root / "materializations")
    adapters = _validate_inputs(workflow, data, confirmation, audit, registry)
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("checkpoint workflow output must not traverse symlinks")
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".checkpoint-workflow.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock):
        if not (root / "workflow.json").exists() and {path.name for path in root.iterdir()} - {lock.name}:
            raise ValueError("refusing to adopt an unowned checkpoint workflow directory")
        if any(path.exists() or path.is_symlink() for path in (root / "confirmation", root / "complete.json")):
            for name in ("selection.json", "native-C.json", "native-W.json", "native-D.json"):
                if not (root / name).exists():
                    raise ValueError("retained confirmation is missing its native selection evidence")
        _freeze(root / "workflow.json", {"workflow": workflow.to_dict(), "workflow_digest": workflow.digest,
                                         "coordinator_source": file_identity(Path(__file__))})
        registry.verify_existing()
        completions = {}
        native_paths = []
        native_digests = {}
        completion_paths = []
        for arm, adapter in zip(workflow.arms, adapters, strict=True):
            mechanism = arm.spec.mechanism
            search = root / "search" / mechanism
            if (search / "session").exists() or (search / "session").is_symlink():
                adapter.verify_session_materializations(search / "session", data=data)
            completions[mechanism] = run_adaptive_search(spec=arm.spec, policy=workflow.search_policy, data=data,
                                                        adapter=adapter, output_dir=search)
            completion_paths.append(search / "complete.json")
            path = root / f"native-{mechanism}.json"
            native = seal_checkpoint_search(search / "complete.json", adapter=adapter, data=data, output_path=path)
            native_paths.append(path)
            native_digests[mechanism] = native.digest
        selection_path = root / "selection.json"
        selection = build_confirmation_selection(data, completion_paths=tuple(completion_paths), selection_path=selection_path)
        _freeze(selection_path, selection.to_dict())
        actual_artifacts = ComparisonArtifacts(
            base_checkpoint=workflow.confirmation_plan.artifacts.base_checkpoint,
            base_context=workflow.confirmation_plan.artifacts.base_context,
            base_decoding=workflow.confirmation_plan.artifacts.base_decoding,
            learned_context=ContextArtifact.model_validate_json(json.dumps(completions["C"]["artifact"])),
            edited_checkpoint=CheckpointArtifact.model_validate_json(json.dumps(completions["W"]["artifact"])),
            selected_decoding=DecodingArtifact.model_validate_json(json.dumps(completions["D"]["artifact"])),
        )
        for expected, cases in ((workflow.confirmation_plan, data.confirmation), (workflow.audit_plan, data.final_audit)):
            actual = build_comparison_plan(study_id=expected.study_id, cases=cases, artifacts=actual_artifacts,
                                            protocol=expected.protocol, budgets=expected.budgets,
                                            development=DevelopmentProvenance(cases=data.practice))
            if actual.digest != expected.digest:
                raise ValueError("checkpoint search projections/costs differ from the pinned response plan")
        result = confirm_checkpoint_selection(
            workflow.confirmation_plan, confirmation, data=data, adapters=adapters, native_paths=tuple(native_paths),
            selection_path=selection_path, policy=workflow.confirmation_policy, output_dir=root / "confirmation",
        )
        audit_digest = None
        audit_status = "withheld_after_confirmation_veto"
        if all(outcome["status"] == "confirmed" for outcome in result["outcomes"]):
            registry.verify_existing()
            evaluation = evaluate_comparison_fixture(workflow.audit_plan, audit, SyntheticComparisonScorer())
            _publish_or_verify(evaluation, root / "audit")
            audit_digest = evaluation.digest
            audit_status = "completed_fixture_only"
        elif (root / "audit").exists() or (root / "audit").is_symlink():
            raise ValueError("audit artifacts cannot exist after checkpoint confirmation veto")
        summary = {"schema_version": 1, "workflow_digest": workflow.digest, "checkpoint_kind": workflow.checkpoint_kind,
                   "execution_kind": "fixture", "tensor_edit_performed": True,
                   "model_execution_performed": False, "gpu_execution_deferred": True,
                   "promotion_eligible": False, "benchmark_result_claimed": False,
                   "native_artifact_digests": native_digests, "selection_digest": selection.digest,
                   "confirmation_digest": canonical_digest(result), "confirmation_outcomes": result["outcomes"],
                   "audit_status": audit_status, "audit_report_digest": audit_digest}
        _freeze(root / "complete.json", summary)
        return summary
