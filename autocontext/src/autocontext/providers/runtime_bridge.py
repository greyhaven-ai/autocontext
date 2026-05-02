"""Bridge adapter: wrap an AgentRuntime as an LLMProvider.

Used when provider-like surfaces (such as judge_provider) need to route through
subscription-backed local CLIs instead of direct hosted APIs.
"""

from __future__ import annotations

from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError
from autocontext.runtimes.base import AgentRuntime
from autocontext.runtimes.errors import format_runtime_failure


class RuntimeBridgeProvider(LLMProvider):
    """Adapts an AgentRuntime to the LLMProvider interface."""

    def __init__(self, runtime: AgentRuntime, default_model_name: str) -> None:
        self._runtime = runtime
        self._default_model_name = default_model_name

    @property
    def supports_concurrent_requests(self) -> bool:
        return getattr(self._runtime, "supports_concurrent_requests", True) is not False

    def default_model(self) -> str:
        return self._default_model_name

    def close(self) -> None:
        close = getattr(self._runtime, "close", None)
        if callable(close):
            close()

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        del temperature, max_tokens
        output = self._runtime.generate(
            prompt=user_prompt,
            system=system_prompt or None,
        )
        error = output.metadata.get("error") if output.metadata else None
        if error:
            raise ProviderError(format_runtime_failure(self._runtime.name, output.metadata or {}))
        return CompletionResult(
            text=output.text,
            model=output.model or model or self._default_model_name,
            cost_usd=output.cost_usd,
            usage={},
        )

    @property
    def name(self) -> str:
        return "runtime-bridge"
