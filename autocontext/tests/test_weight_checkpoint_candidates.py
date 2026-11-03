from __future__ import annotations

import json
import shutil
import socket
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from autocontext.weight_evolution.adaptive_provider import (
    AdaptiveFixtureInterrupted,
    EditProposal,
    FeedbackRoute,
    GenerationView,
    OfflineFeedbackProvider,
    ProbeFixture,
    ProviderSessionAdapter,
)
from autocontext.weight_evolution.artifacts import TensorFile, TensorInfo, canonical_digest, file_identity, write_tensor_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry, TensorCandidateSpec, TensorSessionAdapter
from autocontext.weight_evolution.comparison_models import CheckpointArtifact
from autocontext.weight_evolution.session import EditingSession, SessionBlockedError, _read_record
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import (
    CandidateRevision,
    EditOperation,
    InspectOperation,
    Mechanism,
    ProbeOperation,
    ProbeOutput,
    SessionBaseline,
    SessionLimits,
    SessionSpec,
    SessionUsage,
    initial_candidate,
)
from autocontext.weight_evolution.session_workflow import load_session_workflow
from autocontext.weight_evolution.tensor_edits import TensorEdit, TensorEditProgram, TensorReplacement, build_checkpoint_contract

FIXTURE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "qwen_sessions" / "fixtures"
RESERVE = SessionUsage(operations=1, editor_input_bytes=32768, editor_output_bytes=16384, simulated_seconds=1.0)
PROBE_RESERVE = SessionUsage(**{**RESERVE.to_dict(), "probe_input_bytes": 4096, "probe_output_bytes": 4096})


@dataclass
class Setup:
    registry: TensorCandidateRegistry
    base: TensorCandidateSpec
    edited: TensorCandidateSpec
    adapter: TensorSessionAdapter
    spec: SessionSpec
    data: SessionDataBundle


def setup(tmp_path: Path, mechanism: Mechanism = "W") -> Setup:
    tmp_path.mkdir(parents=True, exist_ok=True)
    payloads = {"bf16": bytes.fromhex("803f0040"), "f16": struct.pack("<2e", 1.0, 2.0),
                "f32": struct.pack("<2f", 1.0, 2.0), "f64": struct.pack("<2d", 1.0, 2.0)}
    dtypes = {"bf16": "BF16", "f16": "F16", "f32": "F32", "f64": "F64"}
    tensors = tuple(TensorInfo(name, dtypes[name], (2,), 0, len(payloads[name])) for name in sorted(payloads))
    input_path = tmp_path / "base.safetensors"
    write_tensor_file(input_path, tensors, payloads.__getitem__)
    contract = build_checkpoint_contract(input_path, editable_names=("bf16", "f32"), max_changed_entries=2)
    edits = []
    for name, value_hex in (("bf16", "4040"), ("f32", struct.pack("<f", 3.0).hex())):
        pin = next(pin for pin in contract.tensors if pin.name == name)
        edits.append(TensorEdit(name=name, expected_sha256=pin.sha256, dtype=pin.dtype, shape=pin.shape,
                                 replacements=(TensorReplacement(index=0, value_hex=value_hex),)))
    program = TensorEditProgram(input_digest=contract.digest, edits=tuple(edits))
    changed = dict(payloads)
    changed["bf16"] = bytes.fromhex("4040") + changed["bf16"][2:]
    changed["f32"] = struct.pack("<f", 3.0) + changed["f32"][4:]
    expected_path = tmp_path / "expected.safetensors"
    write_tensor_file(expected_path, tensors, changed.__getitem__)
    expected = file_identity(expected_path)
    base = TensorCandidateSpec(candidate_id="base", contract=contract, output_sha256=contract.sha256,
                                 output_size_bytes=contract.size_bytes)
    edited = TensorCandidateSpec(candidate_id="edited", contract=contract, program=program,
                                   output_sha256=str(expected["sha256"]), output_size_bytes=int(expected["size_bytes"]))
    registry = TensorCandidateRegistry((base, edited), input_path=input_path, output_dir=tmp_path / "candidates")
    workflow, data = load_session_workflow(FIXTURE)
    previous = workflow.programs[0].spec
    baseline = SessionBaseline(base_checkpoint=CheckpointArtifact(artifact_id="tensor-base", content=base.handle,
                                                                   source=previous.baseline.base_checkpoint.source),
                                 base_context=previous.baseline.base_context, base_decoding=previous.baseline.base_decoding)
    ids = tuple(case.problem.problem_id for case in data.practice)
    practice = data.practice_projection(ids)
    target = baseline.state.to_dict()
    if mechanism == "W":
        target["weight_descriptor"] = edited.handle
        proposal = EditProposal(mechanism="W", weight_descriptor=edited.handle)
    elif mechanism == "C":
        target["context_text"] = "Check the result."
        proposal = EditProposal(mechanism="C", context_text="Check the result.")
    else:
        target["decoding"] = {**target["decoding"], "temperature": 0.2}
        proposal = EditProposal.model_validate_json(json.dumps({"mechanism": "D", "decoding": target["decoding"]}))
    state = type(baseline.state).model_validate_json(json.dumps(target))
    provider = OfflineFeedbackProvider((FeedbackRoute(
        view_digest=GenerationView(mechanism=mechanism, state=baseline.state, practice=practice).digest,
        response_text=json.dumps(proposal.to_dict()),
    ),))
    outputs = tuple(ProbeOutput(problem_id=key, text=text) for key, text in zip(
        ids, (r"\boxed{3}", r"\boxed{9}", "</think>green leaf"), strict=True))
    delegate = ProviderSessionAdapter(mechanism, provider, (ProbeFixture(state_digest=baseline.state.digest, outputs=outputs),
                                                          ProbeFixture(state_digest=state.digest, outputs=outputs)))
    adapter = TensorSessionAdapter(delegate, registry)
    spec = SessionSpec(session_id=f"checkpoint-{mechanism.lower()}", mechanism=mechanism, baseline=baseline,
                         data_digest=data.digest, scorer_digest=previous.scorer_digest, adapter_digest=adapter.identity_digest,
                         limits=SessionLimits(max_operations=10, max_editor_input_bytes=327680,
                                                max_editor_output_bytes=163840, max_probe_input_bytes=40960,
                                                max_probe_output_bytes=40960, max_simulated_seconds=10.0))
    return Setup(registry, base, edited, adapter, spec, data)


def edit(values: Setup) -> EditOperation:
    parent = initial_candidate(values.spec)
    payload: dict[str, Any]
    if values.spec.mechanism == "W":
        payload = {"weight_descriptor": values.edited.handle}
    elif values.spec.mechanism == "C":
        payload = {"context_text": "Check the result."}
    else:
        payload = {"decoding": values.spec.baseline.base_decoding.model_copy(update={"temperature": 0.2}).settings()}
    return EditOperation.model_validate_json(json.dumps({"operation_id": "edit", "candidate_digest": parent.digest,
                                                         "reservation": RESERVE.to_dict(), **payload}))


def probe(values: Setup, candidate: CandidateRevision, *, name: str = "probe") -> ProbeOperation:
    return ProbeOperation(operation_id=name, candidate_digest=candidate.digest, reservation=PROBE_RESERVE,
                            problem_ids=tuple(case.problem.problem_id for case in values.data.practice))


def session(values: Setup, path: Path) -> EditingSession:
    return EditingSession(spec=values.spec, data=values.data, output_dir=path, adapter=values.adapter)


def test_registry_constructor_is_read_only_and_identity_is_path_independent(tmp_path: Path) -> None:
    values = setup(tmp_path)
    assert not values.registry.output_dir.exists()
    other_input = tmp_path / "renamed.safetensors"
    shutil.copyfile(values.registry.input_path, other_input)
    other = TensorCandidateRegistry(values.registry.candidates, input_path=other_input, output_dir=tmp_path / "elsewhere")
    assert values.registry.identity_digest == other.identity_digest
    assert values.registry.verify(values.base.handle) == other.verify(values.base.handle)
    assert not other.output_dir.exists()


@pytest.mark.parametrize("change", ["plain", "whitespace", "unknown", "output", "extra", "duplicate"])
def test_handles_are_exact_typed_registry_members(tmp_path: Path, change: str) -> None:
    values = setup(tmp_path)
    handle = values.edited.handle
    payload = json.loads(handle)
    if change == "plain":
        handle = "a synthetic weight description"
    elif change == "whitespace":
        handle += " "
    elif change == "unknown":
        payload["candidate_digest"] = "f" * 64
        handle = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    elif change == "output":
        payload["output_sha256"] = "f" * 64
        handle = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    elif change == "extra":
        payload["path"] = "../../unsafe"
        handle = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    else:
        handle = '{"schema_version":1,' + handle[1:]
    with pytest.raises(ValueError):
        values.registry.parse_handle(handle)


def test_materialization_changes_only_permitted_tensor_entries_and_is_idempotent(tmp_path: Path) -> None:
    values = setup(tmp_path)
    verified = values.registry.materialize(values.edited.handle)
    assert verified.changed_entries == 2 and verified.reference.output_sha256 == values.edited.output_sha256
    assert verified.model_execution_performed is False and verified.gpu_execution_deferred is True
    original = TensorFile(values.registry.input_path)
    checkpoint = TensorFile(values.registry.checkpoint_path(values.edited.handle))
    assert checkpoint.read("f32") == struct.pack("<2f", 3.0, 2.0)
    assert checkpoint.read("bf16") == bytes.fromhex("40400040")
    assert checkpoint.read("f16") == original.read("f16")
    assert checkpoint.read("f64") == original.read("f64")
    assert values.registry.materialize(values.edited.handle) == verified
    assert len(values.registry.verify_existing()) == 2


@pytest.mark.parametrize("mechanism", ["C", "W", "D"])
def test_claimed_edits_materialize_before_probe_and_retained_bindings_verify(tmp_path: Path, mechanism: Mechanism) -> None:
    values = setup(tmp_path, mechanism)
    current = session(values, tmp_path / "session")
    receipt = current.run(edit(values))
    assert receipt["status"] == "completed"
    handle = values.edited.handle if mechanism == "W" else values.base.handle
    assert values.registry.verify(handle).reference.output_sha256 == values.registry.parse_handle(handle).output_sha256
    candidate = CandidateRevision.model_validate_json(json.dumps(receipt["candidate"]))
    assert current.run(probe(values, candidate))["status"] == "completed"
    snapshot = values.adapter.verify_session_materializations(current.output_dir, data=values.data)
    assert snapshot["reserved_usage"]["operations"] == 2
    result = _read_record(current.output_dir / "results" / "00000001.json")
    binding = json.loads(result["message"])
    assert binding["checkpoint"]["reference"]["output_sha256"] == values.registry.parse_handle(handle).output_sha256
    assert values.registry.output_dir.exists() is (mechanism == "W")


def test_completed_proposal_requires_membership_but_not_unapplied_materialization(tmp_path: Path) -> None:
    values = setup(tmp_path)
    current = session(values, tmp_path / "session")
    operation = InspectOperation(operation_id="propose", candidate_digest=initial_candidate(values.spec).digest,
                                   reservation=RESERVE, target="practice",
                                   problem_ids=tuple(case.problem.problem_id for case in values.data.practice))
    assert current.run(operation)["status"] == "completed"
    assert not values.registry.output_dir.exists()
    assert values.adapter.verify_session_materializations(current.output_dir, data=values.data)["status"] == "active"
    assert not values.registry.output_dir.exists()


@pytest.mark.parametrize("damage", ["bytes", "missing", "receipt"])
def test_completed_edit_missing_or_tampered_materialization_never_recreates(
    tmp_path: Path, damage: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path)
    current = session(values, tmp_path / "session")
    assert current.run(edit(values))["status"] == "completed"
    path = values.registry.checkpoint_path(values.edited.handle)
    if damage == "bytes":
        payload = path.read_bytes()
        path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    elif damage == "missing":
        shutil.rmtree(path.parent)
    else:
        (path.parent / "receipt.json").write_text("{}")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("verification reconstructed missing/changed checkpoint bytes")

    monkeypatch.setattr("autocontext.weight_evolution.checkpoint_candidates.apply_tensor_edit", forbidden)
    with pytest.raises((ValueError, FileNotFoundError)):
        values.adapter.verify_session_materializations(current.output_dir, data=values.data)


def test_exact_session_replay_and_export_keep_checkpoint_bytes_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    values = setup(tmp_path)
    current = session(values, tmp_path / "session")
    operation = edit(values)
    receipt = current.run(operation)
    candidate = CandidateRevision.model_validate_json(json.dumps(receipt["candidate"]))
    probe_operation = probe(values, candidate)
    current.run(probe_operation)
    exported = current.export_candidate()
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("completed session redispatched")

    monkeypatch.setattr(values.adapter, "execute", forbidden)
    values.adapter.verify_session_materializations(current.output_dir, data=values.data)
    assert current.run(operation) == receipt
    current.run(probe_operation)
    assert current.export_candidate() == exported
    assert before == {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_interrupted_edit_with_published_checkpoint_remains_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    values = setup(tmp_path)
    current = session(values, tmp_path / "session")
    materialize = values.registry.materialize

    def interrupted(handle: str) -> Any:
        materialize(handle)
        raise AdaptiveFixtureInterrupted("crash after checkpoint publication")

    monkeypatch.setattr(values.registry, "materialize", interrupted)
    with pytest.raises(AdaptiveFixtureInterrupted):
        current.run(edit(values))
    assert values.registry.checkpoint_path(values.edited.handle).is_file()
    snapshot = values.adapter.verify_session_materializations(current.output_dir, data=values.data)
    assert snapshot["status"] == "blocked" and snapshot["reserved_usage"]["operations"] == 1
    with pytest.raises(SessionBlockedError):
        current.run(edit(values))


@pytest.mark.parametrize("kind", ["edit", "probe"])
def test_binding_tampering_is_rejected_even_when_generic_session_hashes_are_rewritten(tmp_path: Path, kind: str) -> None:
    values = setup(tmp_path)
    current = session(values, tmp_path / "session")
    operation = edit(values) if kind == "edit" else probe(values, initial_candidate(values.spec))
    assert current.run(operation)["status"] == "completed"
    result_path = current.output_dir / "results" / "00000001.json"
    result = _read_record(result_path)
    binding = json.loads(result["message"])
    binding["checkpoint"]["verification_digest"] = "f" * 64
    result["message"] = json.dumps(binding, sort_keys=True, separators=(",", ":"))
    result_path.write_text(json.dumps({"payload": result, "digest": canonical_digest(result)}))
    receipt_path = current.output_dir / "completed" / "00000001.json"
    receipt = _read_record(receipt_path)
    receipt["result_digest"] = canonical_digest(result)
    receipt_path.write_text(json.dumps({"payload": receipt, "digest": canonical_digest(receipt)}))
    assert current.snapshot()["status"] == "active"
    with pytest.raises(ValueError, match="operation binding"):
        values.adapter.verify_session_materializations(current.output_dir, data=values.data)


@pytest.mark.parametrize("name", ["unknown", ".unknown.tensor-edit.lock", ".tensor-candidates.lock"])
def test_store_rejects_unknown_entries_and_symlink_locks(tmp_path: Path, name: str) -> None:
    values = setup(tmp_path)
    values.registry.output_dir.mkdir()
    path = values.registry.output_dir / name
    if name == ".tensor-candidates.lock":
        path.symlink_to(tmp_path / "missing")
    else:
        path.write_text("unexpected")
    with pytest.raises(ValueError):
        values.registry.verify_existing()


def test_expected_output_pin_is_enforced_after_materialization(tmp_path: Path) -> None:
    values = setup(tmp_path)
    changed = TensorCandidateSpec.model_validate_json(json.dumps({**values.edited.to_dict(), "output_sha256": "f" * 64}))
    registry = TensorCandidateRegistry((values.base, changed), input_path=values.registry.input_path,
                                         output_dir=tmp_path / "wrong-output")
    with pytest.raises(ValueError, match="pinned candidate output"):
        registry.materialize(changed.handle)


def test_no_network_or_subprocess_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    values = setup(tmp_path)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("CPU checkpoint fixture attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    current = session(values, tmp_path / "session")
    assert current.run(edit(values))["status"] == "completed"
    values.adapter.verify_session_materializations(current.output_dir, data=values.data)


@pytest.mark.parametrize("second_call", ["materialize", "verify_existing", "verify"])
def test_cooperating_registry_calls_wait_for_staged_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second_call: str,
) -> None:
    from autocontext.weight_evolution import tensor_edits

    values = setup(tmp_path)
    staged, release, entered = Event(), Event(), Event()
    original_write = tensor_edits.write_tensor_file

    def paused(*args: Any, **kwargs: Any) -> Any:
        staged.set()
        if not release.wait(5.0):
            raise AssertionError("test did not release the staged tensor writer")
        return original_write(*args, **kwargs)

    def second() -> Any:
        entered.set()
        if second_call == "verify_existing":
            return values.registry.verify_existing()
        return getattr(values.registry, second_call)(values.edited.handle)

    monkeypatch.setattr(tensor_edits, "write_tensor_file", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(values.registry.materialize, values.edited.handle)
        try:
            assert staged.wait(5.0)
            following = pool.submit(second)
            assert entered.wait(5.0)
            with pytest.raises(TimeoutError):
                following.result(timeout=0.05)
        finally:
            release.set()
        expected = first.result(timeout=5.0)
        observed = following.result(timeout=5.0)
    assert observed[-1] == expected if second_call == "verify_existing" else observed == expected
    assert values.registry.verify(values.edited.handle) == expected
