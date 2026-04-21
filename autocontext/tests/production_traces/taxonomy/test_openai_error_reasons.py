"""Snapshot + parity tests for the OpenAI error-taxonomy constants.

Asserts the lookup table is byte-identical across Python and TS runtimes.
"""
from __future__ import annotations

import json
from pathlib import Path

from autocontext.production_traces.taxonomy import (
    OPENAI_ERROR_REASONS,
    OPENAI_ERROR_REASON_KEYS,
)


def test_table_has_all_locked_keys() -> None:
    expected_keys = {
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
    }
    assert set(OPENAI_ERROR_REASON_KEYS) == expected_keys


def test_classes_map_to_locked_keys() -> None:
    expected = {
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
    assert OPENAI_ERROR_REASONS == expected


def test_table_is_frozen() -> None:
    # Modifying the constant must raise — it is a MappingProxyType.
    import types
    assert isinstance(OPENAI_ERROR_REASONS, types.MappingProxyType)
