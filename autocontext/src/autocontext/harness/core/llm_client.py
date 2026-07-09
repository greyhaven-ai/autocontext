"""Domain-agnostic language model client base class."""

from __future__ import annotations

from autocontext.harness.core.types import ModelResponse


class LanguageModelClient:
    # ERP-67: True only for backends whose generate_multiturn genuinely routes a
    # separate system turn (real message roles). Default False → the base
    # generate_multiturn flattens system+user, so structural isolation must NOT
    # be applied (the flat prompt is preserved instead). Wrappers inherit False
    # unless they explicitly forward the capability.
    supports_structural_isolation: bool = False

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
        return self.generate(
            model=model,
            prompt=combined,
            max_tokens=max_tokens,
            temperature=temperature,
            role=role,
        )
