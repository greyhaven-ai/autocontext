"""Shared security boundaries for outbound requests and artifact scanning."""

from __future__ import annotations

from autocontext.security.outbound_url import (
    OutboundHttpError,
    OutboundResponse,
    OutboundUrlError,
    OutboundUrlPolicy,
    request_outbound_bytes,
    validate_outbound_url,
)
from autocontext.security.scanner import ScanFinding, ScanResult, SecretScanner, is_trufflehog_available

__all__ = [
    "OutboundHttpError",
    "OutboundResponse",
    "OutboundUrlError",
    "OutboundUrlPolicy",
    "ScanFinding",
    "ScanResult",
    "SecretScanner",
    "is_trufflehog_available",
    "request_outbound_bytes",
    "validate_outbound_url",
]
