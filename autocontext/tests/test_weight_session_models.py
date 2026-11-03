from __future__ import annotations

import json
from typing import Any

import pytest

from autocontext.weight_evolution.comparison_models import (
    ArtifactSource,
    CheckpointArtifact,
    ContextArtifact,
    DecodingArtifact,
)
from autocontext.weight_evolution.session_models import (
    AdapterResult,
    CandidateRevision,
    EditOperation,
    InspectOperation,
    PracticeProblem,
    ProbeOperation,
    SessionAdapterRequest,
    SessionBaseline,
    SessionDecoding,
    SessionLimits,
    SessionSpec,
    SessionUsage,
    apply_edit,
    initial_candidate,
    validate_candidate,
)


def session_spec(mechanism: str = "C") -> SessionSpec:
    baseline = ArtifactSource(kind="baseline_fixture")
    return SessionSpec.model_validate_json(json.dumps({
        "session_id": "unit-session", "mechanism": mechanism,
        "baseline": SessionBaseline(
            base_checkpoint=CheckpointArtifact(artifact_id="base-weights", content="descriptor base", source=baseline),
            base_context=ContextArtifact(artifact_id="base-context", text="baseline context", source=baseline),
            base_decoding=DecodingArtifact(artifact_id="base-decoding", temperature=0.6, top_p=0.95, top_k=20,
                                           presence_penalty=1.5, source=baseline),
        ).to_dict(),
        "data_digest": "a" * 64, "scorer_digest": "b" * 64, "adapter_digest": "c" * 64,
        "limits": {"max_operations": 10, "max_editor_input_bytes": 100000, "max_editor_output_bytes": 100000,
                   "max_probe_input_bytes": 100000, "max_probe_output_bytes": 100000, "max_simulated_seconds": 10.0},
    }))


def reservation(*, probe: bool = False) -> SessionUsage:
    return SessionUsage(operations=1, editor_input_bytes=10000, editor_output_bytes=1000,
                        probe_input_bytes=1000 if probe else 0, probe_output_bytes=1000 if probe else 0,
                        simulated_seconds=1.0)


@pytest.mark.parametrize("field", list(SessionLimits.model_fields))
@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan")])
def test_every_budget_requires_a_finite_positive_value(field: str, value: Any) -> None:
    raw = session_spec().limits.to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        SessionLimits.model_validate(raw)


def test_usage_keeps_editor_probe_and_simulated_time_accounts_separate() -> None:
    left = reservation()
    combined = left.add(reservation(probe=True))
    assert combined.operations == 2
    assert combined.editor_input_bytes == 20000
    assert combined.probe_input_bytes == 1000 and combined.probe_output_bytes == 1000
    assert combined.simulated_seconds == 2.0
    assert left.within(combined) and not combined.within(left)
    assert combined.within_limits(session_spec().limits)
    reduced = session_spec().limits.model_copy(update={"max_probe_output_bytes": 999})
    assert not combined.within_limits(reduced)
    with pytest.raises(ValueError):
        left.operations = 2


@pytest.mark.parametrize("mechanism,field,value", [
    ("C", "context_text", "Learned context"),
    ("W", "weight_descriptor", "Changed descriptor"),
    ("D", "decoding", SessionDecoding(temperature=0.2, top_p=0.9, top_k=10, presence_penalty=0.0)),
])
def test_typed_edit_creates_candidate_with_exact_parent_and_operation(mechanism: str, field: str, value: Any) -> None:
    spec = session_spec(mechanism)
    before = initial_candidate(spec)
    operation = EditOperation(operation_id="edit-1", candidate_digest=before.digest, reservation=reservation(),
                              **{field: value})
    after = apply_edit(spec, before, operation)
    assert after.revision == 1 and after.parent_digest == before.digest and after.operation_digest == operation.digest
    assert after.digest != before.digest and after.state.digest != before.state.digest
    assert getattr(after.state, field) == value
    for unchanged in {"weight_descriptor", "context_text", "decoding"} - {field}:
        assert getattr(after.state, unchanged) == getattr(before.state, unchanged)
    assert initial_candidate(spec) == before
    assert validate_candidate(spec, after) == after
    with pytest.raises(ValueError, match="stale"):
        apply_edit(spec, after, operation)
    same_content = EditOperation(operation_id="edit-2", candidate_digest=after.digest, reservation=reservation(),
                                **{field: value})
    with pytest.raises(ValueError, match="no-op"):
        apply_edit(spec, after, same_content)


@pytest.mark.parametrize("mechanism,field", [
    ("C", "weight_descriptor"), ("C", "decoding"), ("W", "context_text"), ("W", "decoding"),
    ("D", "context_text"), ("D", "weight_descriptor"),
])
def test_cross_surface_edits_are_rejected(mechanism: str, field: str) -> None:
    spec = session_spec(mechanism)
    candidate = initial_candidate(spec)
    value = SessionDecoding(temperature=0.2, top_p=0.9, top_k=10, presence_penalty=0.0) if field == "decoding" else "other"
    operation = EditOperation(operation_id="forbidden", candidate_digest=candidate.digest,
                              reservation=reservation(), **{field: value})
    with pytest.raises(ValueError, match="forbidden"):
        apply_edit(spec, candidate, operation)


def test_candidate_identity_is_independent_of_fixture_script_pin() -> None:
    spec = session_spec()
    another = spec.model_copy(update={"adapter_digest": "f" * 64})
    assert another.digest != spec.digest
    before = initial_candidate(spec)
    assert initial_candidate(another).digest == before.digest
    operation = EditOperation(operation_id="edit", candidate_digest=before.digest, reservation=reservation(),
                              context_text="changed")
    assert apply_edit(spec, before, operation) == apply_edit(another, before, operation)


def test_lineage_and_baseline_mutation_cannot_be_smuggled_into_typed_edit() -> None:
    spec = session_spec()
    original = initial_candidate(spec)
    for updates in ({"parent_digest": "f" * 64}, {"revision": 1}):
        with pytest.raises(ValueError, match="parent|identities"):
            CandidateRevision.model_validate_json(json.dumps({**original.to_dict(), **updates}))
    corrupted = original.model_copy(update={"state": original.state.model_copy(update={"weight_descriptor": "other"})})
    operation = EditOperation(operation_id="edit", candidate_digest=corrupted.digest, reservation=reservation(),
                              context_text="new context")
    with pytest.raises(ValueError, match="baseline"):
        apply_edit(spec, corrupted, operation)
    changed_session = original.model_copy(update={"session_id": "other-session"})
    with pytest.raises(ValueError, match="another session"):
        validate_candidate(spec, changed_session)
    invalid = operation.model_copy(update={"context_text": None})
    with pytest.raises(ValueError, match="exactly one"):
        apply_edit(spec, original, invalid)


def test_baseline_cannot_be_declared_as_previously_developed_candidate() -> None:
    raw = session_spec().to_dict()
    raw["baseline"]["base_context"]["source"] = {
        "kind": "development_fixture", "source_problem_ids": ["practice/add"],
        "declared_search_calls": 1, "declared_search_units": 1,
    }
    with pytest.raises(ValueError, match="baseline fixture"):
        SessionSpec.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("update", [
    {"context_text": None}, {"weight_descriptor": "also changing weights"}, {"untyped_code": "print('hello')"},
])
def test_edit_requires_exactly_one_data_only_surface(update: dict[str, Any]) -> None:
    candidate = initial_candidate(session_spec())
    raw = {"operation_id": "edit", "candidate_digest": candidate.digest, "reservation": reservation().to_dict(),
           "context_text": "changed", **update}
    with pytest.raises(ValueError):
        EditOperation.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("update", [
    {"operations": 0}, {"operations": 2}, {"editor_input_bytes": 0}, {"editor_output_bytes": 0},
    {"simulated_seconds": 0.0}, {"probe_input_bytes": 1},
])
def test_nonprobe_reservations_must_bound_one_complete_editor_call(update: dict[str, Any]) -> None:
    raw = {"operation_id": "inspect", "candidate_digest": "a" * 64,
           "reservation": {**reservation().to_dict(), **update}}
    with pytest.raises(ValueError):
        InspectOperation.model_validate_json(json.dumps(raw))


def test_practice_operations_require_exact_unique_requested_projection() -> None:
    spec = session_spec()
    candidate = initial_candidate(spec)
    op = ProbeOperation(operation_id="probe", candidate_digest=candidate.digest, reservation=reservation(probe=True),
                        problem_ids=("practice/one", "practice/two"))
    practice = tuple(PracticeProblem(dataset="math500", problem_id=key, text=key) for key in op.problem_ids)
    request = SessionAdapterRequest(spec_digest=spec.digest, operation=op, candidate=candidate, practice=practice)
    parsed = SessionAdapterRequest.model_validate_json(json.dumps(request.to_dict()))
    assert isinstance(parsed.operation, ProbeOperation)
    assert parsed == request
    with pytest.raises(ValueError, match="projection"):
        SessionAdapterRequest(spec_digest=spec.digest, operation=op, candidate=candidate, practice=tuple(reversed(practice)))
    with pytest.raises(ValueError, match="stale"):
        SessionAdapterRequest(spec_digest=spec.digest, operation=op.model_copy(update={"candidate_digest": "d" * 64}),
                              candidate=candidate, practice=practice)
    with pytest.raises(ValueError, match="duplicate"):
        ProbeOperation(operation_id="probe", candidate_digest=candidate.digest, reservation=reservation(probe=True),
                       problem_ids=("practice/one", "practice/one"))
    with pytest.raises(ValueError, match="probe"):
        ProbeOperation(operation_id="probe", candidate_digest=candidate.digest, reservation=reservation(),
                       problem_ids=("practice/one",))
    with pytest.raises(ValueError, match="inspection"):
        InspectOperation(operation_id="inspect", candidate_digest=candidate.digest, reservation=reservation(),
                         problem_ids=("practice/one",))


@pytest.mark.parametrize("update", [
    {"execution_kind": "vllm"}, {"gpu_execution_deferred": False}, {"model_execution_performed": True},
])
def test_fixture_contract_cannot_claim_live_model_execution(update: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        SessionSpec.model_validate_json(json.dumps({**session_spec().to_dict(), **update}))
    with pytest.raises(ValueError):
        AdapterResult.model_validate_json(json.dumps({"operation_digest": "a" * 64, **update}))
