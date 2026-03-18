"""Tests for AC-315: provider registry wraps providers with RetryProvider.

Verifies that create_provider and get_provider return retry-wrapped
providers that handle transient 500 errors with backoff.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestProviderRegistryRetry:
    def test_create_provider_returns_retry_wrapped(self) -> None:
        """Anthropic provider from create_provider should be retry-wrapped."""
        from autocontext.providers.registry import create_provider
        from autocontext.providers.retry import RetryProvider

        provider = create_provider(
            provider_type="anthropic",
            api_key="sk-test",
        )
        assert isinstance(provider, RetryProvider)

    def test_ollama_provider_returns_retry_wrapped(self) -> None:
        """Ollama uses OpenAI-compatible — skip if openai not installed."""
        import pytest

        from autocontext.providers.retry import RetryProvider

        try:
            from autocontext.providers.registry import create_provider

            provider = create_provider(provider_type="ollama", api_key="", model="llama3.1")
        except Exception:
            pytest.skip("openai package not available")
        assert isinstance(provider, RetryProvider)

    def test_retry_provider_retries_on_500(self) -> None:
        """RetryProvider should retry on 500 errors."""
        from autocontext.providers.base import CompletionResult, ProviderError
        from autocontext.providers.retry import RetryProvider

        mock_provider = MagicMock()
        mock_provider.default_model.return_value = "test-model"

        # First call fails with 500, second succeeds
        mock_provider.complete.side_effect = [
            ProviderError("Anthropic API error: 500 Internal Server Error"),
            CompletionResult(text="success", model="test-model"),
        ]

        retry = RetryProvider(mock_provider, max_retries=2, base_delay=0.01)
        result = retry.complete("system", "user")

        assert result.text == "success"
        assert mock_provider.complete.call_count == 2

    def test_retry_gives_up_after_max_retries(self) -> None:
        import pytest

        from autocontext.providers.base import ProviderError
        from autocontext.providers.retry import RetryProvider

        mock_provider = MagicMock()
        mock_provider.complete.side_effect = ProviderError("500 Internal Server Error")

        retry = RetryProvider(mock_provider, max_retries=2, base_delay=0.01)

        with pytest.raises(ProviderError, match="500"):
            retry.complete("system", "user")

        assert mock_provider.complete.call_count == 3  # 1 initial + 2 retries

    def test_get_provider_also_wraps(self) -> None:
        """get_provider() should also return retry-wrapped providers."""
        from autocontext.providers.registry import get_provider
        from autocontext.providers.retry import RetryProvider

        settings = MagicMock()
        settings.judge_provider = "anthropic"
        settings.judge_model = ""
        settings.judge_base_url = ""
        settings.judge_api_key = ""
        settings.anthropic_api_key = "sk-test"

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            provider = get_provider(settings)

        assert isinstance(provider, RetryProvider)
