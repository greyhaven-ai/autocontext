"""Provider registry — create providers from config."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from autocontext.offline import require_runtime_available
from autocontext.providers.base import LLMProvider, ProviderError

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings


# Transports this package can construct. `create_provider` handles the API-backed
# ones; `get_provider` adds the runtime-bridged and MLX branches. Kept as one
# frozenset so the contract test has a single thing to compare against, instead of
# re-deriving the list by reading dispatch branches.
SUPPORTED_PROVIDER_TYPES: frozenset[str] = frozenset(
    {
        "anthropic",
        "openai",
        "openai-compatible",
        "openrouter",
        "ollama",
        "vllm",
        "mlx",
        "claude-cli",
        "codex",
        "pi",
        "pi-rpc",
    }
)

# Canonical HTTP roots for transports that expose the OpenAI-compatible
# surface. Runtime construction and preflight both consume this table: a probe
# must never reconstruct a different destination from the client it protects.
OPENAI_COMPATIBLE_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openai-compatible": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
}


def supported_provider_types() -> frozenset[str]:
    """Transports this package can construct. Compared against the shared contract."""
    return SUPPORTED_PROVIDER_TYPES


def resolve_provider_base_url(provider_type: str, configured: str | None = None) -> str | None:
    """Return the HTTP root runtime construction will use for a provider."""
    if configured:
        return configured
    return OPENAI_COMPATIBLE_DEFAULT_BASE_URLS.get(provider_type.lower().strip())


def create_provider(
    provider_type: str,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Create an LLM provider by type name.

    Args:
        provider_type: One of ``anthropic``, ``openai``, ``openai-compatible``,
            ``openrouter``, ``ollama``, ``vllm``.
        api_key: API key for the provider.
        base_url: Base URL for OpenAI-compatible endpoints.
        model: Default model name.

    Returns:
        An initialized LLMProvider instance.

    Raises:
        ProviderError: If the provider type is unknown or configuration is invalid.
    """
    provider_type = provider_type.lower().strip()

    # AC-917: refuse the subprocess runtimes here rather than letting them
    # start and dial out. They shell out to a third-party binary whose sockets
    # this process does not control, so offline mode cannot vouch for them --
    # they are unavailable rather than silently trusted.
    require_runtime_available(provider_type)

    if provider_type == "anthropic":
        from autocontext.providers.anthropic import AnthropicProvider
        from autocontext.providers.retry import RetryProvider

        return RetryProvider(
            AnthropicProvider(
                api_key=api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("AUTOCONTEXT_ANTHROPIC_API_KEY"),
                default_model_name=model or "claude-sonnet-5",
            )
        )

    if provider_type in ("openai", "openai-compatible"):
        from autocontext.providers.openai_compat import OpenAICompatibleProvider
        from autocontext.providers.retry import RetryProvider

        kwargs: dict = {
            "api_key": api_key or os.getenv("OPENAI_API_KEY"),
            "default_model_name": model or "gpt-5.6-terra",
            "base_url": resolve_provider_base_url(provider_type, base_url),
        }
        return RetryProvider(OpenAICompatibleProvider(**kwargs))

    if provider_type == "openrouter":
        from autocontext.providers.openai_compat import OpenAICompatibleProvider
        from autocontext.providers.retry import RetryProvider

        return RetryProvider(
            OpenAICompatibleProvider(
                # OpenAICompatibleProvider has a generic OPENAI_API_KEY
                # fallback. Supplying a sentinel here is what keeps a missing
                # OpenRouter credential from crossing that vendor boundary.
                api_key=(
                    api_key
                    or os.getenv("OPENROUTER_API_KEY")
                    or os.getenv("AUTOCONTEXT_OPENROUTER_API_KEY")
                    or "no-key"
                ),
                base_url=resolve_provider_base_url(provider_type, base_url),
                default_model_name=model or "anthropic/claude-sonnet-5",
            )
        )

    if provider_type == "ollama":
        from autocontext.providers.openai_compat import OpenAICompatibleProvider
        from autocontext.providers.retry import RetryProvider

        return RetryProvider(
            OpenAICompatibleProvider(
                api_key="ollama",
                base_url=resolve_provider_base_url(provider_type, base_url),
                default_model_name=model or "llama3.1",
            )
        )

    if provider_type == "vllm":
        from autocontext.providers.openai_compat import OpenAICompatibleProvider
        from autocontext.providers.retry import RetryProvider

        return RetryProvider(
            OpenAICompatibleProvider(
                api_key=api_key or "no-key",
                base_url=resolve_provider_base_url(provider_type, base_url),
                default_model_name=model or "default",
            )
        )

    if provider_type == "mlx":
        from autocontext.providers.mlx_provider import MLXProvider

        if not model:
            raise ProviderError("MLX provider requires a model path (model_path). Set AUTOCONTEXT_MLX_MODEL_PATH.")
        return MLXProvider(model_path=model)

    supported = "anthropic, openai, openai-compatible, openrouter, ollama, vllm, mlx"
    raise ProviderError(f"Unknown provider type: {provider_type!r}. Supported: {supported}")


# Agent providers that can be inherited as judge providers without extra
# credentials. When judge_provider is left as its "auto" default (AC-586),
# get_provider() inherits from the effective execution provider if it's in this
# set.
_RUNTIME_BRIDGE_PROVIDERS: frozenset[str] = frozenset({"claude-cli", "codex", "pi", "pi-rpc"})

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


def resolve_auto_judge_provider(settings: AppSettings) -> str:
    """Map judge_provider='auto' to an effective provider type (AC-586).

    Prefer the first explicitly configured execution provider in priority order:
    competitor → architect → analyst → coach → global agent_provider. If that
    effective provider is one of the runtime-bridged values (claude-cli, codex,
    pi, pi-rpc), use it for the judge too — so subscription-tier users who only
    have local CLI auth don't hit the Anthropic SDK's "Could not resolve
    authentication method" error downstream. For any other provider, preserve
    the historical anthropic default.

    Public so CLI override-application logic can gate provider-specific flags
    on the same effective provider that `get_provider()` will dispatch to.
    """
    for field_name in _AUTO_JUDGE_PROVIDER_PRIORITY:
        provider = _configured_provider(settings, field_name)
        if not provider:
            continue
        if provider in _RUNTIME_BRIDGE_PROVIDERS:
            return provider
        break
    return "anthropic"


# AC-933: the env vars each transport expects, in one place.
#
# This existed twice -- completely in agents/provider_bridge._provider_api_key
# and partially here -- and the incomplete copy handed OpenRouter the Anthropic
# key because `openrouter` had no branch and fell through to the default. One
# table, two readers.
def transport_env_api_key(provider_type: str, settings: AppSettings) -> str | None:
    """The key a transport expects, from its own setting and environment.

    Returns None for transports that carry no credential of their own (ollama,
    vllm, mlx); callers decide what a missing key means for those.
    """
    if provider_type == "anthropic":
        return settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("AUTOCONTEXT_ANTHROPIC_API_KEY")
    if provider_type in ("openai", "openai-compatible"):
        return os.getenv("OPENAI_API_KEY")
    if provider_type == "openrouter":
        return os.getenv("OPENROUTER_API_KEY") or os.getenv("AUTOCONTEXT_OPENROUTER_API_KEY")
    return None


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
        provider_type = resolve_auto_judge_provider(settings)
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
        # AC-735: route through the shared factory so judge/provider paths
        # honor claude_max_total_seconds (the budget is attached uniformly).
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider
        from autocontext.runtimes.claude_cli import build_claude_cli_runtime

        claude_runtime = build_claude_cli_runtime(settings)
        return RuntimeBridgeProvider(claude_runtime, default_model_name=settings.claude_model)

    if provider_type == "codex":
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider
        from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

        codex_runtime = CodexCLIRuntime(
            CodexCLIConfig(
                model=settings.codex_model,
                approval_mode=settings.codex_approval_mode,
                timeout=settings.codex_timeout,
                workspace=settings.codex_workspace,
                quiet=settings.codex_quiet,
            )
        )
        return RuntimeBridgeProvider(codex_runtime, default_model_name=settings.codex_model)

    if provider_type == "pi":
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider
        from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime

        pi_runtime = PiCLIRuntime(
            PiCLIConfig(
                pi_command=settings.pi_command,
                timeout=settings.pi_timeout,
                workspace=settings.pi_workspace,
                model=settings.pi_model,
                no_context_files=settings.pi_no_context_files,
            )
        )
        return RuntimeBridgeProvider(pi_runtime, default_model_name=settings.pi_model or "pi-default")

    if provider_type == "pi-rpc":
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider
        from autocontext.runtimes.pi_rpc import PiRPCConfig, build_pi_rpc_runtime

        pi_rpc_runtime = build_pi_rpc_runtime(
            PiRPCConfig(
                pi_command=settings.pi_command,
                model=settings.pi_model or settings.judge_model,
                timeout=settings.pi_timeout,
                workspace=settings.pi_workspace,
                session_persistence=settings.pi_rpc_session_persistence,
                no_context_files=settings.pi_no_context_files,
            ),
            persistent=settings.pi_rpc_persistent,
        )
        return RuntimeBridgeProvider(
            pi_rpc_runtime,
            default_model_name=settings.pi_model or settings.judge_model or "pi-rpc-default",
        )

    # Use judge_api_key if set, otherwise consult only this transport's own
    # credential source. Runtime-backed and MLX providers returned above;
    # Ollama ignores the value and vLLM substitutes its no-key sentinel.
    # Falling back to Anthropic here would send that credential to vLLM.
    api_key = settings.judge_api_key or transport_env_api_key(provider_type, settings)

    return create_provider(
        provider_type=provider_type,
        api_key=api_key,
        base_url=base_url,
        model=settings.judge_model,
    )
