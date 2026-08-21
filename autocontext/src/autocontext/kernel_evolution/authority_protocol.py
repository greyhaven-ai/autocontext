"""Typed contracts for a trusted evaluator and isolated accelerator candidates.

The models in this module are intentionally accelerator-neutral. CUDA, MIG,
and H100 are deployment profiles; they are not protocol identity fields.
Candidate processes may return tensor bytes and a bounded outcome code, but
only the trusted evaluator may create measurements or an authenticated receipt.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

AUTHORITY_PROTOCOL_VERSION: Literal["autocontext.accelerator-authority/v1"] = (
    "autocontext.accelerator-authority/v1"
)
AUTHORITY_RECEIPT_VERSION: Literal["autocontext.accelerator-authority-receipt/v1"] = (
    "autocontext.accelerator-authority-receipt/v1"
)
AUTHORITY_AUTHENTICATION_ALGORITHM: Literal["hmac-sha256"] = "hmac-sha256"
MIN_AUTHORITY_HMAC_SECRET_BYTES = 32
MAX_AUTHORITY_HMAC_SECRET_BYTES = 4_096
AuthorityRole = Literal["candidate", "incumbent"]
AuthorityOperation = Literal["initialize", "execute", "shutdown"]
AuthorityResponseOutcome = Literal["complete", "candidate_error", "oom", "protocol_error"]
AuthorityExecutionOutcome = Literal[
    "complete",
    "candidate_error",
    "timeout",
    "oom",
    "protocol_corruption",
    "evaluator_crashed",
    "candidate_crashed",
    "teardown_failed",
]
_AUTHORITY_EXECUTION_OUTCOMES: tuple[AuthorityExecutionOutcome, ...] = (
    "complete",
    "candidate_error",
    "timeout",
    "oom",
    "protocol_corruption",
    "evaluator_crashed",
    "candidate_crashed",
    "teardown_failed",
)
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
AuthenticationTag = Annotated[str, Field(pattern=r"^hmac-sha256:[0-9a-f]{64}$")]
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


def canonical_authority_digest(payload: dict[str, Any] | bytes | str) -> str:
    """Return a branded SHA-256 digest for a canonical authority payload."""

    if isinstance(payload, dict):
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = payload
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _canonical_authority_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _validate_key_id(value: str) -> None:
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("authority authentication key_id must be a safe non-empty identifier")


def _validated_hmac_secret(secret: bytes) -> bytes:
    if not isinstance(secret, bytes):
        raise TypeError("authority HMAC secret must be bytes")
    if not MIN_AUTHORITY_HMAC_SECRET_BYTES <= len(secret) <= MAX_AUTHORITY_HMAC_SECRET_BYTES:
        raise ValueError(
            "authority HMAC secret must contain between "
            f"{MIN_AUTHORITY_HMAC_SECRET_BYTES} and {MAX_AUTHORITY_HMAC_SECRET_BYTES} bytes"
        )
    return secret


def read_authority_hmac_secret(path: Path) -> bytes:
    """Read a bounded, stable, non-symlink host trust secret exactly once."""

    supplied = Path(path)
    if ".." in supplied.parts:
        raise ValueError("authority HMAC secret path must not contain '..'")
    absolute = supplied if supplied.is_absolute() else Path.cwd() / supplied
    current = Path(absolute.anchor)
    current_stat = current.lstat()
    try:
        for component in absolute.parts[1:]:
            current /= component
            current_stat = current.lstat()
            if stat.S_ISLNK(current_stat.st_mode):
                raise ValueError("authority HMAC secret path cannot contain symlinks")
    except FileNotFoundError as exc:
        raise ValueError("authority HMAC secret file does not exist") from exc
    if not stat.S_ISREG(current_stat.st_mode):
        raise ValueError("authority HMAC secret must be a regular file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("authority HMAC secret must be a regular file")
        expected_owner = getattr(os, "geteuid", lambda: before.st_uid)()
        if before.st_uid != expected_owner:
            raise ValueError("authority HMAC secret must be owned by the current host user")
        if stat.S_IMODE(before.st_mode) not in {0o400, 0o600}:
            raise ValueError("authority HMAC secret permissions must be exactly 0400 or 0600")
        payload = bytearray()
        while len(payload) <= MAX_AUTHORITY_HMAC_SECRET_BYTES:
            chunk = os.read(descriptor, MAX_AUTHORITY_HMAC_SECRET_BYTES + 1 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        current_stat = absolute.lstat()
        identities = {
            (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_size, item.st_mtime_ns)
            for item in (before, after, current_stat)
        }
        if len(identities) != 1:
            raise ValueError("authority HMAC secret changed while it was read")
        return _validated_hmac_secret(bytes(payload))
    finally:
        os.close(descriptor)


class _AuthorityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True, strict=True)


class AcceleratorAttestation(_AuthorityModel):
    """Host-verified identity for one accelerator partition or device grant."""

    backend: str
    vendor: str
    architecture: str
    device_id: str
    isolation_kind: str
    enforced_memory_bytes: int = Field(ge=1)
    runtime: str
    driver: str
    attestor_id: str
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identifiers(self) -> Self:
        for name in (
            "backend",
            "vendor",
            "architecture",
            "device_id",
            "isolation_kind",
            "runtime",
            "driver",
            "attestor_id",
        ):
            value = getattr(self, name)
            if _SAFE_IDENTIFIER.fullmatch(value) is None:
                raise ValueError(f"{name} must be a safe non-empty identifier")
        for key, value in self.metadata.items():
            if _SAFE_IDENTIFIER.fullmatch(key) is None or not value or any(char in value for char in "\r\n\0"):
                raise ValueError("accelerator metadata must contain safe single-line string pairs")
        return self

    @property
    def digest(self) -> str:
        return canonical_authority_digest(self.model_dump(mode="json"))


class AuthorityRequest(_AuthorityModel):
    """One evaluator-owned request sent to an isolated candidate authority."""

    protocol_version: Literal["autocontext.accelerator-authority/v1"] = AUTHORITY_PROTOCOL_VERSION
    request_id: Digest
    session_nonce: Digest
    sequence: int = Field(ge=0)
    role: AuthorityRole
    operation: AuthorityOperation
    artifact_digest: Digest
    input_manifest_digest: Digest
    payload_digest: Digest
    payload_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_operation_payload(self) -> Self:
        empty_digest = canonical_authority_digest(b"")
        if self.operation == "shutdown" and (
            self.payload_bytes != 0 or self.payload_digest != empty_digest or self.input_manifest_digest != empty_digest
        ):
            raise ValueError("shutdown requests cannot carry candidate-controlled payloads")
        return self


class AuthorityResponse(_AuthorityModel):
    """Untrusted candidate response; timing and resource claims are excluded."""

    protocol_version: Literal["autocontext.accelerator-authority/v1"] = AUTHORITY_PROTOCOL_VERSION
    request_id: Digest
    session_nonce: Digest
    sequence: int = Field(ge=0)
    role: AuthorityRole
    artifact_digest: Digest
    outcome: AuthorityResponseOutcome
    output_manifest_digest: Digest
    payload_digest: Digest
    payload_bytes: int = Field(ge=0)
    diagnostic_code: Literal[
        "none",
        "compile",
        "execution",
        "oom",
        "malformed_request",
        "malformed_output",
    ] = "none"

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> Self:
        if self.outcome == "complete" and self.diagnostic_code != "none":
            raise ValueError("complete responses cannot include a diagnostic code")
        if self.outcome != "complete" and self.diagnostic_code == "none":
            raise ValueError("failed responses require a bounded diagnostic code")
        if self.outcome != "complete" and (
            self.payload_bytes != 0
            or self.payload_digest != canonical_authority_digest(b"")
            or self.output_manifest_digest != canonical_authority_digest({})
        ):
            raise ValueError("failed responses cannot carry candidate-controlled output payloads")
        return self


class AuthorityMeasurement(_AuthorityModel):
    """Evaluator-owned measurement for one request/response exchange."""

    sequence: int = Field(ge=0)
    role: AuthorityRole
    request_digest: Digest
    response_digest: Digest
    input_commitment: Digest
    output_commitment: Digest
    elapsed_ns: int = Field(ge=1)
    observed_peak_memory_bytes: int = Field(ge=0)
    outcome: AuthorityExecutionOutcome


class AuthorityOutcomeCounts(_AuthorityModel):
    """Bounded outcome histogram authenticated by the trusted evaluator."""

    complete: int = Field(ge=0)
    candidate_error: int = Field(ge=0)
    timeout: int = Field(ge=0)
    oom: int = Field(ge=0)
    protocol_corruption: int = Field(ge=0)
    evaluator_crashed: int = Field(ge=0)
    candidate_crashed: int = Field(ge=0)
    teardown_failed: int = Field(ge=0)

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class AuthorityTranscriptSummary(_AuthorityModel):
    """Constant-size commitment to an evaluator-owned exchange transcript."""

    measurement_count: int = Field(ge=2)
    candidate_measurement_count: int = Field(ge=1)
    incumbent_measurement_count: int = Field(ge=1)
    unique_request_count: int = Field(ge=2)
    unique_response_count: int = Field(ge=2)
    request_set_digest: Digest
    response_set_digest: Digest
    total_elapsed_ns: int = Field(ge=2)
    candidate_observed_peak_memory_bytes: int = Field(ge=0)
    incumbent_observed_peak_memory_bytes: int = Field(ge=0)
    outcomes: AuthorityOutcomeCounts

    @model_validator(mode="after")
    def validate_totals_and_uniqueness(self) -> Self:
        if self.candidate_measurement_count + self.incumbent_measurement_count != self.measurement_count:
            raise ValueError("authority transcript role counts do not match its measurement count")
        if self.unique_request_count != self.measurement_count:
            raise ValueError("authority transcript contains duplicate request digests")
        if self.unique_response_count != self.measurement_count:
            raise ValueError("authority transcript contains duplicate response digests")
        if self.outcomes.total != self.measurement_count:
            raise ValueError("authority transcript outcome counts do not match its measurement count")
        return self


class AuthorityReceiptAuthentication(_AuthorityModel):
    """Operator-pinned authentication metadata; the shared secret is never serialized."""

    algorithm: Literal["hmac-sha256"] = AUTHORITY_AUTHENTICATION_ALGORITHM
    key_id: str
    tag: AuthenticationTag

    @model_validator(mode="after")
    def validate_key_id(self) -> Self:
        _validate_key_id(self.key_id)
        return self


class KernelEvaluatorAuthorityReceipt(_AuthorityModel):
    """Authenticated, replay-verifiable evidence that candidates did not own evaluation."""

    schema_version: Literal["autocontext.accelerator-authority-receipt/v1"] = AUTHORITY_RECEIPT_VERSION
    protocol_version: Literal["autocontext.accelerator-authority/v1"] = AUTHORITY_PROTOCOL_VERSION
    evaluator_build_digest: Digest
    boundary_manifest_digest: Digest
    plan_commitment: Digest
    accelerator_attestation: AcceleratorAttestation
    candidate_artifact_digest: Digest
    incumbent_artifact_digest: Digest
    transcript: AuthorityTranscriptSummary
    transcript_digest: Digest
    report_content_digest: Digest
    authentication: AuthorityReceiptAuthentication

    @property
    def receipt_digest(self) -> str:
        return canonical_authority_digest(self.model_dump(mode="json"))


def authority_message_digest(message: AuthorityRequest | AuthorityResponse) -> str:
    """Digest one typed message without trusting candidate serialization."""

    return canonical_authority_digest(message.model_dump(mode="json"))


def authority_report_content_digest(report: dict[str, Any]) -> str:
    """Digest a report while excluding the receipt that binds that digest."""

    payload = dict(report)
    payload.pop("evaluator_authority_receipt", None)
    return canonical_authority_digest(payload)


def _receipt_authentication_payload(receipt: KernelEvaluatorAuthorityReceipt) -> dict[str, Any]:
    payload = receipt.model_dump(mode="json")
    authentication = dict(payload["authentication"])
    authentication.pop("tag")
    payload["authentication"] = authentication
    return payload


def _authentication_tag(payload: dict[str, Any], secret: bytes) -> str:
    tag = hmac.new(_validated_hmac_secret(secret), _canonical_authority_bytes(payload), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{tag}"


def _summarize_measurements(
    measurements: tuple[AuthorityMeasurement, ...],
) -> tuple[AuthorityTranscriptSummary, str]:
    if not measurements:
        raise ValueError("authority receipts require at least one evaluator-owned measurement")
    sequences = [measurement.sequence for measurement in measurements]
    if sequences != list(range(len(sequences))):
        raise ValueError("authority receipt measurements must have a contiguous ordered sequence")
    roles = {measurement.role for measurement in measurements}
    if roles != {"candidate", "incumbent"}:
        raise ValueError("authority receipts must measure candidate and incumbent independently")

    request_digests = [measurement.request_digest for measurement in measurements]
    response_digests = [measurement.response_digest for measurement in measurements]
    unique_request_count = len(set(request_digests))
    unique_response_count = len(set(response_digests))
    if unique_request_count != len(measurements):
        raise ValueError("authority receipt measurements contain a replayed request digest")
    if unique_response_count != len(measurements):
        raise ValueError("authority receipt measurements contain a replayed response digest")

    outcome_counts = {
        outcome: sum(measurement.outcome == outcome for measurement in measurements)
        for outcome in _AUTHORITY_EXECUTION_OUTCOMES
    }
    summary = AuthorityTranscriptSummary(
        measurement_count=len(measurements),
        candidate_measurement_count=sum(measurement.role == "candidate" for measurement in measurements),
        incumbent_measurement_count=sum(measurement.role == "incumbent" for measurement in measurements),
        unique_request_count=unique_request_count,
        unique_response_count=unique_response_count,
        request_set_digest=canonical_authority_digest(
            {"kind": "authority-request-set/v1", "digests": sorted(request_digests)}
        ),
        response_set_digest=canonical_authority_digest(
            {"kind": "authority-response-set/v1", "digests": sorted(response_digests)}
        ),
        total_elapsed_ns=sum(measurement.elapsed_ns for measurement in measurements),
        candidate_observed_peak_memory_bytes=max(
            measurement.observed_peak_memory_bytes
            for measurement in measurements
            if measurement.role == "candidate"
        ),
        incumbent_observed_peak_memory_bytes=max(
            measurement.observed_peak_memory_bytes
            for measurement in measurements
            if measurement.role == "incumbent"
        ),
        outcomes=AuthorityOutcomeCounts(**outcome_counts),
    )
    transcript_digest = canonical_authority_digest(
        {
            "protocol_version": AUTHORITY_PROTOCOL_VERSION,
            "measurements": [measurement.model_dump(mode="json") for measurement in measurements],
        }
    )
    return summary, transcript_digest


def _require_report_resource(report: dict[str, Any], name: str) -> int:
    resources = report.get("resources")
    if not isinstance(resources, dict):
        raise ValueError("authority receipt report is missing trusted resource telemetry")
    value = resources.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"authority receipt report has invalid {name}")
    return value


def _verify_report_outcomes(receipt: KernelEvaluatorAuthorityReceipt, report: dict[str, Any]) -> None:
    outcomes = receipt.transcript.outcomes
    status = report.get("evaluation_status")
    failure = report.get("failure_kind")
    if status == "complete":
        if failure is not None or outcomes.complete != receipt.transcript.measurement_count:
            raise ValueError("authority transcript outcomes contradict a complete report")
        return
    if status not in {"candidate_error", "infrastructure_error"} or not isinstance(failure, str):
        raise ValueError("authority receipt report has an invalid evaluation outcome")
    required_outcomes: dict[str, tuple[int, ...]] = {
        "oom": (outcomes.oom,),
        "protocol_corruption": (outcomes.protocol_corruption,),
        "candidate_crash": (outcomes.candidate_crashed,),
        "evaluator_crash": (outcomes.evaluator_crashed,),
        "timeout": (outcomes.timeout,),
        "teardown_failure": (outcomes.teardown_failed,),
        "syntax": (outcomes.candidate_error, outcomes.candidate_crashed),
        "compile": (outcomes.candidate_error, outcomes.candidate_crashed),
        "crash": (outcomes.candidate_error, outcomes.candidate_crashed),
        "contract": (outcomes.protocol_corruption,),
    }
    required = required_outcomes.get(failure)
    if required is not None and not any(value > 0 for value in required):
        raise ValueError("authority transcript outcomes do not explain the report failure")
    if (
        failure in {"correctness", "reference_failure", "unstable_environment"}
        and outcomes.complete != receipt.transcript.measurement_count
    ):
        raise ValueError("authority transcript outcomes contradict an evaluator-owned report failure")


def verify_authority_receipt_integrity(
    receipt: KernelEvaluatorAuthorityReceipt,
    report: dict[str, Any],
) -> None:
    """Replay authenticated receipt claims against report content without granting trust."""

    if receipt.report_content_digest != authority_report_content_digest(report):
        raise ValueError("authority receipt is bound to different report content")
    if report.get("candidate_artifact_digest") != receipt.candidate_artifact_digest:
        raise ValueError("authority receipt candidate identity does not match the report")
    if report.get("incumbent_artifact_digest") != receipt.incumbent_artifact_digest:
        raise ValueError("authority receipt incumbent identity does not match the report")
    protocol = report.get("protocol")
    if not isinstance(protocol, dict) or protocol.get("seed_commitment") != receipt.plan_commitment:
        raise ValueError("authority receipt plan commitment does not match the report")
    resources = report.get("resources")
    if not isinstance(resources, dict):
        raise ValueError("authority receipt report is missing trusted resource telemetry")
    if (
        _require_report_resource(report, "candidate_observed_peak_bytes")
        != receipt.transcript.candidate_observed_peak_memory_bytes
        or _require_report_resource(report, "incumbent_observed_peak_bytes")
        != receipt.transcript.incumbent_observed_peak_memory_bytes
    ):
        raise ValueError("authority transcript peaks do not match report resource telemetry")
    if resources.get("telemetry_authority") != "trusted-evaluator-observed/v1":
        raise ValueError("authority receipt report does not identify trusted resource telemetry")
    if resources.get("accelerator_attestation_digest") != receipt.accelerator_attestation.digest:
        raise ValueError("authority receipt report is bound to a different accelerator attestation")
    if resources.get("device_total_memory_bytes") != receipt.accelerator_attestation.enforced_memory_bytes:
        raise ValueError("authority receipt report accelerator capacity does not match its attestation")
    _verify_report_outcomes(receipt, report)


def build_authority_receipt(
    *,
    evaluator_build_digest: str,
    boundary_manifest_digest: str,
    plan_commitment: str,
    accelerator_attestation: AcceleratorAttestation,
    candidate_artifact_digest: str,
    incumbent_artifact_digest: str,
    measurements: tuple[AuthorityMeasurement, ...],
    report: dict[str, Any],
    signing_key_id: str,
    signing_secret: bytes,
) -> KernelEvaluatorAuthorityReceipt:
    """Create a compact authenticated receipt from evaluator-owned measurements."""

    _validate_key_id(signing_key_id)
    _validated_hmac_secret(signing_secret)
    transcript, transcript_digest = _summarize_measurements(measurements)
    unsigned = KernelEvaluatorAuthorityReceipt(
        evaluator_build_digest=evaluator_build_digest,
        boundary_manifest_digest=boundary_manifest_digest,
        plan_commitment=plan_commitment,
        accelerator_attestation=accelerator_attestation,
        candidate_artifact_digest=candidate_artifact_digest,
        incumbent_artifact_digest=incumbent_artifact_digest,
        transcript=transcript,
        transcript_digest=transcript_digest,
        report_content_digest=authority_report_content_digest(report),
        authentication=AuthorityReceiptAuthentication(
            key_id=signing_key_id,
            tag="hmac-sha256:" + "0" * 64,
        ),
    )
    tag = _authentication_tag(_receipt_authentication_payload(unsigned), signing_secret)
    receipt = unsigned.model_copy(update={"authentication": unsigned.authentication.model_copy(update={"tag": tag})})
    verify_authority_receipt_integrity(receipt, report)
    return receipt


def verify_authority_receipt(
    receipt: KernelEvaluatorAuthorityReceipt,
    report: dict[str, Any],
    *,
    trusted_key_id: str,
    trusted_secret: bytes,
    expected_evaluator_build_digest: str | None = None,
    expected_boundary_manifest_digest: str | None = None,
) -> None:
    """Authenticate and replay a persisted receipt against pinned host trust."""

    _validate_key_id(trusted_key_id)
    if receipt.authentication.key_id != trusted_key_id:
        raise ValueError("authority receipt authentication key is not trusted")
    expected_tag = _authentication_tag(_receipt_authentication_payload(receipt), trusted_secret)
    if not hmac.compare_digest(receipt.authentication.tag, expected_tag):
        raise ValueError("authority receipt authentication tag is invalid")
    if (
        expected_evaluator_build_digest is not None
        and receipt.evaluator_build_digest != expected_evaluator_build_digest
    ):
        raise ValueError("authority receipt evaluator build does not match the host-computed digest")
    if (
        expected_boundary_manifest_digest is not None
        and receipt.boundary_manifest_digest != expected_boundary_manifest_digest
    ):
        raise ValueError("authority receipt boundary does not match the host-computed digest")
    verify_authority_receipt_integrity(receipt, report)


__all__ = [
    "AUTHORITY_AUTHENTICATION_ALGORITHM",
    "AUTHORITY_PROTOCOL_VERSION",
    "AUTHORITY_RECEIPT_VERSION",
    "MAX_AUTHORITY_HMAC_SECRET_BYTES",
    "MIN_AUTHORITY_HMAC_SECRET_BYTES",
    "AcceleratorAttestation",
    "AuthorityExecutionOutcome",
    "AuthorityMeasurement",
    "AuthorityOperation",
    "AuthorityOutcomeCounts",
    "AuthorityReceiptAuthentication",
    "AuthorityRequest",
    "AuthorityResponse",
    "AuthorityResponseOutcome",
    "AuthorityRole",
    "AuthorityTranscriptSummary",
    "KernelEvaluatorAuthorityReceipt",
    "authority_message_digest",
    "authority_report_content_digest",
    "build_authority_receipt",
    "canonical_authority_digest",
    "read_authority_hmac_secret",
    "verify_authority_receipt",
    "verify_authority_receipt_integrity",
]
