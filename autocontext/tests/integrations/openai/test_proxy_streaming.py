"""Streaming chat.completions tests — finalize-on-end, abandoned, mid-stream exception."""
from __future__ import annotations

import gc
import json
from typing import Any

import httpx
import pytest

from autocontext.integrations.openai import FileSink, instrument_client
from .conftest import canned_sse_chunks


def _sse_handler(chunks: list[bytes]) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        body = b"".join(chunks)
        return httpx.Response(
            200, content=body,
            headers={"content-type": "text/event-stream"},
        )
    return handler


def test_streaming_normal_finalize_on_end(tmp_path, make_openai_client) -> None:
    handler = _sse_handler(canned_sse_chunks(
        content_pieces=["a", "b", "c"],
        usage={"prompt_tokens": 1, "completion_tokens": 3, "total_tokens": 4},
    ))
    client = make_openai_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")

    collected: list[str] = []
    with wrapped.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    ) as stream:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                collected.append(chunk.choices[0].delta.content)

    sink.close()
    assert "".join(collected) == "abc"
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["outcome"]["label"] == "success"
    assert trace["usage"] == {"tokensIn": 1, "tokensOut": 3}


def test_streaming_include_usage_auto_injected(tmp_path, make_openai_client) -> None:
    seen_body: dict[str, Any] = {}
    def handler(req):
        nonlocal seen_body
        seen_body = json.loads(req.content.decode())
        return httpx.Response(
            200,
            content=b"".join(canned_sse_chunks(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})),
            headers={"content-type": "text/event-stream"},
        )
    client = make_openai_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")

    with wrapped.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True,
    ) as stream:
        for _ in stream: pass

    assert seen_body.get("stream_options") == {"include_usage": True}
    sink.close()


def test_streaming_customer_include_usage_preserved(tmp_path, make_openai_client) -> None:
    seen_body: dict[str, Any] = {}
    def handler(req):
        nonlocal seen_body
        seen_body = json.loads(req.content.decode())
        return httpx.Response(
            200,
            content=b"".join(canned_sse_chunks(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})),
            headers={"content-type": "text/event-stream"},
        )
    client = make_openai_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")

    with wrapped.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": False},  # customer-set; must not be overwritten
    ) as stream:
        for _ in stream: pass

    assert seen_body["stream_options"] == {"include_usage": False}
    sink.close()


def test_streaming_abandoned_emits_partial(tmp_path, make_openai_client) -> None:
    handler = _sse_handler(canned_sse_chunks(
        content_pieces=["a", "b", "c", "d", "e"],
        usage={"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6},
    ))
    client = make_openai_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")

    stream = wrapped.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True,
    )
    it = iter(stream)
    next(it)  # consume one chunk, then drop reference
    del stream, it
    gc.collect()

    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["outcome"]["label"] == "partial"
    assert trace["outcome"]["reasoning"] == "abandonedStream"


@pytest.mark.asyncio
async def test_async_streaming_normal_finalize(tmp_path, make_async_openai_client) -> None:
    handler = _sse_handler(canned_sse_chunks(
        content_pieces=["x", "y"],
        usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    ))
    client = make_async_openai_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")
    async with await wrapped.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}], stream=True,
    ) as stream:
        async for _ in stream: pass
    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["outcome"]["label"] == "success"
