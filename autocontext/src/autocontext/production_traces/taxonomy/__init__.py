"""Shared taxonomy constants for production-traces integrations.

Each submodule is pure data: lookup tables + constant names. No runtime logic.
Parity with the TypeScript counterpart under ``ts/src/production-traces/taxonomy/``
is enforced by snapshot tests + cross-runtime parity tests.
"""
from autocontext.production_traces.taxonomy.openai_error_reasons import (
    OPENAI_ERROR_REASON_KEYS,
    OPENAI_ERROR_REASONS,
    OpenAiErrorReasonKey,
)

__all__ = [
    "OPENAI_ERROR_REASONS",
    "OPENAI_ERROR_REASON_KEYS",
    "OpenAiErrorReasonKey",
]
