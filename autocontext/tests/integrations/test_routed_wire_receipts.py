"""Use real SDK parsing with deterministic HTTP responses; never paid inference."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from autocontext.providers.usage_receipt import ProviderReceiptError, parse_wire_usage
from autocontext.runtimes._workspace_process import default_shell_env, run_bounded_process


@pytest.mark.parametrize("counter", [100, "100", True, 100.0, "invalid", {"invalid": 1}])
def test_anthropic_wire_receipt_precedes_sdk_coercion(counter, tmp_path):
    # SDK initialization starts native threads. Keep them in a child, as the
    # routed model worker does, so unrelated fork-isolation tests remain valid.
    result = run_bounded_process(
        [sys.executable, "-I", str(Path(__file__).resolve()), json.dumps(counter)], cwd=tmp_path,
        env=default_shell_env(), shell=False, timeout_ms=10000,
    )
    assert result.exit_code == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["requests"] == 1
    assert report["counter_type"] == type(counter).__name__
    assert report["verified"] == (type(counter) is int)


@pytest.mark.parametrize("body", [
    b'{"usage":{"input_tokens":1,"input_tokens":100,"output_tokens":30}}',
    b'{"usage":{"input_tokens":NaN,"output_tokens":30}}',
    b'{"usage":null}', b'{"usage":[]}', b'not JSON',
])
def test_ambiguous_or_missing_wire_usage_fails_closed(body):
    with pytest.raises(ProviderReceiptError, match="invalid_provider_receipt"):
        parse_wire_usage(body)


def test_wire_receipt_preserves_types_and_omits_optional_nulls():
    assert parse_wire_usage(b'{"usage":{"input_tokens":true,"output_tokens":"30","cache_creation":null}}') == {
        "input_tokens": True, "output_tokens": "30",
    }


def _anthropic_probe(counter):
    import anthropic
    import httpx

    from autocontext.execution.skill_routing import _model_receipt
    from autocontext.execution.skill_routing_models import ModelTarget
    from autocontext.providers.anthropic import AnthropicProvider

    requests = []

    def respond(request):
        requests.append(str(request.url))
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        return httpx.Response(200, json={
            "id": "fixture", "type": "message", "role": "assistant", "model": "fixture-model",
            "content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": counter, "output_tokens": 30, "cache_creation_input_tokens": None},
        })

    provider = AnthropicProvider(api_key="fixture-key", single_dispatch=True, follow_redirects=False, capture_raw_usage=True)
    provider._client.close()
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
        provider._client = anthropic.Anthropic(api_key="fixture-key", http_client=client, max_retries=0)
        completion = provider.complete("system", "input", model="fixture-model")
    selected = ModelTarget(provider="anthropic", model="fixture-model", api_key_env="FIXTURE_API_KEY",
                            input_cost_per_1k=0.001, output_cost_per_1k=0.002)
    try:
        _model_receipt(completion, selected)
        verified = True
    except ValueError:
        verified = False
    return {"counter_type": type(completion.raw_usage["input_tokens"]).__name__,
            "requests": len(requests), "verified": verified}


if __name__ == "__main__":
    print(json.dumps(_anthropic_probe(json.loads(sys.argv[1]))))
