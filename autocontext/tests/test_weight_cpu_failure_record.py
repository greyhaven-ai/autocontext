from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_tasks import _task_payloads
from autocontext.weight_evolution.cpu_failure_models import CpuFailureContract, CpuFailureRecord
from autocontext.weight_evolution.cpu_failure_record import (
    build_cpu_failure_record,
    capture_observations,
    validate_cpu_failure_record,
)
from autocontext.weight_evolution.cpu_models import CpuDecoding, CpuInferenceRequest, CpuModelContract, CpuWorkerLimits
from autocontext.weight_evolution.cpu_runner import CpuRunnerError


@pytest.fixture
def claim() -> dict[str, Any]:
    requests = [CpuInferenceRequest(request_id=f"confirmation-CW.{index}",
        model=CpuModelContract(vocabulary=("token0", "token1"), output_tokens=("zero", "one")),
        checkpoint_sha256="d" * 64, checkpoint_size_bytes=256, prompt=prompt,
        decoding=CpuDecoding(seed=31), limits=CpuWorkerLimits(max_input_tokens=8))
        for index, prompt in enumerate(("token0 token1 token0", "token1 token0 token1"))]
    return {"schema_version": 1, "study_digest": "b" * 64, "runtime_digest": "c" * 64,
            "index": 33, "kind": "inference", "label": "confirmation-CW",
            "input": {"candidate_digest": "e" * 64, "case_ids": ["confirmation-01", "confirmation-02"],
                      "requests": [request.to_dict() for request in requests]},
            "reserved": {"proposals": 0, "materializations": 0, "inference_calls": 2, "input_tokens": 6, "output_tokens": 2},
            "previous_result_digest": "f" * 64}


def result(claim: dict[str, Any], *, status: str = "failed") -> dict[str, Any]:
    return {"operation_digest": canonical_digest(claim), "status": status,
            **({"error_type": "CpuRunnerError"} if status == "failed" else {"value": {}})}


def build(claim: dict[str, Any] | None, outcome: dict[str, Any] | None, **kwargs: Any) -> CpuFailureRecord:
    contract = kwargs.pop("contract", CpuFailureContract())
    return build_cpu_failure_record(contract, supervisor_digest="a" * 64, run_id="task-01.repeat-01.fixed-order-v1",
        study_digest="b" * 64, runtime_digest="c" * 64, terminal_claim=claim, terminal_result=outcome, **kwargs)


def validate(value: CpuFailureRecord, claim: dict[str, Any] | None, outcome: dict[str, Any] | None) -> CpuFailureRecord:
    return validate_cpu_failure_record(value, value.contract, supervisor_digest="a" * 64,
        run_id="task-01.repeat-01.fixed-order-v1", study_digest="b" * 64, runtime_digest="c" * 64,
        terminal_claim=claim, terminal_result=outcome)


def test_live_capture_binds_exact_terminal_identities_without_raw_prompts(claim: dict[str, Any]) -> None:
    outcome = result(claim)
    value = build(claim, outcome, exception=CpuRunnerError("worker rejected response"))
    assert value.terminal_claim_digest == canonical_digest(claim)
    assert value.terminal_result_digest == canonical_digest(outcome)
    assert (value.operation_index, value.operation_kind, value.operation_label) == (33, "inference", "confirmation-CW")
    assert value.operation_input_digest == canonical_digest(claim["input"])
    assert value.terminal_status == "failed" and value.observation_kind == "live-exception"
    assert value.requests[0].declared_limits.memory_mb == 512
    assert value.requests[0].request_digest == CpuInferenceRequest.model_validate_json(
        json.dumps(claim["input"]["requests"][0])).digest
    assert value.checkpoints[0].sha256 == "d" * 64
    encoded = json.dumps(value.to_dict())
    assert '"prompt"' not in encoded
    assert all(request["prompt"] not in encoded for request in claim["input"]["requests"])
    assert validate(value, claim, outcome) == value


def test_explicit_cause_and_suppressed_implicit_context_are_both_preserved(claim: dict[str, Any]) -> None:
    worker = CpuRunnerError("worker response invalid")
    cleanup = OSError("cleanup failed")
    wrapper = RuntimeError("parent supervisor failed")
    wrapper.__cause__, wrapper.__context__, wrapper.__suppress_context__ = worker, cleanup, True
    value = build(claim, result(claim), exception=wrapper)
    assert [node.exception_type for node in value.exceptions] == ["RuntimeError", "CpuRunnerError", "OSError"]
    assert (value.exceptions[0].cause_id, value.exceptions[0].context_id) == (1, 2)
    assert value.exceptions[0].suppress_context
    assert not value.exception_cycle_detected and not value.exception_chain_truncated


def test_cleanup_error_keeps_primary_failure_in_implicit_context(claim: dict[str, Any]) -> None:
    observed = None
    try:
        try:
            raise CpuRunnerError("primary worker failure")
        finally:
            raise OSError("cleanup masking primary failure")
    except OSError as exc:
        observed = exc
    assert observed is not None
    value = build(claim, result(claim), exception=observed)
    assert value.exceptions[0].exception_type == "OSError"
    assert value.exceptions[0].context_id == 1
    assert value.exceptions[1].message.text == "primary worker failure"


def test_cycles_shared_nodes_and_truncated_edges_are_explicit(claim: dict[str, Any]) -> None:
    first, second = RuntimeError("outer"), ValueError("inner")
    first.__cause__ = first.__context__ = second
    second.__context__ = first
    value = build(claim, result(claim), exception=first)
    assert len(value.exceptions) == 2 and value.exception_cycle_detected
    assert value.exceptions[0].cause_id == value.exceptions[0].context_id == 1
    assert value.exceptions[1].context_id == 0
    limited = build(claim, result(claim), exception=first, contract=CpuFailureContract(max_exception_nodes=1))
    assert len(limited.exceptions) == 1 and limited.exception_chain_truncated
    assert limited.exceptions[0].cause_truncated and limited.exceptions[0].context_truncated
    assert not limited.exception_cycle_detected


def test_message_rendering_failure_does_not_mask_original_class(claim: dict[str, Any]) -> None:
    class BrokenMessage(Exception):
        def __str__(self) -> str:
            raise KeyboardInterrupt("message rendering failed")

    value = build(claim, result(claim), exception=BrokenMessage())
    assert value.exceptions[0].exception_type == "BrokenMessage"
    assert value.exceptions[0].message.rendering_failed
    assert value.exceptions[0].message.text == "[exception message unavailable]"


def test_utf8_messages_and_streams_are_bounded_and_prompts_redacted(claim: dict[str, Any]) -> None:
    contract = CpuFailureContract(max_message_bytes=32, max_stream_bytes=32)
    prompt = claim["input"]["requests"][0]["prompt"]
    observed = capture_observations(contract, stdout=(prompt + "🧪" * 100).encode(), stderr=b"bad\xff" * 100,
                                    redact=(prompt,), returncode=3, elapsed_seconds=0.2, peak_rss_bytes=42)
    value = build(claim, result(claim), exception=RuntimeError(prompt + "🧪" * 100),
                  contract=contract, observations=observed)
    message = value.exceptions[0].message
    assert message.truncated and message.redacted and len(message.text.encode()) <= 32
    assert value.observations.stdout is not None and value.observations.stdout.redacted
    assert value.observations.stderr is not None and value.observations.stderr.encoding_replaced
    assert value.observations.stdout.truncated and value.observations.stderr.truncated
    assert value.observations.returncode == 3 and value.observations.signal is None
    assert validate(value, claim, result(claim)) == value


def test_retained_class_only_and_uncertain_missing_observation_do_not_invent_cause(claim: dict[str, Any]) -> None:
    retained = build(claim, result(claim))
    assert retained.observation_kind == "retained-class-only" and retained.retained_error_type == "CpuRunnerError"
    assert not retained.exceptions
    missing = build(claim, None)
    assert missing.observation_kind == "observation-lost" and missing.terminal_status == "uncertain"
    assert missing.terminal_result_digest is None and missing.retained_error_type is None
    interrupted = build(claim, None, exception=KeyboardInterrupt("worker outcome unavailable"))
    assert interrupted.observation_kind == "live-exception" and interrupted.terminal_status == "uncertain"
    for value in (retained, missing, interrupted):
        assert all(field is None for field in value.observations.to_dict().values())
        assert validate(value, claim, result(claim) if value is retained else None) == value


def test_parent_failure_after_completed_operation_does_not_relabel_native_result(claim: dict[str, Any]) -> None:
    outcome = result(claim, status="completed")
    value = build(claim, outcome, exception=OSError("publication failed after operation completed"))
    assert value.terminal_status == "completed" and value.retained_error_type is None
    assert value.attribution == "terminal-binding-does-not-attribute-failure-to-operation"
    assert validate(value, claim, outcome) == value


@pytest.mark.parametrize("mutation", ["claim-index", "claim-input", "claim-runtime", "result-binding", "result-class",
    "missing-result", "parent-run", "parent-spec", "contract", "request-limit", "checkpoint", "prompt-in-message"])
def test_replay_validation_rejects_binding_and_schema_tampering(claim: dict[str, Any], mutation: str) -> None:
    outcome = result(claim)
    value = build(claim, outcome, exception=CpuRunnerError("observed failure"))
    changed_claim, changed_result = copy.deepcopy(claim), copy.deepcopy(outcome)
    updates: dict[str, Any] = {}
    if mutation == "claim-index":
        changed_claim["index"] += 1
    elif mutation == "claim-input":
        changed_claim["input"]["requests"][0]["prompt"] = "token1 token1"
    elif mutation == "claim-runtime":
        changed_claim["runtime_digest"] = "0" * 64
    elif mutation == "result-binding":
        changed_result["operation_digest"] = "0" * 64
    elif mutation == "result-class":
        changed_result["error_type"] = "OSError"
    elif mutation == "missing-result":
        changed_result = None
    elif mutation == "parent-run":
        updates["run_id"] = "another-run"
    elif mutation == "parent-spec":
        updates["supervisor_digest"] = "0" * 64
    elif mutation == "contract":
        updates["contract"] = CpuFailureContract(max_message_bytes=64)
    else:
        data = value.to_dict()
        if mutation == "request-limit":
            data["requests"][0]["declared_limits"]["memory_mb"] = 1024
        elif mutation == "checkpoint":
            data["checkpoints"][0]["sha256"] = "0" * 64
        else:
            data["exceptions"][0]["message"]["text"] = claim["input"]["requests"][0]["prompt"]
        value = CpuFailureRecord.model_validate_json(json.dumps(data))
    value = value.model_copy(update=updates)
    with pytest.raises(ValueError):
        validate(value, changed_claim, changed_result)


def test_materialization_and_native_proposal_claims_bind_without_inference_requests(claim: dict[str, Any]) -> None:
    spec, _, _ = _task_payloads(task_id="t", generation_seed=31, cases_per_role=2)
    for kind, inputs in (("materialize", {"candidate": spec.candidates[1].to_dict()}),
                         ("proposal", {"mechanism": "W", "round": 1})):
        value = dict(claim, kind=kind, label=f"{kind}-1", input=inputs)
        record = build(value, result(value), exception=RuntimeError("operation failed"))
        assert not record.requests
        assert bool(record.checkpoints) == (kind == "materialize")
        assert validate(record, value, result(value)) == record


def test_pure_capture_and_replay_do_not_access_files_or_run_inference(
    claim: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("pure failure capture must not access files")

    for name in ("read_bytes", "write_bytes", "read_text", "write_text", "mkdir"):
        monkeypatch.setattr(Path, name, forbidden)
    value = build(claim, result(claim), exception=CpuRunnerError("CPU worker exceeded wall deadline"))
    assert value.observations.elapsed_seconds is None and value.observations.returncode is None
    assert value.observations.signal is None and value.observations.peak_rss_bytes is None
    assert validate(value, claim, result(claim)) == value


@pytest.mark.parametrize("kind", ["inference", "checkpoint-verification"])
def test_native_reference_claims_bind_reference_and_diagnostic_identities(claim: dict[str, Any], kind: str) -> None:
    native = dict(claim)
    native["reference_digest"] = native.pop("study_digest")
    native["diagnostic_digest"] = "9" * 64
    native["kind"] = kind
    if kind == "inference":
        native["label"] = "A"
        native["input"] = {key: value for key, value in claim["input"].items() if key != "case_ids"}
        native["input"].update(option_id="A", practice_cases_digest="8" * 64)
    else:
        native["label"] = "checkpoint-base"
        native["input"] = {"candidate_digest": "e" * 64}
    outcome = {"claim_digest": canonical_digest(native), "status": "failed", "error_type": "CpuRunnerError"}
    captured = build(native, outcome, exception=CpuRunnerError("reference operation failure"))
    assert captured.native_journal_kind == "reference" and captured.diagnostic_digest == "9" * 64
    assert bool(captured.requests) == bool(captured.checkpoints) == (kind == "inference")
    assert captured.candidate_digest == "e" * 64
    assert validate(captured, native, outcome) == captured
    for key in ("reference_digest", "diagnostic_digest"):
        altered = dict(native, **{key: "7" * 64})
        with pytest.raises(ValueError):
            validate(captured, altered, outcome)
    wrong_binding = {"operation_digest": outcome["claim_digest"], "status": "failed", "error_type": "CpuRunnerError"}
    with pytest.raises(ValueError, match="exact terminal claim"):
        build(native, wrong_binding, exception=CpuRunnerError("failure"))
