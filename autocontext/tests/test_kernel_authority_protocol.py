from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from types import SimpleNamespace

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
    read_authority_hmac_secret,
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


_HMAC_KEY_ID = "test-authority-key-v1"
_HMAC_SECRET = b"test-only-authority-secret-material-32-bytes"


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


def _report(
    candidate_digest: str,
    incumbent_digest: str,
    *,
    candidate_peak: int,
    incumbent_peak: int,
) -> dict[str, object]:
    attestation = _attestation()
    return {
        "evaluation_status": "complete",
        "failure_kind": None,
        "candidate_artifact_digest": candidate_digest,
        "incumbent_artifact_digest": incumbent_digest,
        "protocol": {"seed_commitment": _digest("private-plan")},
        "performance": {"blocks": [{"candidate_ms": 0.01, "incumbent_ms": 0.011}]},
        "resources": {
            "candidate_observed_peak_bytes": candidate_peak,
            "incumbent_observed_peak_bytes": incumbent_peak,
            "telemetry_authority": "trusted-evaluator-observed/v1",
            "accelerator_attestation_digest": attestation.digest,
            "device_total_memory_bytes": attestation.enforced_memory_bytes,
        },
    }


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

    raw_header = json.dumps(request.model_dump(mode="json"), separators=(",", ":"))
    duplicate_header = raw_header.replace('"sequence":0', '"sequence":999,"sequence":0').encode()
    duplicate_frame = struct.pack("!6sIQ", WIRE_MAGIC, len(duplicate_header), len(payload)) + duplicate_header + payload
    with pytest.raises(AuthorityWireError, match="invalid typed JSON header"):
        receive_authority_frame(_MemoryConnection(duplicate_frame), AuthorityRequest)


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
    report = _report(
        candidate_request.artifact_digest,
        incumbent_request.artifact_digest,
        candidate_peak=1_024,
        incumbent_peak=2_048,
    )
    receipt = build_authority_receipt(
        evaluator_build_digest=_digest("evaluator-build"),
        boundary_manifest_digest=_digest("boundary-manifest"),
        plan_commitment=_digest("private-plan"),
        accelerator_attestation=_attestation(),
        candidate_artifact_digest=candidate_request.artifact_digest,
        incumbent_artifact_digest=incumbent_request.artifact_digest,
        measurements=measurements,
        report=report,
        signing_key_id=_HMAC_KEY_ID,
        signing_secret=_HMAC_SECRET,
    )
    report["evaluator_authority_receipt"] = receipt.model_dump(mode="json")

    verify_authority_receipt(
        receipt,
        report,
        trusted_key_id=_HMAC_KEY_ID,
        trusted_secret=_HMAC_SECRET,
        expected_evaluator_build_digest=_digest("evaluator-build"),
        expected_boundary_manifest_digest=_digest("boundary-manifest"),
    )
    assert receipt.receipt_digest.startswith("sha256:")
    assert receipt.transcript.measurement_count == 2
    assert receipt.transcript.unique_request_count == 2
    assert receipt.transcript.unique_response_count == 2

    tampered = json.loads(json.dumps(report))
    tampered["performance"]["blocks"][0]["candidate_ms"] = 0.000001
    with pytest.raises(ValueError, match="different report content"):
        verify_authority_receipt(
            receipt,
            tampered,
            trusted_key_id=_HMAC_KEY_ID,
            trusted_secret=_HMAC_SECRET,
        )

    forged_build = receipt.model_copy(update={"evaluator_build_digest": _digest("forged-build")})
    with pytest.raises(ValueError, match="authentication tag"):
        verify_authority_receipt(
            forged_build,
            report,
            trusted_key_id=_HMAC_KEY_ID,
            trusted_secret=_HMAC_SECRET,
        )

    with pytest.raises(ValueError, match="authentication key"):
        verify_authority_receipt(
            receipt,
            report,
            trusted_key_id="different-host-key",
            trusted_secret=_HMAC_SECRET,
        )

    with pytest.raises(ValueError, match="host-computed digest"):
        verify_authority_receipt(
            receipt,
            report,
            trusted_key_id=_HMAC_KEY_ID,
            trusted_secret=_HMAC_SECRET,
            expected_evaluator_build_digest=_digest("different-host-build"),
        )

    contradictory = list(measurements)
    contradictory[0] = contradictory[0].model_copy(update={"outcome": "candidate_error"})
    with pytest.raises(ValueError, match="outcomes contradict a complete report"):
        build_authority_receipt(
            evaluator_build_digest=_digest("evaluator-build"),
            boundary_manifest_digest=_digest("boundary-manifest"),
            plan_commitment=_digest("private-plan"),
            accelerator_attestation=_attestation(),
            candidate_artifact_digest=candidate_request.artifact_digest,
            incumbent_artifact_digest=incumbent_request.artifact_digest,
            measurements=tuple(contradictory),
            report={key: value for key, value in report.items() if key != "evaluator_authority_receipt"},
            signing_key_id=_HMAC_KEY_ID,
            signing_secret=_HMAC_SECRET,
        )

    wrong_resources = {key: value for key, value in report.items() if key != "evaluator_authority_receipt"}
    wrong_resources = json.loads(json.dumps(wrong_resources))
    wrong_resources["resources"]["candidate_observed_peak_bytes"] = 999
    with pytest.raises(ValueError, match="transcript peaks"):
        build_authority_receipt(
            evaluator_build_digest=_digest("evaluator-build"),
            boundary_manifest_digest=_digest("boundary-manifest"),
            plan_commitment=_digest("private-plan"),
            accelerator_attestation=_attestation(),
            candidate_artifact_digest=candidate_request.artifact_digest,
            incumbent_artifact_digest=incumbent_request.artifact_digest,
            measurements=measurements,
            report=wrong_resources,
            signing_key_id=_HMAC_KEY_ID,
            signing_secret=_HMAC_SECRET,
        )


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
    with pytest.raises(ValueError, match="candidate and incumbent"):
        build_authority_receipt(
            evaluator_build_digest=_digest("evaluator-build"),
            boundary_manifest_digest=_digest("boundary-manifest"),
            plan_commitment=_digest("private-plan"),
            accelerator_attestation=_attestation(),
            candidate_artifact_digest=_digest("candidate"),
            incumbent_artifact_digest=_digest("incumbent"),
            measurements=(measurement,),
            report=_report(_digest("candidate"), _digest("incumbent"), candidate_peak=1, incumbent_peak=1),
            signing_key_id=_HMAC_KEY_ID,
            signing_secret=_HMAC_SECRET,
        )


def test_authority_receipt_rejects_replayed_messages_and_stays_constant_size() -> None:
    measurements = tuple(
        AuthorityMeasurement(
            sequence=index,
            role="candidate" if index % 2 == 0 else "incumbent",
            request_digest=_digest(f"request-{index}"),
            response_digest=_digest(f"response-{index}"),
            input_commitment=_digest(f"input-{index}"),
            output_commitment=_digest(f"output-{index}"),
            elapsed_ns=index + 1,
            observed_peak_memory_bytes=1_000 + index,
            outcome="complete",
        )
        for index in range(2_000)
    )
    report = _report(
        _digest("candidate"),
        _digest("incumbent"),
        candidate_peak=2_998,
        incumbent_peak=2_999,
    )
    receipt = build_authority_receipt(
        evaluator_build_digest=_digest("evaluator-build"),
        boundary_manifest_digest=_digest("boundary-manifest"),
        plan_commitment=_digest("private-plan"),
        accelerator_attestation=_attestation(),
        candidate_artifact_digest=_digest("candidate"),
        incumbent_artifact_digest=_digest("incumbent"),
        measurements=measurements,
        report=report,
        signing_key_id=_HMAC_KEY_ID,
        signing_secret=_HMAC_SECRET,
    )

    assert receipt.transcript.measurement_count == 2_000
    assert "measurements" not in receipt.model_dump(mode="json")
    assert len(receipt.model_dump_json()) < 4_096

    replayed = list(measurements)
    replayed[-1] = replayed[-1].model_copy(update={"response_digest": replayed[0].response_digest})
    with pytest.raises(ValueError, match="replayed response digest"):
        build_authority_receipt(
            evaluator_build_digest=_digest("evaluator-build"),
            boundary_manifest_digest=_digest("boundary-manifest"),
            plan_commitment=_digest("private-plan"),
            accelerator_attestation=_attestation(),
            candidate_artifact_digest=_digest("candidate"),
            incumbent_artifact_digest=_digest("incumbent"),
            measurements=tuple(replayed),
            report=report,
            signing_key_id=_HMAC_KEY_ID,
            signing_secret=_HMAC_SECRET,
        )


def test_authority_models_reject_coercible_protocol_values() -> None:
    request = _request()
    with pytest.raises(ValidationError, match="int_type"):
        AuthorityRequest.model_validate({**request.model_dump(mode="json"), "sequence": "0"})
    with pytest.raises(ValidationError, match="int_type"):
        AuthorityRequest.model_validate({**request.model_dump(mode="json"), "payload_bytes": False})


@pytest.mark.skipif(os.name == "nt", reason="authority secret ownership and mode are POSIX host controls")
def test_authority_hmac_secret_reader_rejects_unsafe_or_unstable_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = tmp_path / "authority.secret"
    secret.write_bytes(_HMAC_SECRET)
    secret.chmod(0o600)
    assert read_authority_hmac_secret(secret) == _HMAC_SECRET

    with pytest.raises(ValueError, match="regular file"):
        read_authority_hmac_secret(Path("/"))

    secret.chmod(0o644)
    with pytest.raises(ValueError, match="exactly 0400 or 0600"):
        read_authority_hmac_secret(secret)
    secret.chmod(0o600)

    oversized = tmp_path / "oversized.secret"
    oversized.write_bytes(b"x" * 4_097)
    oversized.chmod(0o600)
    with pytest.raises(ValueError, match="between 32 and 4096"):
        read_authority_hmac_secret(oversized)

    secret_dir = tmp_path / "secret-dir"
    secret_dir.mkdir()
    nested = secret_dir / "nested.secret"
    nested.write_bytes(_HMAC_SECRET)
    nested.chmod(0o600)
    symlink = tmp_path / "secret-link"
    symlink.symlink_to(secret_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="cannot contain symlinks"):
        read_authority_hmac_secret(symlink / nested.name)

    real_fstat = os.fstat
    calls = 0

    def unstable_fstat(descriptor: int) -> os.stat_result | SimpleNamespace:
        nonlocal calls
        calls += 1
        result = real_fstat(descriptor)
        if calls != 2:
            return result
        return SimpleNamespace(
            st_dev=result.st_dev,
            st_ino=result.st_ino,
            st_mode=result.st_mode,
            st_uid=result.st_uid,
            st_size=result.st_size,
            st_mtime_ns=result.st_mtime_ns + 1,
        )

    monkeypatch.setattr(os, "fstat", unstable_fstat)
    with pytest.raises(ValueError, match="changed while it was read"):
        read_authority_hmac_secret(secret)
