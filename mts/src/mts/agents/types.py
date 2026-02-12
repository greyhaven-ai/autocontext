from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(slots=True)
class AgentOutputs:
    strategy: dict[str, Any]
    analysis_markdown: str
    coach_markdown: str
    coach_playbook: str
    coach_lessons: str
    coach_competitor_hints: str
    architect_markdown: str
    architect_tools: list[dict[str, Any]]
    role_executions: list[RoleExecution]
