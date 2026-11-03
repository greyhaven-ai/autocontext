"""Freeze tiny real tensor edits and new literal, non-inference evaluation catalogs."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import tempfile
from pathlib import Path
from typing import Literal

from autocontext.weight_evolution.adaptive_provider import (
    EditProposal,
    FeedbackRoute,
    GenerationFeedback,
    GenerationView,
    OfflineFeedbackProvider,
    ProbeFixture,
    ProviderSessionAdapter,
)
from autocontext.weight_evolution.adaptive_workflow import AdaptiveArm, load_adaptive_workflow
from autocontext.weight_evolution.artifacts import TensorInfo, write_tensor_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry, TensorCandidateSpec, TensorSessionAdapter
from autocontext.weight_evolution.checkpoint_workflow import CheckpointFixtureLock, CheckpointWorkflow
from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence, fixture_observation
from autocontext.weight_evolution.comparison_models import (
    ArtifactSource,
    CheckpointArtifact,
    ComparisonArtifacts,
    ComparisonBudgets,
    ComparisonPlan,
    ComparisonProtocol,
    ContextArtifact,
    DecodingArtifact,
    DevelopmentProvenance,
    build_comparison_plan,
)
from autocontext.weight_evolution.session_models import (
    CandidateState,
    Mechanism,
    ProbeOutput,
    SessionBaseline,
    SessionDecoding,
    SessionLimits,
    SessionSpec,
)
from autocontext.weight_evolution.tensor_edits import (
    TensorEdit,
    TensorEditProgram,
    TensorReplacement,
    apply_tensor_edit,
    build_checkpoint_contract,
)

ADAPTIVE = Path(__file__).resolve().parents[1] / "qwen_adaptive" / "fixtures"


def _candidates(scratch: Path) -> tuple[TensorCandidateSpec, ...]:
    # Four actual payloads, including untouched F16/F64 tensors. No downloaded weights.
    tensors = (
        TensorInfo("embedding.weight", "F16", (2,), 0, 4),
        TensorInfo("layer.0.weight", "BF16", (2, 2), 4, 12),
        TensorInfo("layer.1.weight", "F32", (2,), 12, 20),
        TensorInfo("norm.weight", "F64", (1,), 20, 28),
    )
    payloads = {"embedding.weight": struct.pack("<ee", 1.0, 2.0),
                "layer.0.weight": bytes.fromhex("803f004040408040"),
                "layer.1.weight": struct.pack("<ff", 1.0, -1.0), "norm.weight": struct.pack("<d", 0.5)}
    source = scratch / "base.safetensors"
    write_tensor_file(source, tensors, payloads.__getitem__)
    contract = build_checkpoint_contract(source, editable_names=("layer.0.weight", "layer.1.weight"),
                                         max_changed_entries=2)
    candidates = [TensorCandidateSpec(candidate_id="tiny-base", contract=contract,
                                       output_sha256=contract.sha256, output_size_bytes=contract.size_bytes)]
    pins = {pin.name: pin for pin in contract.tensors}
    for label, bf16, f32 in (("weak", "0040", 0.0), ("selected", "4040", 2.0), ("regressed", "8040", 9.0)):
        edits = []
        for name, index, bits in (("layer.0.weight", 0, bf16), ("layer.1.weight", 1, struct.pack("<f", f32).hex())):
            pin = pins[name]
            edits.append(TensorEdit(name=name, expected_sha256=pin.sha256, dtype=pin.dtype, shape=pin.shape,
                                     replacements=(TensorReplacement(index=index, value_hex=bits),)))
        program = TensorEditProgram(input_digest=contract.digest, edits=tuple(edits))
        receipt = apply_tensor_edit(contract, program, input_path=source, output_dir=scratch / f"freeze-{label}")
        candidates.append(TensorCandidateSpec(candidate_id=f"tiny-{label}", contract=contract, program=program,
                                               output_sha256=receipt.output_sha256,
                                               output_size_bytes=receipt.output_size_bytes))
    return tuple(candidates)


def _literal_evidence(plan: ComparisonPlan, *, role: Literal["confirmation", "audit"],
                      outcome: Literal["pass", "veto"]) -> FixtureEvidence:
    # These are newly authored literal catalogs for the checkpoint-reference cells.
    # No prior observations or gold answers are read to synthesize responses.
    catalogs: dict[str, tuple[tuple[str, str], ...]]
    if role == "confirmation":
        words = "</think>coral reef"
        catalogs = {
            "A": ((r"\boxed{13}", r"\boxed{13}"), (r"\boxed{7}", r"\boxed{7}"), (words, words)),
            "C": ((r"\boxed{14}", r"\boxed{14}"), (r"\boxed{7}", r"\boxed{7}"), (words, words)),
            "W": ((r"\boxed{14}", r"\boxed{14}"), (r"\boxed{7}", r"\boxed{7}"), (words, words)),
            "CW": ((r"\boxed{14}", r"\boxed{14}"), (r"\boxed{7}", r"\boxed{7}"), (words, words)),
            "D": ((r"\boxed{14}", r"\boxed{14}"),
                  (r"\boxed{6}", r"\boxed{6}") if outcome == "veto" else (r"\boxed{7}", r"\boxed{7}"),
                  (words, words)),
        }
        order = {"confirmation.sum": 0, "confirmation.coins": 1, "confirmation.words": 2}
    else:
        violet, upper = "</think>violet sky glows", "</think>HELLO WORLD"
        good = ((r"\boxed{42}", r"\boxed{42}"), (r"\boxed{42}", r"\boxed{42}"),
                (r"\boxed{6}", r"\boxed{6}"), (r"\boxed{15}", r"\boxed{15}"), (violet, violet), (upper, upper))
        catalogs = {
            "A": ((r"\boxed{41}", r"\boxed{42}"), (r"\boxed{42}", r"\boxed{42}"),
                  (r"\boxed{9}", r"\boxed{6}"), (r"\boxed{15}", r"\boxed{15}"),
                  ("violet sky glows", violet), (upper, upper)),
            "C": (good[0], (r"\boxed{42}", r"\boxed{40}"), *good[2:]),
            "W": good,
            "CW": (*good[:5], (upper, "")),
            "D": (good[0], good[1], (r"\boxed{9}", r"\boxed{6}"), *good[3:]),
        }
        order = {"audit.sum": 0, "audit.product": 1, "audit.books": 2,
                 "audit.boxes": 3, "audit.violet": 4, "audit.upper": 5}
    return FixtureEvidence(plan_digest=plan.digest, observations=tuple(
        fixture_observation(cell, catalogs[cell.condition_id][order[cell.problem_id]][plan.protocol.seeds.index(cell.seed)],
                            elapsed_seconds=0.01) for cell in plan.cells))


def build_fixture(output: Path, *, confirmation_outcome: Literal["pass", "veto"] = "pass") -> None:
    if output.exists() or any(path.is_symlink() for path in (output.absolute(), *output.absolute().parents)):
        raise ValueError("fixture output must be a new directory without symlinks")
    if confirmation_outcome not in ("pass", "veto"):
        raise ValueError("unsupported confirmation fixture outcome")
    previous, data, _ = load_adaptive_workflow(ADAPTIVE)
    policy = previous.search_policy
    with tempfile.TemporaryDirectory(prefix="qwen-checkpoint-fixture-") as temporary:
        scratch = Path(temporary).resolve()
        candidates = _candidates(scratch)
        registry = TensorCandidateRegistry(candidates, input_path=scratch / "base.safetensors",
                                           output_dir=scratch / "unused-store")
        baseline = SessionBaseline(
            base_checkpoint=CheckpointArtifact(artifact_id="tiny-checkpoint-base-projection", content=candidates[0].handle,
                                                source=ArtifactSource(kind="baseline_fixture")),
            base_context=previous.confirmation_plan.artifacts.base_context,
            base_decoding=previous.confirmation_plan.artifacts.base_decoding,
        )
        limits = SessionLimits(**{f"max_{key}": value for key, value in policy.required_usage.to_dict().items()})
        ids = tuple(case.problem.problem_id for case in data.practice)
        practice = data.practice_projection(ids)
        response_sets = ((r"\boxed{4}", r"\boxed{8}", "</think>green leaf"),
                         (r"\boxed{3}", "", "</think>green leaf"),
                         (r"\boxed{3}", r"\boxed{9}", "</think>green leaf"),
                         (r"\boxed{0}", r"\boxed{0}", "</think>green leaf"))
        score_sets = ((0, 0, 1), (1, 0, 1), (1, 1, 1))
        arms = []
        exports: dict[str, ContextArtifact | CheckpointArtifact | DecodingArtifact] = {}
        mechanisms: tuple[Mechanism, ...] = ("C", "W", "D")
        for mechanism in mechanisms:
            states = [baseline.state]
            for index in range(3):
                values = baseline.state.to_dict()
                if mechanism == "C":
                    values["context_text"] = ("Check each answer once.",
                                               previous.confirmation_plan.artifacts.learned_context.text,
                                               "Give a quick answer without checking.")[index]
                elif mechanism == "W":
                    values["weight_descriptor"] = candidates[index + 1].handle
                else:
                    values["decoding"] = (
                        SessionDecoding(temperature=0.45, top_p=0.95, top_k=20, presence_penalty=0.5).to_dict(),
                        previous.confirmation_plan.artifacts.selected_decoding.settings(),
                        SessionDecoding(temperature=1.2, top_p=0.95, top_k=20, presence_penalty=1.5).to_dict(),
                    )[index]
                states.append(CandidateState.model_validate_json(json.dumps(values)))
            routes = []
            for index in range(3):
                feedback = tuple(GenerationFeedback(problem_id=key, score=score,
                                                      response_sha256=hashlib.sha256(text.encode()).hexdigest())
                                 for key, score, text in zip(ids, score_sets[index], response_sets[index], strict=True))
                view = GenerationView(mechanism=mechanism, state=states[index], practice=practice, feedback=feedback)
                if mechanism == "C" and index == 2:
                    routes.append(FeedbackRoute(view_digest=view.digest, outcome="failure",
                                                 message="scripted context proposal failure"))
                else:
                    state = states[index + 1]
                    proposal = EditProposal(mechanism=mechanism, rationale="Literal proposal from this practice view.",
                                             context_text=state.context_text if mechanism == "C" else None,
                                             weight_descriptor=state.weight_descriptor if mechanism == "W" else None,
                                             decoding=state.decoding if mechanism == "D" else None)
                    routes.append(FeedbackRoute(view_digest=view.digest, response_text=json.dumps(proposal.to_dict()),
                                                 constrained=index != 0))
            probes = []
            for index, state in enumerate(states):
                if mechanism == "D" and index == 3:
                    probes.append(ProbeFixture(state_digest=state.digest, outcome="failure",
                                                message="scripted decoding probe failure"))
                else:
                    probes.append(ProbeFixture(state_digest=state.digest, outputs=tuple(
                        ProbeOutput(problem_id=key, text=text) for key, text in zip(ids, response_sets[index], strict=True))))
            delegate = ProviderSessionAdapter(mechanism, OfflineFeedbackProvider(tuple(routes)), tuple(probes))
            adapter = TensorSessionAdapter(delegate, registry)
            spec = SessionSpec(session_id=f"checkpoint-{mechanism.lower()}", mechanism=mechanism, baseline=baseline,
                                data_digest=data.digest, scorer_digest=previous.confirmation_plan.protocol.scorer_digest,
                                adapter_digest=adapter.identity_digest, limits=limits)
            arms.append(AdaptiveArm(spec=spec, routes=tuple(routes), probes=tuple(probes)))
            usage = policy.required_usage
            if mechanism == "C":
                usage = policy.probe_reservation.add(policy.probe_reservation)
                for _ in range(2):
                    usage = usage.add(policy.proposal_reservation).add(policy.edit_reservation).add(policy.probe_reservation)
                usage = usage.add(policy.proposal_reservation)
            origin = ArtifactSource(kind="development_fixture", source_problem_ids=tuple(sorted(ids)),
                                     declared_search_calls=usage.operations,
                                     declared_search_units=sum(getattr(usage, name) for name in (
                                         "editor_input_bytes", "editor_output_bytes", "probe_input_bytes", "probe_output_bytes")))
            artifact_id = f"{spec.session_id}-{mechanism.lower()}-candidate"
            if mechanism == "C":
                exports[mechanism] = ContextArtifact(artifact_id=artifact_id, text=states[2].context_text, source=origin)
            elif mechanism == "W":
                exports[mechanism] = CheckpointArtifact(artifact_id=artifact_id, content=states[2].weight_descriptor,
                                                         source=origin)
            else:
                exports[mechanism] = DecodingArtifact(artifact_id=artifact_id, source=origin, **states[2].decoding.settings())
        artifacts = ComparisonArtifacts(
            base_checkpoint=baseline.base_checkpoint, base_context=baseline.base_context, base_decoding=baseline.base_decoding,
            learned_context=ContextArtifact.model_validate_json(json.dumps(exports["C"].to_dict())),
            edited_checkpoint=CheckpointArtifact.model_validate_json(json.dumps(exports["W"].to_dict())),
            selected_decoding=DecodingArtifact.model_validate_json(json.dumps(exports["D"].to_dict())),
        )
        protocol = ComparisonProtocol.model_validate_json(json.dumps({**previous.confirmation_plan.protocol.to_dict(),
            "runtime_digest": hashlib.sha256(b"tiny-checkpoint-literal-responses-no-inference-v1").hexdigest()}))
        budgets = ComparisonBudgets.model_validate_json(json.dumps(previous.confirmation_plan.budgets.to_dict()))
        plans = {role: build_comparison_plan(study_id=f"tiny-checkpoint-{role}-v1", cases=cases, artifacts=artifacts,
                                             protocol=protocol, budgets=budgets,
                                             development=DevelopmentProvenance(cases=data.practice))
                 for role, cases in (("confirmation", data.confirmation), ("audit", data.final_audit))}
        confirmation = _literal_evidence(plans["confirmation"], role="confirmation", outcome=confirmation_outcome)
        audit = _literal_evidence(plans["audit"], role="audit", outcome=confirmation_outcome)
        workflow = CheckpointWorkflow(
            workflow_id=f"tiny-checkpoint-{confirmation_outcome}-v1", data_digest=data.digest, candidates=candidates,
            search_policy=policy, confirmation_policy=previous.confirmation_policy, arms=tuple(arms),
            confirmation_plan=plans["confirmation"], confirmation_evidence_digest=confirmation.digest,
            audit_plan=plans["audit"], audit_evidence_digest=audit.digest,
        )
        payloads = {"data": data.to_dict(), "workflow": workflow.to_dict(),
                    "confirmation": confirmation.to_dict(), "audit": audit.to_dict()}
        raw = {name: (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode() for name, payload in payloads.items()}
        lock_values: dict[str, str | int] = {"base_sha256": candidates[0].output_sha256,
                                            "base_size_bytes": candidates[0].output_size_bytes}
        for name, content in raw.items():
            lock_values[f"{name}_sha256"] = hashlib.sha256(content).hexdigest()
            lock_values[f"{name}_size_bytes"] = len(content)
        lock = CheckpointFixtureLock.model_validate(lock_values)
        output.mkdir(parents=True)
        (output / "base.safetensors").write_bytes((scratch / "base.safetensors").read_bytes())
        for name, content in raw.items():
            (output / f"{name}.json").write_bytes(content)
        (output / "fixtures.lock.json").write_text(json.dumps(lock.to_dict(), sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirmation-outcome", choices=("pass", "veto"), default="pass")
    args = parser.parse_args()
    build_fixture(args.output, confirmation_outcome=args.confirmation_outcome)
