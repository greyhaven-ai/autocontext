"""End-to-end: OpenAI raises → taxonomy mapped → outcome.error.type correct, exception re-raised."""
import httpx
import json
import openai
import pytest

from autocontext.integrations.openai import FileSink, instrument_client


def test_rate_limit_maps_and_reraises(tmp_path, make_openai_client):
    def handler(req):
        return httpx.Response(429, json={"error": {"message": "rate limit exceeded", "type": "rate_limit_error"}})
    client = make_openai_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")
    with pytest.raises(openai.RateLimitError):
        wrapped.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
        )
    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["outcome"]["label"] == "failure"
    assert trace["outcome"]["error"]["type"] == "rateLimited"


def test_401_maps_authentication(tmp_path, make_openai_client):
    def handler(req):
        return httpx.Response(401, json={"error": {"message": "bad key", "type": "invalid_request_error"}})
    client = make_openai_client(handler)
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="a")
    with pytest.raises(openai.AuthenticationError):
        wrapped.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
        )
    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["outcome"]["error"]["type"] == "authentication"
