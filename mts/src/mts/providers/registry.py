"""Provider registry — create providers from config."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from mts.providers.base import LLMProvider, ProviderError

if TYPE_CHECKING:
    from mts.config.settings import AppSettings


def create_provider(
    provider_type: str,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Create an LLM provider by type name.

    Args:
        provider_type: One of ``anthropic``, ``openai``, ``openai-compatible``,
            ``ollama``, ``vllm``.
        api_key: API key for the provider.
        base_url: Base URL for OpenAI-compatible endpoints.
        model: Default model name.

    Returns:
        An initialized LLMProvider instance.

    Raises:
        ProviderError: If the provider type is unknown or configuration is invalid.
    """
    provider_type = provider_type.lower().strip()

    if provider_type == "anthropic":
        from mts.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
            default_model_name=model or "claude-sonnet-4-20250514",
        )

    if provider_type in ("openai", "openai-compatible"):
        from mts.providers.openai_compat import OpenAICompatibleProvider

        kwargs: dict = {
            "api_key": api_key or os.getenv("OPENAI_API_KEY"),
            "default_model_name": model or "gpt-4o",
        }
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAICompatibleProvider(**kwargs)

    if provider_type == "ollama":
        from mts.providers.openai_compat import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key="ollama",
            base_url=base_url or "http://localhost:11434/v1",
            default_model_name=model or "llama3.1",
        )

    if provider_type == "vllm":
        from mts.providers.openai_compat import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=api_key or "no-key",
            base_url=base_url or "http://localhost:8000/v1",
            default_model_name=model or "default",
        )

    raise ProviderError(
        f"Unknown provider type: {provider_type!r}. "
        f"Supported: anthropic, openai, openai-compatible, ollama, vllm"
    )


def get_provider(settings: AppSettings) -> LLMProvider:
    """Create a judge provider from MTS settings.

    Uses ``settings.judge_provider``, ``settings.judge_base_url``, and
    ``settings.judge_api_key``. Falls back to provider-specific env vars
    (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``) when ``judge_api_key`` is not set.
    """
    provider_type = settings.judge_provider
    base_url = settings.judge_base_url

    # Use judge_api_key if set, otherwise fall back to provider-specific keys
    api_key = settings.judge_api_key
    if not api_key:
        if provider_type in ("openai", "openai-compatible"):
            api_key = os.getenv("OPENAI_API_KEY")
        else:
            api_key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")

    return create_provider(
        provider_type=provider_type,
        api_key=api_key,
        base_url=base_url,
        model=settings.judge_model,
    )
