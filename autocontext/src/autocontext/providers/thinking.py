"""Shared contract helpers for explicit provider thinking-tool capture."""

from __future__ import annotations

import json
from typing import Any

DEEP_THINK_TOOL_NAME = "deep_think"
DEEP_THINK_DESCRIPTION = "Record private scratchpad reasoning before producing the final answer."
DEEP_THINK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {"thoughts": {"type": "string"}},
    "required": ["thoughts"],
    "additionalProperties": False,
}
DEEP_THINK_JUICE_BY_EFFORT: dict[str, int] = {
    "none": 0,
    "minimal": 2,
    "low": 4,
    "medium": 8,
    "high": 48,
    "xhigh": 112,
    "max": 960,
}
DEEP_THINK_SYSTEM_SUFFIX = (
    "Use the deep_think tool as a private scratchpad before drafting the final answer. Restate the task "
    "and constraints, resolve the work in ordered steps, and check likely errors or boundary cases there. "
    "Its arguments are captured separately and are not part of the final answer. Call it again only for "
    "materially new reasoning, never to narrate progress, then put only the requested deliverable in the "
    "final response."
)


def deep_think_juice(reasoning_effort: str) -> int:
    """Map an external-reasoning level to its prompt budget."""
    return DEEP_THINK_JUICE_BY_EFFORT.get(reasoning_effort.lower().strip(), 8)


def with_deep_think_instruction(system_prompt: str, *, juice: int | None = None) -> str:
    """Append the cross-provider thinking-tool instruction once."""
    instruction = f"{system_prompt.rstrip()}\n\n{DEEP_THINK_SYSTEM_SUFFIX}".strip()
    if juice is not None:
        instruction = f"{instruction}\n\n# Juice: {juice} !important"
    return instruction


def extract_deep_thought(payload: Any) -> str:
    """Validate and extract one strict ``deep_think`` payload."""
    if isinstance(payload, str):
        try:
            return extract_deep_thought(json.loads(payload))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("arguments must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("arguments must be an object")
    if set(payload) != {"thoughts"}:
        raise ValueError("arguments must contain only the required thoughts field")
    thoughts = payload.get("thoughts")
    if not isinstance(thoughts, str):
        raise ValueError("thoughts must be a string")
    return thoughts


def deep_think_acknowledgement(index: int) -> str:
    """Return a content-free result so captured scratchpads are not re-injected."""
    return json.dumps({"recorded": index}, separators=(",", ":"))
