from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AgentTaskSpec:
    """Specification for an agent task scenario."""

    task_prompt: str
    judge_rubric: str
    output_format: str = "free_text"  # free_text | json_schema | code
    judge_model: str = "claude-sonnet-4-20250514"
    difficulty_tiers: list[dict] | None = None
