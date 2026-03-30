"""OpenAI-compatible provider implementation.

Works with: OpenAI, Azure OpenAI, vLLM, Ollama, LiteLLM, any
server that implements the OpenAI chat completions API.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError

logger = logging.getLogger(__name__)

try:
    import openai  # type: ignore[import-not-found]

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


class OpenAICompatibleProvider(LLMProvider):
    """LLM provider for any OpenAI-compatible API endpoint.

    Supports OpenAI, Azure, vLLM, Ollama, and any server implementing
    the ``/v1/chat/completions`` endpoint.

    Args:
        api_key: API key (or ``"ollama"`` for keyless local servers).
        base_url: Base URL for the API (e.g. ``http://localhost:11434/v1``).
        default_model_name: Model to use when none is specified.
        extra_headers: Additional HTTP headers for every request.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model_name: str = "gpt-4o",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not _HAS_OPENAI:
            raise ProviderError(
                "openai package is required for OpenAICompatibleProvider. "
                "Install with: pip install openai"
            )

        resolved_key = api_key or os.getenv("OPENAI_API_KEY") or "no-key"
        kwargs: dict[str, Any] = {"api_key": resolved_key}
        if base_url:
            kwargs["base_url"] = base_url
        if extra_headers:
            kwargs["default_headers"] = extra_headers

        self._client = openai.OpenAI(**kwargs)
        self._default_model = default_model_name

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        model_id = model or self._default_model
        try:
            response = self._client.chat.completions.create(
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            logger.debug("providers.openai_compat: caught Exception", exc_info=True)
            raise ProviderError(f"OpenAI-compatible API error: {exc}") from exc

        choice = response.choices[0] if response.choices else None
        text = choice.message.content or "" if choice else ""

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens or 0,
                "output_tokens": response.usage.completion_tokens or 0,
            }

        return CompletionResult(
            text=text,
            model=model_id,
            usage=usage,
        )

    def default_model(self) -> str:
        return self._default_model
