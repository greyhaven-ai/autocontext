from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mts.harness.core.types import RoleExecution, RoleUsage


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


__all__ = ["RoleUsage", "RoleExecution", "AgentOutputs"]
