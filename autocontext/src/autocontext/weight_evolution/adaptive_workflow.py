"""Offline adaptive search, one confirmation look, then a gated fixture audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.adaptive import AdaptivePolicy, run_adaptive_search, validate_equal_search_budgets
from autocontext.weight_evolution.adaptive_confirmation import (
    ConfirmationPolicy,
    build_confirmation_selection,
    confirm_adaptive_selection,
)
from autocontext.weight_evolution.adaptive_provider import (
    FeedbackRoute,
    OfflineFeedbackProvider,
    ProbeFixture,
    ProviderSessionAdapter,
)
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence, _validate_evidence, evaluate_comparison_fixture
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import _verified_bytes, load_comparison_fixture
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
from autocontext.weight_evolution.session_models import SessionSpec
from autocontext.weight_evolution.session_workflow import _freeze, _publish_or_verify


class AdaptiveArm(FrozenModel):
    spec: SessionSpec
    routes: tuple[FeedbackRoute, ...] = Field(min_length=1, max_length=10000)
    probes: tuple[ProbeFixture, ...] = Field(min_length=1, max_length=10000)

    def adapter(self) -> ProviderSessionAdapter:
        return ProviderSessionAdapter(self.spec.mechanism, OfflineFeedbackProvider(self.routes), self.probes)


class AdaptiveWorkflow(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    workflow_id: Identifier
    data_digest: Digest
    source_plan_digest: Digest
    source_evidence_digest: Digest
    search_policy: AdaptivePolicy
    confirmation_policy: ConfirmationPolicy
    confirmation_plan: ComparisonPlan
    confirmation_evidence_digest: Digest
    arms: tuple[AdaptiveArm, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def equal_search_contract(self) -> Self:
        validate_equal_search_budgets(tuple(arm.spec for arm in self.arms), self.search_policy)
        if len({arm.spec.session_id for arm in self.arms}) != 3:
            raise ValueError("adaptive arms require distinct session IDs")
        if any(arm.spec.data_digest != self.data_digest for arm in self.arms):
            raise ValueError("adaptive arms must share the pinned data")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class AdaptiveFixtureLock(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    data_sha256: Digest
    data_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)
    workflow_sha256: Digest
    workflow_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)
    confirmation_sha256: Digest
    confirmation_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)


def load_adaptive_workflow(directory: Path) -> tuple[AdaptiveWorkflow, SessionDataBundle, FixtureEvidence]:
    lock = AdaptiveFixtureLock.model_validate_json(json.dumps(load_small_json(directory / "fixtures.lock.json")))
    data = load_session_data(directory / "data.json", expected_sha256=lock.data_sha256,
                             expected_size_bytes=lock.data_size_bytes)
    workflow = AdaptiveWorkflow.model_validate_json(_verified_bytes(
        directory / "workflow.json", lock.workflow_sha256, lock.workflow_size_bytes,
    ))
    confirmation = FixtureEvidence.model_validate_json(_verified_bytes(
        directory / "confirmation.json", lock.confirmation_sha256, lock.confirmation_size_bytes,
    ))
    if data.digest != workflow.data_digest or confirmation.digest != workflow.confirmation_evidence_digest:
        raise ValueError("adaptive workflow data or confirmation identity changed")
    return workflow, data, confirmation


def _validate_inputs(workflow: AdaptiveWorkflow, data: SessionDataBundle, confirmation: FixtureEvidence,
                     source: ComparisonPlan, audit: FixtureEvidence) -> None:
    if source.digest != workflow.source_plan_digest or audit.digest != workflow.source_evidence_digest:
        raise ValueError("adaptive source comparison identity changed")
    if data.practice != source.development.cases or data.final_audit != source.cases:
        raise ValueError("adaptive practice/audit data must match its pinned source fixture")
    plan = workflow.confirmation_plan
    if plan.cases != data.confirmation or plan.development.cases != data.practice or plan.protocol != source.protocol:
        raise ValueError("adaptive confirmation cohort or protocol differs from the frozen contract")
    _validate_evidence(source, audit)
    _validate_evidence(plan, confirmation)
    scorer = SyntheticComparisonScorer()
    if plan.protocol.scorer_digest != scorer.lock_digest:
        raise ValueError("adaptive confirmation scorer source changed")
    expected_base = {name: getattr(source.artifacts, name).to_dict()
                     for name in ("base_checkpoint", "base_context", "base_decoding")}
    if any(getattr(plan.artifacts, name).to_dict() != value for name, value in expected_base.items()):
        raise ValueError("confirmation baseline differs from the source search baseline")
    required = workflow.search_policy.required_usage
    search_units = sum(getattr(required, name) for name in (
        "editor_input_bytes", "editor_output_bytes", "probe_input_bytes", "probe_output_bytes"))
    if (required.operations > plan.budgets.max_search_calls_per_mechanism
            or search_units > plan.budgets.max_search_units_per_mechanism):
        raise ValueError("comparison budgets cannot cover worst-case adaptive search reservations")
    for arm in workflow.arms:
        if arm.spec.baseline.to_dict() != expected_base or arm.spec.scorer_digest != scorer.lock_digest:
            raise ValueError("adaptive baseline or scorer source changed")
        if arm.spec.adapter_digest != arm.adapter().identity_digest:
            raise ValueError("adaptive provider or probe source/catalog changed")
    # Preflight scorer routes without scoring or exposing held-out rows to editors.
    for dataset in ("math500", "gsm8k", "ifeval"):
        scorer.validate_cohort(dataset, tuple(case.problem for role in (data.practice, data.confirmation, data.final_audit)
                                               for case in role if case.dataset == dataset))


def validate_adaptive_workflow(directory: Path, comparison_directory: Path) -> dict[str, Any]:
    workflow, data, confirmation = load_adaptive_workflow(directory)
    source, audit = load_comparison_fixture(comparison_directory)
    _validate_inputs(workflow, data, confirmation, source, audit)
    return {"workflow_digest": workflow.digest, "execution_kind": "fixture", "model_execution_performed": False,
            "gpu_execution_deferred": True, "splits": data.split_commitments(),
            "search_policy": workflow.search_policy.to_dict(), "confirmation_policy": workflow.confirmation_policy.to_dict()}


def run_adaptive_workflow(directory: Path, *, comparison_directory: Path, output_dir: Path) -> dict[str, Any]:
    workflow, data, confirmation = load_adaptive_workflow(directory)
    source, audit = load_comparison_fixture(comparison_directory)
    _validate_inputs(workflow, data, confirmation, source, audit)
    root = output_dir.absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("adaptive output must not traverse symlinks")
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".adaptive-workflow.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock):
        if not (root / "workflow.json").exists() and {path.name for path in root.iterdir()} - {lock.name}:
            raise ValueError("refusing to adopt an unowned adaptive output directory")
        _freeze(root / "workflow.json", {"workflow": workflow.to_dict(), "workflow_digest": workflow.digest,
                                         "coordinator_source": file_identity(Path(__file__))})
        completions = {}
        for arm in workflow.arms:
            completions[arm.spec.mechanism] = run_adaptive_search(
                spec=arm.spec, policy=workflow.search_policy, data=data, adapter=arm.adapter(),
                output_dir=root / "search" / arm.spec.mechanism,
            )
        selection_path = root / "selection.json"
        selection = build_confirmation_selection(
            data, completion_paths=tuple(root / "search" / mechanism / "complete.json" for mechanism in ("C", "W", "D")),
            selection_path=selection_path,
        )
        _freeze(selection_path, selection.to_dict())
        artifacts = ComparisonArtifacts(
            base_checkpoint=source.artifacts.base_checkpoint, base_context=source.artifacts.base_context,
            base_decoding=source.artifacts.base_decoding,
            learned_context=ContextArtifact.model_validate_json(json.dumps(completions["C"]["artifact"])),
            edited_checkpoint=CheckpointArtifact.model_validate_json(json.dumps(completions["W"]["artifact"])),
            selected_decoding=DecodingArtifact.model_validate_json(json.dumps(completions["D"]["artifact"])),
        )
        plan = build_comparison_plan(
            study_id=workflow.confirmation_plan.study_id, cases=data.confirmation, artifacts=artifacts,
            protocol=source.protocol, budgets=workflow.confirmation_plan.budgets,
            development=DevelopmentProvenance(cases=data.practice),
        )
        if plan.digest != workflow.confirmation_plan.digest:
            raise ValueError("selected artifacts/costs differ from the pinned confirmation fixture plan")
        confirmed = confirm_adaptive_selection(plan, confirmation, data=data, selection_path=selection_path,
                                                policy=workflow.confirmation_policy, output_dir=root / "confirmation")
        outcomes = confirmed["outcomes"]
        audit_digest = None
        audit_status = "withheld_after_confirmation_veto"
        if all(outcome["status"] == "confirmed" for outcome in outcomes):
            audit_plan = build_comparison_plan(
                study_id=f"{workflow.workflow_id}-audit", cases=data.final_audit, artifacts=artifacts,
                protocol=source.protocol, budgets=plan.budgets, development=DevelopmentProvenance(cases=data.practice),
            )
            if tuple(cell.digest for cell in audit_plan.cells) != tuple(cell.digest for cell in source.cells):
                raise ValueError("selected audit cells differ; prerecorded responses cannot be reused")
            _freeze(root / "audit-plan.json", audit_plan.to_dict())
            evaluation = evaluate_comparison_fixture(
                audit_plan, FixtureEvidence(plan_digest=audit_plan.digest, observations=audit.observations),
                SyntheticComparisonScorer(),
            )
            _publish_or_verify(evaluation, root / "audit")
            audit_digest = evaluation.digest
            audit_status = "completed_fixture_only"
        elif any(path.exists() or path.is_symlink() for path in (root / "audit", root / "audit-plan.json")):
            raise ValueError("audit artifacts cannot exist after a confirmation veto")
        summary = {"schema_version": 1, "workflow_digest": workflow.digest, "selection_digest": selection.digest,
                   "execution_kind": "fixture", "model_execution_performed": False, "gpu_execution_deferred": True,
                   "promotion_eligible": False, "benchmark_result_claimed": False,
                   "confirmation_digest": canonical_digest(confirmed), "confirmation_outcomes": outcomes,
                   "audit_status": audit_status, "audit_report_digest": audit_digest,
                   "search_completion_digest": selection.search_completion_digest}
        _freeze(root / "complete.json", summary)
        return summary
