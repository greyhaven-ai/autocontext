"""Base provider interface for LLM calls."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ProviderError(Exception):
    """Raised when an LLM provider call fails."""


@dataclass(slots=True)
class CompletionResult:
    """Result from a provider completion call."""

    text: str
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Implementations must provide `complete()` for synchronous calls.
    The interface is intentionally simple — AutoContext only needs
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
    ) -> CompletionResult:
        """Send a completion request and return the result.

        Args:
            system_prompt: System message for the LLM.
            user_prompt: User message / main prompt.
            model: Override the provider's default model.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.

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

    @property
    def name(self) -> str:
        """Human-readable provider name."""
        return self.__class__.__name__
