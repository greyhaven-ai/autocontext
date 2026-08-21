"""Authenticated envelope for portable kernel profile evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autocontext.kernel_evolution.authority_protocol import (
    MAX_AUTHORITY_HMAC_SECRET_BYTES,
    MIN_AUTHORITY_HMAC_SECRET_BYTES,
    canonical_authority_digest,
)

PROFILE_EVIDENCE_ENVELOPE_VERSION: Literal["autocontext.kernel-profile-evidence-envelope/v1"] = (
    "autocontext.kernel-profile-evidence-envelope/v1"
)
MAX_PROFILE_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_PROFILE_EVIDENCE_ENVELOPE_BYTES = MAX_PROFILE_EVIDENCE_BYTES + 64 * 1024
MAX_PROFILE_EVIDENCE_DEPTH = 64
MAX_PROFILE_EVIDENCE_ENTRIES = 1_000_000
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
AuthenticationTag = Annotated[str, Field(pattern=r"^hmac-sha256:[0-9a-f]{64}$")]
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


class _ProfileEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True, strict=True)


class ProfileEvidenceAuthentication(_ProfileEvidenceModel):
    """Operator-pinned authentication metadata for one complete profile payload."""

    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    key_id: str
    tag: AuthenticationTag

    @model_validator(mode="after")
    def validate_key_id(self) -> Self:
        if _SAFE_IDENTIFIER.fullmatch(self.key_id) is None:
            raise ValueError("profile evidence authentication key_id must be a safe non-empty identifier")
        return self


class ProfileEvidenceEnvelope(_ProfileEvidenceModel):
    """Strict outer envelope whose tag authenticates every portable evidence field."""

    schema_version: Literal["autocontext.kernel-profile-evidence-envelope/v1"] = (
        PROFILE_EVIDENCE_ENVELOPE_VERSION
    )
    profile: dict[str, Any]
    content_digest: Digest
    authentication: ProfileEvidenceAuthentication

    @model_validator(mode="after")
    def validate_profile_json(self) -> Self:
        _validate_profile_json(self.profile)
        return self


def _validate_profile_json(profile: dict[str, Any]) -> None:
    if not profile:
        raise ValueError("profile evidence payload must not be empty")
    schema_version = profile.get("schema_version")
    if (
        not isinstance(schema_version, str)
        or not schema_version
        or any(char in schema_version for char in "\r\n\0")
    ):
        raise ValueError("profile evidence payload must contain a safe schema_version")
    entries = 0

    def validate(value: Any, *, depth: int) -> None:
        nonlocal entries
        if depth > MAX_PROFILE_EVIDENCE_DEPTH:
            raise ValueError("profile evidence payload exceeded its nesting limit")
        entries += 1
        if entries > MAX_PROFILE_EVIDENCE_ENTRIES:
            raise ValueError("profile evidence payload exceeded its entry limit")
        if value is None or type(value) in {bool, int, str}:
            return
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("profile evidence payload must contain only finite numbers")
            return
        if type(value) is list:
            for item in value:
                validate(item, depth=depth + 1)
            return
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("profile evidence JSON object keys must be strings")
                validate(item, depth=depth + 1)
            return
        raise ValueError("profile evidence payload must contain only canonical JSON values")

    validate(profile, depth=0)
    if len(_canonical_bytes(profile)) > MAX_PROFILE_EVIDENCE_BYTES:
        raise ValueError("profile evidence payload exceeded its byte limit")


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _decode_envelope_json(raw: bytes | str) -> dict[str, Any]:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_PROFILE_EVIDENCE_ENVELOPE_BYTES:
        raise ValueError("profile evidence envelope exceeded its byte limit")

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, value in pairs:
            if key in decoded:
                raise ValueError(f"duplicate JSON object key {key!r}")
            decoded[key] = value
        return decoded

    decoded = json.loads(
        encoded.decode("utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(decoded, dict):
        raise ValueError("profile evidence envelope root must be a JSON object")
    return decoded


def _validated_secret(secret: bytes) -> bytes:
    if not isinstance(secret, bytes):
        raise TypeError("profile evidence HMAC secret must be bytes")
    if not MIN_AUTHORITY_HMAC_SECRET_BYTES <= len(secret) <= MAX_AUTHORITY_HMAC_SECRET_BYTES:
        raise ValueError(
            "profile evidence HMAC secret must contain between "
            f"{MIN_AUTHORITY_HMAC_SECRET_BYTES} and {MAX_AUTHORITY_HMAC_SECRET_BYTES} bytes"
        )
    return secret


def _authentication_payload(envelope: ProfileEvidenceEnvelope) -> dict[str, Any]:
    payload = envelope.model_dump(mode="json")
    authentication = dict(payload["authentication"])
    authentication.pop("tag")
    payload["authentication"] = authentication
    return payload


def _authentication_tag(envelope: ProfileEvidenceEnvelope, secret: bytes) -> str:
    raw = _canonical_bytes(_authentication_payload(envelope))
    tag = hmac.new(_validated_secret(secret), raw, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{tag}"


def build_profile_evidence_envelope(
    profile: dict[str, Any],
    *,
    signing_key_id: str,
    signing_secret: bytes,
) -> ProfileEvidenceEnvelope:
    """Authenticate one canonical, bounded profile payload in a strict envelope."""

    unsigned = ProfileEvidenceEnvelope(
        profile=profile,
        content_digest=canonical_authority_digest(profile),
        authentication=ProfileEvidenceAuthentication(
            key_id=signing_key_id,
            tag="hmac-sha256:" + "0" * 64,
        ),
    )
    tag = _authentication_tag(unsigned, signing_secret)
    envelope = unsigned.model_copy(
        update={"authentication": unsigned.authentication.model_copy(update={"tag": tag})}
    )
    verify_profile_evidence_envelope(
        envelope,
        trusted_key_id=signing_key_id,
        trusted_secret=signing_secret,
    )
    return envelope


def verify_profile_evidence_envelope(
    envelope: ProfileEvidenceEnvelope | dict[str, Any] | bytes | str,
    *,
    trusted_key_id: str,
    trusted_secret: bytes,
) -> ProfileEvidenceEnvelope:
    """Verify exact content, operator identity, and authentication for an envelope."""

    candidate = _decode_envelope_json(envelope) if isinstance(envelope, (bytes, str)) else envelope
    validated = ProfileEvidenceEnvelope.model_validate(candidate)
    if _SAFE_IDENTIFIER.fullmatch(trusted_key_id) is None:
        raise ValueError("trusted profile evidence key_id must be a safe non-empty identifier")
    if validated.authentication.key_id != trusted_key_id:
        raise ValueError("profile evidence authentication key is not trusted")
    if not hmac.compare_digest(validated.content_digest, canonical_authority_digest(validated.profile)):
        raise ValueError("profile evidence content digest does not match its payload")
    expected_tag = _authentication_tag(validated, trusted_secret)
    if not hmac.compare_digest(validated.authentication.tag, expected_tag):
        raise ValueError("profile evidence authentication tag is invalid")
    return validated


__all__ = [
    "MAX_PROFILE_EVIDENCE_BYTES",
    "MAX_PROFILE_EVIDENCE_DEPTH",
    "MAX_PROFILE_EVIDENCE_ENVELOPE_BYTES",
    "MAX_PROFILE_EVIDENCE_ENTRIES",
    "PROFILE_EVIDENCE_ENVELOPE_VERSION",
    "ProfileEvidenceAuthentication",
    "ProfileEvidenceEnvelope",
    "build_profile_evidence_envelope",
    "verify_profile_evidence_envelope",
]
