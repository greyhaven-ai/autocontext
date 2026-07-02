"""autocontext control plane package."""

# pyright: reportUnsupportedDunderAll=false

from typing import Any

from autocontext.extensions import ExtensionAPI, HookBus, HookEvents, HookResult

__all__ = ["AutoContext", "ExtensionAPI", "HookBus", "HookEvents", "HookResult", "__version__"]

__version__ = "0.11.0"


def __getattr__(name: str) -> Any:
    if name == "AutoContext":
        from autocontext.sdk import AutoContext

        return AutoContext
    raise AttributeError(name)
