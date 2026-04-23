"""Tests for the cross-provider shared OutcomeReasonKey union."""
from __future__ import annotations

from autocontext.production_traces.taxonomy import (
    OUTCOME_REASON_KEYS,
)


def test_shared_outcome_reason_keys_includes_all_provider_keys() -> None:
    expected = {
        "rateLimited", "timeout", "badRequest", "authentication",
        "permissionDenied", "notFound", "apiConnection", "contentFilter",
        "lengthCap", "upstreamError", "overloaded", "uncategorized",
    }
    assert OUTCOME_REASON_KEYS == expected


def test_openai_keys_are_subset_of_shared() -> None:
    from autocontext.production_traces.taxonomy import OPENAI_ERROR_REASON_KEYS
    assert OPENAI_ERROR_REASON_KEYS <= OUTCOME_REASON_KEYS
