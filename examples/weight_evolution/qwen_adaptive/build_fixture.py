"""Freeze literal feedback-conditioned routes and separate confirmation evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from autocontext.weight_evolution.adaptive import AdaptivePolicy
from autocontext.weight_evolution.adaptive_confirmation import ConfirmationPolicy
from autocontext.weight_evolution.adaptive_provider import (
    EditProposal,
    FeedbackRoute,
    GenerationFeedback,
    GenerationView,
    OfflineFeedbackProvider,
    ProbeFixture,
    ProviderSessionAdapter,
)
from autocontext.weight_evolution.adaptive_workflow import AdaptiveArm, AdaptiveFixtureLock, AdaptiveWorkflow
from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence, fixture_observation
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import load_comparison_fixture
from autocontext.weight_evolution.comparison_models import (
    ArtifactSource,
    CheckpointArtifact,
    ComparisonArtifacts,
    ComparisonBudgets,
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
    SessionUsage,
)
from autocontext.weight_evolution.session_workflow import load_session_workflow

COMPARISON = Path(__file__).resolve().parents[1] / "qwen_comparison" / "fixtures"
SESSIONS = Path(__file__).resolve().parents[1] / "qwen_sessions" / "fixtures"


def build_fixture(output: Path, *, confirmation_outcome: Literal["pass", "veto"] = "veto") -> None:
    if output.exists() or any(path.is_symlink() for path in (output.absolute(), *output.absolute().parents)):
        raise ValueError("fixture output must be a new directory without symlinks")
    if confirmation_outcome not in ("pass", "veto"):
        raise ValueError("unsupported confirmation fixture outcome")
    source, audit_evidence = load_comparison_fixture(COMPARISON)
    _, data = load_session_workflow(SESSIONS)
    baseline = SessionBaseline(base_checkpoint=source.artifacts.base_checkpoint,
                               base_context=source.artifacts.base_context, base_decoding=source.artifacts.base_decoding)
    policy = AdaptivePolicy(
        proposal_rounds=3,
        proposal_reservation=SessionUsage(operations=1, editor_input_bytes=16384, editor_output_bytes=4096,
                                           simulated_seconds=1.0),
        edit_reservation=SessionUsage(operations=1, editor_input_bytes=8192, editor_output_bytes=4096,
                                       simulated_seconds=1.0),
        probe_reservation=SessionUsage(operations=1, editor_input_bytes=8192, editor_output_bytes=4096,
                                        probe_input_bytes=4096, probe_output_bytes=4096, simulated_seconds=1.0),
    )
    limits = SessionLimits(**{f"max_{key}": value for key, value in policy.required_usage.to_dict().items()})
    ids = tuple(case.problem.problem_id for case in data.practice)
    practice = data.practice_projection(ids)
    # Literal observations and expected fixture feedback. No model generates answers.
    response_sets = ((r"\boxed{4}", r"\boxed{8}", "</think>green leaf"),
                     (r"\boxed{3}", "", "</think>green leaf"),
                     (r"\boxed{3}", r"\boxed{9}", "</think>green leaf"),
                     (r"\boxed{0}", r"\boxed{0}", "</think>green leaf"))
    score_sets = ((0, 0, 1), (1, 0, 1), (1, 1, 1))

    def outputs(index: int) -> tuple[ProbeOutput, ...]:
        return tuple(ProbeOutput(problem_id=key, text=text)
                     for key, text in zip(ids, response_sets[index], strict=True))

    def state(mechanism: Mechanism, round_index: int) -> CandidateState:
        values = baseline.state.to_dict()
        if mechanism == "C":
            values["context_text"] = ("Check each answer once.", source.artifacts.learned_context.text,
                                       "Give a quick answer without checking.")[round_index - 1]
        elif mechanism == "W":
            values["weight_descriptor"] = ("Synthetic weak proposal descriptor; contains no model weights.",
                                            source.artifacts.edited_checkpoint.content,
                                            "Synthetic regressed descriptor; contains no model weights.")[round_index - 1]
        else:
            values["decoding"] = (SessionDecoding(temperature=0.45, top_p=0.95, top_k=20, presence_penalty=0.5).to_dict(),
                                   source.artifacts.selected_decoding.settings(),
                                   SessionDecoding(temperature=1.2, top_p=0.95, top_k=20, presence_penalty=1.5).to_dict())[
                                       round_index - 1]
        return CandidateState.model_validate_json(json.dumps(values))

    arms = []
    exports: dict[str, ContextArtifact | CheckpointArtifact | DecodingArtifact] = {}
    mechanisms: tuple[Mechanism, ...] = ("C", "W", "D")
    for mechanism in mechanisms:
        states = (baseline.state, *(state(mechanism, index) for index in (1, 2, 3)))
        routes = []
        for index in range(3):
            feedback = tuple(GenerationFeedback(problem_id=key, score=score,
                                                  response_sha256=hashlib.sha256(text.encode()).hexdigest())
                             for key, score, text in zip(ids, score_sets[index], response_sets[index], strict=True))
            view = GenerationView(mechanism=mechanism, state=states[index], practice=practice, feedback=feedback)
            if mechanism == "W" and index == 2:
                routes.append(FeedbackRoute(view_digest=view.digest, outcome="failure",
                                             message="scripted proposal infrastructure failure"))
            else:
                target = states[index + 1]
                proposal = EditProposal(mechanism=mechanism, rationale="Literal edit selected for this practice view.",
                                         context_text=target.context_text if mechanism == "C" else None,
                                         weight_descriptor=target.weight_descriptor if mechanism == "W" else None,
                                         decoding=target.decoding if mechanism == "D" else None)
                routes.append(FeedbackRoute(view_digest=view.digest, response_text=json.dumps(proposal.to_dict()),
                                             constrained=index != 0))
        probes = []
        for index, candidate_state in enumerate(states):
            if mechanism == "D" and index == 3:
                probes.append(ProbeFixture(state_digest=candidate_state.digest, outcome="failure",
                                            message="scripted probe infrastructure failure"))
            else:
                probes.append(ProbeFixture(state_digest=candidate_state.digest, outputs=outputs(index)))
        adapter = ProviderSessionAdapter(mechanism, OfflineFeedbackProvider(tuple(routes)), tuple(probes))
        spec = SessionSpec(session_id=f"adaptive-{mechanism.lower()}", mechanism=mechanism, baseline=baseline,
                            data_digest=data.digest, scorer_digest=SyntheticComparisonScorer().lock_digest,
                            adapter_digest=adapter.identity_digest, limits=limits)
        arms.append(AdaptiveArm(spec=spec, routes=tuple(routes), probes=tuple(probes)))
        usage = policy.required_usage
        if mechanism == "W":
            usage = policy.probe_reservation.add(policy.probe_reservation)
            for _ in range(2):
                usage = usage.add(policy.proposal_reservation).add(policy.edit_reservation).add(policy.probe_reservation)
            usage = usage.add(policy.proposal_reservation)
        artifact_source = ArtifactSource(kind="development_fixture", source_problem_ids=tuple(sorted(ids)),
                                         declared_search_calls=usage.operations,
                                         declared_search_units=sum(getattr(usage, name) for name in (
                                             "editor_input_bytes", "editor_output_bytes", "probe_input_bytes",
                                             "probe_output_bytes")))
        artifact_id = f"{spec.session_id}-{mechanism.lower()}-candidate"
        if mechanism == "C":
            exports[mechanism] = ContextArtifact(artifact_id=artifact_id, text=states[2].context_text, source=artifact_source)
        elif mechanism == "W":
            exports[mechanism] = CheckpointArtifact(artifact_id=artifact_id, content=states[2].weight_descriptor,
                                                    source=artifact_source)
        else:
            exports[mechanism] = DecodingArtifact(artifact_id=artifact_id, source=artifact_source,
                                                  **states[2].decoding.settings())
    artifacts = ComparisonArtifacts(
        base_checkpoint=baseline.base_checkpoint, base_context=baseline.base_context, base_decoding=baseline.base_decoding,
        learned_context=ContextArtifact.model_validate_json(json.dumps(exports["C"].to_dict())),
        edited_checkpoint=CheckpointArtifact.model_validate_json(json.dumps(exports["W"].to_dict())),
        selected_decoding=DecodingArtifact.model_validate_json(json.dumps(exports["D"].to_dict())),
    )
    budgets = ComparisonBudgets.model_validate_json(json.dumps({**source.budgets.to_dict(),
                                                                "max_search_calls_per_mechanism": 12,
                                                                "max_search_units_per_mechanism": 250000}))
    plan = build_comparison_plan(study_id="qwen-adaptive-confirmation-v1", cases=data.confirmation,
                                 artifacts=artifacts, protocol=source.protocol, budgets=budgets,
                                 development=DevelopmentProvenance(cases=data.practice))
    # Fixed confirmation evidence is a different catalog from all practice probes.
    good_words = "</think>coral reef"
    confirmation_responses = {
        "A": ((r"\boxed{13}", r"\boxed{13}"), (r"\boxed{7}", r"\boxed{7}"), (good_words, good_words)),
        "C": ((r"\boxed{14}", r"\boxed{14}"), (r"\boxed{7}", r"\boxed{7}"), (good_words, good_words)),
        "W": ((r"\boxed{14}", r"\boxed{13}"), (r"\boxed{7}", r"\boxed{7}"), (good_words, good_words)),
        "CW": ((r"\boxed{14}", r"\boxed{14}"), (r"\boxed{7}", r"\boxed{7}"), (good_words, good_words)),
        "D": ((r"\boxed{14}", r"\boxed{14}"),
              (r"\boxed{6}", r"\boxed{6}") if confirmation_outcome == "veto" else (r"\boxed{7}", r"\boxed{7}"),
              (good_words, good_words)),
    }
    order = {case.problem.problem_id: index for index, case in enumerate(data.confirmation)}
    confirmation = FixtureEvidence(plan_digest=plan.digest, observations=tuple(
        fixture_observation(cell, confirmation_responses[cell.condition_id][order[cell.problem_id]][
            plan.protocol.seeds.index(cell.seed)], elapsed_seconds=0.01) for cell in plan.cells))
    workflow = AdaptiveWorkflow(
        workflow_id=f"qwen-adaptive-{confirmation_outcome}-v1", data_digest=data.digest,
        source_plan_digest=source.digest, source_evidence_digest=audit_evidence.digest, search_policy=policy,
        confirmation_policy=ConfirmationPolicy(minimum_mean_gain=0.1), confirmation_plan=plan,
        confirmation_evidence_digest=confirmation.digest, arms=tuple(arms),
    )
    payloads = {"data": data.to_dict(), "workflow": workflow.to_dict(), "confirmation": confirmation.to_dict()}
    raw = {name: (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode() for name, payload in payloads.items()}
    values: dict[str, str | int] = {}
    for name, content in raw.items():
        values[f"{name}_sha256"] = hashlib.sha256(content).hexdigest()
        values[f"{name}_size_bytes"] = len(content)
    lock = AdaptiveFixtureLock.model_validate(values)
    output.mkdir(parents=True)
    for name, content in raw.items():
        (output / f"{name}.json").write_bytes(content)
    (output / "fixtures.lock.json").write_text(json.dumps(lock.to_dict(), sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirmation-outcome", choices=("pass", "veto"), default="veto")
    args = parser.parse_args()
    build_fixture(args.output, confirmation_outcome=args.confirmation_outcome)
