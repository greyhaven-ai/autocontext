"""Pi-shaped extension hooks for autocontext runtime surfaces."""

from autocontext.extensions.hooks import (
    ExtensionAPI,
    HookBus,
    HookError,
    HookEvent,
    HookEvents,
    HookResult,
    active_hook_bus,
    event_block_error,
    get_current_hook_bus,
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
    "active_hook_bus",
    "event_block_error",
    "get_current_hook_bus",
    "load_extensions",
    "wrap_language_model_client",
]
