"""Smoke tests for AC-352: Hermes via the OpenAI-compatible provider path.

Exercises the documented Hermes gateway configuration through the same
surfaces users see — ``create_provider``, ``build_client_from_settings``,
and ``load_settings`` — without requiring a live Hermes instance.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autocontext.config.settings import AppSettings, load_settings
from autocontext.providers.base import ProviderError

try:
    import openai  # noqa: F401

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

_skip_no_openai = pytest.mark.skipif(not _HAS_OPENAI, reason="openai package not installed")


@pytest.fixture(autouse=True)
def _avoid_real_network_client() -> Iterator[MagicMock]:
    """These factory tests exercise wiring, not the host's proxy configuration."""
    if not _HAS_OPENAI:
        yield MagicMock()
        return
    with patch("autocontext.providers.openai_compat.openai.OpenAI", autospec=True) as constructor:
        constructor.return_value = MagicMock()
        yield constructor


def _settings(**overrides: object) -> AppSettings:
    defaults: dict[str, object] = {
        "agent_provider": "deterministic",
        "knowledge_root": Path("/tmp/ac-hermes-test"),
    }
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Provider factory: Hermes-like openai-compatible endpoint
# ---------------------------------------------------------------------------

class TestHermesProviderFactory:
    """Verify create_provider builds a working provider for Hermes-like endpoints."""

    @_skip_no_openai
    def test_create_provider_openai_compatible_for_hermes(
        self,
        _avoid_real_network_client: MagicMock,
    ) -> None:
        """create_provider('openai-compatible') with Hermes base_url should construct."""
        from autocontext.providers.registry import create_provider

        provider = create_provider(
            provider_type="openai-compatible",
            api_key="hermes-test-key",
            base_url="http://localhost:8080/v1",
            model="hermes-3-llama-3.1-8b",
        )
        assert provider is not None
        assert provider.default_model() == "hermes-3-llama-3.1-8b"
        _avoid_real_network_client.assert_called_once_with(
            api_key="hermes-test-key",
            base_url="http://localhost:8080/v1",
        )

    @_skip_no_openai
    def test_hermes_provider_sends_correct_model(self) -> None:
        """The provider should pass the Hermes model name to the API."""
        from autocontext.providers.openai_compat import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            api_key="hermes-test-key",
            base_url="http://hermes.local:8080/v1",
            default_model_name="hermes-3-llama-3.1-8b",
        )
        # Mock the OpenAI client's chat.completions.create
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"aggression": 0.6}'
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        provider._client.chat.completions.create = MagicMock(return_value=mock_response)

        result = provider.complete("system", "user prompt", model="hermes-3-llama-3.1-8b")
        assert result.text == '{"aggression": 0.6}'
        call_kwargs = provider._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "hermes-3-llama-3.1-8b"

    @_skip_no_openai
    def test_hermes_provider_wraps_api_errors(self) -> None:
        """API errors should be wrapped in ProviderError for intelligible failures."""
        from autocontext.providers.openai_compat import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            api_key="bad-key",
            base_url="http://nonexistent:9999/v1",
            default_model_name="hermes-3",
        )
        provider._client.chat.completions.create = MagicMock(
            side_effect=Exception("Connection refused"),
        )
        with pytest.raises(ProviderError, match="Connection refused"):
            provider.complete("system", "test")


# ---------------------------------------------------------------------------
# Env var → load_settings → build_client round-trip for Hermes
# ---------------------------------------------------------------------------

class TestHermesEnvVarRoundTrip:
    """Verify the documented Hermes env var combinations work end-to-end."""

    def test_hermes_env_vars_load_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Documented env vars should be parsed correctly by load_settings."""
        monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "openai-compatible")
        monkeypatch.setenv("AUTOCONTEXT_AGENT_BASE_URL", "http://localhost:8080/v1")
        monkeypatch.setenv("AUTOCONTEXT_AGENT_API_KEY", "hermes-key")
        monkeypatch.setenv("AUTOCONTEXT_AGENT_DEFAULT_MODEL", "hermes-3-llama-3.1-8b")
        settings = load_settings()
        assert settings.agent_provider == "openai-compatible"
        assert settings.agent_base_url == "http://localhost:8080/v1"
        assert settings.agent_api_key == "hermes-key"
        assert settings.agent_default_model == "hermes-3-llama-3.1-8b"

    @_skip_no_openai
    def test_hermes_build_client_from_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _avoid_real_network_client: MagicMock,
    ) -> None:
        """build_client_from_settings should construct a ProviderBridgeClient for Hermes."""
        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.agents.provider_bridge import ProviderBridgeClient

        settings = _settings(
            agent_provider="openai-compatible",
            agent_base_url="http://localhost:8080/v1",
            agent_api_key="hermes-key",
            agent_default_model="hermes-3-llama-3.1-8b",
        )
        client = build_client_from_settings(settings)
        assert isinstance(client, ProviderBridgeClient)
        _avoid_real_network_client.assert_called_once_with(
            api_key="hermes-key",
            base_url="http://localhost:8080/v1",
        )


# ---------------------------------------------------------------------------
# Judge provider path for Hermes
# ---------------------------------------------------------------------------

class TestHermesJudgePath:
    """Verify Hermes can be used as the judge provider too."""

    def test_hermes_judge_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Judge provider env vars should work for Hermes endpoints."""
        monkeypatch.setenv("AUTOCONTEXT_JUDGE_PROVIDER", "openai-compatible")
        monkeypatch.setenv("AUTOCONTEXT_JUDGE_BASE_URL", "http://localhost:8080/v1")
        monkeypatch.setenv("AUTOCONTEXT_JUDGE_API_KEY", "hermes-judge-key")
        monkeypatch.setenv("AUTOCONTEXT_JUDGE_MODEL", "hermes-3-llama-3.1-70b")
        settings = load_settings()
        assert settings.judge_provider == "openai-compatible"
        assert settings.judge_base_url == "http://localhost:8080/v1"
        assert settings.judge_model == "hermes-3-llama-3.1-70b"

    @_skip_no_openai
    def test_create_judge_provider_for_hermes(
        self,
        _avoid_real_network_client: MagicMock,
    ) -> None:
        """create_provider should build a judge-capable provider for Hermes."""
        from autocontext.providers.registry import create_provider

        provider = create_provider(
            provider_type="openai-compatible",
            api_key="hermes-judge-key",
            base_url="http://localhost:8080/v1",
            model="hermes-3-llama-3.1-70b",
        )
        assert provider.default_model() == "hermes-3-llama-3.1-70b"
        _avoid_real_network_client.assert_called_once_with(
            api_key="hermes-judge-key",
            base_url="http://localhost:8080/v1",
        )


# ---------------------------------------------------------------------------
# Caveats: Hermes-specific operational concerns
# ---------------------------------------------------------------------------

class TestHermesCaveats:
    """Test edge cases documented as Hermes-specific caveats."""

    @_skip_no_openai
    def test_hermes_no_api_key_uses_no_key_fallback(self) -> None:
        """When no API key is provided, provider should still construct (Hermes may not require one)."""
        from autocontext.providers.openai_compat import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            api_key="",
            base_url="http://localhost:8080/v1",
            default_model_name="hermes-3",
        )
        # Should construct without error — Hermes local servers often don't need auth
        assert provider.default_model() == "hermes-3"

    def test_openai_package_missing_raises_clear_error(self) -> None:
        """Without openai package, construction should raise a clear ProviderError."""
        with patch.dict("sys.modules", {"openai": None}):
            # Force reimport to trigger the ImportError path

            from autocontext.providers import openai_compat

            original = openai_compat._HAS_OPENAI
            openai_compat._HAS_OPENAI = False
            try:
                with pytest.raises(ProviderError, match="openai package is required"):
                    openai_compat.OpenAICompatibleProvider(
                        api_key="key",
                        base_url="http://localhost:8080/v1",
                    )
            finally:
                openai_compat._HAS_OPENAI = original
