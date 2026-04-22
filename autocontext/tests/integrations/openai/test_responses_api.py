"""responses.create coverage — sync + async."""
import json

import httpx
import pytest

from autocontext.integrations.openai import FileSink, instrument_client


def _responses_handler():
    payload = {
        "id": "resp-fake",
        "object": "response",
        "created_at": 1714000000,
        "model": "gpt-4o",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
        "parallel_tool_calls": True,
        "temperature": 1.0,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
    }
    return lambda req: httpx.Response(200, json=payload)


def test_sync_responses_create(tmp_path, make_openai_client):
    client = make_openai_client(_responses_handler())
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")
    wrapped.responses.create(model="gpt-4o", input="hi")
    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["provider"]["name"] == "openai"
    assert trace["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_async_responses_create(tmp_path, make_async_openai_client):
    client = make_async_openai_client(_responses_handler())
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")
    await wrapped.responses.create(model="gpt-4o", input="hi")
    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["provider"]["name"] == "openai"
