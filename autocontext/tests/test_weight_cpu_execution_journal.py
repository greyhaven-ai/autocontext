from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from test_weight_cpu_search import builder, files, forbid, rehash

from autocontext.weight_evolution import cpu_execution_journal as journal_module
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_execution_journal import CpuExecutionError, ExecutionJournal, _ExecutionBlocked, prepare
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionUsage
from autocontext.weight_evolution.cpu_execution_outcomes import build_cpu_execution_contract
from autocontext.weight_evolution.cpu_search import load_cpu_search
from autocontext.weight_evolution.session import _read_record


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path, Any, TensorCandidateRegistry]:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    builder().build_bundle(bundle)
    spec = load_cpu_search(bundle)
    contract = build_cpu_execution_contract(scope="search", design_digest=spec.digest, workflow_digest=spec.digest,
                workflow_implementation_digest=spec.implementation_digest, capacity=CpuExecutionUsage(**spec.required_usage))
    output.mkdir()
    (output / ".cpu-execution.lock").write_bytes(b"")
    prepare(output, {"contract": contract.to_dict()}, write=True, operation_count=64, search=True)
    registry = TensorCandidateRegistry(spec.candidates, input_path=bundle / "base.safetensors",
                                       output_dir=output / "materializations")
    # Isolate durable-operation behavior from host identity checks; pin
    # enforcement has its own case below.
    monkeypatch.setattr(ExecutionJournal, "_current", lambda self: None)
    return spec, output, contract, registry


def operation(context: tuple[Any, Path, Any, TensorCandidateRegistry], *, replay: bool = False) -> tuple[Any, ...]:
    spec, output, contract, registry = context
    journal = ExecutionJournal(spec, output, contract, registry, replay=replay)
    candidate = spec.baseline
    args = ("materialize", f"materialize-{candidate.candidate_id}", {"candidate": candidate.to_dict()},
            {"proposals": 0, "materializations": 1, "inference_calls": 0, "input_tokens": 0, "output_tokens": 0})
    value = registry.verify(candidate.handle).to_dict()
    return journal, args, value


def test_outcome_precedes_callback_and_success_is_readonly_replayable(context: Any) -> None:
    journal, args, value = operation(context)
    root = context[1]

    def validate(raw: dict[str, Any]) -> None:
        assert _read_record(root / "outcomes" / "00000000.json")["value"] == value
        assert not (root / "checks" / "00000000.json").exists()
        assert raw == value

    assert journal.perform(*args, lambda: value, validate) == value
    assert journal.available == journal.index == 1
    before = files(root)
    replay, args, value = operation(context, replay=True)
    assert replay.perform(*args, forbid, lambda raw: None) == value
    assert files(root) == before
    assert replay.spent["materializations"] == 1 and replay.terminal_result["status"] == "completed"


def test_live_callback_failure_keeps_returned_value_and_separate_failure(context: Any) -> None:
    journal, args, _ = operation(context)
    root = context[1]

    def reject(raw: dict[str, Any]) -> None:
        assert _read_record(root / "outcomes" / "00000000.json")["value"] == {"invalid-return": True}
        raise ValueError("fixture live native callback rejected return")

    with pytest.raises(_ExecutionBlocked):
        journal.perform(*args, lambda: {"invalid-return": True}, reject)
    assert _read_record(root / "outcomes" / "00000000.json")["status"] == "completed"
    check = _read_record(root / "checks" / "00000000.json")
    assert check["status"] == "rejected" and check["failure"]["observations"]["returncode"] is None
    assert journal.observation_inventory[-1]["outcome_status"] == "completed"
    assert journal.observation_inventory[-1]["check_status"] == "rejected"
    assert journal.observation_inventory[-1]["result_status"] == "failed"
    assert "native callback" in check["failure"]["exceptions"][0]["message"]["text"]
    replay, args, _ = operation(context, replay=True)
    with pytest.raises(_ExecutionBlocked):
        replay.perform(*args, forbid, forbid)
    assert replay.status == "failed" and replay.spent == journal.spent


def test_noninference_exception_has_no_invented_process_observation(context: Any) -> None:
    journal, args, _ = operation(context)

    def fail() -> dict[str, Any]:
        raise OSError("fixture materialization failure")

    with pytest.raises(_ExecutionBlocked):
        journal.perform(*args, fail, forbid)
    outcome = journal.terminal_outcome
    assert outcome["status"] == "failed" and outcome["measured"] is None
    assert all(value is None for value in outcome["failure"]["observations"].values())
    assert not list((context[1] / "checks").iterdir())


@pytest.mark.parametrize("stage", ["outcomes", "checks", "results"])
@pytest.mark.parametrize("after", [False, True])
def test_interrupted_publication_never_reexecutes_claimed_operation(context: Any, monkeypatch: pytest.MonkeyPatch,
                                                                  stage: str, after: bool) -> None:
    journal, args, value = operation(context)
    root = context[1]
    original = journal_module._immutable_json
    calls: list[bool] = []

    def publish(path: Path, raw: dict[str, Any]) -> None:
        if path.parent.name == stage:
            if after:
                original(path, raw)
            raise KeyboardInterrupt("fixture publication interruption")
        original(path, raw)

    def execute() -> dict[str, Any]:
        calls.append(True)
        return value

    with monkeypatch.context() as patch:
        patch.setattr(journal_module, "_immutable_json", publish)
        with pytest.raises(KeyboardInterrupt):
            journal.perform(*args, execute, lambda raw: None)
    before = files(root)
    replay, args, value = operation(context, replay=True)
    if stage == "outcomes" and not after:
        with pytest.raises(_ExecutionBlocked):
            replay.perform(*args, forbid, forbid)
        assert replay.status == "uncertain" and replay.terminal_outcome is None
    else:
        assert replay.perform(*args, forbid, lambda raw: None) == value
        if not (root / "results" / "00000000.json").exists():
            assert replay.reconciliation_needed and replay.terminal_result is None
    assert files(root) == before and calls == [True]
    if stage != "outcomes" or after:
        repaired, args, value = operation(context)
        assert repaired.perform(*args, forbid, lambda raw: None) == value
        assert (root / "results" / "00000000.json").exists() and calls == [True]


def test_saved_outcome_drift_is_not_recast_as_historical_failure(context: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    journal, args, value = operation(context)
    original = journal_module._immutable_json

    def stop(path: Path, raw: dict[str, Any]) -> None:
        original(path, raw)
        if path.parent.name == "outcomes":
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(journal_module, "_immutable_json", stop)
        with pytest.raises(KeyboardInterrupt):
            journal.perform(*args, lambda: value, lambda raw: None)
    before = files(context[1])

    def drift(raw: dict[str, Any]) -> None:
        raise ValueError("current checkpoint metadata changed")

    for readonly in (True, False):
        replay, args, _ = operation(context, replay=readonly)
        with pytest.raises(CpuExecutionError, match="no longer passes"):
            replay.perform(*args, forbid, drift)
        assert files(context[1]) == before


@pytest.mark.parametrize("directory", ["claims", "outcomes", "checks", "results"])
def test_rehashed_binding_tampering_is_rejected(context: Any, directory: str) -> None:
    journal, args, value = operation(context)
    journal.perform(*args, lambda: value, lambda raw: None)
    path = context[1] / directory / "00000000.json"
    raw = _read_record(path)
    raw["contract_digest"] = "0" * 64
    rehash(path, raw)
    replay, args, _ = operation(context, replay=True)
    with pytest.raises(ValueError):
        replay.perform(*args, forbid, lambda raw: None)


def test_source_pin_drift_stops_before_next_claim(context: Any) -> None:
    journal, args, value = operation(context)

    def changed() -> None:
        raise CpuExecutionError("fixture source pin drift")

    journal._current = changed
    with pytest.raises(CpuExecutionError, match="source pin drift"):
        journal.perform(*args, forbid, forbid)
    assert not list((context[1] / "claims").iterdir())


def test_inventory_fails_at_bound_before_sorting_or_parsing(tmp_path: Path) -> None:
    for index in range(4):
        (tmp_path / str(index)).write_text("not-json")
    with pytest.raises(CpuExecutionError, match="inventory exceeds"):
        journal_module.entries(tmp_path, 3)


def test_claim_reservation_cannot_overrun_capacity(context: Any) -> None:
    journal, args, _ = operation(context)
    args = (*args[:3], {**args[3], "materializations": context[2].capacity.materializations + 1})
    with pytest.raises(CpuExecutionError, match="capacity"):
        journal.perform(*args, forbid, forbid)
    assert not list((context[1] / "claims").iterdir())


def test_exact_noninference_inputs_are_validated_before_claim_or_callback(context: Any) -> None:
    journal, args, _ = operation(context)
    bad_reserved = {**args[3], "materializations": 0}
    with pytest.raises(ValueError):
        journal.perform(*args[:3], bad_reserved, forbid, forbid)
    assert not list((context[1] / "claims").iterdir())


@pytest.mark.parametrize("linked", [False, True])
def test_crashed_publication_staging_is_retained_but_never_promoted(context: Any, monkeypatch: pytest.MonkeyPatch,
                                                                  linked: bool) -> None:
    journal, args, value = operation(context)
    root = context[1]
    original = journal_module._immutable_json
    staged = root / "outcomes" / ".session-publish-crashed-fixture"

    def crash(path: Path, raw: dict[str, Any]) -> None:
        original(path, raw)
        if path.parent.name == "outcomes":
            os.link(path, staged)
            if not linked:
                path.unlink()
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(journal_module, "_immutable_json", crash)
        with pytest.raises(KeyboardInterrupt):
            journal.perform(*args, lambda: value, lambda raw: None)
    before = files(root)
    prepare(root, {"contract": context[2].to_dict()}, write=False, operation_count=64, search=True)
    replay, args, _ = operation(context, replay=True)
    if linked:
        assert replay.perform(*args, forbid, lambda raw: None) == value
        assert replay.reconciliation_needed
    else:
        with pytest.raises(_ExecutionBlocked):
            replay.perform(*args, forbid, forbid)
        assert replay.terminal_outcome is None
    assert files(root) == before
    resumed, args, _ = operation(context)
    if linked:
        assert resumed.perform(*args, forbid, lambda raw: None) == value
    else:
        with pytest.raises(_ExecutionBlocked):
            resumed.perform(*args, forbid, forbid)
    assert staged.read_bytes() == before["outcomes/" + staged.name]


def test_staging_allowance_is_bounded_and_never_parsed(tmp_path: Path) -> None:
    for index in range(9):
        (tmp_path / f".session-publish-{index}").write_text("not-json")
    with pytest.raises(CpuExecutionError, match="staging inventory"):
        journal_module.entries(tmp_path, 0)
