from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any


@dataclass(slots=True)
class AgentTaskSpec:
    """Specification for an agent task scenario."""

    task_prompt: str
    judge_rubric: str
    output_format: str = "free_text"  # free_text | json_schema | code
    judge_model: str = ""
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
    sample_input: str | None = None  # Sample input data for data-dependent tasks


def _serialize_agent_task_text_payload(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value, indent=2)
    return str(value)


def _extract_numeric_scalar(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    if isinstance(value, dict):
        for nested in value.values():
            extracted = _extract_numeric_scalar(nested)
            if extracted is not None:
                return extracted
        return None
    if isinstance(value, list):
        for nested in value:
            extracted = _extract_numeric_scalar(nested)
            if extracted is not None:
                return extracted
        return None
    return None


def _normalize_max_rounds(value: Any) -> int:
    extracted = _extract_numeric_scalar(value)
    if extracted is None:
        return 1
    return max(1, int(extracted))


def _normalize_quality_threshold(value: Any) -> float:
    extracted = _extract_numeric_scalar(value)
    if extracted is None:
        return 0.9
    return extracted


def normalize_agent_task_runtime_fields(spec: AgentTaskSpec) -> AgentTaskSpec:
    """Coerce structured prompt-adjacent fields into runtime-safe strings.

    LLM-designed agent-task specs occasionally return structured JSON for fields
    like sample_input. The generated runtime embeds those fields into prompts via
    string concatenation, so we normalize them once at the spec boundary.
    """
    return replace(
        spec,
        task_prompt=_serialize_agent_task_text_payload(spec.task_prompt) or "",
        judge_rubric=_serialize_agent_task_text_payload(spec.judge_rubric) or "",
        reference_context=_serialize_agent_task_text_payload(spec.reference_context),
        context_preparation=_serialize_agent_task_text_payload(spec.context_preparation),
        max_rounds=_normalize_max_rounds(spec.max_rounds),
        quality_threshold=_normalize_quality_threshold(spec.quality_threshold),
        revision_prompt=_serialize_agent_task_text_payload(spec.revision_prompt),
        sample_input=_serialize_agent_task_text_payload(spec.sample_input),
    )
