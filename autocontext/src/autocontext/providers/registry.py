"""Provider registry — create providers from config."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from autocontext.providers.base import LLMProvider, ProviderError

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings


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
        from autocontext.providers.anthropic import AnthropicProvider
        from autocontext.providers.retry import RetryProvider

        return RetryProvider(
            AnthropicProvider(
                api_key=api_key
                or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("AUTOCONTEXT_ANTHROPIC_API_KEY"),
                default_model_name=model or "claude-sonnet-4-20250514",
            )
        )

    if provider_type in ("openai", "openai-compatible"):
        from autocontext.providers.openai_compat import OpenAICompatibleProvider
        from autocontext.providers.retry import RetryProvider

        kwargs: dict = {
            "api_key": api_key or os.getenv("OPENAI_API_KEY"),
            "default_model_name": model or "gpt-4o",
        }
        if base_url:
            kwargs["base_url"] = base_url
        return RetryProvider(OpenAICompatibleProvider(**kwargs))

    if provider_type == "ollama":
        from autocontext.providers.openai_compat import OpenAICompatibleProvider
        from autocontext.providers.retry import RetryProvider

        return RetryProvider(OpenAICompatibleProvider(
            api_key="ollama",
            base_url=base_url or "http://localhost:11434/v1",
            default_model_name=model or "llama3.1",
        ))

    if provider_type == "vllm":
        from autocontext.providers.openai_compat import OpenAICompatibleProvider
        from autocontext.providers.retry import RetryProvider

        return RetryProvider(OpenAICompatibleProvider(
            api_key=api_key or "no-key",
            base_url=base_url or "http://localhost:8000/v1",
            default_model_name=model or "default",
        ))

    if provider_type == "mlx":
        from autocontext.providers.mlx_provider import MLXProvider

        if not model:
            raise ProviderError("MLX provider requires a model path (model_path). Set AUTOCONTEXT_MLX_MODEL_PATH.")
        return MLXProvider(model_path=model)

    raise ProviderError(
        f"Unknown provider type: {provider_type!r}. "
        f"Supported: anthropic, openai, openai-compatible, ollama, vllm, mlx"
    )


# Agent providers that can be inherited as judge providers without extra
# credentials. When judge_provider is left as its "auto" default (AC-586),
# get_provider() inherits from the effective execution provider if it's in this
# set.
_RUNTIME_BRIDGE_PROVIDERS: frozenset[str] = frozenset(
    {"claude-cli", "codex", "pi", "pi-rpc"}
)

_AUTO_JUDGE_PROVIDER_PRIORITY: tuple[str, ...] = (
    "competitor_provider",
    "architect_provider",
    "analyst_provider",
    "coach_provider",
    "agent_provider",
)


def _configured_provider(settings: AppSettings, field_name: str) -> str:
    value = getattr(settings, field_name, "")
    return value.lower().strip() if isinstance(value, str) else ""


def _resolve_auto_judge_provider(settings: AppSettings) -> str:
    """Map judge_provider='auto' to an effective provider type (AC-586).

    Prefer the first explicitly configured execution provider in priority order:
    competitor → architect → analyst → coach → global agent_provider. If that
    effective provider is one of the runtime-bridged values (claude-cli, codex,
    pi, pi-rpc), use it for the judge too — so subscription-tier users who only
    have local CLI auth don't hit the Anthropic SDK's "Could not resolve
    authentication method" error downstream. For any other provider, preserve
    the historical anthropic default.
    """
    for field_name in _AUTO_JUDGE_PROVIDER_PRIORITY:
        provider = _configured_provider(settings, field_name)
        if not provider:
            continue
        if provider in _RUNTIME_BRIDGE_PROVIDERS:
            return provider
        break
    return "anthropic"


def get_provider(settings: AppSettings) -> LLMProvider:
    """Create a judge provider from autocontext settings.

    Uses ``settings.judge_provider``, ``settings.judge_base_url``, and
    ``settings.judge_api_key``. Falls back to provider-specific env vars
    (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``) when ``judge_api_key`` is not set.

    When ``judge_provider`` is ``"auto"`` (the default), inherits a
    runtime-bridged provider from ``settings.agent_provider`` (AC-586).
    """
    provider_type = settings.judge_provider.lower().strip()
    if provider_type == "auto":
        provider_type = _resolve_auto_judge_provider(settings)
    base_url = settings.judge_base_url

    # MLX provider has its own construction path using mlx_* settings
    if provider_type == "mlx":
        from autocontext.providers.mlx_provider import MLXProvider

        model_path = settings.mlx_model_path
        if not model_path:
            raise ProviderError("MLX provider requires mlx_model_path. Set AUTOCONTEXT_MLX_MODEL_PATH.")
        return MLXProvider(
            model_path=model_path,
            temperature=settings.mlx_temperature,
            max_tokens=settings.mlx_max_tokens,
        )

    if provider_type == "claude-cli":
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider
        from autocontext.runtimes.claude_cli import ClaudeCLIConfig, ClaudeCLIRuntime

        claude_runtime = ClaudeCLIRuntime(ClaudeCLIConfig(
            model=settings.claude_model,
            tools=settings.claude_tools,
            permission_mode=settings.claude_permission_mode,
            session_persistence=settings.claude_session_persistence,
            timeout=settings.claude_timeout,
        ))
        return RuntimeBridgeProvider(claude_runtime, default_model_name=settings.claude_model)

    if provider_type == "codex":
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider
        from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

        codex_runtime = CodexCLIRuntime(CodexCLIConfig(
            model=settings.codex_model,
            approval_mode=settings.codex_approval_mode,
            timeout=settings.codex_timeout,
            workspace=settings.codex_workspace,
            quiet=settings.codex_quiet,
        ))
        return RuntimeBridgeProvider(codex_runtime, default_model_name=settings.codex_model)

    if provider_type == "pi":
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider
        from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime

        pi_runtime = PiCLIRuntime(PiCLIConfig(
            pi_command=settings.pi_command,
            timeout=settings.pi_timeout,
            workspace=settings.pi_workspace,
            model=settings.pi_model,
            no_context_files=settings.pi_no_context_files,
        ))
        return RuntimeBridgeProvider(pi_runtime, default_model_name=settings.pi_model or "pi-default")

    if provider_type == "pi-rpc":
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider
        from autocontext.runtimes.pi_rpc import PiRPCConfig, PiRPCRuntime

        pi_rpc_runtime = PiRPCRuntime(PiRPCConfig(
            pi_command=settings.pi_command,
            model=settings.pi_model or settings.judge_model,
            timeout=settings.pi_timeout,
            session_persistence=settings.pi_rpc_session_persistence,
            no_context_files=settings.pi_no_context_files,
        ))
        return RuntimeBridgeProvider(
            pi_rpc_runtime,
            default_model_name=settings.pi_model or settings.judge_model or "pi-rpc-default",
        )

    # Use judge_api_key if set, otherwise fall back to provider-specific keys
    api_key = settings.judge_api_key
    if not api_key:
        if provider_type in ("openai", "openai-compatible"):
            api_key = os.getenv("OPENAI_API_KEY")
        else:
            api_key = (
                settings.anthropic_api_key
                or os.getenv("ANTHROPIC_API_KEY")
                or os.getenv("AUTOCONTEXT_ANTHROPIC_API_KEY")
            )

    return create_provider(
        provider_type=provider_type,
        api_key=api_key,
        base_url=base_url,
        model=settings.judge_model,
    )
