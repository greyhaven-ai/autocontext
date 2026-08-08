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


def extract_json(text: str, *, on_failure: str = "raise") -> dict[str, Any] | None:
    """Extract a JSON object from LLM text.

    Tries a fenced code block first. If a fence is present but its captured
    content does not parse directly, this scans for a ``{...}`` span WITHIN
    that fenced content only -- never the surrounding prose. A broken fence
    is evidence the model intended that block to be the payload; silently
    substituting unrelated JSON found elsewhere in the text would return a
    wrong answer instead of no answer (see AC-921), so it is never attempted.

    Only when there is no fenced block at all does the ``{...}`` scan fall
    back to the whole text -- this is what lets bare, unfenced JSON embedded
    in prose still be recovered.

    ``on_failure`` controls what happens when no JSON object is found:
    ``"raise"`` (default) re-raises the underlying parse error; ``"none"``
    returns ``None`` instead.
    """
    fence_match = _JSON_FENCE_RE.search(text)
    scope = fence_match.group(1).strip() if fence_match else text.strip()

    candidates = [scope]
    start, end = scope.find("{"), scope.rfind("}")
    if start != -1 and end > start:
        brace_candidate = scope[start : end + 1]
        if brace_candidate != scope:
            candidates.append(brace_candidate)

    last_exc: Exception | None = None
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_exc = exc
            continue
        if isinstance(decoded, Mapping):
            return dict(decoded)
        last_exc = ValueError("Expected JSON object, got " + type(decoded).__name__)

    assert last_exc is not None
    if on_failure == "none":
        return None
    raise last_exc


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
