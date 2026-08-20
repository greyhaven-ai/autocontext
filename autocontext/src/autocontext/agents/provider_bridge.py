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
import shlex
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from autocontext.extensions.llm import HookedLanguageModelClient
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage
from autocontext.offline import require_endpoint_available, require_runtime_available
from autocontext.runtimes.errors import format_runtime_failure

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings
    from autocontext.providers.base import LLMProvider, OutputSchema
    from autocontext.runtimes.base import AgentRuntime
    from autocontext.session.runtime_session import RuntimeSession


class ProviderBridgeClient(LanguageModelClient):
    """Adapts an LLMProvider to the LanguageModelClient interface.

    This bridge enables any LLMProvider (Anthropic, MLX, OpenAI-compat, etc.)
    to be used as a client for agent role runners.
    """

    def __init__(self, provider: LLMProvider, *, use_provider_default_model: bool = False) -> None:
        self._provider = provider
        self._use_provider_default_model = use_provider_default_model
        self.supports_constrained_output = _accepts_output_schema(provider.complete)

    def generate_constrained(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        output_schema: OutputSchema,
        role: str = "",
        system: str = "",
    ) -> ModelResponse:
        """Generate with a schema, recording whether the backend enforced it."""
        return self._complete(
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            role=role,
            output_schema=output_schema,
            system=system,
        )

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        return self._complete(
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            role=role,
            output_schema=None,
        )

    def _complete(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str,
        output_schema: OutputSchema | None,
        system: str = "",
    ) -> ModelResponse:
        del role
        t0 = time.monotonic()
        resolved_model = None if self._use_provider_default_model else model
        # LLMProvider is a public interface and subclasses written before
        # output_schema was added are still valid Python implementations. Only
        # pass the new keyword when the concrete method opted into it; do not
        # catch TypeError here, because that would also swallow bugs raised
        # inside a provider implementation.
        extra: dict[str, Any] = {}
        if output_schema is not None and self.supports_constrained_output:
            extra["output_schema"] = output_schema
        result = self._provider.complete(
            system_prompt=system,
            user_prompt=prompt,
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        usage_model = result.model or resolved_model or self._provider.default_model()

        metadata: dict[str, Any] = {}
        if result.cost_usd is not None:
            metadata["cost_usd"] = result.cost_usd
        # What the backend actually did, not what was asked for. A provider
        # that ignored the schema reports False here and the run record shows it.
        metadata["constrained"] = result.constrained
        return ModelResponse(
            text=result.text,
            usage=RoleUsage(
                input_tokens=result.usage.get("input_tokens", 0),
                output_tokens=result.usage.get("output_tokens", 0),
                latency_ms=elapsed_ms,
                model=usage_model,
            ),
            metadata=metadata,
        )


def _accepts_output_schema(complete: Callable[..., object]) -> bool:
    """Whether a provider's concrete complete method accepts the new keyword."""
    try:
        parameters = inspect.signature(complete).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "output_schema" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
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
            raise RuntimeError(format_runtime_failure(self._runtime.name, output.metadata))
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        metadata = dict(output.metadata)
        if output.cost_usd is not None:
            metadata["cost_usd"] = output.cost_usd
        return ModelResponse(
            text=output.text,
            usage=RoleUsage(
                input_tokens=max(1, len(prompt) // 4),
                output_tokens=max(1, len(output.text) // 4),
                latency_ms=elapsed_ms,
                model=output.model or model,
            ),
            metadata=metadata,
        )

    def close(self) -> None:
        close = getattr(self._runtime, "close", None)
        if callable(close):
            close()


class RuntimeSessionRecordingClient(LanguageModelClient):
    """Record runtime-backed client calls into a run-scoped RuntimeSession."""

    def __init__(
        self,
        inner: LanguageModelClient,
        *,
        session: RuntimeSession,
        role: str,
        cwd: str = "",
    ) -> None:
        self.inner = inner
        self.session = session
        self.role = role
        self.cwd = cwd
        # Forward explicitly — __getattr__ never fires for this inherited base
        # attribute, so without this a session-recorded capable client (the normal
        # run path) reports False and ERP-67 isolation no-ops.
        self.supports_structural_isolation = bool(getattr(inner, "supports_structural_isolation", False))

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def _record(self, *, prompt: str, model: str, resolved_role: str, call: Callable[[], ModelResponse]) -> ModelResponse:
        from autocontext.session.runtime_session import RuntimeSessionPromptHandlerOutput

        response: ModelResponse | None = None
        failure: Exception | None = None

        def handler(_input: object) -> RuntimeSessionPromptHandlerOutput:
            nonlocal response, failure
            try:
                response = call()
            except Exception as exc:
                failure = exc
                raise
            return RuntimeSessionPromptHandlerOutput(
                text=response.text,
                metadata=_runtime_session_response_metadata(
                    self.inner,
                    response,
                    runtime_session_id=self.session.session_id,
                    operation="generate",
                ),
            )

        result = self.session.submit_prompt(prompt=prompt, handler=handler, role=resolved_role, cwd=self.cwd)
        if result.is_error:
            raise failure or RuntimeError(result.error)
        if response is None:
            return ModelResponse(
                text=result.text,
                usage=RoleUsage(
                    input_tokens=max(1, len(prompt) // 4),
                    output_tokens=max(1, len(result.text) // 4),
                    latency_ms=0,
                    model=model,
                ),
                metadata={"runtimeSessionId": self.session.session_id},
            )
        metadata = dict(response.metadata)
        metadata["runtimeSessionId"] = self.session.session_id
        return ModelResponse(text=response.text, usage=response.usage, metadata=metadata)

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        resolved_role = role or self.role
        return self._record(
            prompt=prompt,
            model=model,
            resolved_role=resolved_role,
            call=lambda: self.inner.generate(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                role=resolved_role,
            ),
        )

    def generate_multiturn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        # Record the flattened turns for the session log/replay, but call the
        # inner client's real generate_multiturn so a capable backend keeps the
        # untrusted content in a separate user turn (ERP-67 structural isolation).
        resolved_role = role or self.role
        recorded = system + "\n\n" + "\n\n".join(m["content"] for m in messages if m.get("role") == "user")
        return self._record(
            prompt=recorded,
            model=model,
            resolved_role=resolved_role,
            call=lambda: self.inner.generate_multiturn(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                role=resolved_role,
            ),
        )

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()


def wrap_runtime_session_client(
    client: LanguageModelClient,
    *,
    session: RuntimeSession,
    role: str,
    cwd: str = "",
) -> LanguageModelClient:
    """Attach recording to runtime-backed clients, leaving plain LLM clients alone."""
    if isinstance(client, RuntimeSessionRecordingClient):
        return client
    if isinstance(client, HookedLanguageModelClient):
        inner = wrap_runtime_session_client(client.inner, session=session, role=role, cwd=cwd)
        if inner is client.inner:
            return client
        return HookedLanguageModelClient(inner, client.hook_bus, provider_name=client.provider_name)
    if _find_runtime_bridge_client(client) is None:
        return client
    return RuntimeSessionRecordingClient(client, session=session, role=role, cwd=cwd)


def _find_runtime_bridge_client(client: object) -> RuntimeBridgeClient | None:
    current = client
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RuntimeBridgeClient):
            return current
        current = getattr(current, "inner", None)
    return None


def _runtime_session_response_metadata(
    client: LanguageModelClient,
    response: ModelResponse,
    *,
    runtime_session_id: str,
    operation: str,
) -> dict[str, Any]:
    bridge = _find_runtime_bridge_client(client)
    runtime = getattr(bridge, "_runtime", None) if bridge is not None else None
    metadata: dict[str, Any] = dict(response.metadata)
    metadata.update(
        {
            "operation": operation,
            "runtime": getattr(runtime, "name", client.__class__.__name__),
            "runtimeSessionId": runtime_session_id,
            "model": response.usage.model,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "latency_ms": response.usage.latency_ms,
            },
        }
    )
    return metadata


def _role_setting(settings: AppSettings, role: str, suffix: str) -> str:
    if not role:
        return ""
    value = getattr(settings, f"{role}_{suffix}", "")
    return value.strip() if isinstance(value, str) else ""


def configured_role_provider(role: str, settings: AppSettings) -> str:
    return _role_setting(settings, role, "provider").lower()


def has_role_client_override(role: str, settings: AppSettings) -> bool:
    return any(
        (
            configured_role_provider(role, settings),
            _role_setting(settings, role, "api_key"),
            _role_setting(settings, role, "base_url"),
        )
    )


def _provider_api_key(provider_type: str, settings: AppSettings, *, role: str = "") -> str | None:
    role_api_key = _role_setting(settings, role, "api_key")
    if role_api_key:
        return role_api_key
    # AC-933: the per-transport env vars come from one shared table, so this
    # path and registry.get_provider cannot drift again. The PRECEDENCE differs
    # by design and stays here: anthropic ignores the generic agent/judge keys,
    # while the OpenAI-shaped transports prefer them.
    from autocontext.providers.registry import resolve_auto_judge_provider, transport_env_api_key

    normalized = provider_type.lower().strip()
    # The independent auditor is a security boundary. It may use its own
    # explicit key or the selected transport's native environment key, but it
    # must never inherit a generic agent/judge credential for another route.
    if role == "campaign_auditor":
        provider_key = transport_env_api_key(normalized, settings)
        if provider_key:
            return provider_key
        return "no-key" if normalized == "vllm" else None
    if normalized == "anthropic":
        return transport_env_api_key("anthropic", settings)

    # Generic keys are valid only for the provider they configure. In a mixed
    # setup, reusing the default OpenAI agent key for an OpenRouter role is the
    # same cross-vendor leak as the registry bug this helper prevents.
    agent_provider = settings.agent_provider.lower().strip()
    if normalized == agent_provider and settings.agent_api_key:
        return settings.agent_api_key

    judge_provider = settings.judge_provider.lower().strip()
    if judge_provider == "auto":
        judge_provider = resolve_auto_judge_provider(settings)
    if normalized == judge_provider and settings.judge_api_key:
        return settings.judge_api_key

    provider_key = transport_env_api_key(normalized, settings)
    if provider_key:
        return provider_key
    if normalized == "vllm":
        return "no-key"
    return None


def _provider_base_url(settings: AppSettings, *, role: str = "") -> str | None:
    role_base_url = _role_setting(settings, role, "base_url")
    if role_base_url:
        return role_base_url
    # Do not send an independently selected auditor to the agent or judge's
    # private OpenAI-compatible endpoint. The registry will choose the
    # selected provider's canonical default when no dedicated endpoint exists.
    if role == "campaign_auditor":
        return None
    return settings.agent_base_url or settings.judge_base_url


def resolved_role_base_url(provider_type: str, settings: AppSettings, *, role: str = "") -> str | None:
    """Return the endpoint input used by one concrete role client, if any."""

    normalized = provider_type.lower().strip()
    if normalized in {"openai", "openai-compatible", "openrouter", "ollama", "vllm"}:
        return _provider_base_url(settings, role=role)
    if normalized == "hermes":
        return _role_setting(settings, role, "base_url") or settings.hermes_base_url or None
    return None


def _provider_model(
    provider_type: str,
    settings: AppSettings,
    *,
    model_override: str | None = None,
) -> str | None:
    """Resolve the model passed to an OpenAI-shaped provider client."""
    if model_override is not None:
        return model_override
    from autocontext.config.provider_model_defaults import resolve_model_default

    return resolve_model_default(
        settings,
        provider=provider_type,
        field_name="agent_default_model",
        configured=settings.agent_default_model,
    )


def _create_provider_bridge(
    provider_type: str,
    settings: AppSettings,
    *,
    model_override: str | None = None,
    role: str = "",
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
            api_key=_provider_api_key(provider_type, settings, role=role),
            base_url=_provider_base_url(settings, role=role),
            model=_provider_model(provider_type, settings, model_override=model_override),
        )
        use_provider_default_model = True
    return ProviderBridgeClient(provider, use_provider_default_model=use_provider_default_model)


def _create_claude_cli_bridge(
    settings: AppSettings,
    *,
    model_override: str | None = None,
) -> LanguageModelClient:
    # AC-735: route through the shared factory so per-role overrides also
    # honor claude_max_total_seconds (the budget is attached uniformly).
    from autocontext.runtimes.claude_cli import build_claude_cli_runtime

    return RuntimeBridgeClient(build_claude_cli_runtime(settings, model_override=model_override))


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
    role: str = "",
) -> LanguageModelClient | None:
    """Create a LanguageModelClient for a per-role provider override.

    Args:
        provider_type: Provider name (e.g. "mlx", "anthropic", "deterministic").
            Empty string returns None (use default).
        settings: App settings for provider configuration.
        model_override: Authoritative model for the constructed route when set.
        scenario_name: Scenario name used for scenario-local runtime handoff.
        role: Role-specific endpoint and credential namespace.

    Returns:
        A LanguageModelClient, or None if provider_type is empty.

    Raises:
        ValueError: If the provider type is unsupported.
    """
    if not provider_type:
        return None

    provider_type = provider_type.lower().strip()
    require_runtime_available(provider_type, settings=settings)

    # Native LanguageModelClient implementations
    if provider_type == "deterministic":
        from autocontext.agents.llm_client import DeterministicDevClient

        return DeterministicDevClient()

    if provider_type == "anthropic":
        from autocontext.agents.llm_client import AnthropicClient

        api_key = _provider_api_key(provider_type, settings, role=role)
        if not api_key:
            role_key = f"AUTOCONTEXT_{role.upper()}_API_KEY, " if role else ""
            raise ValueError(
                f"Anthropic client requires {role_key}AUTOCONTEXT_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY",
            )
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

        resolved_model = model_override if model_override is not None else settings.pi_model
        if model_override is None and (scenario_name or settings.pi_model):
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
            no_context_files=settings.pi_no_context_files,
        )
        return RuntimeBridgeClient(PiCLIRuntime(pi_config))

    if provider_type == "pi-rpc":
        from autocontext.runtimes.pi_rpc import PiRPCConfig, build_pi_rpc_runtime

        rpc_config = PiRPCConfig(
            pi_command=settings.pi_command,
            model=model_override if model_override is not None else settings.pi_model,
            timeout=settings.pi_timeout,
            workspace=settings.pi_workspace,
            session_persistence=settings.pi_rpc_session_persistence,
            no_context_files=settings.pi_no_context_files,
        )
        return RuntimeBridgeClient(build_pi_rpc_runtime(rpc_config, persistent=settings.pi_rpc_persistent))

    if provider_type == "hermes":
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        hermes_config = HermesCLIConfig(
            hermes_command=settings.hermes_command,
            model=model_override or settings.hermes_model,
            timeout=settings.hermes_timeout,
            workspace=settings.hermes_workspace,
            base_url=_role_setting(settings, role, "base_url") or settings.hermes_base_url,
            api_key=_role_setting(settings, role, "api_key") or settings.hermes_api_key,
            toolsets=settings.hermes_toolsets,
            skills=settings.hermes_skills,
            worktree=settings.hermes_worktree,
            quiet=settings.hermes_quiet,
            provider=settings.hermes_provider,
        )
        return RuntimeBridgeClient(HermesCLIRuntime(hermes_config))

    # LLMProvider-based providers — use the bridge
    if provider_type in ("mlx", "openai", "openai-compatible", "openrouter", "ollama", "vllm"):
        return _create_provider_bridge(provider_type, settings, model_override=model_override, role=role)

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
        require_runtime_available("openclaw-factory", settings=settings)
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
        require_runtime_available("openclaw-cli", settings=settings)
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
        require_endpoint_available("call an OpenClaw endpoint", endpoint, settings=settings)
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
