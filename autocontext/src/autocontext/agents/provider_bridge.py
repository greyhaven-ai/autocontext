"""Bridge adapter: wrap an LLMProvider as a LanguageModelClient.

Enables per-role provider overrides (AC-184) by allowing any LLMProvider
(e.g. MLXProvider) to be used where the agent system expects a
LanguageModelClient.
"""
from __future__ import annotations

import importlib
import inspect
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings
    from autocontext.providers.base import LLMProvider


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


def _create_provider_bridge(
    provider_type: str,
    settings: AppSettings,
    *,
    model_override: str | None = None,
) -> LanguageModelClient:
    """Create a ProviderBridgeClient for a given provider type."""
    from autocontext.providers.registry import create_provider

    if provider_type == "mlx":
        from autocontext.providers.mlx_provider import MLXProvider  # type: ignore[import-untyped]

        model_path = str(model_override or getattr(settings, "mlx_model_path", ""))
        provider: LLMProvider = MLXProvider(
            model_path=model_path,
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


def _load_openclaw_factory(factory_path: str) -> Callable[..., object]:
    """Load a module:callable factory reference for OpenClaw agents."""
    module_name, sep, attr_name = factory_path.partition(":")
    if not sep or not module_name or not attr_name:
        raise ValueError(
            "AUTOCONTEXT_OPENCLAW_AGENT_FACTORY must be in the form 'module:callable'",
        )
    module = importlib.import_module(module_name)
    try:
        factory = getattr(module, attr_name)
    except AttributeError as exc:
        raise ValueError(f"OpenClaw factory {factory_path!r} not found") from exc
    if not callable(factory):
        raise ValueError(f"OpenClaw factory {factory_path!r} is not callable")
    return cast(Callable[..., object], factory)


def create_role_client(
    provider_type: str,
    settings: AppSettings,
    *,
    model_override: str | None = None,
) -> LanguageModelClient | None:
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
        from autocontext.agents.llm_client import DeterministicDevClient

        return DeterministicDevClient()

    if provider_type == "anthropic":
        from autocontext.agents.llm_client import AnthropicClient

        api_key = _provider_api_key(provider_type, settings)
        if not api_key:
            raise ValueError("Anthropic per-role override requires AUTOCONTEXT_ANTHROPIC_API_KEY")
        return AnthropicClient(api_key=api_key)

    if provider_type == "agent_sdk":
        from autocontext.agents.agent_sdk_client import AgentSdkClient, AgentSdkConfig

        return AgentSdkClient(config=AgentSdkConfig(connect_mcp_server=settings.agent_sdk_connect_mcp))

    if provider_type == "openclaw":
        agent = _build_openclaw_agent(settings)
        from autocontext.openclaw.agent_adapter import OpenClawClient

        return OpenClawClient(
            agent=agent,
            max_retries=int(getattr(settings, "openclaw_max_retries", 2)),
            timeout_seconds=float(getattr(settings, "openclaw_timeout_seconds", 30.0)),
            retry_base_delay=float(getattr(settings, "openclaw_retry_base_delay", 0.25)),
        )

    # LLMProvider-based providers — use the bridge
    if provider_type in ("mlx", "openai", "openai-compatible", "ollama", "vllm"):
        return _create_provider_bridge(provider_type, settings, model_override=model_override)

    raise ValueError(f"unsupported role provider: {provider_type!r}")


def _build_openclaw_agent(settings: AppSettings) -> object:
    """Build an OpenClaw agent instance from settings.

    The factory is configured via ``AUTOCONTEXT_OPENCLAW_AGENT_FACTORY=module:callable``.
    The callable may accept ``settings`` or no arguments.
    """
    factory_path = settings.openclaw_agent_factory.strip()
    if not factory_path:
        raise ValueError(
            "OpenClaw per-role override requires AUTOCONTEXT_OPENCLAW_AGENT_FACTORY=module:callable",
        )

    factory = _load_openclaw_factory(factory_path)
    signature = inspect.signature(factory)
    if len(signature.parameters) == 0:
        agent = factory()
    else:
        agent = factory(settings)

    if not hasattr(agent, "execute"):
        raise ValueError(
            f"OpenClaw factory {factory_path!r} did not return an agent with an execute(...) method",
        )
    return agent
