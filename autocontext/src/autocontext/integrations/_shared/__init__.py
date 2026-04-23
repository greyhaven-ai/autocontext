"""Shared primitives for autocontext integration libraries.

Provider-specific integrations (``autocontext.integrations.openai``,
``autocontext.integrations.anthropic``, etc.) consume these via direct
import or via re-exports from their own top-level module.

Stability commitment: the surface exported here follows SemVer with the
parent ``autocontext`` package. See ``STABILITY.md`` in this directory.
"""
from autocontext.integrations._shared.session import (
    autocontext_session,
    current_session,
)
from autocontext.integrations._shared.sink import FileSink, TraceSink

__all__ = [
    "FileSink",
    "TraceSink",
    "autocontext_session",
    "current_session",
]
