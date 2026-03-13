"""Domain-agnostic types for agent harness infrastructure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RoleUsage:
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str


@dataclass(slots=True)
class RoleExecution:
    role: str
    content: str
    usage: RoleUsage
    subagent_id: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelResponse:
    text: str
    usage: RoleUsage
    metadata: dict[str, Any] = field(default_factory=dict)
