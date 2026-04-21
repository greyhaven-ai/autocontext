"""Exception → reason-key lookup with SDK-version-presence guards.

Spec §4.3 + §10 risks. Classes absent in older ``openai`` SDK versions fall
through to ``uncategorized``.
"""
from __future__ import annotations

import openai

from autocontext.production_traces.taxonomy import (
    OPENAI_ERROR_REASONS,
    OpenAiErrorReasonKey,
)


def map_exception_to_reason(exc: BaseException) -> OpenAiErrorReasonKey:
    """Look up ``exc``'s class name in the taxonomy; ``uncategorized`` on miss."""
    name = type(exc).__name__
    return OPENAI_ERROR_REASONS.get(name, "uncategorized")  # type: ignore[return-value]


def is_mapped_class_present(class_name: str) -> bool:
    """Test helper — does the installed OpenAI SDK export ``class_name``?"""
    return hasattr(openai, class_name)
