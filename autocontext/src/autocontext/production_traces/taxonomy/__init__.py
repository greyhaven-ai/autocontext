"""Shared taxonomy constants for production-traces integrations.

Each submodule is pure data: lookup tables + constant names. No runtime logic.
Parity with the TypeScript counterpart under ``ts/src/production-traces/taxonomy/``
is enforced by snapshot tests + cross-runtime parity tests.
"""
from __future__ import annotations

from typing import Final, Literal

from autocontext.production_traces.taxonomy.anthropic_error_reasons import (
    ANTHROPIC_ERROR_REASON_KEYS,
    ANTHROPIC_ERROR_REASONS,
    AnthropicErrorReasonKey,
)
from autocontext.production_traces.taxonomy.openai_error_reasons import (
    OPENAI_ERROR_REASON_KEYS,
    OPENAI_ERROR_REASONS,
    OpenAiErrorReasonKey,
)

OutcomeReasonKey = Literal[
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
    "overloaded",
    "uncategorized",
]

OUTCOME_REASON_KEYS: Final = frozenset({
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
    "overloaded",
    "uncategorized",
})

__all__ = [
    "OutcomeReasonKey",
    "OUTCOME_REASON_KEYS",
    "OPENAI_ERROR_REASONS",
    "OPENAI_ERROR_REASON_KEYS",
    "OpenAiErrorReasonKey",
    "ANTHROPIC_ERROR_REASONS",
    "ANTHROPIC_ERROR_REASON_KEYS",
    "AnthropicErrorReasonKey",
]
