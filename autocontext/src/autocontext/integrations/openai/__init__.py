"""Customer-facing OpenAI integration.

Public surface: ``instrument_client``, ``FileSink``, ``autocontext_session``,
``TraceSink``. See ``STABILITY.md`` for stability commitments.
"""
from autocontext.integrations.openai._session import autocontext_session
from autocontext.integrations.openai._sink import FileSink, TraceSink
from autocontext.integrations.openai._wrap import instrument_client

__all__ = ["FileSink", "TraceSink", "autocontext_session", "instrument_client"]
