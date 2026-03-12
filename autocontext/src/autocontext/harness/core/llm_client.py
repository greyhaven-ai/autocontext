"""Domain-agnostic language model client base class."""

from __future__ import annotations

from autocontext.harness.core.types import ModelResponse


class LanguageModelClient:
    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        raise NotImplementedError

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
        """Multi-turn generation with conversation history.

        Default implementation concatenates into a single-turn call for backwards compat.
        """
        combined = system + "\n\n" + "\n\n".join(m["content"] for m in messages if m["role"] == "user")
        return self.generate(model=model, prompt=combined, max_tokens=max_tokens, temperature=temperature)
