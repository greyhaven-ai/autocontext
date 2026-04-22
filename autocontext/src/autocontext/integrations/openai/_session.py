"""autocontext_session contextvar + current_session lookup.

Spec §4.1. Uses contextvars.ContextVar; propagates naturally across
``asyncio.to_thread`` and ``contextvars.copy_context()`` but NOT across
raw ``threading.Thread`` targets — documented in STABILITY.md.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[dict[str, str] | None] = ContextVar(
    "autocontext_session_current", default=None
)


@contextmanager
def autocontext_session(
    *, user_id: str | None = None, session_id: str | None = None
) -> Iterator[None]:
    """Bind user_id / session_id for the duration of the with-block.

    Ambient default resolution: per-call ``autocontext={}`` kwarg wins over
    this context; no-context means no session identity on the trace.
    """
    new: dict[str, str] = {}
    if user_id is not None:
        new["user_id"] = user_id
    if session_id is not None:
        new["session_id"] = session_id
    token = _current.set(new)
    try:
        yield
    finally:
        _current.reset(token)


def current_session() -> dict[str, str]:
    """Read the active session dict. Returns empty dict when unbound."""
    val = _current.get()
    return dict(val) if val is not None else {}
