"""SftTorchClient -- LanguageModelClient over a torch/peft SFT LoRA adapter (base + adapter).

The serving counterpart to the TRL ``sft`` training backend, so a harness-trained torch/peft
adapter can drive an agent role (the recursive loop for capable models on CUDA). Mirrors
``MLXLMClient`` exactly; the only difference is the underlying provider. Lives in its own module
(not ``llm_client.py``) to keep that module under the 800-line size limit.

``SftTorchProvider`` is imported lazily inside ``__init__`` so this module imports without torch
(Linux/CI); constructing the client in a torch-absent environment raises ``ImportError``, which the
serving resolver caller catches to fall back to the frontier client.
"""

from __future__ import annotations

import time

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage
from autocontext.providers.base import ProviderError


class SftTorchClient(LanguageModelClient):
    """LanguageModelClient over a local torch/peft base model, optionally with a LoRA adapter."""

    def __init__(
        self,
        model: str,
        *,
        adapter_path: str | None = None,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ) -> None:
        from autocontext.providers.sft_torch_provider import SftTorchProvider

        self._provider = SftTorchProvider(
            model,
            adapter_path=adapter_path,
            temperature=temperature,
            max_tokens=max_tokens,
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
        del model, role
        started = time.perf_counter()
        try:
            result = self._provider.complete("", prompt, temperature=temperature, max_tokens=max_tokens)
        except ProviderError as exc:
            raise RuntimeError(str(exc)) from exc
        elapsed = int((time.perf_counter() - started) * 1000)
        usage = RoleUsage(
            input_tokens=result.usage.get("input_tokens", 0),
            output_tokens=result.usage.get("output_tokens", 0),
            latency_ms=elapsed,
            model=result.model or self._provider.default_model(),
        )
        return ModelResponse(text=result.text, usage=usage)

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
        del role
        user_parts = [m["content"] for m in messages if m["role"] == "user"]
        combined = "\n\n".join(user_parts)
        prompt = f"{system}\n\n{combined}" if system else combined
        return self.generate(model=model, prompt=prompt, max_tokens=max_tokens, temperature=temperature)
