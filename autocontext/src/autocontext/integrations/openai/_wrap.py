"""``instrument_client`` factory — double-wrap detection + identity resolution.

Spec §4.1.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, TypeVar

from autocontext.integrations.openai._proxy import ClientProxy
from autocontext.integrations.openai._sink import TraceSink

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI  # noqa: F401

_WRAPPED_SENTINEL = "__autocontext_wrapped__"

T = TypeVar("T")


def instrument_client(
    client: T,
    *,
    sink: TraceSink,
    app_id: str | None = None,
    environment_tag: str = "production",
) -> T:
    """Wrap ``client`` (an ``OpenAI`` / ``AsyncOpenAI`` instance) with
    autocontext instrumentation. Returns a proxy object that forwards every
    attribute access to the underlying client, intercepting only the chat +
    responses call paths.

    Raises ``ValueError`` on double-wrap.
    Raises ``ValueError`` when ``app_id`` is unresolvable.
    """
    if getattr(client, _WRAPPED_SENTINEL, False):
        raise ValueError("client is already wrapped")
    resolved_app_id = app_id or os.environ.get("AUTOCONTEXT_APP_ID")
    if not resolved_app_id:
        raise ValueError(
            "app_id is required — pass app_id=... to instrument_client() or set AUTOCONTEXT_APP_ID env var",
        )
    return ClientProxy(  # type: ignore[return-value]
        inner=client,
        sink=sink,
        app_id=resolved_app_id,
        environment_tag=environment_tag,
    )
