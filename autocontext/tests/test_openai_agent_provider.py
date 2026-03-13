"""Tests for AC-222: first-class OpenAI-compatible agent provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.llm_client import build_client_from_settings
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.agents.provider_bridge import ProviderBridgeClient, _provider_api_key, create_role_client
from autocontext.agents.role_router import ProviderClass, ProviderConfig
from autocontext.config.settings import AppSettings

# ---------------------------------------------------------------------------
# Settings field defaults
# ---------------------------------------------------------------------------


def test_settings_agent_base_url_default() -> None:
    s = AppSettings()
    assert s.agent_base_url == ""


def test_settings_agent_api_key_default() -> None:
    s = AppSettings()
    assert s.agent_api_key == ""


def test_settings_agent_default_model_default() -> None:
    s = AppSettings()
    assert s.agent_default_model == "gpt-4o"


# ---------------------------------------------------------------------------
# build_client_from_settings → ProviderBridgeClient for each alias
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider_name", ["openai-compatible", "openai", "ollama", "vllm"])
def test_build_client_returns_provider_bridge(provider_name: str) -> None:
    s = AppSettings(agent_provider=provider_name, agent_api_key="test-key")
    with patch("autocontext.providers.openai_compat.OpenAICompatibleProvider"):
        client = build_client_from_settings(s)
    assert isinstance(client, ProviderBridgeClient)


def test_build_client_openai_compatible_uses_agent_model() -> None:
    s = AppSettings(
        agent_provider="openai-compatible",
        agent_api_key="test-key",
        agent_base_url="http://localhost:1234/v1",
        agent_default_model="custom-model",
    )
    with patch("autocontext.providers.openai_compat.OpenAICompatibleProvider") as mock_cls:
        mock_cls.return_value = MagicMock()
        build_client_from_settings(s)
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args
    assert call_kwargs[1].get("default_model_name") == "custom-model" or call_kwargs[0][-1] == "custom-model"


def test_build_client_openai_falls_back_to_judge_key() -> None:
    """When agent_api_key is empty, falls back to judge_api_key."""
    s = AppSettings(agent_provider="openai", agent_api_key="", judge_api_key="judge-key-123")
    with patch("autocontext.providers.openai_compat.OpenAICompatibleProvider") as mock_cls:
        mock_cls.return_value = MagicMock()
        client = build_client_from_settings(s)
    assert isinstance(client, ProviderBridgeClient)


def test_build_client_openai_falls_back_to_judge_base_url() -> None:
    """When agent_base_url is empty, falls back to judge_base_url."""
    s = AppSettings(
        agent_provider="openai-compatible",
        agent_api_key="key",
        agent_base_url="",
        judge_base_url="http://judge:1234/v1",
    )
    with patch("autocontext.providers.openai_compat.OpenAICompatibleProvider") as mock_cls:
        mock_cls.return_value = MagicMock()
        build_client_from_settings(s)
    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs.get("base_url") == "http://judge:1234/v1"


# ---------------------------------------------------------------------------
# _provider_api_key prefers agent_api_key over judge_api_key
# ---------------------------------------------------------------------------


def test_provider_api_key_prefers_agent_key_openai() -> None:
    s = AppSettings(agent_api_key="agent-key", judge_api_key="judge-key")
    with patch.dict("os.environ", {}, clear=True):
        result = _provider_api_key("openai-compatible", s)
    assert result == "agent-key"


def test_provider_api_key_falls_back_to_judge_key_openai() -> None:
    s = AppSettings(agent_api_key="", judge_api_key="judge-key")
    with patch.dict("os.environ", {}, clear=True):
        result = _provider_api_key("openai-compatible", s)
    assert result == "judge-key"


def test_provider_api_key_vllm_prefers_agent_key() -> None:
    s = AppSettings(agent_api_key="agent-vllm", judge_api_key="judge-vllm")
    result = _provider_api_key("vllm", s)
    assert result == "agent-vllm"


def test_provider_api_key_vllm_fallback_no_key() -> None:
    s = AppSettings(agent_api_key="", judge_api_key="")
    result = _provider_api_key("vllm", s)
    assert result == "no-key"


# ---------------------------------------------------------------------------
# Per-role override still works alongside top-level openai-compatible
# ---------------------------------------------------------------------------


def test_per_role_override_works() -> None:
    s = AppSettings(agent_provider="openai-compatible", agent_api_key="top-key")
    with patch("autocontext.providers.openai_compat.OpenAICompatibleProvider"):
        client = create_role_client("openai-compatible", s)
    assert isinstance(client, ProviderBridgeClient)


def test_per_role_override_empty_returns_none() -> None:
    s = AppSettings()
    assert create_role_client("", s) is None


# ---------------------------------------------------------------------------
# Full generation loop with mocked OpenAI provider
# ---------------------------------------------------------------------------


def test_provider_bridge_generate_delegates_to_provider() -> None:
    mock_provider = MagicMock()
    mock_provider.complete.return_value = MagicMock(
        text="hello world",
        model="gpt-4o",
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    mock_provider.default_model.return_value = "gpt-4o"

    client = ProviderBridgeClient(mock_provider, use_provider_default_model=True)
    resp = client.generate(model="ignored", prompt="test", max_tokens=100, temperature=0.5)
    assert resp.text == "hello world"
    assert resp.usage.model == "gpt-4o"
    mock_provider.complete.assert_called_once()


def test_unsupported_provider_raises() -> None:
    s = AppSettings(agent_provider="unsupported-xyz")
    with pytest.raises(ValueError, match="unsupported agent provider"):
        build_client_from_settings(s)


def test_orchestrator_creates_routed_client_for_nondefault_openai_model() -> None:
    settings = AppSettings(
        agent_provider="openai-compatible",
        agent_api_key="test-key",
        agent_default_model="gpt-4o-mini",
    )
    with patch("autocontext.providers.openai_compat.OpenAICompatibleProvider") as mock_cls:
        mock_cls.return_value = MagicMock()
        orch = AgentOrchestrator.from_settings(settings)
        client = orch._client_for_provider_config(
            "competitor",
            ProviderConfig(
                provider_type="openai-compatible",
                model="gpt-4.1",
                provider_class=ProviderClass.MID_TIER,
                estimated_cost_per_1k_tokens=0.003,
            ),
        )

    assert client is not orch.client
    assert mock_cls.call_count == 2
    assert mock_cls.call_args_list[0].kwargs["default_model_name"] == "gpt-4o-mini"
    assert mock_cls.call_args_list[1].kwargs["default_model_name"] == "gpt-4.1"
