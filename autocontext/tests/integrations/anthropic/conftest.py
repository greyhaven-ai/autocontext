"""Shared fixtures for Anthropic integration tests."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from anthropic import Anthropic, AsyncAnthropic

from autocontext.production_traces.hashing import initialize_install_salt


@pytest.fixture(autouse=True)
def _scratch_cwd(monkeypatch, tmp_path_factory) -> None:
    scratch = tmp_path_factory.mktemp("autoctx-anthropic-cwd")
    monkeypatch.chdir(scratch)
    initialize_install_salt(".")


@pytest.fixture
def make_anthropic_client() -> Callable:
    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> Anthropic:
        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(transport=transport, base_url="https://api.anthropic.com")
        return Anthropic(api_key="test-key", http_client=http_client)
    return factory


@pytest.fixture
def make_async_anthropic_client() -> Callable:
    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> AsyncAnthropic:
        transport = httpx.MockTransport(handler)
        http_client = httpx.AsyncClient(transport=transport, base_url="https://api.anthropic.com")
        return AsyncAnthropic(api_key="test-key", http_client=http_client)
    return factory


def canned_messages_response(
    *,
    content: str = "hello world",
    usage: dict[str, int] | None = None,
    stop_reason: str = "end_turn",
    tool_use: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content_blocks: list[dict[str, Any]] = [{"type": "text", "text": content}]
    if tool_use:
        content_blocks.append({
            "type": "tool_use",
            "id": tool_use.get("id", "tu_1"),
            "name": tool_use["name"],
            "input": tool_use.get("input", {}),
        })
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": "claude-sonnet-4-5-20250514",
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }


def canned_anthropic_sse_chunks(
    *,
    text_pieces: list[str] | None = None,
    tool_use: dict[str, Any] | None = None,
    usage: dict[str, int] | None = None,
    stop_reason: str = "end_turn",
) -> list[bytes]:
    pieces = text_pieces or ["hello", " world"]
    events: list[dict[str, Any]] = []
    events.append({"type": "message_start", "message": {
        "id": "msg_fake", "role": "assistant", "content": [],
        "usage": usage or {"input_tokens": 1, "output_tokens": 0},
    }})
    events.append({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    for p in pieces:
        events.append({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}})
    events.append({"type": "content_block_stop", "index": 0})
    if tool_use:
        idx = 1
        events.append({"type": "content_block_start", "index": idx, "content_block": {
            "type": "tool_use", "id": tool_use["id"], "name": tool_use["name"], "input": {},
        }})
        for chunk in tool_use.get("input_json_delta_chunks", []):
            events.append({"type": "content_block_delta", "index": idx, "delta": {"type": "input_json_delta", "partial_json": chunk}})
        events.append({"type": "content_block_stop", "index": idx})
    events.append({
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": len(pieces)},
    })
    events.append({"type": "message_stop"})
    chunks: list[bytes] = []
    for ev in events:
        name = ev["type"]
        chunks.append(f"event: {name}\ndata: {json.dumps(ev)}\n\n".encode())
    return chunks
