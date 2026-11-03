from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.providers.base import CompletionResult, LLMProvider, OutputSchema, ProviderError
from autocontext.weight_evolution.adaptive_provider import (
    FIXTURE_MAX_TOKENS,
    FIXTURE_MODEL,
    MAX_PROPOSAL_BYTES,
    SYSTEM_PROMPT,
    AdaptiveFixtureInterrupted,
    EditProposal,
    FeedbackRoute,
    GenerationFeedback,
    GenerationView,
    OfflineFeedbackProvider,
    ProbeFixture,
    ProposalReceipt,
    ProviderSessionAdapter,
    generation_prompt,
    generation_view,
    parse_proposal_receipt,
)
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import load_comparison_fixture
from autocontext.weight_evolution.session import EditingSession
from autocontext.weight_evolution.session_models import (
    CandidateRevision,
    EditOperation,
    InspectOperation,
    Mechanism,
    PracticeFeedback,
    PracticeProblem,
    ProbeOperation,
    ProbeOutput,
    SessionAdapterRequest,
    SessionBaseline,
    SessionDecoding,
    SessionLimits,
    SessionSpec,
    SessionUsage,
)
from autocontext.weight_evolution.session_workflow import load_session_workflow

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "qwen_comparison" / "fixtures"


def request(*, feedback: tuple[PracticeFeedback, ...] = (), kind: str = "inspect") -> SessionAdapterRequest:
    source, _ = load_comparison_fixture(EXAMPLE)
    baseline = SessionBaseline(base_checkpoint=source.artifacts.base_checkpoint,
                               base_context=source.artifacts.base_context, base_decoding=source.artifacts.base_decoding)
    candidate = CandidateRevision(session_id="adaptive-provider-test", revision=0, state=baseline.state)
    practice = tuple(PracticeProblem(dataset=case.dataset, problem_id=case.problem.problem_id, text=case.problem.text)
                     for case in source.development.cases)
    ids = tuple(problem.problem_id for problem in practice)
    reserve = SessionUsage(operations=1, editor_input_bytes=65536, editor_output_bytes=32768, simulated_seconds=1.0,
                            probe_input_bytes=4096 if kind == "probe" else 0,
                            probe_output_bytes=4096 if kind == "probe" else 0)
    operation: InspectOperation | EditOperation | ProbeOperation
    if kind == "inspect":
        operation = InspectOperation(operation_id="propose", candidate_digest=candidate.digest,
                                     reservation=reserve, target="practice", problem_ids=ids)
    elif kind == "probe":
        operation = ProbeOperation(operation_id="probe", candidate_digest=candidate.digest,
                                   reservation=reserve, problem_ids=ids)
    elif kind == "edit":
        operation = EditOperation(operation_id="edit", candidate_digest=candidate.digest,
                                  reservation=reserve, context_text="a new context")
        practice = ()
    else:
        operation = InspectOperation(operation_id="inspect-candidate", candidate_digest=candidate.digest,
                                     reservation=reserve, target="candidate")
        practice = ()
    return SessionAdapterRequest(spec_digest="a" * 64, operation=operation, candidate=candidate,
                                  practice=practice, feedback=feedback)


def proposal(mechanism: Mechanism = "C") -> EditProposal:
    return EditProposal(mechanism=mechanism, rationale="literal proposal selected from practice feedback",
                        context_text="Check every arithmetic step." if mechanism == "C" else None,
                        weight_descriptor="Synthetic revised weights descriptor." if mechanism == "W" else None,
                        decoding=SessionDecoding(temperature=0.2, top_p=0.9, top_k=20, presence_penalty=1.0)
                        if mechanism == "D" else None)


def probe_for(value: SessionAdapterRequest, **updates: Any) -> ProbeFixture:
    return ProbeFixture(**{
        "state_digest": value.candidate.state.digest,
        "outputs": tuple(ProbeOutput(problem_id=problem.problem_id, text=text) for problem, text in zip(
            value.practice, (r"\boxed{3}", r"\boxed{9}", "</think>green leaf"), strict=True,
        )), **updates,
    })


def adapter_for(value: SessionAdapterRequest, mechanism: Mechanism = "C", **updates: Any) -> ProviderSessionAdapter:
    route = FeedbackRoute(**{"view_digest": generation_view(mechanism, value).digest,
                              "response_text": json.dumps(proposal(mechanism).to_dict()), **updates})
    return ProviderSessionAdapter(mechanism, OfflineFeedbackProvider((route,)), (probe_for(value),))


def complete(provider: OfflineFeedbackProvider, view: GenerationView, **updates: Any) -> CompletionResult:
    kwargs = {"system_prompt": SYSTEM_PROMPT, "user_prompt": generation_prompt(view), "model": FIXTURE_MODEL,
              "temperature": 0.0, "max_tokens": FIXTURE_MAX_TOKENS,
              "output_schema": OutputSchema("weight_edit_proposal", EditProposal.model_json_schema()), **updates}
    return provider.complete(**kwargs)


@pytest.mark.parametrize("mechanism", ["C", "W", "D"])
@pytest.mark.parametrize("constrained", [False, True])
def test_existing_provider_interface_returns_locally_validated_proposal(mechanism: Mechanism, constrained: bool) -> None:
    value = request()
    adapter = adapter_for(value, mechanism, constrained=constrained, served_model="fixture-served-model")
    result = adapter.execute(value)
    receipt = parse_proposal_receipt(result.message, mechanism)
    assert receipt.proposal == proposal(mechanism)
    assert receipt.constrained is constrained
    assert receipt.model == FIXTURE_MODEL and receipt.served_model == "fixture-served-model"
    assert receipt.stop_reason == "stop"
    assert receipt.model_execution_performed is False and receipt.gpu_execution_deferred is True
    edit = receipt.proposal.to_edit(operation_id="generated-edit", candidate_digest=value.candidate.digest,
                                    reservation=value.operation.reservation)
    assert edit.operation_id == "generated-edit" and edit.candidate_digest == value.candidate.digest
    assert result.operation_digest == value.operation.digest
    assert result.probe_outputs == ()
    assert receipt.provider_input_bytes > len(json.dumps(value.to_dict()).encode())
    assert receipt.provider_output_bytes == len(json.dumps(proposal(mechanism).to_dict()).encode())


def test_feedback_changes_route_without_call_index_or_mutable_provider_state() -> None:
    value = request()
    failed = PracticeFeedback(problem_id=value.practice[0].problem_id, score=0, response_sha256="b" * 64,
                               candidate_digest=value.candidate.digest)
    changed = request(feedback=(failed,))
    first, second = generation_view("C", value), generation_view("C", changed)
    revised = EditProposal(mechanism="C", context_text="Correct the failed sum before checking formatting.")
    provider = OfflineFeedbackProvider((
        FeedbackRoute(view_digest=first.digest, response_text=json.dumps(proposal().to_dict())),
        FeedbackRoute(view_digest=second.digest, response_text=json.dumps(revised.to_dict())),
    ))
    adapter = ProviderSessionAdapter("C", provider, (probe_for(value),))
    assert parse_proposal_receipt(adapter.execute(value).message).proposal == proposal()
    assert parse_proposal_receipt(adapter.execute(changed).message).proposal == revised
    assert parse_proposal_receipt(adapter.execute(value).message).proposal == proposal()
    assert first.digest != second.digest


def test_view_contains_only_current_candidate_feedback_and_latest_per_problem() -> None:
    value = request()
    current = value.candidate.digest
    feedback = (
        PracticeFeedback(problem_id="practice.sum", score=0, response_sha256="1" * 64, candidate_digest=current),
        PracticeFeedback(problem_id="practice.sum", score=1, response_sha256="2" * 64, candidate_digest=current),
        PracticeFeedback(problem_id="practice.pears", score=0, response_sha256="3" * 64, candidate_digest="f" * 64),
    )
    view = generation_view("C", request(feedback=feedback))
    assert view.feedback == (GenerationFeedback(problem_id="practice.sum", score=1, response_sha256="2" * 64),)
    encoded = generation_prompt(view)
    for hidden in ('"answer"', '"instructions"', "audit.", "confirmation.", "spec_digest", "candidate_digest"):
        assert hidden not in encoded
    assert "--- STRATEGY REFINEMENT ---" in encoded


@pytest.mark.parametrize("text", [
    "not JSON", "[]", '{"mechanism":"C","context_text":"a","context_text":"b"}',
    '{"mechanism":"C","context_text":"new","score":1}',
    '{"mechanism":"C","weight_descriptor":"bad"}',
    '{"mechanism":"W","weight_descriptor":"another mechanism"}',
    '{"mechanism":"C","context_text":1}',
    '{"mechanism":"C","context_text":""}',
    '{"mechanism":"C","context_text":"new","rationale":NaN}',
    " " * (MAX_PROPOSAL_BYTES + 1),
])
@pytest.mark.parametrize("constrained", [False, True])
def test_provider_schema_claim_does_not_bypass_host_validation(text: str, constrained: bool) -> None:
    value = request()
    with pytest.raises(ValueError):
        adapter_for(value, response_text=text, constrained=constrained).execute(value)


@pytest.mark.parametrize("stop_reason", ["length", "max_tokens", "max_output_tokens"])
def test_truncated_generation_cannot_become_an_edit(stop_reason: str) -> None:
    value = request()
    with pytest.raises(ValueError, match="truncated"):
        adapter_for(value, stop_reason=stop_reason).execute(value)


@pytest.mark.parametrize("outcome,error", [("failure", ProviderError), ("interruption", AdaptiveFixtureInterrupted)])
def test_generation_failure_kind_preserved(outcome: str, error: type[BaseException]) -> None:
    value = request()
    with pytest.raises(error, match="configured issue"):
        adapter_for(value, outcome=outcome, message="configured issue").execute(value)


def test_candidate_inspect_cannot_dispatch_generation() -> None:
    value = request()
    with pytest.raises(ValueError, match="practice prompts"):
        adapter_for(value).execute(request(kind="candidate-inspect"))


def test_probe_catalog_exact_state_and_order_binding() -> None:
    value = request()
    adapter = adapter_for(value)
    probe_request = request(kind="probe")
    result = adapter.execute(probe_request)
    assert result.probe_outputs == probe_for(value).outputs
    assert result.operation_digest == probe_request.operation.digest
    wrong = ProbeFixture(state_digest="0" * 64, outputs=probe_for(value).outputs)
    with pytest.raises(ValueError, match="absent"):
        ProviderSessionAdapter("C", adapter.provider, (wrong,)).execute(probe_request)
    reversed_rows = ProbeFixture(state_digest=value.candidate.state.digest, outputs=tuple(reversed(probe_for(value).outputs)))
    with pytest.raises(ValueError, match="exactly cover"):
        ProviderSessionAdapter("C", adapter.provider, (reversed_rows,)).execute(probe_request)


@pytest.mark.parametrize("outcome,error", [("failure", ProviderError), ("interruption", AdaptiveFixtureInterrupted)])
def test_probe_failure_kind_preserved(outcome: str, error: type[BaseException]) -> None:
    value = request()
    adapter = adapter_for(value)
    probe = probe_for(value, outputs=(), outcome=outcome, message="probe issue")
    with pytest.raises(error, match="probe issue"):
        ProviderSessionAdapter("C", adapter.provider, (probe,)).execute(request(kind="probe"))


def test_edits_acknowledge_only_allowed_surface_without_dispatching_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    value = request()
    adapter = adapter_for(value)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("typed edit called provider")

    monkeypatch.setattr(adapter.provider, "complete", forbidden)
    result = adapter.execute(request(kind="edit"))
    assert result.message == "typed edit acknowledged"
    wrong = ProviderSessionAdapter("W", adapter.provider, adapter.probes)
    with pytest.raises(ValueError, match="allowed surface"):
        wrong.execute(request(kind="edit"))


def test_source_catalog_and_mechanism_bind_adapter_identity() -> None:
    value = request()
    first = adapter_for(value)
    same = adapter_for(value)
    different = adapter_for(value, response_text=json.dumps(EditProposal(mechanism="C", context_text="other").to_dict()))
    assert first.identity_digest == same.identity_digest
    assert first.identity_digest != different.identity_digest
    assert first.identity_digest != ProviderSessionAdapter("W", first.provider, first.probes).identity_digest
    assert "loop/refinement_prompt.py" in first.provenance()["sources"]


def test_unknown_feedback_route_does_not_fall_back_to_another_proposal() -> None:
    value = request()
    adapter = adapter_for(value)
    other = request(feedback=(PracticeFeedback(problem_id="practice.sum", score=0, response_sha256="f" * 64,
                                               candidate_digest=value.candidate.digest),))
    with pytest.raises(ProviderError, match="exact practice feedback"):
        adapter.execute(other)


@pytest.mark.parametrize("change", [
    {"system_prompt": "changed"}, {"user_prompt": "unrelated"}, {"temperature": 0.5}, {"max_tokens": 8192},
    {"output_schema": None}, {"output_schema": OutputSchema("wrong", {})}, {"model": "live-model"},
])
def test_provider_rejects_unpinned_call_interface(change: dict[str, Any]) -> None:
    value = request()
    with pytest.raises((ProviderError, ValueError)):
        complete(adapter_for(value).provider, generation_view("C", value), **change)


def test_provider_rejects_prompt_prose_tampering_even_if_view_intact() -> None:
    value = request()
    view = generation_view("C", value)
    prompt = generation_prompt(view).replace("CPU fixture editing.", "Unpinned instruction.")
    with pytest.raises(ValueError, match="exact projected view"):
        complete(adapter_for(value).provider, view, user_prompt=prompt)


def test_real_or_unpinned_provider_rejected_before_completion() -> None:
    class OtherProvider(LLMProvider):
        def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
            raise AssertionError("live provider called")

        def default_model(self) -> str:
            return "live"

        @property
        def supports_single_dispatch(self) -> bool:
            return True

    with pytest.raises(ValueError, match="offline fixture provider"):
        ProviderSessionAdapter("C", OtherProvider(), (probe_for(request()),))


def test_no_network_or_subprocess_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    value = request()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline provider attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    adapter = adapter_for(value)
    adapter.execute(value)
    adapter.execute(request(kind="edit"))
    adapter.execute(request(kind="probe"))


def test_receipt_type_and_mechanism_cannot_be_bypassed() -> None:
    receipt = ProposalReceipt(proposal=proposal(), model=FIXTURE_MODEL, constrained=False,
                               provider_input_bytes=1000, provider_output_bytes=200)
    assert parse_proposal_receipt(json.dumps(receipt.to_dict())) == receipt
    with pytest.raises(ValueError, match="another mechanism"):
        parse_proposal_receipt(json.dumps(receipt.to_dict()), "W")
    payload = receipt.to_dict()
    payload["model_execution_performed"] = True
    with pytest.raises(ValueError):
        parse_proposal_receipt(json.dumps(payload))
    payload = receipt.to_dict()
    payload["constrained"] = 0
    with pytest.raises(ValueError):
        parse_proposal_receipt(json.dumps(payload))


def test_view_rejects_hidden_or_repeated_feedback_ids() -> None:
    value = request()
    hidden = GenerationFeedback(problem_id="audit.sum", score=1, response_sha256="f" * 64)
    with pytest.raises(ValueError, match="selected practice"):
        GenerationView(mechanism="C", state=value.candidate.state, practice=value.practice, feedback=(hidden,))
    known = GenerationFeedback(problem_id="practice.sum", score=1, response_sha256=hashlib.sha256(b"3").hexdigest())
    with pytest.raises(ValueError, match="repeats"):
        GenerationView(mechanism="C", state=value.candidate.state, practice=value.practice, feedback=(known, known))


def test_transformed_provider_input_reservation_is_checked_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    value = request()
    payload = value.to_dict()
    payload["operation"]["reservation"]["editor_input_bytes"] = len(json.dumps(payload).encode()) + 10
    changed = SessionAdapterRequest.model_validate_json(json.dumps(payload))
    adapter = adapter_for(changed)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unreserved expanded provider call was dispatched")

    monkeypatch.setattr(adapter.provider, "complete", forbidden)
    with pytest.raises(ValueError, match="transformed provider input"):
        adapter.execute(changed)


def test_raw_oversized_provider_completion_is_a_charged_session_failure(tmp_path: Path) -> None:
    value = request()
    payload = value.to_dict()
    payload["operation"]["reservation"]["editor_output_bytes"] = 256
    changed = SessionAdapterRequest.model_validate_json(json.dumps(payload))
    text = json.dumps(EditProposal(mechanism="C", context_text="x" * 1024).to_dict())
    adapter = adapter_for(changed, response_text=text)
    workflow, data = load_session_workflow(EXAMPLE.parent.parent / "qwen_sessions" / "fixtures")
    spec = SessionSpec(
        session_id=changed.candidate.session_id, mechanism="C", baseline=workflow.programs[0].spec.baseline,
        data_digest=data.digest, scorer_digest=SyntheticComparisonScorer().lock_digest,
        adapter_digest=adapter.identity_digest,
        limits=SessionLimits(max_operations=1, max_editor_input_bytes=65536, max_editor_output_bytes=256,
                             max_probe_input_bytes=1, max_probe_output_bytes=1, max_simulated_seconds=1.0),
    )
    session = EditingSession(spec=spec, data=data, output_dir=tmp_path / "session", adapter=adapter)
    receipt = session.run(changed.operation)
    assert receipt["status"] == "failed"
    assert "raw provider output" in receipt["error"]["message"]
    assert receipt["charged_usage"]["editor_output_bytes"] == 256
    assert session.snapshot()["reserved_usage"]["operations"] == 1
    assert session.run(changed.operation) == receipt
