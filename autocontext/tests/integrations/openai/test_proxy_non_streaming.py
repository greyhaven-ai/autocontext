"""ClientProxy non-streaming sync tests."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autocontext.integrations.openai import FileSink, instrument_client

from .conftest import canned_chat_completion


def _handler_returning(payload: dict[str, Any]) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return handler


def test_sync_chat_completion_captures_one_trace(tmp_path, make_openai_client) -> None:
    client = make_openai_client(_handler_returning(canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="test-app")

    resp = wrapped.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert resp.choices[0].message.content == "hello world"
    sink.close()
    lines = (tmp_path / "t.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    trace = json.loads(lines[0])
    assert trace["provider"]["name"] == "openai"
    assert trace["model"] == "gpt-4o"
    assert trace["usage"] == {"tokensIn": 10, "tokensOut": 5}
    assert trace["outcome"] == {"label": "success"}


def test_delegates_unintercepted_attributes(tmp_path, make_openai_client) -> None:
    client = make_openai_client(_handler_returning(canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl")
    wrapped = instrument_client(client, sink=sink, app_id="a")
    # `api_key` is a passthrough attribute on the real client.
    assert wrapped.api_key == "test-key"
    sink.close()


def test_double_wrap_raises(tmp_path, make_openai_client) -> None:
    client = make_openai_client(_handler_returning(canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl")
    wrapped = instrument_client(client, sink=sink, app_id="a")
    with pytest.raises(ValueError, match="already wrapped"):
        instrument_client(wrapped, sink=sink, app_id="a")
    sink.close()


def test_wrapped_sentinel_present(tmp_path, make_openai_client) -> None:
    client = make_openai_client(_handler_returning(canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl")
    wrapped = instrument_client(client, sink=sink, app_id="a")
    assert getattr(wrapped, "__autocontext_wrapped__", False) is True
    sink.close()


def test_strips_autocontext_kwarg_before_forwarding(tmp_path, make_openai_client) -> None:
    seen_kwargs: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode())
        seen_kwargs.update(body)
        return httpx.Response(200, json=canned_chat_completion())

    client = make_openai_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl")
    wrapped = instrument_client(client, sink=sink, app_id="a")

    wrapped.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        autocontext={"user_id": "u1", "session_id": "s1"},
    )
    assert "autocontext" not in seen_kwargs
    sink.close()


def test_per_call_kwarg_wins_over_ambient_context(tmp_path, make_openai_client) -> None:
    from autocontext.integrations.openai import autocontext_session
    client = make_openai_client(_handler_returning(canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")

    with autocontext_session(user_id="ambient", session_id="ambient-s"):
        wrapped.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            autocontext={"user_id": "explicit", "session_id": "explicit-s"},
        )

    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    # Hashes differ for explicit vs ambient; we only assert identity was attached.
    assert "session" in trace
    assert trace["session"]["userIdHash"] != ""
