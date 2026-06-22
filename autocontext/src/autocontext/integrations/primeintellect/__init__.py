# pyright: reportUnsupportedDunderAll=false

from __future__ import annotations

from typing import Any

__all__ = ["PrimeIntellectClient"]


def __getattr__(name: str) -> Any:
    if name == "PrimeIntellectClient":
        from .client import PrimeIntellectClient

        return PrimeIntellectClient
    raise AttributeError(name)
