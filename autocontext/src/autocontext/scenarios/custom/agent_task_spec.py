from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

_SAMPLE_INPUT_COMPACT_THRESHOLD_CHARS = 1000


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


def _compact_json_string(value: str) -> str:
    if len(value) < _SAMPLE_INPUT_COMPACT_THRESHOLD_CHARS:
        return value
    stripped = value.strip()
    if not stripped:
        return value
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return value
    if not isinstance(parsed, dict | list):
        return value
    return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)


def _serialize_agent_task_text_payload(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value, indent=2)
    return str(value)


QUALITY_THRESHOLD_NUMERIC_KEYS = (
    "minimum",
    "min",
    "threshold",
    "target",
    "required",
    "value",
)


def _normalized_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _matches_preferred_key(key: str, preferred: str) -> bool:
    if key == preferred:
        return True
    parts = tuple(part for part in key.split("_") if part)
    return preferred in parts


def _extract_numeric_scalar(value: Any, *, preferred_keys: tuple[str, ...] = ()) -> float | None:
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
        normalized_items = [(_normalized_key(key), nested) for key, nested in value.items()]
        for preferred in preferred_keys:
            for key, nested in normalized_items:
                if not _matches_preferred_key(key, preferred):
                    continue
                extracted = _extract_numeric_scalar(nested, preferred_keys=preferred_keys)
                if extracted is not None:
                    return extracted
        for nested in value.values():
            extracted = _extract_numeric_scalar(nested, preferred_keys=preferred_keys)
            if extracted is not None:
                return extracted
        return None
    if isinstance(value, list):
        for nested in value:
            extracted = _extract_numeric_scalar(nested, preferred_keys=preferred_keys)
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
    extracted = _extract_numeric_scalar(value, preferred_keys=QUALITY_THRESHOLD_NUMERIC_KEYS)
    if extracted is None:
        return 0.9
    if 10.0 <= extracted <= 100.0:
        return extracted / 100.0
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
        sample_input=(
            _compact_json_string(spec.sample_input)
            if isinstance(spec.sample_input, str)
            else _serialize_agent_task_text_payload(spec.sample_input)
        ),
    )
