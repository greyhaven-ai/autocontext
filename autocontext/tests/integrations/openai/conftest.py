"""Shared fixtures for OpenAI integration tests.

``make_openai_client`` returns an ``OpenAI`` (or ``AsyncOpenAI``) wired to an
``httpx.MockTransport``. Tests specify the mock responses; no real HTTP.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI, OpenAI

from autocontext.production_traces.hashing import initialize_install_salt


@pytest.fixture(autouse=True)
def _scratch_cwd(monkeypatch, tmp_path_factory) -> None:
    """Every test runs in a fresh scratch cwd with a pre-initialized install salt."""
    scratch = tmp_path_factory.mktemp("autoctx-cwd")
    monkeypatch.chdir(scratch)
    initialize_install_salt(".")


@pytest.fixture
def mock_transport_factory() -> Callable[[Callable[[httpx.Request], httpx.Response]], httpx.MockTransport]:
    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
        return httpx.MockTransport(handler)
    return factory


@pytest.fixture
def make_openai_client() -> Callable[..., OpenAI]:
    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAI:
        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(transport=transport, base_url="https://api.openai.com/v1")
        return OpenAI(api_key="test-key", http_client=http_client)
    return factory


@pytest.fixture
def make_async_openai_client() -> Callable[..., AsyncOpenAI]:
    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> AsyncOpenAI:
        transport = httpx.MockTransport(handler)
        http_client = httpx.AsyncClient(transport=transport, base_url="https://api.openai.com/v1")
        return AsyncOpenAI(api_key="test-key", http_client=http_client)
    return factory


def canned_chat_completion(
    *,
    content: str = "hello world",
    usage: dict[str, int] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 1714000000,
        "model": "gpt-4o",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def canned_sse_chunks(
    *,
    content_pieces: list[str] | None = None,
    usage: dict[str, int] | None = None,
) -> list[bytes]:
    """Return a list of SSE-encoded byte chunks for a streaming chat.completion."""
    pieces = content_pieces or ["hello", " ", "world"]
    chunks: list[bytes] = []
    for piece in pieces:
        chunks.append(
            b"data: "
            + json.dumps({
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": 1714000000,
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            }).encode()
            + b"\n\n"
        )
    if usage is not None:
        chunks.append(
            b"data: "
            + json.dumps({
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "created": 1714000000,
                "model": "gpt-4o",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": usage,
            }).encode()
            + b"\n\n"
        )
    chunks.append(b"data: [DONE]\n\n")
    return chunks
