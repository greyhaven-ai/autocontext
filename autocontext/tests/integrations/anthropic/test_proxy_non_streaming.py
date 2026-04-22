"""ClientProxy non-streaming sync tests (TDD — RED phase)."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autocontext.integrations.anthropic import FileSink, instrument_client

from .conftest import canned_messages_response


def _handler_returning(payload: dict[str, Any]) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return handler


def test_sync_messages_create_captures_one_trace(tmp_path, make_anthropic_client) -> None:
    """messages.create emits a trace with correct provider, model, outcome."""
    client = make_anthropic_client(_handler_returning(canned_messages_response()))
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="test-app")

    resp = wrapped.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert resp.content[0].text == "hello world"
    sink.close()
    lines = (tmp_path / "t.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    trace = json.loads(lines[0])
    assert trace["provider"]["name"] == "anthropic"
    assert trace["model"] == "claude-sonnet-4-5"
    assert trace["outcome"] == {"label": "success"}


def test_delegates_unintercepted_attributes(tmp_path, make_anthropic_client) -> None:
    """Non-intercepted attributes delegate to inner client."""
    client = make_anthropic_client(_handler_returning(canned_messages_response()))
    sink = FileSink(path=tmp_path / "t.jsonl")
    wrapped = instrument_client(client, sink=sink, app_id="test-app")
    assert wrapped.api_key == "test-key"
    sink.close()


def test_content_flattened_in_trace_messages(tmp_path, make_anthropic_client) -> None:
    """The assistant message in the trace has content flattened to a string."""
    client = make_anthropic_client(_handler_returning(canned_messages_response(content="Hello there")))
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="test-app")

    wrapped.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "Hi"}],
    )

    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    # Last message should be assistant
    msgs = trace["messages"]
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert assistant_msgs, "No assistant message in trace"
    assert assistant_msgs[-1]["content"] == "Hello there"


def test_usage_correctly_mapped(tmp_path, make_anthropic_client) -> None:
    """Usage is extracted from response and mapped to tokensIn/tokensOut."""
    client = make_anthropic_client(_handler_returning(
        canned_messages_response(usage={"input_tokens": 15, "output_tokens": 8})
    ))
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="test-app")

    wrapped.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "Hello"}],
    )

    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["usage"]["tokensIn"] == 15
    assert trace["usage"]["tokensOut"] == 8


def test_error_emits_failure_trace(tmp_path, make_anthropic_client) -> None:
    """A 429 from the API results in a failure trace with rateLimited type."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"type": "error", "error": {"type": "rate_limit_error", "message": "rate limited"}},
            headers={"x-request-id": "req_123"},
        )

    client = make_anthropic_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="test-app")

    with pytest.raises(Exception):
        wrapped.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": "hi"}],
        )

    sink.close()
    lines = (tmp_path / "t.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    trace = json.loads(lines[0])
    assert trace["outcome"]["label"] == "failure"
    assert trace["outcome"]["error"]["type"] == "rateLimited"


def test_strips_autocontext_kwarg_before_forwarding(tmp_path, make_anthropic_client) -> None:
    """The autocontext= kwarg is stripped and not forwarded to the inner client."""
    seen_kwargs: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode())
        seen_kwargs.update(body)
        return httpx.Response(200, json=canned_messages_response())

    client = make_anthropic_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="test-app")

    wrapped.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "hi"}],
        autocontext={"user_id": "u1", "session_id": "s1"},
    )
    assert "autocontext" not in seen_kwargs
    sink.close()
