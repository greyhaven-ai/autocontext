"""Customer-facing OpenAI integration.

Public surface: ``instrument_client``, ``FileSink``, ``autocontext_session``,
``TraceSink``. See ``STABILITY.md`` for stability commitments.
"""
from autocontext.integrations.openai._sink import FileSink, TraceSink

__all__ = ["FileSink", "TraceSink"]
