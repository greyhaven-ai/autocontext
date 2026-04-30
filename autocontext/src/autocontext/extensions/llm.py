from __future__ import annotations

from dataclasses import asdict
from typing import Any

from autocontext.extensions.hooks import HookBus, HookEvents
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage
from autocontext.providers.base import CompletionResult, LLMProvider


class HookedLanguageModelClient(LanguageModelClient):
    """Wrap any LanguageModelClient with provider request/response hooks."""

    def __init__(self, inner: LanguageModelClient, hook_bus: HookBus, *, provider_name: str = "") -> None:
        self.inner = inner
        self.hook_bus = hook_bus
        self.provider_name = provider_name or inner.__class__.__name__

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        payload = {
            "provider": self.provider_name,
            "role": role,
            "model": model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "multiturn": False,
        }
        before = self.hook_bus.emit(HookEvents.BEFORE_PROVIDER_REQUEST, payload)
        before.raise_if_blocked()
        request = before.payload
        response = self.inner.generate(
            model=str(request.get("model", model)),
            prompt=str(request.get("prompt", prompt)),
            max_tokens=int(request.get("max_tokens", max_tokens)),
            temperature=float(request.get("temperature", temperature)),
            role=str(request.get("role", role)),
        )
        return self._emit_response(response, request)

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
        payload = {
            "provider": self.provider_name,
            "role": role,
            "model": model,
            "system": system,
            "messages": [dict(message) for message in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "multiturn": True,
        }
        before = self.hook_bus.emit(HookEvents.BEFORE_PROVIDER_REQUEST, payload)
        before.raise_if_blocked()
        request = before.payload
        response = self.inner.generate_multiturn(
            model=str(request.get("model", model)),
            system=str(request.get("system", system)),
            messages=_message_list(request.get("messages", messages)),
            max_tokens=int(request.get("max_tokens", max_tokens)),
            temperature=float(request.get("temperature", temperature)),
            role=str(request.get("role", role)),
        )
        return self._emit_response(response, request)

    def _emit_response(self, response: ModelResponse, request: dict[str, Any]) -> ModelResponse:
        payload = {
            "provider": self.provider_name,
            "role": request.get("role", ""),
            "model": request.get("model", response.usage.model),
            "request": dict(request),
            "text": response.text,
            "usage": asdict(response.usage),
            "metadata": dict(response.metadata),
        }
        after = self.hook_bus.emit(HookEvents.AFTER_PROVIDER_RESPONSE, payload)
        after.raise_if_blocked()
        response_payload = after.payload
        metadata = dict(response.metadata)
        maybe_metadata = response_payload.get("metadata")
        if isinstance(maybe_metadata, dict):
            metadata.update(maybe_metadata)
        usage = _usage_from_payload(response.usage, response_payload.get("usage"))
        return ModelResponse(text=str(response_payload.get("text", response.text)), usage=usage, metadata=metadata)


class HookedLLMProvider(LLMProvider):
    """Wrap any LLMProvider with provider request/response hooks."""

    def __init__(
        self,
        inner: LLMProvider,
        hook_bus: HookBus,
        *,
        provider_name: str = "",
        role: str = "",
    ) -> None:
        self.inner = inner
        self.hook_bus = hook_bus
        self.provider_name = provider_name or inner.name
        self.role = role

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        payload = {
            "provider": self.provider_name,
            "role": self.role,
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "multiturn": False,
        }
        before = self.hook_bus.emit(HookEvents.BEFORE_PROVIDER_REQUEST, payload)
        before.raise_if_blocked()
        request = before.payload
        response = self.inner.complete(
            system_prompt=str(request.get("system_prompt", system_prompt)),
            user_prompt=str(request.get("user_prompt", user_prompt)),
            model=_optional_str(request.get("model", model)),
            temperature=float(request.get("temperature", temperature)),
            max_tokens=int(request.get("max_tokens", max_tokens)),
        )
        response_model = getattr(response, "model", None)
        response_usage = getattr(response, "usage", {})
        response_cost = getattr(response, "cost_usd", None)
        response_payload = {
            "provider": self.provider_name,
            "role": request.get("role", self.role),
            "model": response_model or request.get("model") or model or self.default_model(),
            "request": dict(request),
            "text": response.text,
            "usage": dict(response_usage) if isinstance(response_usage, dict) else {},
            "cost_usd": response_cost,
        }
        after = self.hook_bus.emit(HookEvents.AFTER_PROVIDER_RESPONSE, response_payload)
        after.raise_if_blocked()
        return CompletionResult(
            text=str(after.payload.get("text", response.text)),
            model=_optional_str(after.payload.get("model", response_model)),
            usage=_usage_dict(after.payload.get("usage", response_usage)),
            cost_usd=_optional_float(after.payload.get("cost_usd", response_cost)),
        )

    def default_model(self) -> str:
        return self.inner.default_model()

    @property
    def name(self) -> str:
        return self.provider_name


def _message_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    messages: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            role = str(item.get("role", "user"))
            content = str(item.get("content", ""))
            messages.append({"role": role, "content": content})
    return messages


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _usage_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(item) for key, item in value.items() if isinstance(item, (int, float))}


def _usage_from_payload(default: RoleUsage, value: Any) -> RoleUsage:
    if not isinstance(value, dict):
        return default
    return RoleUsage(
        input_tokens=int(value.get("input_tokens", default.input_tokens)),
        output_tokens=int(value.get("output_tokens", default.output_tokens)),
        latency_ms=int(value.get("latency_ms", default.latency_ms)),
        model=str(value.get("model", default.model)),
    )


def wrap_language_model_client(
    client: LanguageModelClient,
    hook_bus: HookBus | None,
    *,
    provider_name: str = "",
) -> LanguageModelClient:
    if hook_bus is None:
        return client
    if isinstance(client, HookedLanguageModelClient):
        return client
    return HookedLanguageModelClient(client, hook_bus, provider_name=provider_name)


def wrap_llm_provider(
    provider: LLMProvider,
    hook_bus: HookBus | None,
    *,
    provider_name: str = "",
    role: str = "",
) -> LLMProvider:
    if hook_bus is None:
        return provider
    if isinstance(provider, HookedLLMProvider):
        return provider
    return HookedLLMProvider(provider, hook_bus, provider_name=provider_name, role=role)
