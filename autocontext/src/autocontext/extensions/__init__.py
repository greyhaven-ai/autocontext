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
from autocontext.extensions.llm import HookedLanguageModelClient, HookedLLMProvider, wrap_language_model_client, wrap_llm_provider
from autocontext.extensions.loader import load_extensions

__all__ = [
    "ExtensionAPI",
    "HookBus",
    "HookError",
    "HookEvent",
    "HookEvents",
    "HookResult",
    "HookedLanguageModelClient",
    "HookedLLMProvider",
    "active_hook_bus",
    "event_block_error",
    "get_current_hook_bus",
    "load_extensions",
    "wrap_language_model_client",
    "wrap_llm_provider",
]
