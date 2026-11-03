from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.llm_client import AnthropicClient


def _fake_response(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )


def _client_with_mock() -> tuple[AnthropicClient, MagicMock]:
    mock_sdk = MagicMock()
    mock_sdk.messages.create.return_value = _fake_response()
    with patch("autocontext.agents.llm_client.Anthropic", return_value=mock_sdk):
        client = AnthropicClient(api_key="test")
    return client, mock_sdk


@pytest.mark.parametrize("multiturn", [False, True])
@pytest.mark.parametrize(
    ("model", "controls"),
    [
        ("claude-sonnet-5", {"thinking": {"type": "disabled"}}),
        ("claude-opus-5", {"thinking": {"type": "disabled"}}),
        ("anthropic/claude-sonnet-5-20260901", {"thinking": {"type": "disabled"}}),
        ("claude-fable-5", {}),
        ("claude-mythos-5", {}),
        ("claude-sonnet-4-5", {"temperature": 0.2}),
        ("claude-opus-4-6", {"temperature": 0.2}),
    ],
)
def test_request_controls_preserve_message_and_response_contract(
    model: str,
    controls: dict[str, Any],
    multiturn: bool,
) -> None:
    client, mock_sdk = _client_with_mock()
    messages = [{"role": "user", "content": "hi"}]

    if multiturn:
        result = client.generate_multiturn(
            model=model,
            system="sys",
            messages=messages,
            max_tokens=128,
            temperature=0.2,
        )
    else:
        result = client.generate(model=model, prompt="hi", max_tokens=128, temperature=0.2)

    kwargs = mock_sdk.messages.create.call_args.kwargs
    expected: dict[str, Any] = {"model": model, "max_tokens": 128, "messages": messages, **controls}
    if multiturn:
        expected["system"] = "sys"
    assert kwargs == expected
    assert mock_sdk.messages.create.call_count == 1
    assert result.text == "ok"
    assert result.usage.model == model
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 3
