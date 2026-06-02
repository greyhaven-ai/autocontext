"""Internal helpers for bridging sync mission entry points to async
verifier / step-executor callables.

PR #1016 review (P2): a sync entry point that receives a coroutine
while a running event loop is active must NOT mutate mission state.
``asyncio.run`` raises ``RuntimeError`` from inside an active loop,
which the catch-all in ``run_step`` and ``MissionManager.verify``
would otherwise swallow into a falsely-failing step / verification
record. ``AsyncContextError`` propagates instead so the caller can
react without leaving the mission in an inconsistent state.
"""

from __future__ import annotations

import asyncio

__all__ = ["AsyncContextError", "has_running_loop"]


class AsyncContextError(RuntimeError):
    """Raised when a sync mission entry point receives a coroutine
    while a running event loop is active."""


def has_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
