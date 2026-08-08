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


def _top_level_object_spans(text: str) -> list[str]:
    """Find each top-level, brace-balanced ``{...}`` span in ``text``, in order.

    A plain depth counter over ``{``/``}`` characters -- it does not
    understand JSON string quoting, so a literal brace inside a string value
    can still throw off the count (this matches the naive scanning the old
    per-site extractors did; it is not meant to be a real JSON tokenizer).
    Once a span closes (depth returns to 0), scanning resumes for the next
    ``{`` after it, so side-by-side objects are returned as separate spans
    rather than merged into one.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                spans.append(text[start : i + 1])
                start = -1
    return spans


def extract_json(text: str, *, on_failure: str = "raise") -> dict[str, Any] | None:
    """Extract a JSON object from LLM text.

    Tries a fenced code block first. If a fence is present but its captured
    content does not parse directly, this scans for a ``{...}`` span WITHIN
    that fenced content only -- never the surrounding prose. A broken fence
    is evidence the model intended that block to be the payload; silently
    substituting unrelated JSON found elsewhere in the text would return a
    wrong answer instead of no answer (see AC-921), so it is never attempted.
    A fence containing more than one object side by side still fails outright
    for the same reason: the fence is the model's claim about its payload,
    and more than one object inside it is a real conflict, not something to
    guess through.

    Only when there is no fenced block at all does the scan fall back to the
    whole text. There, unlike the fenced case, multiple candidate objects are
    tried in the order they appear, returning the first one that parses --
    this is what lets bare, unfenced JSON embedded in prose still be
    recovered, including when a competitor lists more than one option (e.g.
    "Option A: {...} Option B: {...}"): loose prose makes no claim to a
    single payload the way a fence does, so picking the first valid one is
    the closest match to what a human reader would take as the answer.

    ``on_failure`` controls what happens when no JSON object is found:
    ``"raise"`` (default) re-raises the underlying parse error; ``"none"``
    returns ``None`` instead.
    """
    fence_match = _JSON_FENCE_RE.search(text)
    has_fence = fence_match is not None
    scope = fence_match.group(1).strip() if fence_match else text.strip()

    candidates = [scope]
    if has_fence:
        start, end = scope.find("{"), scope.rfind("}")
        if start != -1 and end > start:
            brace_candidate = scope[start : end + 1]
            if brace_candidate != scope:
                candidates.append(brace_candidate)
    else:
        for span in _top_level_object_spans(scope):
            if span != scope:
                candidates.append(span)

    last_exc: Exception | None = None
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_exc = exc
            continue
        if isinstance(decoded, Mapping):
            return dict(decoded)
        # A candidate that PARSES but isn't a Mapping (e.g. a JSON array) is a
        # decisive answer about what the model produced, not a parse failure
        # to recover from. Stop here rather than falling through to later
        # candidates (the fenced brace scan, or the no-fence multi-object
        # scan): either would then grab whatever object-shaped fragment
        # happens to be nested inside the array and silently return it,
        # which is the same substitute-something-plausible-nearby failure as
        # AC-921, just one level of nesting inward instead of outward. Only
        # a JSONDecodeError above (candidate didn't parse at all) is a
        # reason to keep scanning -- this is also why `candidates` always
        # tries `scope` itself first, before any recovered sub-span.
        last_exc = ValueError("Expected JSON object, got " + type(decoded).__name__)
        break

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
