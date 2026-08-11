"""AC-933: which API key each transport actually receives.

Two call paths historically resolved this independently:

* ``agents/provider_bridge._provider_api_key`` — the per-role client path
* ``providers/registry.get_provider`` — the judge / default provider path

They now share the provider-native lookup and keep only their intentional
caller-specific precedence. The matrix pins both the resolved key and the key
that reaches the final SDK constructor, where another fallback used to leak.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

TRANSPORTS = ("anthropic", "openai", "openai-compatible", "openrouter", "ollama", "vllm")

_KEY_ENV = (
    "ANTHROPIC_API_KEY",
    "AUTOCONTEXT_ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "AUTOCONTEXT_OPENROUTER_API_KEY",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("AUTOCONTEXT_") or key in _KEY_ENV:
            monkeypatch.delenv(key, raising=False)


def _settings(**overrides: Any) -> Any:
    from autocontext.config.settings import load_settings

    settings = load_settings()
    for field, value in overrides.items():
        object.__setattr__(settings, field, value) if settings.model_config.get("frozen") else setattr(settings, field, value)
    return settings


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_role_path_uses_the_transports_own_env_var(clean_env: None, monkeypatch: pytest.MonkeyPatch, transport: str) -> None:
    """The per-role path reads each transport's own key. This one was correct."""
    from autocontext.agents.provider_bridge import _provider_api_key

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key")

    expected = {
        "anthropic": "ant-key",
        "openai": "oai-key",
        "openai-compatible": "oai-key",
        "openrouter": "or-key",
        "ollama": None,
        "vllm": "no-key",
    }[transport]
    assert _provider_api_key(transport, _settings()) == expected


def test_judge_path_reads_openrouter_key(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-933: the defect, stated as the behavior a user expects.

    Before the fix this returned the ANTHROPIC key, because `openrouter` had no
    branch and fell into the else. The user who set OPENROUTER_API_KEY then sees
    a 401 and reasonably concludes their key is bad, rather than that it was
    never sent.
    """
    from autocontext.providers.registry import get_provider

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key")

    captured: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("autocontext.providers.registry.create_provider", _capture)
    get_provider(_settings(judge_provider="openrouter", judge_api_key=""))

    assert captured["api_key"] == "or-key", "judge path sent the wrong key to OpenRouter"


def test_judge_path_does_not_leak_the_anthropic_key_to_openai(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport with its own key must never inherit Anthropic's.

    Guards the shape of the fix as much as the fix: the tempting one-line
    version routes every unmatched transport through the Anthropic fallback,
    which would send an Anthropic key to OpenAI whenever OPENAI_API_KEY is
    unset. Sending a real credential to the wrong vendor is worse than sending
    none.
    """
    from autocontext.providers.registry import get_provider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key")

    captured: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("autocontext.providers.registry.create_provider", _capture)
    get_provider(_settings(judge_provider="openai", judge_api_key=""))

    assert captured["api_key"] != "ant-key"


@pytest.mark.parametrize(
    ("transport", "foreign_env", "foreign_key", "expected_key"),
    (
        ("openrouter", "OPENAI_API_KEY", "openai-key", "no-key"),
        ("vllm", "ANTHROPIC_API_KEY", "anthropic-key", "no-key"),
    ),
)
def test_judge_path_does_not_leak_a_foreign_key_at_provider_construction(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    foreign_env: str,
    foreign_key: str,
    expected_key: str,
) -> None:
    """The provider's final SDK client must receive a sentinel, not a foreign key."""
    pytest.importorskip("openai")
    from autocontext.providers.registry import get_provider

    monkeypatch.setenv(foreign_env, foreign_key)

    with patch("autocontext.providers.openai_compat.openai.OpenAI") as constructor:
        get_provider(_settings(judge_provider=transport, judge_api_key=""))

    assert constructor.call_args.kwargs["api_key"] == expected_key


def test_mixed_role_prefers_its_native_key_over_other_provider_globals(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OpenRouter role must not inherit OpenAI/Anthropic global credentials."""
    from autocontext.agents.provider_bridge import _provider_api_key

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    settings = _settings(
        agent_provider="openai",
        agent_api_key="openai-key",
        judge_provider="anthropic",
        judge_api_key="anthropic-key",
        analyst_provider="openrouter",
    )

    assert _provider_api_key("openrouter", settings, role="analyst") == "openrouter-key"


def test_default_openrouter_agent_uses_its_native_key(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTOCONTEXT_AGENT_PROVIDER=openrouter is a supported default path."""
    pytest.importorskip("openai")
    from autocontext.agents.llm_client import build_client_from_settings
    from autocontext.agents.provider_bridge import ProviderBridgeClient

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")

    with patch("autocontext.providers.openai_compat.openai.OpenAI") as constructor:
        client = build_client_from_settings(_settings(agent_provider="openrouter"))

    assert isinstance(client, ProviderBridgeClient)
    assert constructor.call_args.kwargs["api_key"] == "openrouter-key"
    assert client._provider.default_model() == "anthropic/claude-sonnet-4"


def test_judge_api_key_still_wins(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_provider is the JUDGE factory; an explicit judge key outranks env."""
    from autocontext.providers.registry import get_provider

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    captured: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("autocontext.providers.registry.create_provider", _capture)
    get_provider(_settings(judge_provider="openrouter", judge_api_key="explicit"))

    assert captured["api_key"] == "explicit"
