"""Finite, typed editing-session contracts for trusted offline fixture adapters.

Candidate states reuse the M5 context/descriptor/decoding surfaces. They are not
real checkpoints or sandboxed editor programs; all counters are fixture bytes
and explicitly simulated time, never measured model tokens or GPU usage.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.comparison_models import CheckpointArtifact, ContextArtifact, DecodingArtifact
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

Mechanism = Literal["C", "W", "D"]
DatasetName = Literal["math500", "gsm8k", "ifeval"]
ProblemId = Annotated[str, Field(min_length=1, max_length=512)]


class _DigestModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class SessionLimits(_DigestModel):
    max_operations: int = Field(gt=0, le=10000)
    max_editor_input_bytes: int = Field(gt=0, le=1000000000)
    max_editor_output_bytes: int = Field(gt=0, le=1000000000)
    max_probe_input_bytes: int = Field(gt=0, le=1000000000)
    max_probe_output_bytes: int = Field(gt=0, le=1000000000)
    max_simulated_seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)


class SessionUsage(_DigestModel):
    operations: int = Field(default=0, ge=0, le=10000)
    editor_input_bytes: int = Field(default=0, ge=0, le=1000000000)
    editor_output_bytes: int = Field(default=0, ge=0, le=1000000000)
    probe_input_bytes: int = Field(default=0, ge=0, le=1000000000)
    probe_output_bytes: int = Field(default=0, ge=0, le=1000000000)
    simulated_seconds: float = Field(default=0.0, ge=0, le=86400, allow_inf_nan=False)

    def add(self, other: SessionUsage) -> SessionUsage:
        return SessionUsage(**{name: getattr(self, name) + getattr(other, name) for name in type(self).model_fields})

    def within(self, other: SessionUsage) -> bool:
        return all(getattr(self, name) <= getattr(other, name) for name in type(self).model_fields)

    def within_limits(self, limits: SessionLimits) -> bool:
        return all(getattr(self, name) <= getattr(limits, f"max_{name}") for name in type(self).model_fields)


class SessionDecoding(_DigestModel):
    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    top_p: float = Field(gt=0, le=1, allow_inf_nan=False)
    top_k: int = Field(ge=1, le=1000)
    presence_penalty: float = Field(ge=-2, le=2, allow_inf_nan=False)

    def settings(self) -> dict[str, Any]:
        return self.to_dict()


class CandidateState(_DigestModel):
    representation: Literal["synthetic_descriptor_state"] = "synthetic_descriptor_state"
    weight_descriptor: str = Field(min_length=1, max_length=4096)
    context_text: str = Field(max_length=32768)
    decoding: SessionDecoding


class SessionBaseline(_DigestModel):
    base_checkpoint: CheckpointArtifact
    base_context: ContextArtifact
    base_decoding: DecodingArtifact

    @model_validator(mode="after")
    def baseline_sources(self) -> Self:
        if any(artifact.source.kind != "baseline_fixture" for artifact in (
            self.base_checkpoint, self.base_context, self.base_decoding,
        )):
            raise ValueError("session baseline must contain only baseline fixture artifacts")
        return self

    @property
    def state(self) -> CandidateState:
        return CandidateState(weight_descriptor=self.base_checkpoint.content, context_text=self.base_context.text,
                              decoding=SessionDecoding.model_validate(self.base_decoding.settings()))


class SessionSpec(_DigestModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    gpu_execution_deferred: Literal[True] = True
    model_execution_performed: Literal[False] = False
    session_id: Identifier = Field(max_length=128)
    mechanism: Mechanism
    baseline: SessionBaseline
    data_digest: Digest
    scorer_digest: Digest
    adapter_digest: Digest
    limits: SessionLimits


class CandidateRevision(_DigestModel):
    session_id: Identifier = Field(max_length=128)
    revision: int = Field(ge=0, le=10000)
    parent_digest: Digest | None = None
    operation_digest: Digest | None = None
    state: CandidateState

    @model_validator(mode="after")
    def lineage_shape(self) -> Self:
        if self.revision == 0:
            if self.parent_digest is not None or self.operation_digest is not None:
                raise ValueError("initial candidate cannot have a parent or editing operation")
        elif self.parent_digest is None or self.operation_digest is None:
            raise ValueError("edited candidates require both parent and operation identities")
        return self


class _Operation(_DigestModel):
    kind: str
    operation_id: Identifier = Field(max_length=128)
    candidate_digest: Digest
    reservation: SessionUsage

    @model_validator(mode="after")
    def finite_reservation(self) -> Self:
        reserved = self.reservation
        if (reserved.operations != 1 or reserved.editor_input_bytes < 1 or reserved.editor_output_bytes < 1
                or reserved.simulated_seconds <= 0):
            raise ValueError("each operation must reserve one call and positive editor bytes/simulated time")
        if self.kind == "probe":
            if reserved.probe_input_bytes < 1 or reserved.probe_output_bytes < 1:
                raise ValueError("probe operations require positive input/output byte reservations")
        elif reserved.probe_input_bytes or reserved.probe_output_bytes:
            raise ValueError("only probe operations may reserve probe bytes")
        return self


class InspectOperation(_Operation):
    kind: Literal["inspect"] = "inspect"
    target: Literal["candidate", "practice"] = "candidate"
    problem_ids: tuple[ProblemId, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def inspect_scope(self) -> Self:
        if len(set(self.problem_ids)) != len(self.problem_ids):
            raise ValueError("duplicate inspection problem identity")
        if (self.target == "practice") != bool(self.problem_ids):
            raise ValueError("practice inspection requires IDs; candidate inspection cannot select problems")
        return self


class EditOperation(_Operation):
    kind: Literal["edit"] = "edit"
    context_text: str | None = Field(default=None, max_length=32768)
    weight_descriptor: str | None = Field(default=None, min_length=1, max_length=4096)
    decoding: SessionDecoding | None = None

    @model_validator(mode="after")
    def one_surface(self) -> Self:
        if sum(value is not None for value in (self.context_text, self.weight_descriptor, self.decoding)) != 1:
            raise ValueError("edit must change exactly one typed surface")
        return self


class ProbeOperation(_Operation):
    kind: Literal["probe"] = "probe"
    problem_ids: tuple[ProblemId, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def distinct_problems(self) -> Self:
        if len(set(self.problem_ids)) != len(self.problem_ids):
            raise ValueError("duplicate probe problem identity")
        return self


SessionOperation = Annotated[InspectOperation | EditOperation | ProbeOperation, Field(discriminator="kind")]


class PracticeProblem(_DigestModel):
    dataset: DatasetName
    problem_id: ProblemId
    text: str = Field(min_length=1, max_length=131072)


class PracticeFeedback(_DigestModel):
    problem_id: ProblemId
    score: int = Field(ge=0, le=1)
    response_sha256: Digest
    candidate_digest: Digest


class ProbeOutput(_DigestModel):
    problem_id: ProblemId
    text: str = Field(max_length=131072)
    finish_reason: Literal["stop", "length"] = "stop"


class SessionAdapterRequest(_DigestModel):
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    spec_digest: Digest
    operation: SessionOperation
    candidate: CandidateRevision
    practice: tuple[PracticeProblem, ...] = Field(default=(), max_length=1000)
    feedback: tuple[PracticeFeedback, ...] = Field(default=(), max_length=10000)

    @model_validator(mode="after")
    def projected_scope(self) -> Self:
        if self.operation.candidate_digest != self.candidate.digest:
            raise ValueError("adapter request references a stale candidate")
        expected = self.operation.problem_ids if isinstance(self.operation, (InspectOperation, ProbeOperation)) else ()
        if tuple(problem.problem_id for problem in self.practice) != expected:
            raise ValueError("adapter practice projection differs from selected operation IDs")
        return self


class AdapterResult(_DigestModel):
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    operation_digest: Digest
    message: str = Field(default="", max_length=32768)
    probe_outputs: tuple[ProbeOutput, ...] = Field(default=(), max_length=1000)
    simulated_seconds: float = Field(default=0.0, ge=0, le=86400, allow_inf_nan=False)

    @model_validator(mode="after")
    def distinct_outputs(self) -> Self:
        if len({item.problem_id for item in self.probe_outputs}) != len(self.probe_outputs):
            raise ValueError("duplicate probe output identity")
        return self


def initial_candidate(spec: SessionSpec) -> CandidateRevision:
    return CandidateRevision(session_id=spec.session_id, revision=0, state=spec.baseline.state)


def validate_candidate(spec: SessionSpec, candidate: CandidateRevision) -> CandidateRevision:
    """Validate allowed state surfaces; the journal separately proves lineage."""
    if candidate.session_id != spec.session_id:
        raise ValueError("candidate belongs to another session")
    base, state = spec.baseline.state, candidate.state
    if candidate.revision == 0 and candidate != initial_candidate(spec):
        raise ValueError("initial candidate differs from the pinned baseline")
    immutable = {"C": ("weight_descriptor", "decoding"), "W": ("context_text", "decoding"),
                 "D": ("weight_descriptor", "context_text")}[spec.mechanism]
    if any(getattr(state, name) != getattr(base, name) for name in immutable):
        raise ValueError("candidate changed a surface forbidden by this session mechanism")
    return candidate


def apply_edit(spec: SessionSpec, parent: CandidateRevision, operation: EditOperation) -> CandidateRevision:
    """Construct a new immutable revision; no script or model is executed."""
    spec = SessionSpec.model_validate_json(json.dumps(spec.to_dict()))
    parent = CandidateRevision.model_validate_json(json.dumps(parent.to_dict()))
    operation = EditOperation.model_validate_json(json.dumps(operation.to_dict()))
    validate_candidate(spec, parent)
    if operation.candidate_digest != parent.digest:
        raise ValueError("edit references a stale candidate")
    field = {"C": "context_text", "W": "weight_descriptor", "D": "decoding"}[spec.mechanism]
    replacement = getattr(operation, field)
    if replacement is None:
        raise ValueError("edit surface is forbidden by the session mechanism")
    if replacement == getattr(parent.state, field):
        raise ValueError("edit is a no-op")
    state = parent.state.to_dict()
    state[field] = replacement.to_dict() if isinstance(replacement, SessionDecoding) else replacement
    result = CandidateRevision(session_id=spec.session_id, revision=parent.revision + 1,
                               parent_digest=parent.digest, operation_digest=operation.digest,
                               state=CandidateState.model_validate_json(json.dumps(state)))
    return validate_candidate(spec, result)
