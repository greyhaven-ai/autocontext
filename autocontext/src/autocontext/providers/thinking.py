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
DEEP_THINK_SYSTEM_SUFFIX = (
    "Use the deep_think tool as a private scratchpad before answering. Its arguments are captured "
    "separately and are not part of the final answer. Call it again only for materially new reasoning, "
    "then put only the requested deliverable in the final response."
)


def with_deep_think_instruction(system_prompt: str) -> str:
    """Append the cross-provider thinking-tool instruction once."""
    return f"{system_prompt.rstrip()}\n\n{DEEP_THINK_SYSTEM_SUFFIX}".strip()


def extract_deep_thought(payload: Any) -> str:
    """Normalize tool input while preserving malformed/nonstandard payloads."""
    if isinstance(payload, str):
        try:
            return extract_deep_thought(json.loads(payload))
        except (TypeError, json.JSONDecodeError):
            return payload.strip() or "<empty deep_think arguments>"
    if isinstance(payload, dict):
        thoughts = payload.get("thoughts")
        if isinstance(thoughts, str):
            return thoughts
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deep_think_acknowledgement(index: int) -> str:
    """Return a content-free result so captured scratchpads are not re-injected."""
    return json.dumps(
        {
            "recorded": index,
            "next": "Resolve another material reasoning step with deep_think, or provide the final answer now.",
        },
        separators=(",", ":"),
    )
