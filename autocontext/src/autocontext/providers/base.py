"""Base provider interface for LLM calls."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Raised when an LLM provider call fails."""

    def __init__(self, message: str, *, usage: dict[str, int] | None = None) -> None:
        super().__init__(message)
        # Successful requests earlier in a multi-turn operation may already be
        # billable even when a later request fails. Retry wrappers aggregate
        # this partial usage into the eventual successful result.
        self.usage = dict(usage or {})


class ThinkingUnsupportedError(ProviderError):
    """Raised when an endpoint cannot honor structured thinking capture."""


@dataclass(frozen=True, slots=True)
class OutputSchema:
    """A JSON Schema the backend should constrain generation to (AC-913).

    Passing one is a request, not a guarantee: backends that cannot enforce a
    schema ignore it and report ``constrained=False`` on the result. Callers
    must read that flag rather than assume the text validates, which is the
    whole point -- an unconstrained run should be visible, not inferred.
    """

    name: str
    schema: dict[str, Any]


@dataclass(slots=True)
class CompletionResult:
    """Result from a provider completion call."""

    text: str
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    # AC-904: why generation stopped ("max_tokens"/"length" indicates
    # truncation); None when the provider does not report one.
    stop_reason: str | None = None
    # AC-913: whether the backend actually constrained generation to the
    # requested schema. Defaults to False so a provider that ignores the
    # request, or one written before this existed, reports the truth rather
    # than claiming an enforcement it never performed.
    constrained: bool = False
    # Ordered scratchpad entries captured from an explicit ``deep_think``
    # tool. Keeping them separate from ``text`` prevents a captured thinking
    # stream from becoming the user-visible answer by accident.
    thinking_stream: list[str] = field(default_factory=list)
    thinking_tool: str | None = None
    # ``tool`` means the stream came from structured tool calls. ``unsupported``
    # means the provider could only perform an ordinary completion. The default
    # preserves compatibility with providers written before thinking capture.
    thinking_capture: str = "none"


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Implementations must provide `complete()` for synchronous calls.
    The interface is intentionally simple — autocontext only needs
    (system_prompt, user_prompt) -> text for judging.
    """

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
    ) -> CompletionResult:
        """Send a completion request and return the result.

        Args:
            system_prompt: System message for the LLM.
            user_prompt: User message / main prompt.
            model: Override the provider's default model.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.
            output_schema: Schema to constrain generation to (AC-913). Optional
                and best-effort: a backend that cannot enforce it must still
                answer, and must set ``constrained=False`` on the result so the
                caller can tell prose from validated output.

        Returns:
            CompletionResult with the response text and metadata.

        Raises:
            ProviderError: If the API call fails.
        """
        ...

    @abstractmethod
    def default_model(self) -> str:
        """Return the default model identifier for this provider."""
        ...

    def complete_with_thinking(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
        reasoning_effort: str = "medium",
        max_tool_turns: int = 8,
    ) -> CompletionResult:
        """Complete while requesting an application-captured thinking stream.

        Providers without a tool-loop implementation fall back to ``complete``,
        return an empty ``thinking_stream``, and mark capture as unsupported.
        ``reasoning_effort`` selects the external scratchpad budget; native
        provider reasoning is disabled where the transport supports doing so.
        This method is intentionally non-abstract so existing third-party
        ``LLMProvider`` implementations remain compatible.
        """
        del reasoning_effort, max_tool_turns
        if output_schema is None:
            # Older third-party providers may predate the optional schema
            # keyword even though they still satisfy the core completion
            # contract. Do not make thinking fallback less compatible.
            result = self.complete(
                system_prompt,
                user_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        else:
            result = self.complete(
                system_prompt,
                user_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                output_schema=output_schema,
            )
        if not result.thinking_stream:
            result.thinking_capture = "unsupported"
        return result

    @property
    def supports_thinking_stream(self) -> bool:
        """Whether this provider has a native structured thinking-tool loop."""
        return False

    @property
    def name(self) -> str:
        """Human-readable provider name."""
        return self.__class__.__name__
