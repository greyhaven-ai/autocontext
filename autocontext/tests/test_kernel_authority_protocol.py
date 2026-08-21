from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.kernel_evolution import (
    MAX_AUTHORITY_PAYLOAD_BYTES,
    AcceleratorAttestation,
    AuthorityMeasurement,
    AuthorityRequest,
    AuthorityResponse,
    AuthorityWireError,
    authority_message_digest,
    build_authority_receipt,
    canonical_authority_digest,
    encode_authority_frame,
    receive_authority_frame,
    verify_authority_receipt,
)
from autocontext.kernel_evolution.authority_wire import WIRE_MAGIC


class _MemoryConnection:
    def __init__(self, payload: bytes, *, chunk_size: int = 7) -> None:
        self._payload = bytearray(payload)
        self._chunk_size = chunk_size

    def recv(self, size: int) -> bytes:
        amount = min(size, self._chunk_size, len(self._payload))
        result = bytes(self._payload[:amount])
        del self._payload[:amount]
        return result


def _digest(value: bytes | str) -> str:
    return canonical_authority_digest(value)


def _attestation() -> AcceleratorAttestation:
    return AcceleratorAttestation(
        backend="cuda",
        vendor="nvidia",
        architecture="sm90",
        device_id="MIG-GPU-deadbeef/1/0",
        isolation_kind="hardware-partition",
        enforced_memory_bytes=8 * 1024**3,
        runtime="cuda-12.8",
        driver="570.1",
        attestor_id="nvml-partition-v1",
        metadata={"region": "us-central"},
    )


def _request(payload: bytes = b"tensor-bytes") -> AuthorityRequest:
    return AuthorityRequest(
        request_id=_digest("request-1"),
        session_nonce=_digest("session"),
        sequence=0,
        role="candidate",
        operation="execute",
        artifact_digest=_digest("candidate"),
        input_manifest_digest=_digest("manifest"),
        payload_digest=_digest(payload),
        payload_bytes=len(payload),
    )


def _response(request: AuthorityRequest, payload: bytes = b"output-bytes") -> AuthorityResponse:
    return AuthorityResponse(
        request_id=request.request_id,
        session_nonce=request.session_nonce,
        sequence=request.sequence,
        role=request.role,
        artifact_digest=request.artifact_digest,
        outcome="complete",
        output_manifest_digest=_digest("output-manifest"),
        payload_digest=_digest(payload),
        payload_bytes=len(payload),
    )


def test_authority_protocol_is_accelerator_neutral_and_strict() -> None:
    attestation = _attestation()
    assert attestation.backend == "cuda"
    assert attestation.vendor == "nvidia"
    assert attestation.digest.startswith("sha256:")

    rocm = attestation.model_copy(
        update={
            "backend": "rocm",
            "vendor": "amd",
            "architecture": "gfx942",
            "device_id": "partition-7",
            "runtime": "rocm-7.0",
            "driver": "amdgpu-7.0",
        }
    )
    assert rocm.backend == "rocm"
    assert rocm.digest != attestation.digest

    with pytest.raises(ValidationError, match="extra_forbidden"):
        AcceleratorAttestation.model_validate({**attestation.model_dump(), "h100_only": True})


def test_authority_wire_round_trip_is_bounded_and_non_pickle() -> None:
    payload = b"opaque-safetensors-payload"
    request = _request(payload)
    frame = encode_authority_frame(request, payload)
    decoded, decoded_payload = receive_authority_frame(_MemoryConnection(frame), AuthorityRequest)

    assert decoded == request
    assert decoded_payload == payload
    source = Path(__file__).resolve().parents[1] / "src" / "autocontext" / "kernel_evolution" / "authority_wire.py"
    wire_source = source.read_text(encoding="utf-8")
    assert "import pickle" not in wire_source
    assert "pickle.loads" not in wire_source


def test_authority_wire_rejects_forged_truncated_and_oversized_frames() -> None:
    payload = b"payload"
    request = _request(payload)
    frame = encode_authority_frame(request, payload)

    with pytest.raises(AuthorityWireError, match="digest"):
        receive_authority_frame(_MemoryConnection(frame[:-1] + b"X"), AuthorityRequest)
    with pytest.raises(AuthorityWireError, match="ended"):
        receive_authority_frame(_MemoryConnection(frame[:-1]), AuthorityRequest)

    oversized = struct.pack("!6sIQ", WIRE_MAGIC, 2, MAX_AUTHORITY_PAYLOAD_BYTES + 1) + b"{}"
    with pytest.raises(AuthorityWireError, match="oversized"):
        receive_authority_frame(_MemoryConnection(oversized), AuthorityRequest)

    with pytest.raises(AuthorityWireError, match="byte limit"):
        encode_authority_frame(request, payload, max_payload_bytes=2)


def test_candidate_response_cannot_claim_timing_or_resources() -> None:
    request = _request()
    response = _response(request)
    forged = {
        **response.model_dump(mode="json"),
        "elapsed_ns": 1,
        "peak_memory_bytes": 1,
    }

    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthorityResponse.model_validate(forged)

    with pytest.raises(ValidationError, match="failed responses cannot carry"):
        AuthorityResponse.model_validate(
            {
                **response.model_dump(mode="json"),
                "outcome": "candidate_error",
                "diagnostic_code": "execution",
            }
        )


def test_authority_receipt_replays_report_and_detects_tampering() -> None:
    candidate_request = _request(b"candidate-input")
    candidate_response = _response(candidate_request, b"candidate-output")
    incumbent_request = candidate_request.model_copy(
        update={
            "request_id": _digest("request-2"),
            "sequence": 1,
            "role": "incumbent",
            "artifact_digest": _digest("incumbent"),
        }
    )
    incumbent_response = _response(incumbent_request, b"incumbent-output")
    measurements = (
        AuthorityMeasurement(
            sequence=0,
            role="candidate",
            request_digest=authority_message_digest(candidate_request),
            response_digest=authority_message_digest(candidate_response),
            input_commitment=candidate_request.payload_digest,
            output_commitment=candidate_response.payload_digest,
            elapsed_ns=10_000,
            observed_peak_memory_bytes=1_024,
            outcome="complete",
        ),
        AuthorityMeasurement(
            sequence=1,
            role="incumbent",
            request_digest=authority_message_digest(incumbent_request),
            response_digest=authority_message_digest(incumbent_response),
            input_commitment=incumbent_request.payload_digest,
            output_commitment=incumbent_response.payload_digest,
            elapsed_ns=11_000,
            observed_peak_memory_bytes=2_048,
            outcome="complete",
        ),
    )
    report = {
        "candidate_artifact_digest": candidate_request.artifact_digest,
        "incumbent_artifact_digest": incumbent_request.artifact_digest,
        "protocol": {"seed_commitment": _digest("private-plan")},
        "performance": {"blocks": [{"candidate_ms": 0.01, "incumbent_ms": 0.011}]},
    }
    receipt = build_authority_receipt(
        evaluator_build_digest=_digest("evaluator-build"),
        boundary_manifest_digest=_digest("boundary-manifest"),
        plan_commitment=_digest("private-plan"),
        accelerator_attestation=_attestation(),
        candidate_artifact_digest=candidate_request.artifact_digest,
        incumbent_artifact_digest=incumbent_request.artifact_digest,
        measurements=measurements,
        report=report,
    )
    report["evaluator_authority_receipt"] = receipt.model_dump(mode="json")

    verify_authority_receipt(receipt, report)
    assert receipt.receipt_digest.startswith("sha256:")

    tampered = json.loads(json.dumps(report))
    tampered["performance"]["blocks"][0]["candidate_ms"] = 0.000001
    with pytest.raises(ValueError, match="different report content"):
        verify_authority_receipt(receipt, tampered)


def test_authority_receipt_requires_independent_contiguous_roles() -> None:
    request = _request()
    response = _response(request)
    measurement = AuthorityMeasurement(
        sequence=0,
        role="candidate",
        request_digest=authority_message_digest(request),
        response_digest=authority_message_digest(response),
        input_commitment=request.payload_digest,
        output_commitment=response.payload_digest,
        elapsed_ns=1,
        observed_peak_memory_bytes=1,
        outcome="complete",
    )
    with pytest.raises(ValidationError, match="candidate and incumbent"):
        build_authority_receipt(
            evaluator_build_digest=_digest("evaluator-build"),
            boundary_manifest_digest=_digest("boundary-manifest"),
            plan_commitment=_digest("private-plan"),
            accelerator_attestation=_attestation(),
            candidate_artifact_digest=_digest("candidate"),
            incumbent_artifact_digest=_digest("incumbent"),
            measurements=(measurement,),
            report={
                "candidate_artifact_digest": _digest("candidate"),
                "incumbent_artifact_digest": _digest("incumbent"),
                "protocol": {"seed_commitment": _digest("private-plan")},
            },
        )
