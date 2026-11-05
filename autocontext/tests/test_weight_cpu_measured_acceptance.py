from __future__ import annotations

import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_measured_acceptance as acceptance
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.cpu_failure_v2_record import build_cpu_failure_record_v2
from autocontext.weight_evolution.cpu_measured_acceptance import (
    CpuMeasuredAcceptanceSpec,
    build_cpu_measured_acceptance,
    report_cpu_measured_acceptance,
    run_cpu_measured_acceptance,
    validate_cpu_measured_acceptance,
)
from autocontext.weight_evolution.cpu_measured_models import CpuMeasuredBatchOutcome
from autocontext.weight_evolution.cpu_measured_transport import CpuProcessObservation
from autocontext.weight_evolution.cpu_models import CpuInferenceResult
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt, CpuRunnerError, CpuTelemetry
from autocontext.weight_evolution.session import _read_record


@dataclass
class Fixture:
    bundle: Path
    output: Path
    spec: CpuMeasuredAcceptanceSpec
    receipt: CpuBatchReceipt
    measured: CpuMeasuredBatchOutcome
    calls: list[str]


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    """Synthetic receipts isolate ledger behavior; the real parity test is separate."""
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    spec = build_cpu_measured_acceptance(bundle)
    first = spec.requests[0]
    results = tuple(CpuInferenceResult(request_digest=request.digest, checkpoint_sha256=first.checkpoint_sha256,
        checkpoint_size_bytes=first.checkpoint_size_bytes, input_tokens=tokens, logits=logits,
        output_token_id=choice, text=text) for request, tokens, logits, choice, text in zip(spec.requests,
            ((0,), (1,), (0, 0, 1), (1, 1, 0)), ((2.0, -1.0), (-1.0, 2.0), (3.0, 0.0), (0.0, 3.0)),
            (0, 1, 0, 1), ("yes", "no", "yes", "no"), strict=True))
    memory = first.limits.memory_mb * 1024 * 1024
    telemetry = CpuTelemetry(elapsed_seconds=0.1, peak_rss_bytes=1024,
        memory_enforcement="hard_address_space" if sys.platform == "linux" else "monitored_rss",
        memory_limit_bytes=memory, address_space_limit_bytes=memory if sys.platform == "linux" else None,
        rss_poll_interval_seconds=0.0 if sys.platform == "linux" else 0.01,
        rss_sampling_may_overshoot=sys.platform != "linux", cpu_limit_seconds=math.ceil(first.limits.wall_seconds))
    receipt = CpuBatchReceipt(batch_digest=acceptance._batch_digest(spec),
        request_digests=tuple(r.digest for r in spec.requests), checkpoint_sha256=first.checkpoint_sha256,
        checkpoint_size_bytes=first.checkpoint_size_bytes, model_digest=first.model.digest,
        runtime_digest=spec.native_runtime_digest, results=results, telemetry=telemetry)
    measured = CpuMeasuredBatchOutcome(contract=spec.failure_contract, profile_digest=spec.measured_runtime_digest,
        native_runtime_digest=spec.native_runtime_digest, batch_digest=receipt.batch_digest,
        request_digests=receipt.request_digests, checkpoint_sha256=first.checkpoint_sha256,
        checkpoint_size_bytes=first.checkpoint_size_bytes, model_digest=first.model.digest,
        status="completed", failure_phase=None, receipt=receipt,
        process=CpuProcessObservation(trigger="completed", dispatched=True, elapsed_to_trigger_seconds=0.08,
            total_seconds=0.1, cleanup_seconds=0.02, state_before_cleanup="exited", returncode_before_cleanup=0,
            returncode_after_cleanup=0, cleanup_state="completed", leader_reaped=True))
    calls: list[str] = []

    def legacy(*args: Any, **kwargs: Any) -> CpuBatchReceipt:
        calls.append("legacy-v1")
        return receipt

    def measured_call(*args: Any, **kwargs: Any) -> CpuMeasuredBatchOutcome:
        calls.append("measured-v2")
        return measured

    monkeypatch.setattr(acceptance, "run_cpu_batch", legacy)
    monkeypatch.setattr(acceptance, "run_measured_cpu_batch", measured_call)
    return Fixture(bundle, output, spec, receipt, measured, calls)


def _snapshot(root: Path) -> dict[str, Any]:
    return {str(path.relative_to(root)): file_identity(path) for path in sorted(root.rglob("*")) if path.is_file()}


def _write_record(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}, allow_nan=False))


def _repin_bundle(bundle: Path) -> None:
    lock = json.loads((bundle / "bundle.lock.json").read_text())
    for name in lock["artifacts"]:
        identity = file_identity(bundle / name)
        lock["artifacts"][name] = {key: identity[key] for key in ("sha256", "size_bytes")}
    (bundle / "bundle.lock.json").write_text(json.dumps(lock))


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_declared_schedule_has_exact_capacity_and_no_execution(tmp_path: Path, count: int,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "_dispatch", lambda *a, **k: pytest.fail("builder dispatched"))
    bundle = tmp_path / "bundle"
    spec = build_cpu_measured_acceptance(bundle, request_count=count)
    before = _snapshot(bundle)
    validation = validate_cpu_measured_acceptance(bundle)
    assert spec.required_capacity == {"inference_calls": count * 2, "output_tokens": count * 2,
                                     "input_tokens": 2 * sum((1, 1, 3, 3)[:count])}
    assert validation["required_capacity"] == spec.required_capacity
    assert validation["model_execution_performed"] is False
    assert validation["failure_contract_digest"] == spec.failure_contract.digest
    assert _snapshot(bundle) == before
    with pytest.raises(ValueError, match="fresh"):
        build_cpu_measured_acceptance(bundle)


@pytest.mark.parametrize("count", [-1, 0, 5, True, 1.0])
def test_request_count_rejected_before_writing(tmp_path: Path, count: Any) -> None:
    bundle = tmp_path / "bundle"
    with pytest.raises(ValueError, match="request_count"):
        build_cpu_measured_acceptance(bundle, request_count=count)
    assert not bundle.exists()


def test_real_native_and_measured_profile_parity(tmp_path: Path) -> None:
    """One actual four-request batch per profile, with no retries or campaign."""
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    spec = build_cpu_measured_acceptance(bundle)
    report = run_cpu_measured_acceptance(bundle, output_dir=output)
    assert report["status_counts"] == {"completed": 2, "failed": 0, "uncertain": 0, "not-started": 0}
    assert report["semantic_parity"] is report["acceptance_passed"] is True
    assert report["charged_capacity"] == report["verified_usage"] == spec.required_capacity
    measured = _read_record(output / "outcomes" / "00000001.json")["outcome"]
    assert measured["process"]["dispatched"] is True
    assert measured["process"]["leader_reaped"] is True
    assert measured["receipt"]["gpu_execution_performed"] is False
    assert report_cpu_measured_acceptance(bundle, output_dir=output) == report


def test_completed_replay_is_read_only_and_does_not_retry(fixture: Fixture,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    report = run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert fixture.calls == ["legacy-v1", "measured-v2"]
    assert report["semantic_parity"] is True
    assert report["charged_capacity"] == {"inference_calls": 8, "input_tokens": 16, "output_tokens": 8}
    before = _snapshot(fixture.output)
    assert run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output) == report
    assert fixture.calls == ["legacy-v1", "measured-v2"]
    monkeypatch.setattr(acceptance, "_dispatch", lambda *a, **k: pytest.fail("report dispatched"))
    monkeypatch.setattr(acceptance, "_immutable_json", lambda *a, **k: pytest.fail("report published"))
    monkeypatch.setattr(acceptance, "_clear_staging", lambda *a, **k: pytest.fail("report changed staging"))
    assert report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output) == report
    assert _snapshot(fixture.output) == before


def test_parity_excludes_different_measured_telemetry(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = fixture.receipt.model_copy(update={
        "telemetry": fixture.receipt.telemetry.model_copy(update={"elapsed_seconds": 0.2})})
    measured = fixture.measured.model_copy(update={"receipt": receipt,
        "process": fixture.measured.process.model_copy(update={"total_seconds": 0.2})})
    monkeypatch.setattr(acceptance, "run_measured_cpu_batch", lambda *a, **k: measured)
    assert receipt.digest != fixture.receipt.digest
    assert receipt.semantic_digest == fixture.receipt.semantic_digest
    report = run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert report["semantic_parity"] is report["acceptance_passed"] is True


def test_second_profile_interruption_remains_uncertain_without_replacement(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted(*args: Any, **kwargs: Any) -> Any:
        fixture.calls.append("measured-v2")
        raise RuntimeError("measured dispatch did not return")

    monkeypatch.setattr(acceptance, "run_measured_cpu_batch", interrupted)
    with pytest.raises(RuntimeError, match="did not return"):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    pending = report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert pending["status_counts"] == {"completed": 1, "failed": 0, "uncertain": 1, "not-started": 0}
    assert pending["pending_results"] == 1 and pending["charged_capacity"] == fixture.spec.required_capacity
    report = run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert report["pending_results"] == 0 and report["rows"][1]["outcome_digest"] is None
    assert report["semantic_parity"] is None and report["verified_usage"] == fixture.spec.batch_capacity
    assert fixture.calls == ["legacy-v1", "measured-v2"]


@pytest.mark.parametrize("location,after,retained,dispatched", [
    ("claims", False, False, False), ("claims", True, False, False),
    ("outcomes", False, False, True), ("outcomes", True, True, True),
    ("results", False, True, True), ("results", True, True, True),
])
def test_publication_interruptions_never_redispatch_claims(fixture: Fixture, monkeypatch: pytest.MonkeyPatch,
    location: str, after: bool, retained: bool, dispatched: bool,
) -> None:
    original = acceptance._immutable_json

    def interrupted(path: Path, payload: dict[str, Any]) -> None:
        if path.parent.name == location and path.name == "00000000.json":
            if after:
                original(path, payload)
            raise OSError("injected publication interruption")
        original(path, payload)

    monkeypatch.setattr(acceptance, "_immutable_json", interrupted)
    with pytest.raises(OSError, match="publication interruption"):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert fixture.calls == (["legacy-v1"] if dispatched else [])
    before = _snapshot(fixture.output)
    pending = report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert _snapshot(fixture.output) == before
    unclaimed = location == "claims" and not after
    assert pending["rows"][0]["status"] == ("not-started" if unclaimed else "completed" if retained else "uncertain")
    assert pending["rows"][1]["status"] == "not-started"
    assert pending["pending_results"] == (0 if unclaimed or location == "results" and after else 1)
    monkeypatch.setattr(acceptance, "_immutable_json", original)
    complete = run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    expected_calls = (["legacy-v1"] if dispatched or unclaimed else []) + ["measured-v2"]
    assert fixture.calls == expected_calls
    assert complete["charged_capacity"] == fixture.spec.required_capacity
    assert complete["semantic_parity"] is (True if retained or unclaimed else None)
    if not retained and not unclaimed:
        assert complete["rows"][0]["outcome_digest"] is None
        assert complete["verified_usage"] == fixture.spec.batch_capacity
        assert not (fixture.output / "outcomes" / "00000000.json").exists()
    assert report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output) == complete


@pytest.mark.parametrize("after", [False, True])
def test_report_publication_interruption_preserves_both_outcomes(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch, after: bool,
) -> None:
    original = acceptance._immutable_json

    def interrupted(path: Path, payload: dict[str, Any]) -> None:
        if path.name == "report.json":
            if after:
                original(path, payload)
            raise OSError("report publication interrupted")
        original(path, payload)

    monkeypatch.setattr(acceptance, "_immutable_json", interrupted)
    with pytest.raises(OSError, match="publication interrupted"):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    report = report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert report["acceptance_passed"] is True
    monkeypatch.setattr(acceptance, "_immutable_json", original)
    assert run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output) == report
    assert fixture.calls == ["legacy-v1", "measured-v2"]


def test_known_legacy_failure_is_class_only_and_does_not_cancel_comparison(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(*args: Any, **kwargs: Any) -> Any:
        fixture.calls.append("legacy-v1")
        raise CpuRunnerError("sensitive red prompt not persisted by the legacy profile")

    monkeypatch.setattr(acceptance, "run_cpu_batch", failed)
    report = run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert fixture.calls == ["legacy-v1", "measured-v2"]
    assert report["status_counts"]["failed"] == report["status_counts"]["completed"] == 1
    assert report["semantic_parity"] is None and report["parity_available"] is False
    assert report["charged_capacity"] == fixture.spec.required_capacity
    assert report["verified_usage"] == fixture.spec.batch_capacity
    outcome = _read_record(fixture.output / "outcomes" / "00000000.json")["outcome"]
    assert outcome["error_type"] == "CpuRunnerError" and outcome["receipt"] is None
    assert "sensitive" not in json.dumps(outcome)
    assert report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output) == report


def test_unexpected_runner_exception_is_not_backfilled_or_retried(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupted(*args: Any, **kwargs: Any) -> Any:
        fixture.calls.append("legacy-v1")
        raise RuntimeError("observer interrupted")

    monkeypatch.setattr(acceptance, "run_cpu_batch", interrupted)
    with pytest.raises(RuntimeError, match="observer interrupted"):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    pending = report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert pending["rows"][0]["status"] == "uncertain" and pending["pending_results"] == 1
    assert pending["charged_capacity"] == fixture.spec.batch_capacity
    complete = run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert fixture.calls == ["legacy-v1", "measured-v2"]
    assert complete["status_counts"]["uncertain"] == 1
    assert complete["rows"][0]["outcome_digest"] is None
    assert complete["historical_failure_cause_recovered"] is False


@pytest.mark.parametrize("mutation", ["claim", "result", "receipt", "missing-outcome", "profile", "extra-outcome"])
def test_rehashed_corrupt_prefix_blocks_later_dispatch(fixture: Fixture, mutation: str) -> None:
    run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    (fixture.output / "report.json").unlink()
    for name in ("claims", "outcomes", "results"):
        (fixture.output / name / "00000001.json").unlink()
    if mutation in {"claim", "result"}:
        path = fixture.output / ("claims" if mutation == "claim" else "results") / "00000000.json"
        value = _read_record(path)
        value["previous_result_digest" if mutation == "claim" else "claim_digest"] = "f" * 64
        _write_record(path, value)
    elif mutation == "missing-outcome":
        (fixture.output / "outcomes" / "00000000.json").unlink()
    elif mutation == "extra-outcome":
        shutil.copy(fixture.output / "outcomes" / "00000000.json", fixture.output / "outcomes" / "00000001.json")
    else:
        path = fixture.output / "outcomes" / "00000000.json"
        value = _read_record(path)
        if mutation == "profile":
            value["profile"] = "measured-v2"
        else:
            value["outcome"]["receipt"]["results"][0]["text"] = "no"
        _write_record(path, value)
    before = _snapshot(fixture.output)
    with pytest.raises(ValueError):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    with pytest.raises(ValueError):
        report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert fixture.calls == ["legacy-v1", "measured-v2"]
    assert _snapshot(fixture.output) == before


@pytest.mark.parametrize("mutation", ["checkpoint", "request", "runtime", "implementation", "extra"])
def test_bundle_drift_fails_closed_even_with_rehashed_asset_lock(fixture: Fixture, mutation: str) -> None:
    if mutation == "checkpoint":
        path = fixture.bundle / "model.safetensors"
        path.write_bytes(path.read_bytes()[:-1] + b"x")
    elif mutation == "extra":
        (fixture.bundle / "unexpected.json").write_text("{}")
    elif mutation == "runtime":
        path = fixture.bundle / "runtime.json"
        value = json.loads(path.read_text())
        value["legacy-v1"]["python"] = "unknown"
        path.write_text(json.dumps(value))
    else:
        path = fixture.bundle / "acceptance.json"
        value = json.loads(path.read_text())
        if mutation == "request":
            value["requests"][0]["prompt"] = "blue"
        else:
            value["implementation_digest"] = "0" * 64
        path.write_text(json.dumps(value))
    _repin_bundle(fixture.bundle)
    with pytest.raises(ValueError):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert fixture.calls == [] and not fixture.output.exists()


def test_unknown_output_rejected_before_lock_creation(fixture: Fixture) -> None:
    fixture.output.mkdir()
    (fixture.output / "unowned.json").write_text("{}")
    with pytest.raises(ValueError, match="unowned"):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert not (fixture.output / ".cpu-measured.lock").exists()
    assert fixture.calls == []


def test_output_paths_cannot_overlap_or_follow_symlinks(fixture: Fixture, tmp_path: Path) -> None:
    for output in (fixture.bundle, fixture.bundle / "nested", tmp_path):
        with pytest.raises(ValueError, match="overlap"):
            run_cpu_measured_acceptance(fixture.bundle, output_dir=output)
    alias = tmp_path / "alias"
    alias.symlink_to(fixture.bundle, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run_cpu_measured_acceptance(alias, output_dir=fixture.output)
    assert fixture.calls == []


def test_report_does_not_create_missing_output(fixture: Fixture) -> None:
    with pytest.raises(FileNotFoundError):
        report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert not fixture.output.exists() and fixture.calls == []


def test_sealed_uncertain_result_cannot_accept_late_observation(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = acceptance._immutable_json

    def interrupted(path: Path, payload: dict[str, Any]) -> None:
        if path.parent.name == "outcomes" and path.name == "00000000.json":
            raise OSError("lost outcome")
        original(path, payload)

    monkeypatch.setattr(acceptance, "_immutable_json", interrupted)
    with pytest.raises(OSError):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    monkeypatch.setattr(acceptance, "_immutable_json", original)
    run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    legacy = acceptance._LegacyOutcome(native_runtime_digest=fixture.spec.native_runtime_digest,
        batch_digest=fixture.receipt.batch_digest, status="completed", receipt=fixture.receipt)
    _write_record(fixture.output / "outcomes" / "00000000.json", {"profile": "legacy-v1", "outcome": legacy.to_dict()})
    with pytest.raises(ValueError, match="retained outcome"):
        report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert fixture.calls == ["legacy-v1", "measured-v2"]


@pytest.mark.parametrize("drift", ["checkpoint", "source"])
def test_returned_failure_is_retained_before_post_dispatch_drift_check(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    process = CpuProcessObservation(trigger="preflight-failed", dispatched=False,
                                    state_before_cleanup="not-dispatched", cleanup_state="not-needed")
    failure = build_cpu_failure_record_v2(fixture.spec.failure_contract,
        supervisor_digest=fixture.spec.measured_runtime_digest, run_id="measured-batch",
        study_digest=fixture.measured.batch_digest, runtime_digest=fixture.spec.native_runtime_digest,
        terminal_claim=None, terminal_result=None, exception=CpuRunnerError("observed preflight failure"),
        redact=tuple(request.prompt for request in fixture.spec.requests))
    measured = fixture.measured.model_copy(update={"status": "failed", "failure_phase": "preflight",
        "process": process, "receipt": None, "failure": failure})
    checkpoint = fixture.bundle / "model.safetensors"
    original_checkpoint = checkpoint.read_bytes()
    original_identity = acceptance.measured_runtime_identity

    def changed_source() -> dict[str, Any]:
        return {**original_identity(), "injected-source-drift": True}

    def failed(*args: Any, **kwargs: Any) -> CpuMeasuredBatchOutcome:
        fixture.calls.append("measured-v2")
        if drift == "checkpoint":
            checkpoint.write_bytes(original_checkpoint[:-1] + b"x")
        else:
            monkeypatch.setattr(acceptance, "measured_runtime_identity", changed_source)
        return measured

    monkeypatch.setattr(acceptance, "run_measured_cpu_batch", failed)
    with pytest.raises(ValueError, match="pin changed|runtime or implementation changed"):
        run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    retained = fixture.output / "outcomes" / "00000001.json"
    assert _read_record(retained)["outcome"] == measured.to_dict()
    assert not (fixture.output / "results" / "00000001.json").exists()
    assert fixture.calls == ["legacy-v1", "measured-v2"]
    with pytest.raises(ValueError):
        report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    checkpoint.write_bytes(original_checkpoint)
    monkeypatch.setattr(acceptance, "measured_runtime_identity", original_identity)
    pending = report_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert pending["rows"][1]["status"] == "failed" and pending["pending_results"] == 1
    report = run_cpu_measured_acceptance(fixture.bundle, output_dir=fixture.output)
    assert report["status_counts"]["failed"] == 1 and report["semantic_parity"] is None
    assert report["charged_capacity"] == fixture.spec.required_capacity
    assert fixture.calls == ["legacy-v1", "measured-v2"]
    assert _read_record(retained)["outcome"] == measured.to_dict()
