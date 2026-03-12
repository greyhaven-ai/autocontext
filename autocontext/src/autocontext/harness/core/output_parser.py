"""Generic structured output extraction from LLM text."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def strip_json_fences(text: str) -> str:
    """Strip markdown code fences, returning inner content."""
    match = _JSON_FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def extract_json(text: str) -> dict[str, Any]:
    """Strip fences and parse as JSON object. Raises ValueError on failure."""
    cleaned = strip_json_fences(text)
    decoded = json.loads(cleaned)
    if not isinstance(decoded, Mapping):
        raise ValueError("Expected JSON object, got " + type(decoded).__name__)
    return dict(decoded)


def extract_tagged_content(text: str, tag: str) -> str | None:
    """Extract content from <tag>...</tag>. Returns None if not found."""
    pattern = re.compile(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", re.DOTALL)
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def extract_delimited_section(text: str, start_marker: str, end_marker: str) -> str | None:
    """Extract content between start and end markers. Returns None if not found."""
    start_idx = text.find(start_marker)
    if start_idx == -1:
        return None
    content_start = start_idx + len(start_marker)
    end_idx = text.find(end_marker, content_start)
    if end_idx == -1:
        return None
    return text[content_start:end_idx].strip()
