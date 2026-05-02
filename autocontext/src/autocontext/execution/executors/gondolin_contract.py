"""Contract for optional Gondolin-backed microVM execution.

This module intentionally contains only request/response shapes and a backend
protocol. The open-source package does not ship a Gondolin runtime adapter; a
deployment that needs VM isolation can implement this protocol behind the
existing ``ExecutionEngine`` boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class GondolinSecretRef:
    """Reference to a secret managed outside the task payload."""

    name: str
    env_var: str


@dataclass(frozen=True, slots=True)
class GondolinSandboxPolicy:
    """Isolation policy requested for one microVM execution."""

    allow_network: bool = False
    allowed_egress_hosts: tuple[str, ...] = ()
    read_only_mounts: tuple[Path, ...] = ()
    writable_mounts: tuple[Path, ...] = ()
    secrets: tuple[GondolinSecretRef, ...] = ()
    timeout_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class GondolinExecutionRequest:
    """Portable execution request for a Gondolin backend adapter."""

    scenario_name: str
    strategy: Mapping[str, Any]
    seed: int
    policy: GondolinSandboxPolicy = field(default_factory=GondolinSandboxPolicy)


@dataclass(frozen=True, slots=True)
class GondolinExecutionResult:
    """Backend result after microVM execution completes."""

    result: Mapping[str, Any]
    replay: Mapping[str, Any]
    stdout: str = ""
    stderr: str = ""


class GondolinBackend(Protocol):
    """Backend adapter contract for optional Gondolin integration."""

    def execute(self, request: GondolinExecutionRequest) -> GondolinExecutionResult:
        """Run one isolated execution request and return structured results."""
