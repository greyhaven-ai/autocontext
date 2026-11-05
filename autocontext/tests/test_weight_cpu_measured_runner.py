from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_measured_runner as runner
from autocontext.weight_evolution.artifacts import TensorInfo, canonical_digest, file_identity, write_tensor_file
from autocontext.weight_evolution.cpu_failure_v2_models import CpuFailureContractV2
from autocontext.weight_evolution.cpu_measured_models import CpuMeasuredBatchOutcome
from autocontext.weight_evolution.cpu_measured_transport import CpuProcessObservation, MeasuredTransportResult
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest, CpuInferenceResult, CpuModelContract
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt, CpuRunnerError, CpuTelemetry, resource_policy


@pytest.fixture
def case(tmp_path: Path) -> tuple[CpuInferenceRequest, Path]:
    path = tmp_path / "model.safetensors"
    payload = {"projection.bias": struct.pack("<ff", 0.0, 0.0),
               "projection.weight": struct.pack("<ffff", 2.0, -1.0, -1.0, 2.0)}
    write_tensor_file(path, (TensorInfo("projection.bias", "F32", (2,), 0, 8),
                            TensorInfo("projection.weight", "F32", (2, 2), 8, 24)), payload.__getitem__)
    identity = file_identity(path)
    return CpuInferenceRequest(request_id="measured-one", model=CpuModelContract(
        vocabulary=("a", "prompt"), output_tokens=("yes", "no")), prompt="a",
        checkpoint_sha256=identity["sha256"], checkpoint_size_bytes=identity["size_bytes"]), path


def completed() -> CpuProcessObservation:
    return CpuProcessObservation(
        trigger="completed", dispatched=True, elapsed_to_trigger_seconds=0.1, total_seconds=0.12,
        cleanup_seconds=0.02, state_before_cleanup="exited", returncode_before_cleanup=0,
        returncode_after_cleanup=0, signal_attempted=True, signal_sent=False, signal_number=9,
        cleanup_state="completed", leader_reaped=True,
    )


def response(request: CpuInferenceRequest) -> bytes:
    native = canonical_digest(runner.measured_runtime_identity()["native_runtime"])
    policy = resource_policy()
    receipt = CpuBatchReceipt(
        batch_digest=canonical_digest({"request_digests": [request.digest], "runtime_digest": native}),
        request_digests=(request.digest,), checkpoint_sha256=request.checkpoint_sha256,
        checkpoint_size_bytes=request.checkpoint_size_bytes, model_digest=request.model.digest, runtime_digest=native,
        results=(CpuInferenceResult(request_digest=request.digest, checkpoint_sha256=request.checkpoint_sha256,
                                    checkpoint_size_bytes=request.checkpoint_size_bytes, input_tokens=(0,),
                                    logits=(2.0, -1.0), output_token_id=0, text="yes"),),
        telemetry=CpuTelemetry(elapsed_seconds=0.1, peak_rss_bytes=16 * 1024 * 1024,
                               memory_enforcement=policy["memory_enforcement"],
                               memory_limit_bytes=request.limits.memory_mb * 1024 * 1024,
                               address_space_limit_bytes=(request.limits.memory_mb * 1024 * 1024
                                                          if sys.platform == "linux" else None),
                               rss_poll_interval_seconds=policy["rss_poll_interval_seconds"],
                               rss_sampling_may_overshoot=policy["rss_sampling_may_overshoot"],
                               cpu_limit_seconds=math.ceil(request.limits.wall_seconds)),
    )
    return json.dumps(receipt.to_dict(), sort_keys=True).encode()


def test_success_uses_fixed_command_and_replays_without_execution(
    case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = case
    calls = []

    def transport(command: list[str], payload: bytes, **kwargs: Any) -> MeasuredTransportResult:
        calls.append((command, json.loads(payload), kwargs))
        return MeasuredTransportResult(response(request), b"", completed())

    monkeypatch.setattr(runner, "_run_measured", transport)
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)
    assert outcome.status == "completed" and outcome.receipt is not None
    assert outcome.receipt.telemetry.elapsed_seconds == 0.12
    assert len(calls) == 1
    assert calls[0][0][:2] == [sys.executable, "-I"]
    assert Path(calls[0][0][2]).name == "cpu_worker.py"
    assert calls[0][0][-2:] == ["512", "5"]
    assert calls[0][1]["requests"] == [request.to_dict()]
    monkeypatch.setattr(runner, "_run_measured", lambda *args, **kwargs: pytest.fail("replay dispatched"))
    saved = CpuMeasuredBatchOutcome.model_validate_json(json.dumps(outcome.to_dict()))
    assert runner.validate_measured_outcome(saved, (request,), checkpoint_path=checkpoint).digest == outcome.digest


@pytest.mark.parametrize("raw,stderr", [(b"broken", b""), (b'{"schema_version":1,"schema_version":1}', b""),
                                        (b"", b"a prompt"), (b"{}", b""), (b"\xff", b"")])
def test_malformed_response_keeps_process_measurements_and_redacts(
    case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch, raw: bytes, stderr: bytes,
) -> None:
    request, checkpoint = case
    monkeypatch.setattr(runner, "_run_measured", lambda *args, **kwargs: MeasuredTransportResult(raw, stderr, completed()))
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)
    assert outcome.status == "failed" and outcome.failure_phase == "response-validation"
    assert outcome.process == completed() and outcome.failure is not None
    assert outcome.receipt is None
    observations = outcome.failure.observations
    assert observations.returncode == 0 and observations.signal is None
    for text in (observations.stdout, observations.stderr, *(node.message for node in outcome.failure.exceptions)):
        assert text is not None and request.prompt not in text.text
    assert runner.validate_measured_outcome(outcome, (request,)).digest == outcome.digest


def test_primary_deadline_and_cleanup_failure_are_separate_without_oom_attribution(
    case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = case
    process = CpuProcessObservation(
        trigger="wall-deadline", dispatched=True, elapsed_to_trigger_seconds=5.0, total_seconds=5.01,
        cleanup_seconds=0.01, state_before_cleanup="running", returncode_after_cleanup=-9,
        signal_attempted=True, signal_sent=True, signal_number=9, cleanup_state="failed", leader_reaped=True,
    )
    monkeypatch.setattr(runner, "_run_measured", lambda *args, **kwargs: MeasuredTransportResult(
        b"a prompt", b"", process, TimeoutError("deadline for a"), OSError("cleanup a"),
    ))
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)
    assert outcome.failure is not None and outcome.cleanup_failure is not None
    assert outcome.failure_phase == "transport"
    assert outcome.failure.exceptions[0].exception_type == "TimeoutError"
    assert outcome.cleanup_failure.exceptions[0].exception_type == "OSError"
    assert outcome.failure.observations.returncode is None and outcome.failure.observations.signal is None
    assert outcome.process.returncode_after_cleanup == -9
    assert "oom" not in json.dumps(outcome.to_dict()).lower()


def test_cleanup_only_does_not_accept_a_receipt(case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    request, checkpoint = case
    process = completed().model_copy(update={"trigger": "cleanup-failed", "cleanup_state": "failed"})
    monkeypatch.setattr(runner, "_run_measured", lambda *args, **kwargs: MeasuredTransportResult(
        response(request), b"", process, cleanup_error=OSError("pipe close"),
    ))
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)
    assert outcome.failure is None and outcome.cleanup_failure is not None
    assert outcome.failure_phase == "cleanup" and outcome.receipt is None


def test_preflight_failure_does_not_fabricate_child_observations(
    case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = case
    checkpoint.unlink()
    monkeypatch.setattr(runner, "_run_measured", lambda *args, **kwargs: pytest.fail("preflight dispatched"))
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)
    assert outcome.failure_phase == "preflight" and outcome.failure is not None
    assert not outcome.process.dispatched
    assert outcome.process.total_seconds is None and outcome.failure.observations.stdout is None
    assert runner.validate_measured_outcome(outcome, (request,)).digest == outcome.digest


def test_unreturned_transport_is_uncertain_not_a_fabricated_nondispatch(
    case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = case

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("measurement result publication interrupted")

    monkeypatch.setattr(runner, "_run_measured", interrupted)
    with pytest.raises(RuntimeError, match="publication interrupted"):
        runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)


@pytest.mark.parametrize("phase", ["response-validation", "postcheck", "cleanup"])
def test_missing_dispatch_cannot_be_relabelled_as_a_later_phase(
    case: tuple[CpuInferenceRequest, Path], phase: str,
) -> None:
    request, checkpoint = case
    checkpoint.unlink()
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)
    changed = outcome.to_dict()
    changed["failure_phase"] = phase
    with pytest.raises(ValueError):
        CpuMeasuredBatchOutcome.model_validate_json(json.dumps(changed))


@pytest.mark.parametrize("mutation", ["request", "profile", "runtime", "contract", "elapsed", "process", "gpu", "nested"])
def test_tampered_outcomes_fail_replay(case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
                                      mutation: str) -> None:
    request, checkpoint = case
    monkeypatch.setattr(runner, "_run_measured", lambda *args, **kwargs: MeasuredTransportResult(
        response(request), b"", completed(),
    ))
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)
    value = outcome.to_dict()
    if mutation == "request":
        value["request_digests"] = ["0" * 64]
    elif mutation == "profile":
        value["profile_digest"] = "0" * 64
    elif mutation == "runtime":
        value["native_runtime_digest"] = "0" * 64
    elif mutation == "contract":
        value["contract"]["max_message_bytes"] = 32
    elif mutation == "elapsed":
        value["process"]["total_seconds"] = 0.2
    elif mutation == "process":
        value["process"]["returncode_before_cleanup"] = -9
    elif mutation == "gpu":
        value["gpu_execution_deferred"] = 1
    else:
        value["receipt"]["results"][0]["text"] = "no"
    with pytest.raises(ValueError):
        changed = CpuMeasuredBatchOutcome.model_validate_json(json.dumps(value))
        runner.validate_measured_outcome(changed, (request,))


def test_custom_contract_is_explicitly_required_for_replay(
    case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = case
    monkeypatch.setattr(runner, "_run_measured", lambda *args, **kwargs: MeasuredTransportResult(b"invalid", b"", completed()))
    contract = CpuFailureContractV2(max_message_bytes=32)
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint, contract=contract)
    with pytest.raises(CpuRunnerError, match="contract pins"):
        runner.validate_measured_outcome(outcome, (request,))
    assert runner.validate_measured_outcome(outcome, (request,), contract=contract) == outcome


def test_checkpoint_drift_retains_process_but_rejects_success(
    case: tuple[CpuInferenceRequest, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, checkpoint = case

    def replaced(*args: Any, **kwargs: Any) -> MeasuredTransportResult:
        raw = response(request)
        checkpoint.write_bytes(checkpoint.read_bytes() + b"changed")
        return MeasuredTransportResult(raw, b"", completed())

    monkeypatch.setattr(runner, "_run_measured", replaced)
    outcome = runner.run_measured_cpu_batch((request,), checkpoint_path=checkpoint)
    assert outcome.failure_phase == "postcheck" and outcome.receipt is None
    assert outcome.process.returncode_before_cleanup == 0
    assert runner.validate_measured_outcome(outcome, (request,)) == outcome


def test_native_pins_preserved() -> None:
    from autocontext.weight_evolution.cpu_campaign import campaign_implementation_digest
    from autocontext.weight_evolution.cpu_control import control_implementation_digest
    from autocontext.weight_evolution.cpu_diagnostic import diagnostic_implementation_digest
    from autocontext.weight_evolution.cpu_failure_campaign import failure_implementation_digest

    assert campaign_implementation_digest() == "cfa3e59d986316c5eeec43876cc839605fe904aa1e3adf80016fb7b77ef93bca"
    assert diagnostic_implementation_digest() == "c78627374d5c08c25d15d4adfd3f8a777e5ef757275a0a7c4b43d9b50d986ccd"
    assert control_implementation_digest() == "a7e4bee370e19fe73ab1fe5ca3c332a1aedb193000542deac90b108fbd5c1425"
    assert failure_implementation_digest() == "0be62f4d842832f9bcdce1159c4c0029a7d977040000527447cb69ebae0c6d08"
