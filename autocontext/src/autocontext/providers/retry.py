"""Retry wrapper for LLM providers with exponential backoff.

Handles transient errors (rate limits, timeouts, server errors)
by retrying with configurable backoff. Wraps any LLMProvider.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from autocontext.providers.base import CompletionResult, LLMProvider, OutputSchema, ProviderError

logger = logging.getLogger(__name__)


# Exceptions considered transient and worth retrying.
_TRANSIENT_SUBSTRINGS = frozenset({
    "rate limit",
    "rate_limit",
    "429",
    "timeout",
    "timed out",
    "server error",
    "500",
    "502",
    "503",
    "504",
    "overloaded",
    "capacity",
    "connection",
    "temporarily unavailable",
})


def _is_transient(error: Exception) -> bool:
    """Check if an error looks transient based on message content."""
    msg = str(error).lower()
    return any(sub in msg for sub in _TRANSIENT_SUBSTRINGS)


class RetryProvider(LLMProvider):
    """Wraps an LLMProvider with retry logic and exponential backoff.

    Args:
        provider: The underlying LLM provider to wrap.
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay cap in seconds.
        backoff_factor: Multiplier applied to delay after each retry.
        retry_all: If True, retry all ProviderErrors (not just transient).
    """

    def __init__(
        self,
        provider: LLMProvider,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        retry_all: bool = False,
    ) -> None:
        self._provider = provider
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.retry_all = retry_all

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
    ) -> CompletionResult:
        """Call the underlying provider with retry on transient errors."""
        return self._call_with_retry(
            lambda: self._provider.complete(
                system_prompt,
                user_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                output_schema=output_schema,
            )
        )

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
        """Retry the entire bounded thinking-tool conversation as one call."""
        return self._call_with_retry(
            lambda: self._provider.complete_with_thinking(
                system_prompt,
                user_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                output_schema=output_schema,
                reasoning_effort=reasoning_effort,
                max_tool_turns=max_tool_turns,
            )
        )

    def _call_with_retry(self, operation: Callable[[], CompletionResult]) -> CompletionResult:
        last_error: Exception | None = None
        delay = self.base_delay
        retry_usage: dict[str, int] = {}

        for attempt in range(1 + self.max_retries):
            try:
                result = operation()
                _add_usage(result.usage, retry_usage)
                return result
            except ProviderError as e:
                last_error = e
                _add_usage(retry_usage, e.usage)
                if attempt == self.max_retries:
                    break
                if not self.retry_all and not _is_transient(e):
                    logger.warning(
                        "non-transient provider error (attempt %d), not retrying: %s",
                        attempt + 1, e,
                    )
                    break

                logger.warning(
                    "transient provider error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, 1 + self.max_retries, delay, e,
                )
                time.sleep(delay)
                delay = min(delay * self.backoff_factor, self.max_delay)

        if isinstance(last_error, ProviderError):
            last_error.usage = retry_usage
        raise last_error  # type: ignore[misc]

    def default_model(self) -> str:
        return self._provider.default_model()

    @property
    def supports_thinking_stream(self) -> bool:
        return self._provider.supports_thinking_stream

    @property
    def supports_single_dispatch(self) -> bool:
        return self.max_retries == 0 and self._provider.supports_single_dispatch is True

    @property
    def supports_thinking_output_schema(self) -> bool:
        # Must be forwarded, not inherited from the base default: this wrapper
        # has no thinking loop of its own, so the base would answer for the
        # wrapper rather than for the provider actually doing the work.
        return self._provider.supports_thinking_output_schema

    @property
    def name(self) -> str:
        return f"Retry({self._provider.name})"


def _add_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value
