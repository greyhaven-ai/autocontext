"""Wrapper that adapts a bare callable to the LLMProvider interface.

This provides backward compatibility for existing code that passes
``llm_fn: Callable[[str, str], str]`` to LLMJudge.
"""

from __future__ import annotations

from collections.abc import Callable

from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError


class CallableProvider(LLMProvider):
    """Wraps a ``(system, user) -> str`` callable as an LLMProvider.

    This is a bridge for backward compatibility. New code should use
    a concrete provider directly.
    """

    def __init__(
        self,
        llm_fn: Callable[[str, str], str],
        model_name: str = "unknown",
    ) -> None:
        self._fn = llm_fn
        self._model_name = model_name

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        try:
            text = self._fn(system_prompt, user_prompt)
        except Exception as exc:
            raise ProviderError(f"Callable provider error: {exc}") from exc

        return CompletionResult(text=text, model=model or self._model_name)

    def default_model(self) -> str:
        return self._model_name
