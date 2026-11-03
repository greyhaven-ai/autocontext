from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import session as session_module
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_models import (
    ArtifactSource,
    CheckpointArtifact,
    ComparisonCase,
    ContextArtifact,
    DecodingArtifact,
)
from autocontext.weight_evolution.datasets import EvaluationProblem
from autocontext.weight_evolution.scorers import ScorerInfrastructureError
from autocontext.weight_evolution.session import EditingSession, SessionBlockedError, SessionError, SessionIntegrityError
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import (
    AdapterResult,
    CandidateRevision,
    EditOperation,
    InspectOperation,
    ProbeOperation,
    ProbeOutput,
    SessionAdapterRequest,
    SessionBaseline,
    SessionDecoding,
    SessionLimits,
    SessionSpec,
    SessionUsage,
    initial_candidate,
)


class RecordingAdapter:
    execution_kind = "fixture"

    def __init__(self, *, mode: str = "return", response: str = r"\boxed{2}") -> None:
        self.mode = mode
        self.response = response
        self.calls: list[SessionAdapterRequest] = []
        self.claim_dir: Path | None = None

    @property
    def identity_digest(self) -> str:
        return canonical_digest({"adapter": "test-literal-fixture", "mode": self.mode, "response": self.response})

    def execute(self, request: SessionAdapterRequest) -> AdapterResult:
        self.calls.append(request)
        if self.claim_dir is not None:
            assert len(list(self.claim_dir.glob("*.json"))) == len(self.calls)
        if self.mode == "failure" or (self.mode == "failure-on-first" and len(self.calls) == 1):
            raise RuntimeError("known fixture failure")
        if self.mode == "interruption":
            raise KeyboardInterrupt("ambiguous fixture interruption")
        outputs = tuple(ProbeOutput(problem_id=case.problem_id, text=self.response) for case in request.practice) \
            if isinstance(request.operation, ProbeOperation) else ()
        return AdapterResult(operation_digest="f" * 64 if self.mode == "wrong-operation" else request.operation.digest,
                             probe_outputs=outputs, message="literal fixture response",
                             simulated_seconds=100.0 if self.mode == "over-budget" else 0.25)


def bundle() -> SessionDataBundle:
    return SessionDataBundle(
        practice=(ComparisonCase(dataset="math500", problem=EvaluationProblem(
            problem_id="practice.add", text="Add one and one, please. ü", answer="2")),),
        confirmation=(ComparisonCase(dataset="math500", problem=EvaluationProblem(
            problem_id="confirmation.subtract", text="SEALED CONFIRMATION subtract three from seven", answer="4")),),
        final_audit=(ComparisonCase(dataset="math500", problem=EvaluationProblem(
            problem_id="audit.multiply", text="SEALED FINAL AUDIT multiply five by two", answer="10")),),
    )


def spec_for(adapter: RecordingAdapter, *, mechanism: str = "C", limits: dict[str, Any] | None = None) -> SessionSpec:
    baseline = ArtifactSource(kind="baseline_fixture")
    limit_values = {"max_operations": 10, "max_editor_input_bytes": 100000,
                    "max_editor_output_bytes": 100000, "max_probe_input_bytes": 100000,
                    "max_probe_output_bytes": 100000, "max_simulated_seconds": 10.0, **(limits or {})}
    return SessionSpec.model_validate_json(json.dumps({
        "session_id": "controller-test", "mechanism": mechanism,
        "baseline": SessionBaseline(
            base_checkpoint=CheckpointArtifact(artifact_id="base-weights", content="base descriptor", source=baseline),
            base_context=ContextArtifact(artifact_id="base-context", text="base context", source=baseline),
            base_decoding=DecodingArtifact(artifact_id="base-decoding", temperature=0.6, top_p=0.95, top_k=20,
                                           presence_penalty=1.5, source=baseline),
        ).to_dict(),
        "data_digest": bundle().digest, "scorer_digest": SyntheticComparisonScorer().lock_digest,
        "adapter_digest": adapter.identity_digest, "limits": limit_values,
    }))


def reserve(*, probe: bool = False, **updates: Any) -> SessionUsage:
    return SessionUsage(**{"operations": 1, "editor_input_bytes": 10000, "editor_output_bytes": 1000,
                           "probe_input_bytes": 1000 if probe else 0, "probe_output_bytes": 1000 if probe else 0,
                           "simulated_seconds": 1.0, **updates})


def start(tmp_path: Path, adapter: RecordingAdapter | None = None, **spec_options: Any) -> EditingSession:
    fixture_adapter = adapter or RecordingAdapter()
    return EditingSession(spec=spec_for(fixture_adapter, **spec_options), data=bundle(),
                          output_dir=tmp_path / "session", adapter=fixture_adapter)


def reopen(session: EditingSession) -> EditingSession:
    return EditingSession(spec=session.spec, data=session.data, output_dir=session.output_dir, adapter=session.adapter)


def current(session: EditingSession) -> CandidateRevision:
    return CandidateRevision.model_validate_json(json.dumps(session.snapshot()["candidate"]))


def edit(session: EditingSession, *, operation_id: str = "edit-1", **values: Any) -> EditOperation:
    if not values:
        field = {"C": "context_text", "W": "weight_descriptor", "D": "decoding"}[session.spec.mechanism]
        value = SessionDecoding(temperature=0.2, top_p=0.9, top_k=10, presence_penalty=0.0) \
            if field == "decoding" else "changed synthetic candidate"
        values = {field: value}
    return EditOperation(operation_id=operation_id, candidate_digest=current(session).digest,
                         reservation=reserve(), **values)


def probe(session: EditingSession, *, operation_id: str = "probe-1") -> ProbeOperation:
    return ProbeOperation(operation_id=operation_id, candidate_digest=current(session).digest,
                          reservation=reserve(probe=True), problem_ids=("practice.add",))


def inspect(session: EditingSession, *, operation_id: str = "inspect-1") -> InspectOperation:
    return InspectOperation(operation_id=operation_id, candidate_digest=current(session).digest, reservation=reserve())


def read_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())["payload"]


def replace_payload(path: Path, payload: dict[str, Any], *, rehash: bool = True) -> None:
    before = json.loads(path.read_text())
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload) if rehash else before["digest"]}))


@pytest.mark.parametrize("mechanism,artifact_type", [("C", ContextArtifact), ("W", CheckpointArtifact),
                                                     ("D", DecodingArtifact)])
def test_typed_edits_probe_seal_and_replay_without_dispatch(tmp_path: Path, mechanism: str, artifact_type: type) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter, mechanism=mechanism)
    baseline = initial_candidate(session.spec)
    changed = edit(session)
    receipt = session.run(changed)
    assert receipt["status"] == "completed"
    assert receipt["candidate"]["parent_digest"] == baseline.digest
    assert receipt["candidate"]["operation_digest"] == changed.digest
    assert receipt["candidate"]["revision"] == 1
    probing = probe(session)
    feedback = session.run(probing)
    assert feedback["feedback"][0]["score"] == 1
    artifact = session.export_candidate()
    assert isinstance(artifact, artifact_type)
    assert artifact.source.source_problem_ids == ("practice.add",)
    assert artifact.source.declared_search_calls == 2
    assert artifact.source.declared_search_units == 24000
    restarted = reopen(session)
    assert restarted.snapshot()["status"] == "sealed"
    assert restarted.export_candidate() == artifact
    assert restarted.run(changed) == receipt
    assert restarted.run(probing) == feedback
    assert len(adapter.calls) == 2
    with pytest.raises(SessionError, match="sealed"):
        restarted.run(inspect(restarted, operation_id="after-seal"))


def test_claim_is_durable_before_adapter_dispatch_and_request_has_no_hidden_payload(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter)
    adapter.claim_dir = session.output_dir / "claims"
    operation = InspectOperation(operation_id="practice-inspection", candidate_digest=current(session).digest,
                                 reservation=reserve(), target="practice", problem_ids=("practice.add",))
    session.run(operation)
    request = adapter.calls[0].to_dict()
    assert set(request["practice"][0]) == {"dataset", "problem_id", "text"}
    serialized = json.dumps(request)
    for marker in ('"answer"', '"instructions"', "SEALED", "confirmation.subtract", "audit.multiply"):
        assert marker not in serialized
    assert read_payload(session.output_dir / "claims/00000001.json")["request"] == request


@pytest.mark.parametrize("response", ["", r"\boxed{999}", "no usable answer"])
def test_empty_and_wrong_answers_score_zero_without_becoming_execution_failure(tmp_path: Path, response: str) -> None:
    session = start(tmp_path, RecordingAdapter(response=response))
    session.run(edit(session))
    receipt = session.run(probe(session))
    assert receipt["status"] == "completed" and receipt["feedback"][0]["score"] == 0
    assert session.export_candidate().source.declared_search_calls == 2


def test_only_trusted_practice_feedback_is_visible_to_later_calls(tmp_path: Path) -> None:
    adapter = RecordingAdapter(response=r"\boxed{999}")
    session = start(tmp_path, adapter)
    session.run(probe(session))
    session.run(edit(session))
    feedback = adapter.calls[1].feedback
    assert len(feedback) == 1 and feedback[0].score == 0
    assert feedback[0].problem_id == "practice.add"
    assert feedback[0].candidate_digest == initial_candidate(session.spec).digest
    assert set(feedback[0].to_dict()) == {"problem_id", "score", "response_sha256", "candidate_digest"}


@pytest.mark.parametrize("hidden", ["confirmation.subtract", "audit.multiply", "unknown"])
@pytest.mark.parametrize("kind", ["inspect", "probe"])
def test_hidden_or_unknown_ids_reject_before_claim(tmp_path: Path, hidden: str, kind: str) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter)
    operation = ProbeOperation(operation_id="hidden", candidate_digest=current(session).digest,
                               reservation=reserve(probe=True), problem_ids=(hidden,)) if kind == "probe" else \
        InspectOperation(operation_id="hidden", candidate_digest=current(session).digest, reservation=reserve(),
                         target="practice", problem_ids=(hidden,))
    with pytest.raises(ValueError, match="practice split"):
        session.run(operation)
    assert session.snapshot()["reserved_usage"]["operations"] == 0 and not adapter.calls


def test_forbidden_stale_and_constructed_invalid_edits_do_not_dispatch(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter)
    forbidden = edit(session, weight_descriptor="forbidden W edit in C session")
    with pytest.raises(ValueError, match="forbidden"):
        session.run(forbidden)
    valid = edit(session)
    invalid = valid.model_copy(update={"weight_descriptor": "another surface"})
    with pytest.raises(ValueError, match="exactly one"):
        session.run(invalid)
    session.run(valid)
    stale = valid.model_copy(update={"operation_id": "stale"})
    with pytest.raises(ValueError, match="stale"):
        session.run(stale)
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("field", list(SessionLimits.model_fields))
def test_every_reservation_cap_accumulates_across_restarts(tmp_path: Path, field: str) -> None:
    is_probe = "probe" in field
    reserved = reserve(probe=is_probe)
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter, limits={field: getattr(reserved, field.removeprefix("max_"))})
    first = probe(session) if is_probe else inspect(session)
    session.run(first)
    resumed = reopen(session)
    second = probe(resumed, operation_id="second") if is_probe else inspect(resumed, operation_id="second")
    with pytest.raises(ValueError, match="budget"):
        resumed.run(second)
    assert len(adapter.calls) == 1
    assert resumed.snapshot()["reserved_usage"] == reserved.to_dict()
    assert resumed.snapshot()["observed_usage"]["simulated_seconds"] == 0.25


def test_request_input_budget_is_checked_before_claim_or_dispatch(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter)
    operation = InspectOperation(operation_id="too-small", candidate_digest=current(session).digest,
                                 reservation=reserve(editor_input_bytes=1))
    with pytest.raises(SessionError, match="input exceeds"):
        session.run(operation)
    assert not adapter.calls and not list((session.output_dir / "claims").iterdir())


def test_combined_export_byte_cap_rejects_before_claim_even_when_individual_caps_fit(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter, limits={"max_editor_input_bytes": 1000000000,
                                             "max_editor_output_bytes": 1000000000})
    operation = InspectOperation(operation_id="aggregate-too-large", candidate_digest=current(session).digest,
                                 reservation=reserve(editor_input_bytes=500000001, editor_output_bytes=500000000))
    with pytest.raises(SessionError, match="aggregate reserved-byte"):
        session.run(operation)
    assert not adapter.calls and session.snapshot()["reserved_usage"]["operations"] == 0


def test_export_costs_include_failed_reserved_work_and_cannot_be_caller_supplied(tmp_path: Path) -> None:
    adapter = RecordingAdapter(mode="failure-on-first")
    session = start(tmp_path, adapter)
    assert session.run(inspect(session))["status"] == "failed"
    session.run(edit(session))
    session.run(probe(session))
    artifact = session.export_candidate()
    assert artifact.source.declared_search_calls == 3
    assert artifact.source.declared_search_units == 35000
    assert session.snapshot()["observed_usage"]["operations"] == 2
    assert artifact.source.source_problem_ids == ("practice.add",)


@pytest.mark.parametrize("mode", ["failure", "wrong-operation", "over-budget"])
def test_known_failure_consumes_full_reservation_and_is_never_retried(tmp_path: Path, mode: str) -> None:
    adapter = RecordingAdapter(mode=mode)
    session = start(tmp_path, adapter)
    operation = inspect(session)
    failure = session.run(operation)
    assert failure["status"] == "failed" and failure["feedback"] == []
    assert failure["measured_usage"] is None and failure["actual_usage_unknown"] is True
    resumed = reopen(session)
    assert resumed.run(operation) == failure and len(adapter.calls) == 1
    assert resumed.snapshot()["candidate_digest"] == initial_candidate(session.spec).digest
    assert resumed.snapshot()["reserved_usage"] == reserve().to_dict()
    assert resumed.snapshot()["observed_usage"]["operations"] == 0
    resumed.run(inspect(resumed, operation_id="permitted-new-operation"))
    assert len(adapter.calls) == 2 and resumed.snapshot()["reserved_usage"]["operations"] == 2


def test_scorer_infrastructure_failure_is_recorded_without_zero_feedback(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    session = start(tmp_path)
    operation = probe(session)

    def broken(*args: Any, **kwargs: Any) -> int:
        raise ScorerInfrastructureError("fixture checker unavailable")

    monkeypatch.setattr(session._scorer, "score_sample", broken)
    receipt = session.run(operation)
    assert receipt["status"] == "failed" and receipt["feedback"] == []
    assert receipt["error"]["type"] == "ScorerInfrastructureError"
    assert reopen(session).snapshot()["reserved_usage"] == reserve(probe=True).to_dict()


def test_ambiguous_claim_blocks_retry_new_operations_and_export_after_restart(tmp_path: Path) -> None:
    adapter = RecordingAdapter(mode="interruption")
    session = start(tmp_path, adapter)
    operation = inspect(session)
    with pytest.raises(KeyboardInterrupt, match="ambiguous"):
        session.run(operation)
    resumed = reopen(session)
    assert resumed.snapshot()["status"] == "blocked"
    assert resumed.snapshot()["reserved_usage"] == reserve().to_dict()
    assert resumed.snapshot()["operations"][0]["actual_usage_unknown"] is True
    with pytest.raises(SessionBlockedError):
        resumed.run(operation)
    with pytest.raises(SessionBlockedError):
        resumed.run(inspect(resumed, operation_id="cannot-skip"))
    with pytest.raises(SessionBlockedError):
        resumed.export_candidate()
    assert len(adapter.calls) == 1


def test_published_result_reconciles_without_redispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter)
    operation = edit(session)
    publish = session_module._immutable_json

    def interrupted(path: Path, payload: dict[str, Any]) -> None:
        if path.parent.name == "completed":
            raise KeyboardInterrupt("after result publication")
        publish(path, payload)

    with monkeypatch.context() as context:
        context.setattr(session_module, "_immutable_json", interrupted)
        with pytest.raises(KeyboardInterrupt, match="after result"):
            session.run(operation)
    assert (session.output_dir / "results/00000001.json").is_file()
    assert not (session.output_dir / "completed/00000001.json").exists()
    resumed = reopen(session)
    assert resumed.run(operation)["status"] == "completed"
    assert resumed.snapshot()["candidate"]["revision"] == 1
    assert len(adapter.calls) == 1


def test_crash_after_result_link_before_staging_unlink_reconciles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter)
    operation = edit(session)
    unlink = session_module.os.unlink

    def interrupted(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path).parent.name == "results" and Path(path).name.startswith(".session-publish-"):
            raise KeyboardInterrupt("after immutable result link")
        unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(session_module.os, "unlink", interrupted)
        with pytest.raises(KeyboardInterrupt, match="result link"):
            session.run(operation)
    assert len(list((session.output_dir / "results").iterdir())) == 2
    resumed = reopen(session)
    assert resumed.run(operation)["status"] == "completed"
    assert len(list((session.output_dir / "results").iterdir())) == 1
    assert len(adapter.calls) == 1


def test_unpublished_staging_does_not_turn_an_ambiguous_claim_into_retry(tmp_path: Path) -> None:
    adapter = RecordingAdapter(mode="interruption")
    session = start(tmp_path, adapter)
    operation = inspect(session)
    with pytest.raises(KeyboardInterrupt):
        session.run(operation)
    scratch = session.output_dir / "results/.session-publish-abcdefgh"
    scratch.write_text('{"incomplete":')
    resumed = reopen(session)
    assert not scratch.exists() and resumed.snapshot()["status"] == "blocked"
    with pytest.raises(SessionBlockedError):
        resumed.run(operation)
    assert len(adapter.calls) == 1


def test_staging_symlink_cannot_be_used_to_remove_an_external_file(tmp_path: Path) -> None:
    session = start(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("keep")
    (session.output_dir / "results/.session-publish-abcdefgh").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        session.snapshot()
    assert outside.read_text() == "keep"


def test_reused_operation_id_with_changed_payload_rejected(tmp_path: Path) -> None:
    session = start(tmp_path)
    operation = inspect(session)
    session.run(operation)
    changed = operation.model_copy(update={"reservation": reserve(editor_output_bytes=2000)})
    with pytest.raises(SessionError, match="different payload"):
        reopen(session).run(changed)


def test_concurrent_controllers_dispatch_an_operation_once(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    first = start(tmp_path, adapter)
    second = reopen(first)
    operation = inspect(first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda session: session.run(operation), (first, second)))
    assert receipts[0] == receipts[1] and len(adapter.calls) == 1


def test_export_requires_changed_candidate_and_probe_of_exact_current_revision(tmp_path: Path) -> None:
    session = start(tmp_path)
    with pytest.raises(SessionError, match="changed"):
        session.export_candidate()
    session.run(probe(session))
    session.run(edit(session))
    with pytest.raises(SessionError, match="current candidate"):
        session.export_candidate()
    session.run(probe(session, operation_id="probe-final"))
    session.run(edit(session, operation_id="back-to-base", context_text=session.spec.baseline.state.context_text))
    session.run(probe(session, operation_id="probe-back-to-base"))
    with pytest.raises(SessionError, match="changed"):
        session.export_candidate()


@pytest.mark.parametrize("directory,field,value", [
    ("claims", "previous_digest", "f" * 64),
    ("claims", "request_digest", "f" * 64),
    ("completed", "candidate_digest", "f" * 64),
    ("completed", "result_digest", "f" * 64),
    ("completed", "observed_total", SessionUsage().to_dict()),
    ("completed", "reserved_total", SessionUsage().to_dict()),
])
@pytest.mark.parametrize("rehash", [False, True])
def test_historical_receipts_requests_lineage_and_counters_are_revalidated(
    tmp_path: Path, directory: str, field: str, value: Any, rehash: bool,
) -> None:
    session = start(tmp_path)
    session.run(edit(session))
    session.run(probe(session))
    path = session.output_dir / directory / "00000001.json"
    payload = read_payload(path)
    payload[field] = value
    replace_payload(path, payload, rehash=rehash)
    with pytest.raises(SessionIntegrityError):
        session.snapshot()
    with pytest.raises(SessionIntegrityError):
        session.export_candidate()


def test_practice_feedback_is_recomputed_on_resume(tmp_path: Path) -> None:
    session = start(tmp_path, RecordingAdapter(response=""))
    session.run(probe(session))
    path = session.output_dir / "completed/00000001.json"
    payload = read_payload(path)
    payload["feedback"][0]["score"] = 1
    replace_payload(path, payload)
    with pytest.raises(SessionIntegrityError, match="feedback"):
        reopen(session)


def test_sealed_export_cannot_hide_history_edits_or_alter_provenance(tmp_path: Path) -> None:
    session = start(tmp_path)
    session.run(edit(session))
    session.run(probe(session))
    session.export_candidate()
    path = session.output_dir / "export.json"
    payload = read_payload(path)
    payload["artifact"]["source"]["declared_search_calls"] = 0
    payload["artifact_digest"] = canonical_digest(payload["artifact"])
    replace_payload(path, payload)
    with pytest.raises(SessionIntegrityError, match="sealed export"):
        session.export_candidate()


def test_source_identity_drift_blocks_resume_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = start(tmp_path)
    session.run(edit(session))
    session.run(probe(session))
    session.export_candidate()
    original = session_module.controller_provenance()
    monkeypatch.setattr(session_module, "controller_provenance", lambda: {**original, "changed": True})
    with pytest.raises(SessionIntegrityError, match="source identity"):
        reopen(session)
    with pytest.raises(SessionIntegrityError, match="source identity"):
        session.export_candidate()


@pytest.mark.parametrize("which", ["data", "adapter", "scorer", "spec"])
def test_contract_drift_cannot_reuse_a_journal(tmp_path: Path, which: str) -> None:
    adapter = RecordingAdapter()
    session = start(tmp_path, adapter)
    data = session.data
    spec = session.spec
    if which == "data":
        data = data.model_copy(update={"practice": (data.practice[0].model_copy(update={
            "problem": data.practice[0].problem.model_copy(update={"answer": "3"})}),)})
    elif which == "adapter":
        adapter = RecordingAdapter(response="different")
    elif which == "scorer":
        spec = spec.model_copy(update={"scorer_digest": "f" * 64})
    else:
        spec = spec.model_copy(update={"limits": spec.limits.model_copy(update={"max_operations": 11})})
    with pytest.raises(SessionIntegrityError):
        EditingSession(spec=spec, data=data, output_dir=session.output_dir, adapter=adapter)


@pytest.mark.parametrize("kind", ["orphan", "gap", "conflict", "extra", "symlink", "duplicate-key"])
def test_malformed_journal_layout_or_records_fail_closed(tmp_path: Path, kind: str) -> None:
    session = start(tmp_path)
    session.run(inspect(session))
    root = session.output_dir
    if kind == "orphan":
        (root / "claims/00000001.json").unlink()
    elif kind == "gap":
        (root / "claims/00000001.json").rename(root / "claims/00000002.json")
    elif kind == "conflict":
        (root / "failures/00000001.json").write_text((root / "completed/00000001.json").read_text())
    elif kind == "extra":
        (root / "unexpected.json").write_text("{}")
    elif kind == "symlink":
        (root / "completed/00000001.json").unlink()
        (root / "completed/00000001.json").symlink_to(root / "results/00000001.json")
    else:
        (root / "completed/00000001.json").write_text('{"payload":{},"payload":{},"digest":"a"}')
    with pytest.raises(ValueError):
        reopen(session)


def test_symlink_root_and_lock_paths_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    adapter = RecordingAdapter()
    with pytest.raises(SessionIntegrityError, match="symlink"):
        EditingSession(spec=spec_for(adapter), data=bundle(), output_dir=linked, adapter=adapter)
    session = start(tmp_path, adapter)
    (session.output_dir / ".session.lock").unlink()
    (session.output_dir / ".session.lock").symlink_to(session.output_dir / "manifest.json")
    with pytest.raises(SessionIntegrityError, match="symlink"):
        session.snapshot()
