from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from autocontext.harness.core.types import RoleExecution, RoleUsage

#: A simple LLM function: (system_prompt, user_prompt) -> response text.
LlmFn = Callable[[str, str], str]

if TYPE_CHECKING:
    from autocontext.agents.contracts import AnalystOutput, ArchitectOutput, CoachOutput, CompetitorOutput


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
    architect_harness_specs: list[dict[str, Any]] | None = None
    competitor_output: CompetitorOutput | None = None
    analyst_output: AnalystOutput | None = None
    coach_output: CoachOutput | None = None
    architect_output: ArchitectOutput | None = None


__all__ = ["LlmFn", "RoleUsage", "RoleExecution", "AgentOutputs"]
