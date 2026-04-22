"""Tests for autocontext_session contextvar."""
from __future__ import annotations

import asyncio
import threading

from autocontext.integrations.openai._session import (
    autocontext_session,
    current_session,
)


def test_outside_of_context_returns_empty() -> None:
    assert current_session() == {}


def test_inside_context_returns_values() -> None:
    with autocontext_session(user_id="u1", session_id="s1"):
        assert current_session() == {"user_id": "u1", "session_id": "s1"}
    assert current_session() == {}


def test_nested_context_inner_wins() -> None:
    with autocontext_session(user_id="u1"):
        with autocontext_session(user_id="u2", session_id="s2"):
            assert current_session() == {"user_id": "u2", "session_id": "s2"}
        assert current_session() == {"user_id": "u1"}


def test_propagates_across_asyncio_to_thread() -> None:
    async def run() -> dict:
        with autocontext_session(user_id="u1", session_id="s1"):
            return await asyncio.to_thread(current_session)

    result = asyncio.run(run())
    assert result == {"user_id": "u1", "session_id": "s1"}


def test_does_not_leak_across_raw_threads_without_copy() -> None:
    results: list[dict] = []

    def worker() -> None:
        results.append(current_session())

    with autocontext_session(user_id="u1"):
        t = threading.Thread(target=worker)
        t.start()
        t.join()
    # Raw threading.Thread does NOT copy contextvars; worker sees empty.
    assert results == [{}]
