# pyright: reportUnsupportedDunderAll=false

from __future__ import annotations

from typing import Any

__all__ = ["PrimeIntellectClient", "UnsupportedRemoteCapabilityError"]


def __getattr__(name: str) -> Any:
    if name in {"PrimeIntellectClient", "UnsupportedRemoteCapabilityError"}:
        from .client import PrimeIntellectClient, UnsupportedRemoteCapabilityError

        return {
            "PrimeIntellectClient": PrimeIntellectClient,
            "UnsupportedRemoteCapabilityError": UnsupportedRemoteCapabilityError,
        }[name]
    raise AttributeError(name)
