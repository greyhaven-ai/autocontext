"""OpenAI exception class → ``outcome.error.type`` taxonomy.

Keys are the exact camelCase strings committed to spec §4.3. Values in this
module are *class names* (strings) rather than imported classes so the lookup
table stays importable even when a particular SDK version doesn't define a
class (``ContentFilterFinishReasonError`` was added mid-1.x series).

Cross-runtime parity: ``ts/src/production-traces/taxonomy/openai-error-reasons.ts``
MUST export a ``Record<string, string>`` with the same keys + values. Parity
tests keep the two in lock-step.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Final, Literal

OpenAiErrorReasonKey = Literal[
    "rateLimited",
    "timeout",
    "badRequest",
    "authentication",
    "permissionDenied",
    "notFound",
    "apiConnection",
    "contentFilter",
    "lengthCap",
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
    "ContentFilterFinishReasonError": "contentFilter",
    "LengthFinishReasonError": "lengthCap",
    "UnprocessableEntityError": "upstreamError",
    "ConflictError": "upstreamError",
    "APIError": "upstreamError",
}

OPENAI_ERROR_REASONS: Final = MappingProxyType(_RAW)
OPENAI_ERROR_REASON_KEYS: Final = frozenset(_RAW.values()) | {"uncategorized"}
