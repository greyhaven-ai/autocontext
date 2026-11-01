from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def test_generate_omits_temperature_for_claude_sonnet_5() -> None:
    client, mock_sdk = _client_with_mock()

    client.generate(model="claude-sonnet-5", prompt="hi", max_tokens=128, temperature=0.2)

    kwargs = mock_sdk.messages.create.call_args.kwargs
    assert "temperature" not in kwargs
    assert kwargs["thinking"] == {"type": "disabled"}


def test_generate_multiturn_omits_temperature_for_claude_opus_5() -> None:
    client, mock_sdk = _client_with_mock()

    client.generate_multiturn(
        model="claude-opus-5",
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=128,
        temperature=0.2,
    )

    kwargs = mock_sdk.messages.create.call_args.kwargs
    assert "temperature" not in kwargs
    assert kwargs["thinking"] == {"type": "disabled"}


def test_generate_omits_sampling_and_thinking_controls_for_fable_5() -> None:
    client, mock_sdk = _client_with_mock()

    client.generate(model="claude-fable-5", prompt="hi", max_tokens=128, temperature=0.2)

    kwargs = mock_sdk.messages.create.call_args.kwargs
    assert "temperature" not in kwargs
    assert "thinking" not in kwargs


def test_generate_keeps_temperature_for_pre_claude_5_models() -> None:
    client, mock_sdk = _client_with_mock()

    client.generate(model="claude-sonnet-4-5", prompt="hi", max_tokens=128, temperature=0.2)

    kwargs = mock_sdk.messages.create.call_args.kwargs
    assert kwargs["temperature"] == 0.2
    assert "thinking" not in kwargs
