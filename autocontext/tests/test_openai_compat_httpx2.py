"""Exercise the SDK 3 transport family without making network requests."""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

from autocontext.providers.base import ProviderError
from autocontext.providers.openai_compat import OpenAICompatibleProvider


@pytest.mark.parametrize("status_code", [200, 429])
def test_provider_with_native_httpx2_transport(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    openai = pytest.importorskip("openai", minversion="3.0.0")
    # SDK 1/2 use HTTPX; SDK 3 must install and exercise its native HTTPX2 dependency.
    httpx2 = importlib.import_module("httpx2")
    real_openai = openai.OpenAI
    requests: list[Any] = []

    def respond(request: Any) -> Any:
        requests.append(request)
        if status_code == 429:
            return httpx2.Response(429, json={"error": {"message": "rate limit", "type": "rate_limit_error"}})
        return httpx2.Response(200, json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "compat-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "answer"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })

    monkeypatch.setenv("AUTOCONTEXT_OFFLINE", "1")
    with httpx2.Client(transport=httpx2.MockTransport(respond), trust_env=False) as http_client:
        def make_client(**kwargs: Any) -> Any:
            return real_openai(http_client=http_client, **kwargs)

        monkeypatch.setattr(openai, "OpenAI", make_client)
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            base_url="http://127.0.0.1:9999/v1",
            default_model_name="compat-model",
            extra_headers={"X-Test-Route": "native"},
            single_dispatch=True,
        )
        if status_code == 429:
            with pytest.raises(ProviderError) as caught:
                provider.complete("system", "user", max_tokens=128)
            cause = caught.value.__cause__
            assert isinstance(cause, openai.RateLimitError)
            assert isinstance(cause.response, httpx2.Response)
        else:
            result = provider.complete("system", "user", max_tokens=128)
            assert result.text == "answer"
            assert result.model == "compat-model"
            assert result.usage == {"input_tokens": 3, "output_tokens": 2}
            assert result.stop_reason == "stop"

    # The real SDK serializes the provider request, parses responses, and honors
    # single_dispatch even for retryable errors; only network I/O is mocked.
    assert len(requests) == 1
    request = requests[0]
    assert isinstance(request, httpx2.Request)
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:9999/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["x-test-route"] == "native"
    assert json.loads(request.content) == {
        "model": "compat-model",
        "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
        "max_tokens": 128,
        "temperature": 0.0,
    }
