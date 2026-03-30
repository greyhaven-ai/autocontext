"""Bridge adapter: wrap an LLMProvider as a LanguageModelClient.

Enables per-role provider overrides (AC-184) by allowing any LLMProvider
(e.g. MLXProvider) to be used where the agent system expects a
LanguageModelClient.
"""
from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import shlex
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings
    from autocontext.providers.base import LLMProvider
    from autocontext.runtimes.base import AgentRuntime


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


class RuntimeBridgeClient(LanguageModelClient):
    """Adapts an AgentRuntime to the LanguageModelClient interface.

    This bridge enables any AgentRuntime (PiCLI, ClaudeCLI, etc.)
    to be used as a client for agent role runners.
    """

    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        del max_tokens, temperature, role
        t0 = time.monotonic()
        output = self._runtime.generate(prompt)
        error = output.metadata.get("error")
        if error:
            detail = output.metadata.get("detail") or output.metadata.get("stderr") or ""
            raise RuntimeError(f"{self._runtime.name} failed: {error}{f' ({detail})' if detail else ''}")
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return ModelResponse(
            text=output.text,
            usage=RoleUsage(
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=max(1, len(output.text) // 4),
                latency_ms=elapsed_ms,
                model=output.model or model,
            ),
            metadata=dict(output.metadata),
        )


def _provider_api_key(provider_type: str, settings: AppSettings) -> str | None:
    if provider_type == "anthropic":
        return settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    if provider_type in ("openai", "openai-compatible"):
        return settings.agent_api_key or settings.judge_api_key or os.getenv("OPENAI_API_KEY")
    if provider_type == "vllm":
        return settings.agent_api_key or settings.judge_api_key or "no-key"
    return settings.agent_api_key or settings.judge_api_key


def _create_provider_bridge(
    provider_type: str,
    settings: AppSettings,
    *,
    model_override: str | None = None,
) -> LanguageModelClient:
    """Create a ProviderBridgeClient for a given provider type."""
    from autocontext.providers.registry import create_provider

    if provider_type == "mlx":
        from autocontext.providers.mlx_provider import MLXProvider

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
            base_url=settings.agent_base_url or settings.judge_base_url,
            model=model_override or settings.agent_default_model,
        )
        use_provider_default_model = True
    return ProviderBridgeClient(provider, use_provider_default_model=use_provider_default_model)


def _create_claude_cli_bridge(
    settings: AppSettings,
    *,
    model_override: str | None = None,
) -> LanguageModelClient:
    from autocontext.runtimes.claude_cli import ClaudeCLIConfig, ClaudeCLIRuntime

    config = ClaudeCLIConfig(
        model=model_override or settings.claude_model or "sonnet",
        tools=settings.claude_tools,
        permission_mode=settings.claude_permission_mode,
        session_persistence=settings.claude_session_persistence,
        timeout=settings.claude_timeout,
    )
    return RuntimeBridgeClient(ClaudeCLIRuntime(config))


def _create_codex_cli_bridge(
    settings: AppSettings,
    *,
    model_override: str | None = None,
) -> LanguageModelClient:
    from autocontext.runtimes.codex_cli import CodexCLIConfig, CodexCLIRuntime

    config = CodexCLIConfig(
        model=model_override or settings.codex_model or "o4-mini",
        approval_mode=settings.codex_approval_mode,
        timeout=settings.codex_timeout,
        workspace=settings.codex_workspace,
        quiet=settings.codex_quiet,
    )
    return RuntimeBridgeClient(CodexCLIRuntime(config))


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
    scenario_name: str = "",
) -> LanguageModelClient | None:
    """Create a LanguageModelClient for a per-role provider override.

    Args:
        provider_type: Provider name (e.g. "mlx", "anthropic", "deterministic").
            Empty string returns None (use default).
        settings: App settings for provider configuration.
        scenario_name: Scenario name used for scenario-local runtime handoff.

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

    if provider_type == "claude-cli":
        return _create_claude_cli_bridge(settings, model_override=model_override)

    if provider_type == "codex":
        return _create_codex_cli_bridge(settings, model_override=model_override)

    if provider_type == "pi":
        from autocontext.providers.scenario_routing import resolve_pi_model
        from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime
        from autocontext.training.model_registry import ModelRegistry

        resolved_model = settings.pi_model
        if scenario_name or settings.pi_model:
            try:
                handoff = resolve_pi_model(
                    ModelRegistry(settings.knowledge_root),
                    scenario=scenario_name,
                    backend="mlx",
                    manual_override=settings.pi_model or None,
                )
            except Exception:
                logger.debug("agents.provider_bridge: caught Exception", exc_info=True)
                handoff = None
            if handoff is not None:
                resolved_model = handoff.checkpoint_path

        pi_config = PiCLIConfig(
            pi_command=settings.pi_command,
            timeout=settings.pi_timeout,
            workspace=settings.pi_workspace,
            model=resolved_model,
        )
        return RuntimeBridgeClient(PiCLIRuntime(pi_config))

    if provider_type == "pi-rpc":
        from autocontext.runtimes.pi_rpc import PiRPCConfig, PiRPCRuntime

        rpc_config = PiRPCConfig(
            pi_command=settings.pi_command,
            session_persistence=settings.pi_rpc_session_persistence,
        )
        return RuntimeBridgeClient(PiRPCRuntime(rpc_config))

    if provider_type == "hermes":
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        hermes_config = HermesCLIConfig(
            hermes_command=settings.hermes_command,
            model=model_override or settings.hermes_model,
            timeout=settings.hermes_timeout,
            workspace=settings.hermes_workspace,
            base_url=settings.hermes_base_url,
            api_key=settings.hermes_api_key,
            toolsets=settings.hermes_toolsets,
            skills=settings.hermes_skills,
            worktree=settings.hermes_worktree,
            quiet=settings.hermes_quiet,
            provider=settings.hermes_provider,
        )
        return RuntimeBridgeClient(HermesCLIRuntime(hermes_config))

    # LLMProvider-based providers — use the bridge
    if provider_type in ("mlx", "openai", "openai-compatible", "ollama", "vllm"):
        return _create_provider_bridge(provider_type, settings, model_override=model_override)

    raise ValueError(f"unsupported role provider: {provider_type!r}")


def _build_openclaw_agent(settings: AppSettings) -> object:
    """Build an OpenClaw agent instance from settings.

    The runtime is configured via ``AUTOCONTEXT_OPENCLAW_RUNTIME_KIND`` and one of:
    - ``AUTOCONTEXT_OPENCLAW_AGENT_FACTORY=module:callable``
    - ``AUTOCONTEXT_OPENCLAW_AGENT_COMMAND='binary --flag value'``
    - ``AUTOCONTEXT_OPENCLAW_AGENT_HTTP_ENDPOINT=https://...``
    """
    from autocontext.openclaw.adapters import (
        AdapterBackedOpenClawAgent,
        CLIOpenClawAdapter,
        HTTPOpenClawAdapter,
        capability_from_settings,
    )

    runtime_kind = getattr(settings, "openclaw_runtime_kind", "factory").strip().lower() or "factory"
    compatibility_version = getattr(settings, "openclaw_compatibility_version", "1.0")

    if runtime_kind == "factory":
        factory_path = settings.openclaw_agent_factory.strip()
        if not factory_path:
            raise ValueError(
                "OpenClaw factory runtime requires AUTOCONTEXT_OPENCLAW_AGENT_FACTORY=module:callable",
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

    if runtime_kind == "cli":
        command_parts = shlex.split(getattr(settings, "openclaw_agent_command", ""))
        if not command_parts:
            raise ValueError(
                "OpenClaw CLI runtime requires AUTOCONTEXT_OPENCLAW_AGENT_COMMAND",
            )
        cli_adapter = CLIOpenClawAdapter(
            command=command_parts[0],
            extra_args=command_parts[1:],
            timeout=float(getattr(settings, "openclaw_timeout_seconds", 30.0)),
        )
        return AdapterBackedOpenClawAgent(
            adapter=cli_adapter,
            capability=capability_from_settings(
                "cli",
                compatibility_version=compatibility_version,
                metadata={"command": command_parts[0]},
            ),
        )

    if runtime_kind == "http":
        endpoint = getattr(settings, "openclaw_agent_http_endpoint", "").strip()
        if not endpoint:
            raise ValueError(
                "OpenClaw HTTP runtime requires AUTOCONTEXT_OPENCLAW_AGENT_HTTP_ENDPOINT",
            )
        raw_headers = getattr(settings, "openclaw_agent_http_headers", "").strip()
        headers: dict[str, str] = {}
        if raw_headers:
            try:
                parsed = json.loads(raw_headers)
            except json.JSONDecodeError as exc:
                raise ValueError("AUTOCONTEXT_OPENCLAW_AGENT_HTTP_HEADERS must be valid JSON") from exc
            if not isinstance(parsed, dict):
                raise ValueError("AUTOCONTEXT_OPENCLAW_AGENT_HTTP_HEADERS must be a JSON object")
            headers = {str(k): str(v) for k, v in parsed.items()}

        http_adapter = HTTPOpenClawAdapter(
            endpoint=endpoint,
            timeout=float(getattr(settings, "openclaw_timeout_seconds", 30.0)),
            headers=headers,
        )
        return AdapterBackedOpenClawAgent(
            adapter=http_adapter,
            capability=capability_from_settings(
                "http",
                compatibility_version=compatibility_version,
                metadata={"endpoint": endpoint},
            ),
        )

    raise ValueError(
        f"unsupported OpenClaw runtime kind: {runtime_kind!r} (expected 'factory', 'cli', or 'http')",
    )
