"""Pi-shaped extension hooks for autocontext runtime surfaces."""

from autocontext.extensions.hooks import (
    ExtensionAPI,
    HookBus,
    HookError,
    HookEvent,
    HookEvents,
    HookResult,
    event_block_error,
)
from autocontext.extensions.llm import HookedLanguageModelClient, wrap_language_model_client
from autocontext.extensions.loader import load_extensions

__all__ = [
    "ExtensionAPI",
    "HookBus",
    "HookError",
    "HookEvent",
    "HookEvents",
    "HookResult",
    "HookedLanguageModelClient",
    "event_block_error",
    "load_extensions",
    "wrap_language_model_client",
]
