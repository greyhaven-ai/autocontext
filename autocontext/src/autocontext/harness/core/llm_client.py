"""Domain-agnostic language model client base class."""

from __future__ import annotations

from autocontext.harness.core.types import ModelResponse
from autocontext.providers.base import OutputSchema


class LanguageModelClient:
    # ERP-67: True only for backends whose generate_multiturn genuinely routes a
    # separate system turn (real message roles). Default False → the base
    # generate_multiturn flattens system+user, so structural isolation must NOT
    # be applied (the flat prompt is preserved instead). Wrappers inherit False
    # unless they explicitly forward the capability.
    supports_structural_isolation: bool = False

    # AC-913: True only for backends that can genuinely constrain generation to
    # a schema. Default False -> generate_constrained below falls back to
    # generate() and reports constrained=False, so a CLI runtime that cannot
    # enforce anything keeps working and says so. Mirrors
    # supports_structural_isolation deliberately: same capability-flag shape,
    # same "wrappers inherit False unless they forward it" rule.
    supports_constrained_output: bool = False

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
        """Generate under a schema, falling back when the backend cannot.

        The default is honest rather than optimistic: it runs the ordinary
        generate() and records ``constrained=False`` in the response metadata.
        AC-913 requires that a backend without constrained decoding still
        works and that the run record says the output was unconstrained --
        this is where the second half of that happens, for every backend that
        does not override it.
        """
        del output_schema
        # Honor the structural-isolation split (ERP-67) on the fallback too:
        # dropping the system turn here would quietly weaken role isolation for
        # every schema-requesting call on a backend that cannot constrain.
        if system:
            response = self.generate_multiturn(
                model=model,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                role=role,
            )
        else:
            response = self.generate(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                role=role,
            )
        response.metadata.setdefault("constrained", False)
        return response

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
