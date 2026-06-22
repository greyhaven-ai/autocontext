# pyright: reportUnsupportedDunderAll=false

from __future__ import annotations

from typing import Any

from .base import ExecutionEngine
from .gondolin_contract import (
    GondolinBackend,
    GondolinExecutionRequest,
    GondolinExecutionResult,
    GondolinSandboxPolicy,
    GondolinSecretRef,
)
from .local import LocalExecutor
from .monty import MontyExecutor

__all__ = [
    "ExecutionEngine",
    "GondolinBackend",
    "GondolinExecutionRequest",
    "GondolinExecutionResult",
    "GondolinSandboxPolicy",
    "GondolinSecretRef",
    "LocalExecutor",
    "MontyExecutor",
    "PrimeIntellectExecutor",
]


def __getattr__(name: str) -> Any:
    if name == "PrimeIntellectExecutor":
        from .primeintellect import PrimeIntellectExecutor

        return PrimeIntellectExecutor
    raise AttributeError(name)
