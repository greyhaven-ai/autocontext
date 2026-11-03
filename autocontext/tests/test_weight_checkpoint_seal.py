from __future__ import annotations

import hashlib
import json
import socket
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.adaptive import AdaptivePolicy, run_adaptive_search
from autocontext.weight_evolution.adaptive_confirmation import ConfirmationPolicy, build_confirmation_selection
from autocontext.weight_evolution.adaptive_provider import (
    EditProposal,
    FeedbackRoute,
    GenerationFeedback,
    GenerationView,
    OfflineFeedbackProvider,
    ProbeFixture,
    ProviderSessionAdapter,
)
from autocontext.weight_evolution.artifacts import TensorFile, TensorInfo, canonical_digest, write_tensor_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry, TensorCandidateSpec, TensorSessionAdapter
from autocontext.weight_evolution.checkpoint_seal import (
    TensorCandidateArtifact,
    confirm_checkpoint_selection,
    seal_checkpoint_search,
    verify_checkpoint_search_seal,
)
from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence, fixture_observation
from autocontext.weight_evolution.comparison_io import load_comparison_fixture
from autocontext.weight_evolution.comparison_models import (
    CheckpointArtifact,
    ComparisonArtifacts,
    ComparisonBudgets,
    ComparisonPlan,
    build_comparison_plan,
)
from autocontext.weight_evolution.session import _read_record
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import (
    CandidateState,
    Mechanism,
    ProbeOutput,
    SessionBaseline,
    SessionDecoding,
    SessionLimits,
    SessionSpec,
    SessionUsage,
)
from autocontext.weight_evolution.session_workflow import load_session_workflow
from autocontext.weight_evolution.tensor_edits import (
    TensorEdit,
    TensorEditProgram,
    TensorReplacement,
    apply_tensor_edit,
    build_checkpoint_contract,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution"


@dataclass
class Prepared:
    root: Path
    data: SessionDataBundle
    registry: TensorCandidateRegistry
    adapters: tuple[TensorSessionAdapter, ...]
    native_paths: tuple[Path, ...]
    selection_path: Path
    plan: ComparisonPlan

    def run(self, *, veto: bool = False, minimum_mean_gain: float = 0.1) -> dict[str, Any]:
        return confirm_checkpoint_selection(
            self.plan, evidence(self.plan, veto=veto), data=self.data, adapters=self.adapters,
            native_paths=self.native_paths, selection_path=self.selection_path,
            policy=ConfirmationPolicy(minimum_mean_gain=minimum_mean_gain), output_dir=self.root / "confirmation",
        )


def evidence(plan: ComparisonPlan, *, veto: bool = False) -> FixtureEvidence:
    literal = {"confirmation.sum": r"\boxed{14}", "confirmation.coins": r"\boxed{7}",
               "confirmation.words": "</think>coral reef"}
    rows = []
    for cell in plan.cells:
        text = literal[cell.problem_id]
        if cell.condition_id == "A" and cell.problem_id == "confirmation.sum":
            text = r"\boxed{13}"
        if veto and cell.condition_id == "W" and cell.problem_id == "confirmation.coins":
            text = r"\boxed{8}"
        rows.append(fixture_observation(cell, text))
    return FixtureEvidence(plan_digest=plan.digest, observations=tuple(rows))


@pytest.fixture
def prepared(tmp_path: Path) -> Prepared:
    source, _ = load_comparison_fixture(EXAMPLES / "qwen_comparison" / "fixtures")
    frozen, data = load_session_workflow(EXAMPLES / "qwen_sessions" / "fixtures")
    input_path = tmp_path / "base.safetensors"
    payloads = {"edit.weight": struct.pack("<ff", 1.0, 2.0), "frozen.weight": bytes.fromhex("803f")}
    write_tensor_file(input_path, (TensorInfo("edit.weight", "F32", (2,), 0, 8),
                                   TensorInfo("frozen.weight", "BF16", (1,), 8, 10)), payloads.__getitem__)
    contract = build_checkpoint_contract(input_path, editable_names=("edit.weight",), max_changed_entries=2)
    edit = TensorEdit(name="edit.weight", expected_sha256=contract.tensors[0].sha256, dtype="F32", shape=(2,),
                       replacements=(TensorReplacement(index=0, value_hex=struct.pack("<f", 3.0).hex()),))
    program = TensorEditProgram(input_digest=contract.digest, edits=(edit,))
    predicted = apply_tensor_edit(contract, program, input_path=input_path, output_dir=tmp_path / "predicted")
    base = TensorCandidateSpec(candidate_id="native-base", contract=contract,
                                output_sha256=contract.sha256, output_size_bytes=contract.size_bytes)
    target = TensorCandidateSpec(candidate_id="native-target", contract=contract, program=program,
                                  output_sha256=predicted.output_sha256, output_size_bytes=predicted.output_size_bytes)
    registry = TensorCandidateRegistry((base, target), input_path=input_path, output_dir=tmp_path / "materializations")
    baseline = SessionBaseline(
        base_checkpoint=CheckpointArtifact(artifact_id="native-projection-base", content=base.handle,
                                            source=source.artifacts.base_checkpoint.source),
        base_context=source.artifacts.base_context, base_decoding=source.artifacts.base_decoding,
    )
    policy = AdaptivePolicy(
        proposal_rounds=1,
        proposal_reservation=SessionUsage(operations=1, editor_input_bytes=32768, editor_output_bytes=8192,
                                           simulated_seconds=1.0),
        edit_reservation=SessionUsage(operations=1, editor_input_bytes=16384, editor_output_bytes=8192,
                                       simulated_seconds=1.0),
        probe_reservation=SessionUsage(operations=1, editor_input_bytes=16384, editor_output_bytes=8192,
                                        probe_input_bytes=8192, probe_output_bytes=8192, simulated_seconds=1.0),
    )
    limits = SessionLimits(**{f"max_{key}": value for key, value in policy.required_usage.to_dict().items()})
    ids = tuple(case.problem.problem_id for case in data.practice)
    wrong = tuple(ProbeOutput(problem_id=key, text=text) for key, text in zip(
        ids, (r"\boxed{4}", r"\boxed{8}", "</think>green leaf"), strict=True))
    right = tuple(ProbeOutput(problem_id=key, text=text) for key, text in zip(
        ids, (r"\boxed{3}", r"\boxed{9}", "</think>green leaf"), strict=True))
    feedback = tuple(GenerationFeedback(problem_id=row.problem_id, score=score,
                                          response_sha256=hashlib.sha256(row.text.encode()).hexdigest())
                     for row, score in zip(wrong, (0, 0, 1), strict=True))
    artifacts = source.artifacts.to_dict()
    artifacts["base_checkpoint"] = baseline.base_checkpoint.to_dict()
    adapters, native_paths, completion_paths = [], [], []
    mechanisms: tuple[Mechanism, ...] = ("C", "W", "D")
    for mechanism, artifact_name in zip(mechanisms, ("learned_context", "edited_checkpoint", "selected_decoding"), strict=True):
        proposal = EditProposal(
            mechanism=mechanism, context_text="Check each arithmetic result." if mechanism == "C" else None,
            weight_descriptor=target.handle if mechanism == "W" else None,
            decoding=SessionDecoding(temperature=0.2, top_p=0.95, top_k=20, presence_penalty=1.0)
            if mechanism == "D" else None,
        )
        values = baseline.state.to_dict()
        surface = {"C": "context_text", "W": "weight_descriptor", "D": "decoding"}[mechanism]
        replacement = getattr(proposal, surface)
        values[surface] = replacement.to_dict() if mechanism == "D" else replacement
        changed = CandidateState.model_validate_json(json.dumps(values))
        view = GenerationView(mechanism=mechanism, state=baseline.state,
                              practice=data.practice_projection(ids), feedback=feedback)
        provider = OfflineFeedbackProvider((FeedbackRoute(view_digest=view.digest,
                                                            response_text=json.dumps(proposal.to_dict())),))
        delegate = ProviderSessionAdapter(mechanism, provider, (
            ProbeFixture(state_digest=baseline.state.digest, outputs=wrong),
            ProbeFixture(state_digest=changed.digest, outputs=right),
        ))
        adapter = TensorSessionAdapter(delegate, registry)
        spec = SessionSpec(session_id=f"native-{mechanism.lower()}", mechanism=mechanism, baseline=baseline,
                            data_digest=data.digest, scorer_digest=frozen.programs[0].spec.scorer_digest,
                            adapter_digest=adapter.identity_digest, limits=limits)
        search_root = tmp_path / "search" / mechanism
        complete = run_adaptive_search(spec=spec, policy=policy, data=data, adapter=adapter, output_dir=search_root)
        path = tmp_path / f"native-{mechanism}.json"
        seal_checkpoint_search(search_root / "complete.json", adapter=adapter, data=data, output_path=path)
        artifacts[artifact_name] = complete["artifact"]
        adapters.append(adapter)
        native_paths.append(path)
        completion_paths.append(search_root / "complete.json")
    selection_path = tmp_path / "selection.json"
    selection = build_confirmation_selection(data, completion_paths=tuple(completion_paths), selection_path=selection_path)
    selection_path.write_text(json.dumps(selection.to_dict(), sort_keys=True))
    plan = build_comparison_plan(
        study_id="native-confirmation", cases=data.confirmation,
        artifacts=ComparisonArtifacts.model_validate_json(json.dumps(artifacts)), protocol=source.protocol,
        budgets=ComparisonBudgets.model_validate_json(json.dumps({**source.budgets.to_dict(),
                                                                  "max_search_calls_per_mechanism": 8,
                                                                  "max_search_units_per_mechanism": 1000000})),
        development=source.development,
    )
    return Prepared(tmp_path, data, registry, tuple(adapters), tuple(native_paths), selection_path, plan)


def rewrite(path: Path, transform: Any) -> None:
    payload = _read_record(path)
    transform(payload)
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}))


def test_native_seal_binds_actual_tensors_search_and_projection(prepared: Prepared) -> None:
    seals = [verify_checkpoint_search_seal(path, adapter=adapter, data=prepared.data)
             for path, adapter in zip(prepared.native_paths, prepared.adapters, strict=True)]
    assert [seal.tensor_edit_performed for seal in seals] == [False, True, False]
    weight = seals[1]
    assert weight.representation == "verified_safetensors_fixture"
    assert weight.checkpoint.reference.program_digest is not None and weight.checkpoint.changed_entries == 1
    assert weight.checkpoint.reference.output_sha256 != weight.baseline.reference.output_sha256
    assert weight.source.declared_search_calls == weight.reserved_usage.operations == 5
    assert weight.compatibility_projection_digest == prepared.plan.artifacts.edited_checkpoint.digest
    assert all(seal.model_execution_performed is seal.promotion_eligible is False for seal in seals)
    output = TensorFile(prepared.registry.checkpoint_path(prepared.plan.artifacts.edited_checkpoint.content))
    assert output.read("edit.weight") == struct.pack("<ff", 3.0, 2.0)
    assert output.read("frozen.weight") == bytes.fromhex("803f")


def test_seal_and_confirmation_resume_without_adapter_dispatch(prepared: Prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    first = prepared.run()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("sealed checkpoint replay dispatched")

    monkeypatch.setattr(TensorSessionAdapter, "execute", forbidden)
    before = {str(path.relative_to(prepared.root)): path.read_bytes() for path in prepared.root.rglob("*") if path.is_file()}
    assert prepared.run() == first
    assert before == {str(path.relative_to(prepared.root)): path.read_bytes()
                      for path in prepared.root.rglob("*") if path.is_file()}


def test_native_confirmation_keeps_fixture_claims_and_gate_semantics(prepared: Prepared) -> None:
    result = prepared.run()
    assert result["status"] == "confirmed"
    assert result["promotion_eligible"] is result["benchmark_result_claimed"] is result["model_execution_performed"] is False
    assert result["native_selection_digest"] and result["native_claim_digest"]
    native_claim = _read_record(prepared.root / "confirmation" / "native-claim.json")
    assert set(native_claim["native_selection"]["native_artifacts"]) == {"C", "W", "D"}


def test_native_veto_cannot_be_replaced_with_another_evidence_look(prepared: Prepared) -> None:
    assert prepared.run(veto=True)["status"] == "vetoed"
    with pytest.raises(ValueError, match="immutable journal"):
        prepared.run(veto=False)


@pytest.mark.parametrize("target", ["base", "selected"])
@pytest.mark.parametrize("mode", ["delete", "tamper"])
@pytest.mark.parametrize("after_confirmation", [False, True])
def test_missing_or_changed_checkpoint_blocks_confirmation(
    prepared: Prepared, target: str, mode: str, after_confirmation: bool,
) -> None:
    if after_confirmation:
        prepared.run()
    handle = prepared.registry.baseline.handle if target == "base" else prepared.plan.artifacts.edited_checkpoint.content
    path = prepared.registry.checkpoint_path(handle)
    if mode == "delete":
        path.unlink()
    else:
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises((ValueError, FileNotFoundError)):
        prepared.run()
    assert (prepared.root / "confirmation").exists() is after_confirmation
    if mode == "delete":
        assert not path.exists()


def test_missing_native_seal_is_not_recreated_by_confirmation(prepared: Prepared) -> None:
    prepared.native_paths[1].unlink()
    with pytest.raises(FileNotFoundError):
        prepared.run()
    assert not prepared.native_paths[1].exists()


@pytest.mark.parametrize("field", ["search_completion_digest", "compatibility_projection_digest", "implementation_digest"])
def test_rehashed_native_seal_tampering_is_rejected(prepared: Prepared, field: str) -> None:
    rewrite(prepared.native_paths[1], lambda value: value.update({field: "f" * 64}))
    with pytest.raises(ValueError, match="native checkpoint seal differs"):
        prepared.run()
    assert not (prepared.root / "confirmation").exists()


def test_native_boolean_claim_cannot_be_relabelled(prepared: Prepared) -> None:
    payload = _read_record(prepared.native_paths[1])
    payload["model_execution_performed"] = True
    with pytest.raises(ValueError, match="model_execution_performed"):
        TensorCandidateArtifact.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field,value", [("model_execution_performed", 0), ("schema_version", True)])
def test_native_json_type_substitution_is_rejected(prepared: Prepared, field: str, value: Any) -> None:
    rewrite(prepared.native_paths[1], lambda payload: payload.update({field: value}))
    with pytest.raises(ValueError):
        prepared.run()


def test_legacy_selection_type_substitution_is_rejected(prepared: Prepared) -> None:
    payload = json.loads(prepared.selection_path.read_bytes())
    payload["model_execution_performed"] = 0
    prepared.selection_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        prepared.run()


def test_misordered_native_seals_rejected(prepared: Prepared) -> None:
    with pytest.raises(ValueError):
        confirm_checkpoint_selection(prepared.plan, evidence(prepared.plan), data=prepared.data,
                                     adapters=prepared.adapters, native_paths=tuple(reversed(prepared.native_paths)),
                                     selection_path=prepared.selection_path, policy=ConfirmationPolicy(),
                                     output_dir=prepared.root / "confirmation")


def test_changed_projection_cannot_reuse_native_evidence(prepared: Prepared) -> None:
    artifacts = prepared.plan.artifacts.to_dict()
    artifacts["edited_checkpoint"]["content"] = "free-form descriptor is not a native checkpoint"
    changed = build_comparison_plan(study_id=prepared.plan.study_id, cases=prepared.plan.cases,
                                    artifacts=ComparisonArtifacts.model_validate_json(json.dumps(artifacts)),
                                    protocol=prepared.plan.protocol, budgets=prepared.plan.budgets,
                                    development=prepared.plan.development)
    with pytest.raises(ValueError):
        confirm_checkpoint_selection(changed, evidence(changed), data=prepared.data,
                                     adapters=prepared.adapters, native_paths=prepared.native_paths,
                                     selection_path=prepared.selection_path, policy=ConfirmationPolicy(),
                                     output_dir=prepared.root / "confirmation")
    assert not (prepared.root / "confirmation").exists()


def test_retained_search_terminal_marker_is_required_before_native_replay(prepared: Prepared) -> None:
    complete = prepared.root / "search" / "W" / "complete.json"
    complete.unlink()
    with pytest.raises(FileNotFoundError):
        prepared.run()
    assert not complete.exists()


def test_confirmation_completion_without_inner_evidence_is_not_repaired(prepared: Prepared) -> None:
    prepared.run()
    path = prepared.root / "confirmation" / "comparison" / "complete.json"
    path.unlink()
    with pytest.raises(ValueError, match="missing its comparison"):
        prepared.run()
    assert not path.exists()


def test_native_confirmation_no_network_or_subprocess(prepared: Prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("native confirmation attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert prepared.run()["status"] == "confirmed"


def test_native_output_symlink_rejected(prepared: Prepared) -> None:
    path = prepared.root / "native-link.json"
    path.symlink_to(prepared.root / "missing")
    with pytest.raises(ValueError, match="symlink"):
        seal_checkpoint_search(prepared.root / "search" / "W" / "complete.json", adapter=prepared.adapters[1],
                               data=prepared.data, output_path=path)
