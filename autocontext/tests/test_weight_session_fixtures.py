from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.session_fixtures import (
    MAX_SESSION_SCRIPT_BYTES,
    FixtureAdapterError,
    FixtureAdapterInterrupted,
    ScriptedSessionAdapter,
    SessionFixtureEntry,
    SessionFixtureScript,
    load_session_fixture_script,
)
from autocontext.weight_evolution.session_models import (
    CandidateRevision,
    CandidateState,
    EditOperation,
    InspectOperation,
    PracticeProblem,
    ProbeOperation,
    ProbeOutput,
    SessionAdapterRequest,
    SessionDecoding,
    SessionUsage,
)


def candidate() -> CandidateRevision:
    return CandidateRevision(session_id="adapter-test", revision=0, parent_digest=None, operation_digest=None,
                             state=CandidateState(weight_descriptor="synthetic base", context_text="",
                                                  decoding=SessionDecoding(temperature=0.6, top_p=0.95,
                                                                           top_k=20, presence_penalty=0.0)))


def request(*, probe: bool = False) -> SessionAdapterRequest:
    current = candidate()
    reservation = SessionUsage(operations=1, editor_input_bytes=10000, editor_output_bytes=1000,
                               probe_input_bytes=1000 if probe else 0, probe_output_bytes=1000 if probe else 0,
                               simulated_seconds=1.0)
    if probe:
        operation = ProbeOperation(operation_id="probe-1", candidate_digest=current.digest,
                                   reservation=reservation, problem_ids=("practice.one", "practice.two"))
        practice = (PracticeProblem(dataset="math500", problem_id="practice.one", text="What is one plus one?"),
                    PracticeProblem(dataset="gsm8k", problem_id="practice.two", text="How many things are in a pair?"))
    else:
        operation = InspectOperation(operation_id="inspect-1", candidate_digest=current.digest, reservation=reservation)
        practice = ()
    return SessionAdapterRequest(spec_digest="a" * 64, operation=operation, candidate=current, practice=practice, feedback=())


def script_for(value: SessionAdapterRequest, **updates: Any) -> SessionFixtureScript:
    outputs = tuple(ProbeOutput(problem_id=item.problem_id, text=r"\boxed{2}", finish_reason="stop")
                    for item in value.practice) if value.operation.kind == "probe" else ()
    entry = SessionFixtureEntry(operation_digest=value.operation.digest, candidate_digest=value.candidate.digest,
                                operation_kind=value.operation.kind, message="literal fixture response",
                                probe_outputs=outputs, simulated_seconds=0.25)
    if updates:
        entry = SessionFixtureEntry.model_validate_json(json.dumps({**entry.to_dict(), **updates}))
    return SessionFixtureScript(entries=(entry,))


def edit_request(*, context_text: str = "Check the final arithmetic.") -> SessionAdapterRequest:
    value = request()
    operation = EditOperation(operation_id="edit-1", candidate_digest=value.candidate.digest,
                              reservation=value.operation.reservation, context_text=context_text)
    return SessionAdapterRequest(spec_digest=value.spec_digest, operation=operation, candidate=value.candidate,
                                 practice=(), feedback=())


def test_repeated_inspect_is_stateless_and_identity_is_content_bound() -> None:
    value = request()
    script = script_for(value)
    first, second = ScriptedSessionAdapter(script), ScriptedSessionAdapter(script)
    before = first.identity_digest
    assert first.execute(value) == first.execute(value) == second.execute(value)
    assert first.identity_digest == before == second.identity_digest
    assert first.execute(value).operation_digest == value.operation.digest
    assert first.execute(value).execution_kind == "fixture"
    assert first.provenance()["model_execution_performed"] is False
    changed = ScriptedSessionAdapter(script_for(value, message="different literal response"))
    assert changed.identity_digest != before


def test_probe_returns_literal_text_without_gold_and_without_network_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    value = request(probe=True)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("fixture adapter cannot launch processes or contact services")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = ScriptedSessionAdapter(script_for(value)).execute(value)
    assert [item.text for item in result.probe_outputs] == [r"\boxed{2}", r"\boxed{2}"]
    assert [item.problem_id for item in result.probe_outputs] == ["practice.one", "practice.two"]
    serialized = value.to_dict()
    assert all(set(item) == {"dataset", "problem_id", "text"} for item in serialized["practice"])
    assert all(key not in json.dumps(serialized) for key in ('"answer"', '"instructions"', '"confirmation"', '"audit"'))


@pytest.mark.parametrize("kind", ["inspect", "edit", "probe"])
@pytest.mark.parametrize("outcome,error", [("failure", FixtureAdapterError), ("interruption", FixtureAdapterInterrupted)])
def test_scripted_faults_remain_distinct_for_controller_reconciliation(
    kind: str, outcome: str, error: type[BaseException],
) -> None:
    value = edit_request() if kind == "edit" else request(probe=kind == "probe")
    adapter = ScriptedSessionAdapter(script_for(value, outcome=outcome, message="deliberate fixture fault",
                                                probe_outputs=[]))
    with pytest.raises(error, match="deliberate fixture fault"):
        adapter.execute(value)
    # Retrying the stateless catalog cannot silently turn a fault into success.
    with pytest.raises(error):
        adapter.execute(value)
    assert issubclass(FixtureAdapterError, Exception)
    assert not issubclass(FixtureAdapterInterrupted, Exception)


def test_edit_acknowledges_exact_payload_without_creating_candidate() -> None:
    value = edit_request()
    adapter = ScriptedSessionAdapter(script_for(value))
    before = value.candidate.to_dict()
    result = adapter.execute(value)
    assert result.operation_digest == value.operation.digest
    assert result.probe_outputs == ()
    assert value.candidate.to_dict() == before
    # Candidate transitions belong to the controller, and a different proposed
    # edit cannot reuse an acknowledgment from the pinned action catalog.
    with pytest.raises(FixtureAdapterError, match="absent"):
        adapter.execute(edit_request(context_text="An unpinned replacement instruction."))


@pytest.mark.parametrize("update,match", [
    ({"operation_digest": "f" * 64}, "absent"),
    ({"candidate_digest": "f" * 64}, "candidate"),
    ({"operation_kind": "edit"}, "operation kind"),
])
def test_script_refuses_unmatched_operation_or_candidate(update: dict[str, Any], match: str) -> None:
    value = request()
    with pytest.raises(FixtureAdapterError, match=match):
        ScriptedSessionAdapter(script_for(value, **update)).execute(value)


@pytest.mark.parametrize("problem_ids", [("practice.two", "practice.one"), ("practice.one",), ("hidden.audit", "practice.two")])
def test_probe_catalog_requires_exact_selected_practice_coverage(problem_ids: tuple[str, ...]) -> None:
    value = request(probe=True)
    outputs = [ProbeOutput(problem_id=key, text="literal").to_dict() for key in problem_ids]
    with pytest.raises(FixtureAdapterError, match="selected practice"):
        ScriptedSessionAdapter(script_for(value, probe_outputs=outputs)).execute(value)


def test_request_cannot_inject_scorer_gold_into_practice_projection() -> None:
    raw = request(probe=True).to_dict()
    raw["practice"][0]["answer"] = "2"
    with pytest.raises(ValueError, match="answer"):
        SessionAdapterRequest.model_validate_json(json.dumps(raw))


def test_missing_or_incorrect_probe_answers_are_returned_not_graded_by_adapter() -> None:
    value = request(probe=True)
    outputs = [ProbeOutput(problem_id="practice.one", text="").to_dict(),
               ProbeOutput(problem_id="practice.two", text=r"\boxed{999}").to_dict()]
    result = ScriptedSessionAdapter(script_for(value, probe_outputs=outputs)).execute(value)
    assert result.probe_outputs[0].text == ""
    assert result.probe_outputs[1].text == r"\boxed{999}"


@pytest.mark.parametrize("updates", [
    {"execution_kind": "vllm"}, {"model_execution_performed": True}, {"gpu_execution_deferred": False},
])
def test_script_fixture_labels_cannot_be_replaced(updates: dict[str, Any]) -> None:
    raw = {**script_for(request()).to_dict(), **updates}
    with pytest.raises(ValueError):
        SessionFixtureScript.model_validate_json(json.dumps(raw))


def test_adapter_revalidates_programmatically_constructed_script() -> None:
    altered = script_for(request()).model_copy(update={"model_execution_performed": True})
    with pytest.raises(ValueError, match="model_execution_performed"):
        ScriptedSessionAdapter(altered)


def test_duplicate_operation_or_probe_identity_rejected() -> None:
    script = script_for(request())
    with pytest.raises(ValueError, match="duplicate scripted operation"):
        SessionFixtureScript(entries=(script.entries[0], script.entries[0]))
    value = request(probe=True)
    repeated = ProbeOutput(problem_id="practice.one", text="literal").to_dict()
    with pytest.raises(ValueError, match="duplicate prerecorded"):
        script_for(value, probe_outputs=[repeated, repeated])


@pytest.mark.parametrize("seconds", [-1.0, float("nan"), float("inf"), 86401.0])
def test_invalid_simulated_time_rejected(seconds: float) -> None:
    with pytest.raises(ValueError):
        script_for(request(), simulated_seconds=seconds)


def test_only_successful_probes_accept_response_payloads() -> None:
    output = ProbeOutput(problem_id="practice.one", text="literal").to_dict()
    with pytest.raises(ValueError, match="only successful probe"):
        script_for(request(), probe_outputs=[output])
    with pytest.raises(ValueError, match="only successful probe"):
        script_for(request(probe=True), outcome="failure")
    with pytest.raises(ValueError, match="require prerecorded"):
        script_for(request(probe=True), probe_outputs=[])


def test_source_semantics_participate_in_adapter_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ScriptedSessionAdapter(script_for(request()))
    original, before = Path.read_bytes, adapter.identity_digest

    def changed(path: Path) -> bytes:
        payload = original(path)
        return payload + b"\n" if path.name == "session_models.py" else payload

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert adapter.identity_digest != before


def test_script_loader_rejects_duplicate_keys_and_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "script.json"
    script = script_for(request())
    path.write_text(json.dumps(script.to_dict()))
    assert load_session_fixture_script(path) == script
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        load_session_fixture_script(link)
    path.write_text('{"schema_version":1,' + json.dumps(script.to_dict())[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_session_fixture_script(path)


def test_script_loader_enforces_byte_bound_before_json_parsing(tmp_path: Path) -> None:
    path = tmp_path / "too-large.json"
    path.write_bytes(b" " * (MAX_SESSION_SCRIPT_BYTES + 1))
    with pytest.raises(ValueError, match="byte budget"):
        load_session_fixture_script(path)
