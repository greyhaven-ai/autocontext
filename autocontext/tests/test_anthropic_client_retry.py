from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autocontext.config.settings import AppSettings


class FakeAnthropicAPIError(Exception):
    pass


def _fake_response(text: str = "success") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=12, output_tokens=7),
    )


def test_build_client_from_settings_retries_transient_anthropic_errors() -> None:
    from autocontext.agents.llm_client import build_client_from_settings

    mock_sdk = MagicMock()
    mock_sdk.messages.create.side_effect = [
        FakeAnthropicAPIError("500 Internal Server Error"),
        _fake_response(),
    ]

    settings = AppSettings(agent_provider="anthropic", anthropic_api_key="sk-test")

    with (
        patch("autocontext.agents.llm_client.Anthropic", return_value=mock_sdk),
        patch("autocontext.agents.llm_client.anthropic.APIError", FakeAnthropicAPIError),
        patch("autocontext.agents.llm_client.time.sleep"),
    ):
        client = build_client_from_settings(settings)
        response = client.generate(
            model="claude-sonnet-4-5-20250929",
            prompt="hello",
            max_tokens=128,
            temperature=0.0,
        )

    assert response.text == "success"
    assert mock_sdk.messages.create.call_count == 2


def test_per_role_anthropic_client_retries_transient_errors() -> None:
    from autocontext.agents.provider_bridge import create_role_client

    mock_sdk = MagicMock()
    mock_sdk.messages.create.side_effect = [
        FakeAnthropicAPIError("500 Internal Server Error"),
        _fake_response("role success"),
    ]

    settings = AppSettings(anthropic_api_key="sk-test")

    with (
        patch("autocontext.agents.llm_client.Anthropic", return_value=mock_sdk),
        patch("autocontext.agents.llm_client.anthropic.APIError", FakeAnthropicAPIError),
        patch("autocontext.agents.llm_client.time.sleep"),
    ):
        client = create_role_client("anthropic", settings)
        assert client is not None
        response = client.generate(
            model="claude-sonnet-4-5-20250929",
            prompt="hello",
            max_tokens=128,
            temperature=0.0,
        )

    assert response.text == "role success"
    assert mock_sdk.messages.create.call_count == 2


def test_anthropic_client_retries_multiturn_requests() -> None:
    from autocontext.agents.llm_client import AnthropicClient

    mock_sdk = MagicMock()
    mock_sdk.messages.create.side_effect = [
        FakeAnthropicAPIError("500 Internal Server Error"),
        _fake_response("multiturn success"),
    ]

    with (
        patch("autocontext.agents.llm_client.Anthropic", return_value=mock_sdk),
        patch("autocontext.agents.llm_client.anthropic.APIError", FakeAnthropicAPIError),
        patch("autocontext.agents.llm_client.time.sleep"),
    ):
        client = AnthropicClient(api_key="sk-test")
        response = client.generate_multiturn(
            model="claude-sonnet-4-5-20250929",
            system="system",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=128,
            temperature=0.0,
        )

    assert response.text == "multiturn success"
    assert mock_sdk.messages.create.call_count == 2
