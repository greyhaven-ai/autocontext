"""Bridge adapter: wrap an LLMProvider as a LanguageModelClient.

Enables per-role provider overrides (AC-184) by allowing any LLMProvider
(e.g. MLXProvider) to be used where the agent system expects a
LanguageModelClient.
"""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from mts.harness.core.llm_client import LanguageModelClient
from mts.harness.core.types import ModelResponse, RoleUsage

if TYPE_CHECKING:
    from mts.config.settings import AppSettings
    from mts.providers.base import LLMProvider


class ProviderBridgeClient(LanguageModelClient):
    """Adapts an LLMProvider to the LanguageModelClient interface.

    This bridge enables any LLMProvider (Anthropic, MLX, OpenAI-compat, etc.)
    to be used as a client for agent role runners.
    """

    def __init__(self, provider: LLMProvider, *, use_provider_default_model: bool = False) -> None:
        self._provider = provider
        self._use_provider_default_model = use_provider_default_model

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        t0 = time.monotonic()
        resolved_model = None if self._use_provider_default_model else model
        result = self._provider.complete(
            system_prompt="",
            user_prompt=prompt,
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        usage_model = result.model or resolved_model or self._provider.default_model()

        return ModelResponse(
            text=result.text,
            usage=RoleUsage(
                input_tokens=result.usage.get("input_tokens", 0),
                output_tokens=result.usage.get("output_tokens", 0),
                latency_ms=elapsed_ms,
                model=usage_model,
            ),
        )


def _provider_api_key(provider_type: str, settings: AppSettings) -> str | None:
    if provider_type == "anthropic":
        return settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    if provider_type in ("openai", "openai-compatible"):
        return settings.judge_api_key or os.getenv("OPENAI_API_KEY")
    if provider_type == "vllm":
        return settings.judge_api_key or "no-key"
    return settings.judge_api_key


def _create_provider_bridge(provider_type: str, settings: AppSettings) -> LanguageModelClient:
    """Create a ProviderBridgeClient for a given provider type."""
    from mts.providers.registry import create_provider

    if provider_type == "mlx":
        from mts.providers.mlx_provider import MLXProvider  # type: ignore[import-untyped]

        provider: LLMProvider = MLXProvider(
            model_path=getattr(settings, "mlx_model_path", ""),
            temperature=getattr(settings, "mlx_temperature", 0.8),
            max_tokens=getattr(settings, "mlx_max_tokens", 512),
        )
        use_provider_default_model = True
    else:
        provider = create_provider(
            provider_type=provider_type,
            api_key=_provider_api_key(provider_type, settings),
            base_url=settings.judge_base_url,
        )
        use_provider_default_model = True
    return ProviderBridgeClient(provider, use_provider_default_model=use_provider_default_model)


def create_role_client(provider_type: str, settings: AppSettings) -> LanguageModelClient | None:
    """Create a LanguageModelClient for a per-role provider override.

    Args:
        provider_type: Provider name (e.g. "mlx", "anthropic", "deterministic").
            Empty string returns None (use default).
        settings: App settings for provider configuration.

    Returns:
        A LanguageModelClient, or None if provider_type is empty.

    Raises:
        ValueError: If the provider type is unsupported.
    """
    if not provider_type:
        return None

    provider_type = provider_type.lower().strip()

    # Native LanguageModelClient implementations
    if provider_type == "deterministic":
        from mts.agents.llm_client import DeterministicDevClient

        return DeterministicDevClient()

    if provider_type == "anthropic":
        from mts.agents.llm_client import AnthropicClient

        api_key = _provider_api_key(provider_type, settings)
        if not api_key:
            raise ValueError("Anthropic per-role override requires MTS_ANTHROPIC_API_KEY")
        return AnthropicClient(api_key=api_key)

    if provider_type == "agent_sdk":
        from mts.agents.agent_sdk_client import AgentSdkClient, AgentSdkConfig

        return AgentSdkClient(config=AgentSdkConfig(connect_mcp_server=settings.agent_sdk_connect_mcp))

    # LLMProvider-based providers — use the bridge
    if provider_type in ("mlx", "openai", "openai-compatible", "ollama", "vllm"):
        return _create_provider_bridge(provider_type, settings)

    raise ValueError(f"unsupported role provider: {provider_type!r}")
