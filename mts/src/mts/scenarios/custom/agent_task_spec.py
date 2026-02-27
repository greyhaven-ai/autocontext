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
    reference_context: str | None = None
    reference_sources: list[str] | None = None
    required_concepts: list[str] | None = None
    calibration_examples: list[dict] | None = None
    context_preparation: str | None = None  # Instructions for context gathering
    required_context_keys: list[str] | None = None  # Keys that must be in state after prepare_context
    max_rounds: int = 1  # Max improvement rounds (1 = single-shot)
    quality_threshold: float = 0.9  # Stop improving when score >= this
    revision_prompt: str | None = None  # Instructions for how to revise output
