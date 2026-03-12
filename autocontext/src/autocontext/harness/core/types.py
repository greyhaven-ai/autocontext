"""Domain-agnostic types for agent harness infrastructure."""

from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(slots=True)
class ModelResponse:
    text: str
    usage: RoleUsage
