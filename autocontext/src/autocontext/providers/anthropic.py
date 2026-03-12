"""Anthropic provider implementation."""

from __future__ import annotations

import anthropic

from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError


class AnthropicProvider(LLMProvider):
    """LLM provider using the Anthropic API (Claude models)."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model_name: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
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
            response = self._client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIError as exc:
            raise ProviderError(f"Anthropic API error: {exc}") from exc

        text = ""
        if response.content:
            block = response.content[0]
            if hasattr(block, "text"):
                text = block.text
        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        return CompletionResult(
            text=text,
            model=model_id,
            usage=usage,
        )

    def default_model(self) -> str:
        return self._default_model
