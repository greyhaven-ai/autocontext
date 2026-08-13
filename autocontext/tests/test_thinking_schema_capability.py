"""AC-936: the thinking path declares whether it honors output_schema.

The extension layer used to infer this from the method signature. A signature
cannot tell a parameter that is honored from one that is accepted and then
discarded, and Anthropic's `complete_with_thinking` does the latter -- it opens
with `del output_schema` because its loop already pins `tool_choice` to the
scratchpad tool and cannot force a second one.

So the probe reported support that did not exist. Nothing broke, because
`CompletionResult.constrained` still told the truth and callers read that. The
danger is a capability probe that disagrees with behavior: the next feature
builds on the probe, not on the flag.

Declared rather than inferred, which is the same correction AC-911 made for
provider capability.
"""

from __future__ import annotations

from typing import Any

from autocontext.providers.base import CompletionResult, LLMProvider


class _NoThinking(LLMProvider):
    def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        del args, kwargs
        return CompletionResult(text="", model="stub")

    def default_model(self) -> str:
        return "stub"


class _ThinkingHonorsSchema(_NoThinking):
    @property
    def supports_thinking_stream(self) -> bool:
        return True


class _ThinkingDiscardsSchema(_ThinkingHonorsSchema):
    @property
    def supports_thinking_output_schema(self) -> bool:
        return False


class _LegacyThinkingWithoutSchema(_NoThinking):
    """A pre-declaration provider using the old thinking signature."""

    def complete_with_thinking(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        reasoning_effort: str = "medium",
        max_tool_turns: int = 8,
    ) -> CompletionResult:
        del system_prompt, user_prompt, model, temperature, max_tokens, reasoning_effort, max_tool_turns
        return CompletionResult(text="legacy", model="legacy")

    @property
    def supports_thinking_stream(self) -> bool:
        return True


def test_default_follows_whether_a_thinking_loop_exists() -> None:
    """A provider with no thinking loop has nothing to honor a schema on."""
    assert _NoThinking().supports_thinking_output_schema is False
    assert _ThinkingHonorsSchema().supports_thinking_output_schema is True


def test_a_provider_can_declare_that_it_discards_the_schema() -> None:
    assert _ThinkingDiscardsSchema().supports_thinking_output_schema is False


def test_legacy_subclass_falls_back_to_its_thinking_signature() -> None:
    """An inherited base property must not turn an old method into a declaration."""
    assert _LegacyThinkingWithoutSchema().supports_thinking_output_schema is False


def test_anthropic_declares_it_does_not_honor_the_schema() -> None:
    """The concrete case, asserted against the real provider.

    Pinned to the class rather than an instance so it needs no API key, and
    tied to the `del output_schema` in `complete_with_thinking`: if that line
    ever goes away, this test is the reminder that the declaration must move
    with it.
    """
    from autocontext.providers.anthropic import AnthropicProvider

    assert AnthropicProvider.supports_thinking_output_schema.fget(object.__new__(AnthropicProvider)) is False


def test_the_extension_probe_believes_the_declaration_over_the_signature() -> None:
    """The defect, stated as the behavior that was wrong.

    `_ThinkingDiscardsSchema.complete_with_thinking` inherits a signature that
    accepts `output_schema`, so signature inspection alone answers True here.
    """
    from autocontext.extensions import HookBus
    from autocontext.extensions.llm import HookedLLMProvider, _accepts_output_schema

    provider = _ThinkingDiscardsSchema()
    signature_says = _accepts_output_schema(getattr(provider, "complete_with_thinking", None))
    hooked = HookedLLMProvider(provider, HookBus())

    assert signature_says is True, "signature inspection no longer over-reports; this test is out of date"
    assert hooked._thinking_supports_output_schema is False
    assert hooked.supports_thinking_output_schema is False


def test_hooked_legacy_subclass_does_not_receive_a_new_schema_keyword() -> None:
    from autocontext.extensions import HookBus
    from autocontext.extensions.llm import HookedLLMProvider
    from autocontext.providers.base import OutputSchema

    hooked = HookedLLMProvider(_LegacyThinkingWithoutSchema(), HookBus())

    result = hooked.complete_with_thinking(
        "system",
        "user",
        output_schema=OutputSchema(name="answer", schema={"type": "object"}),
    )

    assert result.text == "legacy"
    assert hooked.supports_thinking_output_schema is False


def test_a_provider_with_no_declaration_still_falls_back_to_the_signature() -> None:
    """Third-party providers predate this attribute and must keep working."""
    from autocontext.extensions import HookBus
    from autocontext.extensions.llm import HookedLLMProvider

    class _Legacy:
        def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
            del args, kwargs
            return CompletionResult(text="", model="legacy")

        def complete_with_thinking(
            self,
            system_prompt: str,
            user_prompt: str,
            output_schema: Any = None,
        ) -> CompletionResult:
            del system_prompt, user_prompt, output_schema
            return CompletionResult(text="", model="legacy")

        @property
        def name(self) -> str:
            return "legacy"

    hooked = HookedLLMProvider(_Legacy(), HookBus())
    assert hooked._thinking_supports_output_schema is True


def test_the_retry_wrapper_forwards_the_declaration() -> None:
    """Otherwise the wrapper answers for itself and the declaration is lost."""
    from autocontext.providers.retry import RetryProvider

    wrapped = RetryProvider(_ThinkingDiscardsSchema())
    assert wrapped.supports_thinking_output_schema is False

    honest = RetryProvider(_ThinkingHonorsSchema())
    assert honest.supports_thinking_output_schema is True
