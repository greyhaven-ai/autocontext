"""Shared kernel-generation exception types without provider/runtime dependencies."""


class KernelGenerationError(RuntimeError):
    """Base class for generation failures that must not reach the evaluator."""


class KernelGenerationValidationError(KernelGenerationError):
    """Raised when a provider response is not an exact executable source artifact."""


_TRANSIENT_ERROR_MARKERS = frozenset(
    {
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
    }
)


def is_transient_provider_error(exc: Exception) -> bool:
    return any(marker in str(exc).lower() for marker in _TRANSIENT_ERROR_MARKERS)


__all__ = [
    "KernelGenerationError",
    "KernelGenerationValidationError",
    "is_transient_provider_error",
]
