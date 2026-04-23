"""Snapshot tests for Anthropic error-reason taxonomy."""
from __future__ import annotations

import types

from autocontext.production_traces.taxonomy import (
    ANTHROPIC_ERROR_REASON_KEYS,
    ANTHROPIC_ERROR_REASONS,
)


def test_table_has_all_locked_keys() -> None:
    expected = {
        "rateLimited", "timeout", "badRequest", "authentication",
        "permissionDenied", "notFound", "apiConnection", "overloaded",
        "upstreamError", "uncategorized",
    }
    assert set(ANTHROPIC_ERROR_REASON_KEYS) == expected


def test_classes_map_to_locked_keys() -> None:
    expected = {
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
    assert ANTHROPIC_ERROR_REASONS == expected


def test_table_is_frozen() -> None:
    assert isinstance(ANTHROPIC_ERROR_REASONS, types.MappingProxyType)
