"""CPU-only fixture sessions whose sealed exports feed the M5 comparison.

The literal audit evidence is reusable only when every evaluation-cell identity
matches the pinned source fixture. This is plumbing validation, not optimization
or inference evidence. Confirmation is committed but deliberately unused.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.comparison_evaluation import (
    ComparisonEvaluation,
    FixtureEvidence,
    _validate_evidence,
    evaluate_comparison_fixture,
    render_comparison_report,
    write_comparison_report,
)
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import _verified_bytes, load_comparison_fixture
from autocontext.weight_evolution.comparison_models import (
    CheckpointArtifact,
    ComparisonArtifacts,
    ComparisonBudgets,
    ComparisonPlan,
    ContextArtifact,
    DecodingArtifact,
    DevelopmentProvenance,
    build_comparison_plan,
)
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier
from autocontext.weight_evolution.replay import _immutable_json
from autocontext.weight_evolution.session import EditingSession
from autocontext.weight_evolution.session_data import MAX_SESSION_DATA_BYTES, SessionDataBundle, load_session_data
from autocontext.weight_evolution.session_fixtures import ScriptedSessionAdapter, SessionFixtureScript
from autocontext.weight_evolution.session_models import (
    EditOperation,
    InspectOperation,
    ProbeOperation,
    SessionOperation,
    SessionSpec,
    SessionUsage,
    apply_edit,
    initial_candidate,
)


class FixtureSessionProgram(FrozenModel):
    spec: SessionSpec
    operations: tuple[SessionOperation, ...] = Field(min_length=1, max_length=10000)
    script: SessionFixtureScript

    @model_validator(mode="after")
    def bound_operations(self) -> Self:
        if len({op.operation_id for op in self.operations}) != len(self.operations):
            raise ValueError("session program repeats an operation ID")
        expected = [(op.digest, op.candidate_digest, op.kind) for op in self.operations]
        actual = [(entry.operation_digest, entry.candidate_digest, entry.operation_kind) for entry in self.script.entries]
        if actual != expected:
            raise ValueError("script must bind exactly the ordered program operations")
        if not any(entry.outcome == "return" and entry.operation_kind == "probe" for entry in self.script.entries):
            raise ValueError("session program requires a successful practice probe")
        if any(entry.outcome == "interruption" for entry in self.script.entries):
            raise ValueError("complete workflow fixtures cannot schedule ambiguous interruptions")
        return self


class SessionWorkflow(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    gpu_execution_deferred: Literal[True] = True
    model_execution_performed: Literal[False] = False
    workflow_id: Identifier
    data_digest: Digest
    source_comparison_plan_digest: Digest
    source_comparison_evidence_digest: Digest
    budgets: ComparisonBudgets
    programs: tuple[FixtureSessionProgram, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def one_program_per_mechanism(self) -> Self:
        if tuple(program.spec.mechanism for program in self.programs) != ("C", "W", "D"):
            raise ValueError("workflow requires exactly ordered C/W/D sessions")
        if len({program.spec.session_id for program in self.programs}) != 3:
            raise ValueError("workflow session IDs must be distinct")
        if any(program.spec.data_digest != self.data_digest for program in self.programs):
            raise ValueError("workflow sessions must share the pinned three-role data")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class SessionWorkflowLock(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    data_sha256: Digest
    data_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)
    workflow_sha256: Digest
    workflow_size_bytes: int = Field(gt=0, le=MAX_SESSION_DATA_BYTES)


def load_session_workflow(directory: Path) -> tuple[SessionWorkflow, SessionDataBundle]:
    lock = SessionWorkflowLock.model_validate_json(json.dumps(load_small_json(directory / "fixtures.lock.json")))
    data = load_session_data(directory / "data.json", expected_sha256=lock.data_sha256,
                             expected_size_bytes=lock.data_size_bytes)
    workflow = SessionWorkflow.model_validate_json(_verified_bytes(
        directory / "workflow.json", lock.workflow_sha256, lock.workflow_size_bytes,
    ))
    if data.digest != workflow.data_digest:
        raise ValueError("workflow belongs to different pinned data")
    return workflow, data


def _validate_inputs(workflow: SessionWorkflow, data: SessionDataBundle, source: ComparisonPlan,
                     evidence: FixtureEvidence, scorer: SyntheticComparisonScorer) -> None:
    if (data.digest != workflow.data_digest or source.digest != workflow.source_comparison_plan_digest
            or evidence.digest != workflow.source_comparison_evidence_digest):
        raise ValueError("workflow data or source comparison identity changed")
    if data.practice != source.development.cases or data.final_audit != source.cases:
        raise ValueError("fixture practice/audit must exactly match the pinned source study")
    if source.protocol.scorer_digest != scorer.lock_digest:
        raise ValueError("source comparison scorer pin changed")
    _validate_evidence(source, evidence)
    expected_base = {name: getattr(source.artifacts, name).to_dict()
                     for name in ("base_checkpoint", "base_context", "base_decoding")}
    for program in workflow.programs:
        if program.spec.baseline.to_dict() != expected_base or program.spec.scorer_digest != scorer.lock_digest:
            raise ValueError("session baseline or scorer differs from source comparison")
        if ScriptedSessionAdapter(program.script).identity_digest != program.spec.adapter_digest:
            raise ValueError("session adapter source/script pin changed")
        reserved = SessionUsage()
        candidate = initial_candidate(program.spec)
        for operation, entry in zip(program.operations, program.script.entries, strict=True):
            reserved = reserved.add(operation.reservation)
            if operation.candidate_digest != candidate.digest:
                raise ValueError("program operation references a stale candidate")
            if isinstance(operation, (InspectOperation, ProbeOperation)) and operation.problem_ids:
                data.practice_projection(operation.problem_ids)
            if isinstance(operation, EditOperation):
                edited = apply_edit(program.spec, candidate, operation)
                if entry.outcome == "return":
                    candidate = edited
        units = sum(getattr(reserved, name) for name in (
            "editor_input_bytes", "editor_output_bytes", "probe_input_bytes", "probe_output_bytes"))
        if (not reserved.within_limits(program.spec.limits)
                or reserved.operations > workflow.budgets.max_search_calls_per_mechanism
                or units > workflow.budgets.max_search_units_per_mechanism):
            raise ValueError("program reservations exceed session or comparison search budgets")
    for dataset in ("math500", "gsm8k", "ifeval"):
        scorer.validate_cohort(dataset, tuple(case.problem for case in data.practice if case.dataset == dataset))


def validate_session_workflow(directory: Path, comparison_directory: Path) -> dict[str, Any]:
    workflow, data = load_session_workflow(directory)
    source, evidence = load_comparison_fixture(comparison_directory)
    _validate_inputs(workflow, data, source, evidence, SyntheticComparisonScorer())
    return {"workflow_digest": workflow.digest, "splits": data.split_commitments(),
            "mechanisms": [program.spec.mechanism for program in workflow.programs],
            "execution_kind": "fixture", "model_execution_performed": False, "gpu_execution_deferred": True}


def _publish_or_verify(evaluation: ComparisonEvaluation, directory: Path) -> dict[str, str]:
    if not directory.exists():
        return write_comparison_report(evaluation, directory)
    if {path.name for path in directory.iterdir()} != {"report.json", "report.md", "complete.json"}:
        raise ValueError("incomplete comparison report; preserve it for reconciliation")
    payload = (json.dumps(evaluation.to_dict(), sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    markdown = render_comparison_report(evaluation).encode()
    expected = {"schema_version": 1, "plan_digest": evaluation.payload["plan_digest"],
                "report_digest": evaluation.digest, "evidence_kind": "offline_comparison_fixture",
                "json_sha256": hashlib.sha256(payload).hexdigest(),
                "markdown_sha256": hashlib.sha256(markdown).hexdigest()}
    if (canonical_digest(load_small_json(directory / "complete.json")) != canonical_digest(expected)
            or file_identity(directory / "report.json", max_bytes=len(payload))["sha256"] != expected["json_sha256"]
            or file_identity(directory / "report.md", max_bytes=len(markdown))["sha256"] != expected["markdown_sha256"]):
        raise ValueError("comparison report differs from independently regraded fixture evidence")
    return {"report_digest": evaluation.digest, "json": str(directory / "report.json"),
            "markdown": str(directory / "report.md")}


def _freeze(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        if canonical_digest(load_small_json(path, max_bytes=MAX_SESSION_DATA_BYTES)) != canonical_digest(payload):
            raise ValueError(f"immutable workflow artifact changed: {path.name}")
    else:
        _immutable_json(path, payload)


def run_session_workflow(directory: Path, *, comparison_directory: Path, output_dir: Path) -> dict[str, Any]:
    """Replay finite practice sessions, seal exports, then grade the fixed audit."""
    workflow, data = load_session_workflow(directory)
    source, evidence = load_comparison_fixture(comparison_directory)
    scorer = SyntheticComparisonScorer()
    _validate_inputs(workflow, data, source, evidence, scorer)
    root = output_dir.absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("workflow output must not traverse symlinks")
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".workflow.lock"
    if lock_path.exists() or lock_path.is_symlink():
        with regular_file(lock_path):
            pass
    with advisory_path_lock(root / ".workflow.lock"):
        if not (root / "workflow.json").exists() and {path.name for path in root.iterdir()} - {".workflow.lock"}:
            raise ValueError("refusing to adopt a nonempty directory without a workflow manifest")
        provenance = {"workflow_digest": workflow.digest, "data_digest": data.digest,
                      "source_comparison_plan_digest": source.digest, "source_evidence_digest": evidence.digest,
                      "workflow_source": file_identity(Path(__file__)), "execution_kind": "fixture",
                      "gpu_execution_deferred": True, "model_execution_performed": False}
        _freeze(root / "workflow.json", {"provenance": provenance, "workflow": workflow.to_dict()})
        exports: dict[str, ContextArtifact | CheckpointArtifact | DecodingArtifact] = {}
        sessions: dict[str, Any] = {}
        for program in workflow.programs:
            mechanism = program.spec.mechanism
            session = EditingSession(spec=program.spec, data=data, output_dir=root / "sessions" / mechanism,
                                     adapter=ScriptedSessionAdapter(program.script))
            for operation, entry in zip(program.operations, program.script.entries, strict=True):
                receipt = session.run(operation)
                expected_status = "failed" if entry.outcome == "failure" else "completed"
                if receipt["status"] != expected_status:
                    raise ValueError("session outcome differs from the frozen fixture program")
            exports[mechanism] = session.export_candidate()
            snapshot = session.snapshot()
            if snapshot["status"] != "sealed" or snapshot["operation_digests"] != [
                operation.digest for operation in program.operations
            ]:
                raise ValueError("session journal differs from the frozen workflow")
            sessions[mechanism] = {"candidate_digest": snapshot["candidate_digest"],
                                   "history_digest": snapshot["history_digest"],
                                   "reserved_usage": snapshot["reserved_usage"],
                                   "artifact_digest": exports[mechanism].digest}
        context, weights, decoding = exports["C"], exports["W"], exports["D"]
        if not isinstance(context, ContextArtifact) or not isinstance(weights, CheckpointArtifact) or not isinstance(
            decoding, DecodingArtifact,
        ):
            raise ValueError("session export has the wrong mechanism")
        artifacts = ComparisonArtifacts(base_checkpoint=source.artifacts.base_checkpoint, edited_checkpoint=weights,
                                        base_context=source.artifacts.base_context, learned_context=context,
                                        base_decoding=source.artifacts.base_decoding, selected_decoding=decoding)
        plan = build_comparison_plan(study_id=workflow.workflow_id, cases=data.final_audit, artifacts=artifacts,
                                     protocol=source.protocol, budgets=workflow.budgets,
                                     development=DevelopmentProvenance(cases=data.practice))
        # Never assign prerecorded responses to a different candidate/prompt/protocol.
        if tuple(cell.digest for cell in plan.cells) != tuple(cell.digest for cell in source.cells):
            raise ValueError("sealed exports change audit cell identities; source responses cannot be reused")
        _freeze(root / "comparison-plan.json", plan.to_dict())
        evaluation = evaluate_comparison_fixture(plan, FixtureEvidence(plan_digest=plan.digest,
                                                                       observations=evidence.observations), scorer)
        report = _publish_or_verify(evaluation, root / "comparison")
        summary = {"schema_version": 1, "workflow_digest": workflow.digest, "execution_kind": "fixture",
                   "model_execution_performed": False, "gpu_execution_deferred": True,
                   "confirmation_used": False, "promotion_eligible": False,
                   "sessions": sessions, "comparison_plan_digest": plan.digest, "report_digest": evaluation.digest}
        _freeze(root / "complete.json", summary)
        return {**summary, "report": report}
