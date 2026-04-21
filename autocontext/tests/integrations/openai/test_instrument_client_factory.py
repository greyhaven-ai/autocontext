import json
import os
import pytest
import httpx

from autocontext.integrations.openai import FileSink, instrument_client
from .conftest import canned_chat_completion


def test_app_id_from_env(monkeypatch, tmp_path, make_openai_client):
    monkeypatch.setenv("AUTOCONTEXT_APP_ID", "env-app")
    client = make_openai_client(lambda r: httpx.Response(200, json=canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl")
    wrapped = instrument_client(client, sink=sink)  # no app_id arg
    sink.close()


def test_missing_app_id_raises(monkeypatch, tmp_path, make_openai_client):
    monkeypatch.delenv("AUTOCONTEXT_APP_ID", raising=False)
    client = make_openai_client(lambda r: httpx.Response(200, json=canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl")
    with pytest.raises(ValueError, match="app_id"):
        instrument_client(client, sink=sink)


def test_arg_wins_over_env(monkeypatch, tmp_path, make_openai_client):
    monkeypatch.setenv("AUTOCONTEXT_APP_ID", "env-app")
    client = make_openai_client(lambda r: httpx.Response(200, json=canned_chat_completion()))
    sink = FileSink(path=tmp_path / "t.jsonl", batch_size=1)
    wrapped = instrument_client(client, sink=sink, app_id="arg-app")
    wrapped.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    sink.close()
    trace = json.loads((tmp_path / "t.jsonl").read_text().strip())
    assert trace["env"]["appId"] == "arg-app"
