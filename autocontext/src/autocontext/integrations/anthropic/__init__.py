"""Customer-facing Anthropic integration.

Public surface: ``instrument_client``, ``FileSink``, ``autocontext_session``,
``TraceSink``. See ``STABILITY.md`` for stability commitments.

Sink + session primitives are re-exported from ``autocontext.integrations._shared``
(single source of truth across all integration libraries).
"""
from autocontext.integrations._shared import (
    FileSink,
    TraceSink,
    autocontext_session,
)
from autocontext.integrations.anthropic._wrap import instrument_client

__all__ = ["FileSink", "TraceSink", "autocontext_session", "instrument_client"]
