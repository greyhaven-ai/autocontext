"""Typed contracts for a trusted evaluator and isolated accelerator candidates.

The models in this module are intentionally accelerator-neutral.  CUDA, MIG,
and H100 are deployment profiles; they are not protocol identity fields.
Candidate processes may return tensor bytes and a bounded outcome code, but
only the trusted evaluator may create measurements or an authority receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

AUTHORITY_PROTOCOL_VERSION: Literal["autocontext.accelerator-authority/v1"] = (
    "autocontext.accelerator-authority/v1"
)
AUTHORITY_RECEIPT_VERSION: Literal["autocontext.accelerator-authority-receipt/v1"] = (
    "autocontext.accelerator-authority-receipt/v1"
)
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
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
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


class _AuthorityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


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


class KernelEvaluatorAuthorityReceipt(_AuthorityModel):
    """Replay-verifiable evidence that candidates did not own evaluation."""

    schema_version: Literal["autocontext.accelerator-authority-receipt/v1"] = AUTHORITY_RECEIPT_VERSION
    protocol_version: Literal["autocontext.accelerator-authority/v1"] = AUTHORITY_PROTOCOL_VERSION
    evaluator_build_digest: Digest
    boundary_manifest_digest: Digest
    plan_commitment: Digest
    accelerator_attestation: AcceleratorAttestation
    candidate_artifact_digest: Digest
    incumbent_artifact_digest: Digest
    measurements: tuple[AuthorityMeasurement, ...]
    transcript_digest: Digest
    report_content_digest: Digest

    @model_validator(mode="after")
    def validate_measurement_chain(self) -> Self:
        if not self.measurements:
            raise ValueError("authority receipts require at least one evaluator-owned measurement")
        sequences = [measurement.sequence for measurement in self.measurements]
        if sequences != list(range(len(sequences))):
            raise ValueError("authority receipt measurements must have a contiguous ordered sequence")
        roles = {measurement.role for measurement in self.measurements}
        if roles != {"candidate", "incumbent"}:
            raise ValueError("authority receipts must measure candidate and incumbent independently")
        expected = canonical_authority_digest(
            {
                "protocol_version": self.protocol_version,
                "measurements": [measurement.model_dump(mode="json") for measurement in self.measurements],
            }
        )
        if self.transcript_digest != expected:
            raise ValueError("authority receipt transcript digest does not match its measurements")
        return self

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
) -> KernelEvaluatorAuthorityReceipt:
    """Create the canonical host-owned receipt for a completed evaluation."""

    transcript_digest = canonical_authority_digest(
        {
            "protocol_version": AUTHORITY_PROTOCOL_VERSION,
            "measurements": [measurement.model_dump(mode="json") for measurement in measurements],
        }
    )
    return KernelEvaluatorAuthorityReceipt(
        evaluator_build_digest=evaluator_build_digest,
        boundary_manifest_digest=boundary_manifest_digest,
        plan_commitment=plan_commitment,
        accelerator_attestation=accelerator_attestation,
        candidate_artifact_digest=candidate_artifact_digest,
        incumbent_artifact_digest=incumbent_artifact_digest,
        measurements=measurements,
        transcript_digest=transcript_digest,
        report_content_digest=authority_report_content_digest(report),
    )


def verify_authority_receipt(receipt: KernelEvaluatorAuthorityReceipt, report: dict[str, Any]) -> None:
    """Fail closed when a persisted receipt cannot replay against its report."""

    if receipt.report_content_digest != authority_report_content_digest(report):
        raise ValueError("authority receipt is bound to different report content")
    if report.get("candidate_artifact_digest") != receipt.candidate_artifact_digest:
        raise ValueError("authority receipt candidate identity does not match the report")
    if report.get("incumbent_artifact_digest") != receipt.incumbent_artifact_digest:
        raise ValueError("authority receipt incumbent identity does not match the report")
    protocol = report.get("protocol")
    if not isinstance(protocol, dict) or protocol.get("seed_commitment") != receipt.plan_commitment:
        raise ValueError("authority receipt plan commitment does not match the report")


__all__ = [
    "AUTHORITY_PROTOCOL_VERSION",
    "AUTHORITY_RECEIPT_VERSION",
    "AcceleratorAttestation",
    "AuthorityExecutionOutcome",
    "AuthorityMeasurement",
    "AuthorityOperation",
    "AuthorityRequest",
    "AuthorityResponse",
    "AuthorityResponseOutcome",
    "AuthorityRole",
    "KernelEvaluatorAuthorityReceipt",
    "authority_message_digest",
    "authority_report_content_digest",
    "build_authority_receipt",
    "canonical_authority_digest",
    "verify_authority_receipt",
]
