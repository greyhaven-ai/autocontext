"""Anthropic exception class → ``outcome.error.type`` taxonomy.

Parity with TS at ``ts/src/production-traces/taxonomy/anthropic-error-reasons.ts``.
Class names stored as strings so the table imports cleanly across SDK versions —
``OverloadedError`` exists throughout Anthropic 0.x but we shouldn't bind to it
at import time.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Final, Literal

AnthropicErrorReasonKey = Literal[
    "rateLimited",
    "timeout",
    "badRequest",
    "authentication",
    "permissionDenied",
    "notFound",
    "apiConnection",
    "overloaded",
    "upstreamError",
    "uncategorized",
]

_RAW: Final = {
    "RateLimitError": "rateLimited",
    "APITimeoutError": "timeout",
    "BadRequestError": "badRequest",
    "AuthenticationError": "authentication",
    "PermissionDeniedError": "permissionDenied",
    "NotFoundError": "notFound",
    "APIConnectionError": "apiConnection",
    "OverloadedError": "overloaded",
    "ConflictError": "upstreamError",
    "UnprocessableEntityError": "upstreamError",
    "InternalServerError": "upstreamError",
    "APIStatusError": "upstreamError",
    "APIError": "upstreamError",
}

ANTHROPIC_ERROR_REASONS: Final = MappingProxyType(_RAW)
ANTHROPIC_ERROR_REASON_KEYS: Final = frozenset(_RAW.values()) | {"uncategorized"}
