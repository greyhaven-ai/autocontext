"""Measured outcomes from the fixed CPU worker; no alternate code execution API.

The native receipt still identifies the unchanged worker. The separately pinned
profile identifies this parent, its failure codec, and process observations.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Literal

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.cpu_failure_v2_models import CpuFailureContractV2, CpuFailureRecordV2
from autocontext.weight_evolution.cpu_failure_v2_record import (
    build_cpu_failure_record_v2,
    capture_observations_v2,
    validate_cpu_failure_record_v2,
)
from autocontext.weight_evolution.cpu_measured_models import CpuMeasuredBatchOutcome
from autocontext.weight_evolution.cpu_measured_transport import (
    CpuProcessObservation,
    MeasuredTransportResult,
    _run_measured,
)
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.cpu_runner import (
    MAX_BATCH_REQUESTS,
    CpuBatchReceipt,
    CpuRunnerError,
    _batch_digest,
    _require_supported_host,
    _validate_response,
    runtime_identity,
    validate_cpu_receipt,
)


def measured_runtime_identity() -> dict[str, Any]:
    """Pin the measured adapter separately from its frozen native worker."""
    package = Path(__file__).parent
    # The v2 codec can validate legacy terminal bindings. Include that full
    # package inventory rather than silently omitting transitive journal/model
    # dependencies from the parent profile. Native worker pins remain unchanged.
    names = sorted(path.name for path in package.glob("*.py"))
    return {"schema_version": 1, "implementation": "measured-token-sum-cpu-outcomes-v2",
            "native_runtime": runtime_identity(),
            "sources": [file_identity(package / name, max_bytes=1024 * 1024) for name in names],
            "device": "cpu", "gpu_execution_performed": False,
            "scope": "trusted-fixed-worker-and-predeclared-parity-only"}


def _requests(requests: tuple[CpuInferenceRequest, ...]) -> tuple[CpuInferenceRequest, ...]:
    if not isinstance(requests, tuple) or not 1 <= len(requests) <= MAX_BATCH_REQUESTS:
        raise CpuRunnerError("measured CPU batch requires 1..32 requests")
    requests = tuple(CpuInferenceRequest.model_validate_json(json.dumps(request.to_dict(), allow_nan=False))
                     for request in requests)
    first = requests[0]
    if (len({request.request_id for request in requests}) != len(requests)
            or any((request.model, request.checkpoint_sha256, request.checkpoint_size_bytes, request.limits)
                   != (first.model, first.checkpoint_sha256, first.checkpoint_size_bytes, first.limits)
                   for request in requests)):
        raise CpuRunnerError("measured CPU requests must share model/checkpoint/limits and have unique IDs")
    return requests


def _checkpoint(path: Path, request: CpuInferenceRequest) -> dict[str, Any]:
    identity = file_identity(path, max_bytes=request.checkpoint_size_bytes)
    if identity["sha256"] != request.checkpoint_sha256 or identity["size_bytes"] != request.checkpoint_size_bytes:
        raise CpuRunnerError("measured CPU checkpoint differs from its pin")
    return identity


def _failure(error: BaseException, *, contract: CpuFailureContractV2, profile: str, batch: str, native: str,
             requests: tuple[CpuInferenceRequest, ...], transport: MeasuredTransportResult | None,
             process: CpuProcessObservation) -> CpuFailureRecordV2:
    code = process.returncode_before_cleanup
    observations = capture_observations_v2(
        contract, returncode=code, signal=-code if code is not None and code < 0 else None,
        elapsed_seconds=process.elapsed_to_trigger_seconds, peak_rss_bytes=process.peak_rss_bytes,
        stdout=transport.stdout if transport is not None else None,
        stderr=transport.stderr if transport is not None else None, redact=tuple(r.prompt for r in requests),
    )
    return build_cpu_failure_record_v2(
        contract, supervisor_digest=profile, run_id="measured-batch", study_digest=batch, runtime_digest=native,
        terminal_claim=None, terminal_result=None, exception=error, observations=observations,
        redact=tuple(r.prompt for r in requests),
    )


def run_measured_cpu_batch(requests: tuple[CpuInferenceRequest, ...], *, checkpoint_path: Path,
                           contract: CpuFailureContractV2 | None = None) -> CpuMeasuredBatchOutcome:
    """Return one measured outcome; invalid input is rejected before acceptance.

    There are no retries. Interruptions outside the accepted transport remain
    exceptions for the enclosing immutable claim to retain as uncertain.
    """
    requests = _requests(requests)
    contract = CpuFailureContractV2.model_validate_json(json.dumps((contract or CpuFailureContractV2()).to_dict()))
    identity = measured_runtime_identity()
    profile = canonical_digest(identity)
    native = canonical_digest(identity["native_runtime"])
    batch = _batch_digest(requests, native)
    first = requests[0]
    process = CpuProcessObservation(
        trigger="preflight-failed", dispatched=False, total_seconds=None,
        state_before_cleanup="not-dispatched", cleanup_state="not-needed",
    )
    transport: MeasuredTransportResult | None = None
    primary: BaseException | None = None
    cleanup: BaseException | None = None
    receipt: CpuBatchReceipt | None = None
    phase: Literal["preflight", "transport", "response-validation", "postcheck", "cleanup"] = "preflight"
    try:
        _require_supported_host()
        checkpoint = _checkpoint(checkpoint_path, first)
        envelope = {"schema_version": 1, "requests": [request.to_dict() for request in requests],
                    "checkpoint_path": str(checkpoint_path.absolute()), "runtime_digest": native}
        payload = json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        command = [sys.executable, "-I", str(Path(__file__).with_name("cpu_worker.py")),
                   str(first.limits.memory_mb), str(max(1, math.ceil(first.limits.wall_seconds)))]
        phase = "transport"
        transport = _run_measured(command, payload, wall_seconds=first.limits.wall_seconds,
                                   memory_bytes=first.limits.memory_mb * 1024 * 1024)
        process = transport.observation
        primary, cleanup = transport.primary_error, transport.cleanup_error
        if primary is None and cleanup is None:
            phase = "response-validation"
            if transport.stderr:
                raise CpuRunnerError("CPU worker reported stderr")
            if (transport.stdout is None or process.returncode_before_cleanup is None
                    or process.total_seconds is None):
                raise CpuRunnerError("CPU worker response has incomplete process measurements")
            receipt = _validate_response(
                transport.stdout, requests, runtime_digest=native, elapsed=process.total_seconds,
                returncode=process.returncode_before_cleanup, parent_peak_rss=process.peak_rss_bytes or 0,
            )
            phase = "postcheck"
            if measured_runtime_identity() != identity:
                raise CpuRunnerError("measured CPU profile sources changed during inference")
            if _checkpoint(checkpoint_path, first) != checkpoint:
                raise CpuRunnerError("measured CPU checkpoint changed during inference")
        elif primary is None:
            phase = "cleanup"
    except Exception as exc:
        if phase == "transport" and transport is None:
            # No result crossed the measurement boundary: dispatch may have
            # happened. Leave the enclosing claim uncertain, never fabricate a
            # nondispatched outcome or rerun it to recover observations.
            raise
        primary = exc
    failed = primary is not None or cleanup is not None
    outcome = CpuMeasuredBatchOutcome(
        contract=contract, profile_digest=profile, native_runtime_digest=native,
        batch_digest=batch, request_digests=tuple(request.digest for request in requests),
        checkpoint_sha256=first.checkpoint_sha256, checkpoint_size_bytes=first.checkpoint_size_bytes,
        model_digest=first.model.digest, status="failed" if failed else "completed",
        failure_phase=phase if failed else None, process=process, receipt=None if failed else receipt,
        failure=(_failure(primary, contract=contract, profile=profile, batch=batch, native=native,
                          requests=requests, transport=transport, process=process) if primary is not None else None),
        cleanup_failure=(_failure(cleanup, contract=contract, profile=profile, batch=batch, native=native,
                                  requests=requests, transport=transport, process=process) if cleanup is not None else None),
    )
    return validate_measured_outcome(outcome, requests, contract=contract,
                                     profile_digest=profile, native_runtime_digest=native)


def validate_measured_outcome(outcome: CpuMeasuredBatchOutcome, requests: tuple[CpuInferenceRequest, ...], *,
                              checkpoint_path: Path | None = None, contract: CpuFailureContractV2 | None = None,
                              profile_digest: str | None = None,
                              native_runtime_digest: str | None = None) -> CpuMeasuredBatchOutcome:
    """Pure evidence replay; optional checkpoint verification never executes a model.

    A pinned ledger supplies its saved profile/native digests. Standalone calls
    default to the current runtime, so foreign or changed profiles fail closed.
    """
    requests = _requests(requests)
    outcome = CpuMeasuredBatchOutcome.model_validate_json(json.dumps(outcome.to_dict(), allow_nan=False))
    if profile_digest is None or native_runtime_digest is None:
        identity = measured_runtime_identity()
        profile_digest = profile_digest or canonical_digest(identity)
        native_runtime_digest = native_runtime_digest or canonical_digest(identity["native_runtime"])
    first = requests[0]
    if (outcome.profile_digest != profile_digest or outcome.native_runtime_digest != native_runtime_digest
            or outcome.contract != (contract or CpuFailureContractV2())
            or outcome.request_digests != tuple(request.digest for request in requests)
            or outcome.checkpoint_sha256 != first.checkpoint_sha256
            or outcome.checkpoint_size_bytes != first.checkpoint_size_bytes or outcome.model_digest != first.model.digest):
        raise CpuRunnerError("measured outcome differs from request/checkpoint/profile/contract pins")
    if checkpoint_path is not None:
        _checkpoint(checkpoint_path, first)
    if outcome.receipt is not None:
        validate_cpu_receipt(outcome.receipt, requests, native_runtime_digest)
        if outcome.receipt.telemetry.elapsed_seconds != outcome.process.total_seconds:
            raise CpuRunnerError("measured receipt elapsed time differs from parent observation")
        if (outcome.process.peak_rss_bytes is not None
                and outcome.receipt.telemetry.peak_rss_bytes < outcome.process.peak_rss_bytes):
            raise CpuRunnerError("measured receipt omits the observed parent RSS peak")
    for record in (outcome.failure, outcome.cleanup_failure):
        if record is None:
            continue
        validate_cpu_failure_record_v2(
            record, outcome.contract, supervisor_digest=profile_digest, run_id="measured-batch",
            study_digest=outcome.batch_digest, runtime_digest=native_runtime_digest,
            terminal_claim=None, terminal_result=None, redact=tuple(r.prompt for r in requests),
        )
        code = outcome.process.returncode_before_cleanup
        if (record.observations.returncode != code
                or record.observations.signal != (-code if code is not None and code < 0 else None)
                or record.observations.elapsed_seconds != outcome.process.elapsed_to_trigger_seconds
                or record.observations.peak_rss_bytes != outcome.process.peak_rss_bytes):
            raise CpuRunnerError("failure observations differ from pre-cleanup process measurements")
    return outcome
