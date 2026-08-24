"""Credential-safe text for durable kernel-generation receipts."""

from __future__ import annotations

import re

from autocontext.sharing.redactor import redact_content

_AUTHORIZATION_HEADER = re.compile(
    r"(?i)(\bauthorization\s*:\s*)(?:bearer|basic|token)\s+[^\s,;]+"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(api[-_ ]?key|access[-_ ]?token|token|secret|password|credential)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_CREDENTIAL_QUERY = re.compile(
    r"(?i)([?&](?:api[-_]?key|access[-_]?token|token|secret|password|credential)=)[^&#\s]+"
)
_CREDENTIAL_URL = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@")


def sanitize_provider_error(exc: Exception) -> str:
    """Return a bounded error string with common credential forms removed."""
    text = str(exc) or type(exc).__name__
    text = _AUTHORIZATION_HEADER.sub(r"\1[REDACTED_CREDENTIAL]", text)
    text = _CREDENTIAL_ASSIGNMENT.sub(r"\1\2[REDACTED_CREDENTIAL]", text)
    text = _CREDENTIAL_QUERY.sub(r"\1[REDACTED_CREDENTIAL]", text)
    text = _CREDENTIAL_URL.sub(r"\1[REDACTED_CREDENTIAL]@", text)
    return redact_content(text)[:1_000]
