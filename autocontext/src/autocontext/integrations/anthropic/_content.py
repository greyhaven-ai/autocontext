"""Content-block flattening for Anthropic messages."""
from __future__ import annotations

from typing import Any


def flatten_content(content: str | list[dict[str, Any]]) -> str:
    """Return a string suitable for ``ProductionTrace.messages[].content``."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def extract_tool_uses(
    content: str | list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Extract ``tool_use`` blocks into trace's ``toolCalls`` shape.
    Returns None when content is a string or has no tool_use blocks.
    """
    if isinstance(content, str):
        return None
    result: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            result.append({
                "toolName": str(block.get("name", "")),
                "args": dict(block.get("input", {})),
            })
    return result or None
