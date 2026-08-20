"""Trusted evidence boundary for campaign-auditor prompts."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterator, Mapping
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from autocontext.context_bundles.models import stable_digest
from autocontext.sharing.redactor import redact_content

BOUNDARY_PROVENANCE: Literal["whitelisted_redacted_v1"] = "whitelisted_redacted_v1"
_HOLDOUT_MARKERS = ("holdout_answer", "holdout answer", "answer_key", "answer key")
_BOUNDARY_SIGNING_KEY = secrets.token_bytes(32)


class EvidencePacket(Protocol):
    @property
    def hidden_holdout_answers_included(self) -> bool: ...

    @property
    def credentials_included(self) -> bool: ...

    @property
    def boundary_provenance(self) -> str: ...

    @property
    def boundary_digest(self) -> str: ...

    def to_dict(self) -> dict[str, Any]: ...


def evidence_boundary_digest(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("boundary_digest", None)
    return stable_digest(payload)


def _seal_evidence_boundary(value: Mapping[str, Any]) -> str:
    digest = evidence_boundary_digest(value)
    signature = hmac.new(_BOUNDARY_SIGNING_KEY, digest.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{digest}.{signature}"


def evidence_fingerprint(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("boundary_digest", None)
    return stable_digest(payload)


def validate_evidence_boundary(packet: EvidencePacket) -> None:
    """Verify provenance, immutability, and redaction before prompt rendering."""

    if packet.hidden_holdout_answers_included:
        raise ValueError("campaign auditor refuses packets that include hidden holdout answers")
    if packet.credentials_included:
        raise ValueError("campaign auditor refuses packets that include credentials")
    if packet.boundary_provenance != BOUNDARY_PROVENANCE:
        raise ValueError("campaign audit packet has untrusted boundary provenance")
    data = packet.to_dict()
    try:
        sealed_digest, signature = packet.boundary_digest.split(".", maxsplit=1)
    except ValueError as error:
        raise ValueError("campaign audit packet boundary signature is invalid") from error
    if sealed_digest != evidence_boundary_digest(data):
        raise ValueError("campaign audit packet boundary seal is invalid")
    expected_signature = hmac.new(
        _BOUNDARY_SIGNING_KEY,
        sealed_digest.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("campaign audit packet boundary signature is invalid")
    for value in _string_values(data):
        lowered = value.lower()
        if any(marker in lowered for marker in _HOLDOUT_MARKERS):
            raise ValueError("campaign auditor refuses possible hidden holdout answer content")
        if redact_content(value) != value:
            raise ValueError("campaign auditor refuses unredacted credential content")
        if _credential_bearing_url(value):
            raise ValueError("campaign auditor refuses credential-bearing artifact references")


def redacted_identity(value: str, label: str) -> str:
    """Preserve identity uniqueness without sending sensitive identifier text."""

    redacted = redact_content(value)
    if redacted == value:
        return value
    return f"redacted-{label}:{stable_digest({label: value})}"


def sanitized_artifact_uri(value: str) -> str:
    """Remove credentials, query strings, and fragments from evidence references."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return f"redacted-artifact:{stable_digest({'artifact_uri': value})}"
    if parsed.scheme:
        hostname = parsed.hostname or ""
        redacted_hostname = redact_content(hostname)
        if redacted_hostname != hostname:
            hostname = f"redacted-host-{stable_digest({'artifact_hostname': hostname})}"
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        try:
            port = parsed.port
        except ValueError:
            return f"redacted-artifact:{stable_digest({'artifact_uri': value})}"
        if port is not None:
            netloc = f"{netloc}:{port}"
        path = redact_content(parsed.path)
        if path != parsed.path:
            path = f"/redacted-{stable_digest({'artifact_path': parsed.path})}"
        return urlunsplit((parsed.scheme, netloc, path, "", ""))
    clean = value.split("?", maxsplit=1)[0].split("#", maxsplit=1)[0]
    redacted = redact_content(clean)
    if redacted != clean:
        return f"redacted-artifact:{stable_digest({'artifact_uri': clean})}"
    return clean


def redacted_fields(data: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    return {key: redact_value(value) for key, value in data.items() if key in model.model_fields}


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_content(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items() if not _secret_key(key)}
    return value


def bounded_redacted_text(value: Any, max_chars: int) -> str:
    text = redact_content(value) if isinstance(value, str) else ""
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def _string_values(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _string_values(item)


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("secret", "password", "credential", "api_key", "authorization"))


def _credential_bearing_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    if not parsed.scheme:
        return False
    if parsed.username is not None or parsed.password is not None:
        return True
    sensitive = ("token", "key", "signature", "sig", "credential", "auth", "password", "secret")
    return any(marker in parsed.query.lower() for marker in sensitive)


__all__ = [
    "BOUNDARY_PROVENANCE",
    "bounded_redacted_text",
    "evidence_boundary_digest",
    "evidence_fingerprint",
    "redacted_identity",
    "redact_value",
    "redacted_fields",
    "sanitized_artifact_uri",
    "validate_evidence_boundary",
]
