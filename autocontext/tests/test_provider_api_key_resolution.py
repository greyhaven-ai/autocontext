"""AC-933: which API key each transport actually receives.

Two independent resolvers decide this and they disagree:

* ``agents/provider_bridge._provider_api_key`` — the per-role client path
* ``providers/registry.get_provider`` — the judge / default provider path

Characterized here across every transport before changing either, because the
defect is precisely that one of them was complete and the other was not, and
only a table shows which rows differ on purpose and which by omission.
"""

from __future__ import annotations

import os
from typing import Any

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
