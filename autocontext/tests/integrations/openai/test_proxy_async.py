"""Async OpenAI client tests — AsyncOpenAI.chat.completions.create."""
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


@pytest.mark.asyncio
async def test_async_chat_completion(tmp_path, make_async_openai_client) -> None:
    client = make_async_openai_client(_handler_returning(canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")
    resp = await wrapped.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
    )
    assert resp.choices[0].message.content == "hello world"
    sink.close()
    assert len(json.loads(open(tmp_path / "t.jsonl").read().strip()).get("traceId", "")) > 0
