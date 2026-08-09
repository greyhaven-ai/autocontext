"""Generic structured output extraction from LLM text."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)

# A plain json.JSONDecoder (no object_hook etc) exposes raw_decode: given a
# starting index, it parses exactly one JSON value from there using the real
# parser and reports where that value ended. _top_level_object_spans uses it
# to find top-level value boundaries instead of hand-rolling a brace counter
# that reimplements JSON string-quoting/escaping rules on its own -- a
# hand-rolled version was tried and worked, but it cannot help disagreeing
# with json.loads at the margins (that disagreement is exactly how the two
# holes this function now closes got in) where the real parser can't.


def strip_json_fences(text: str) -> str:
    """Strip markdown code fences, returning inner content."""
    match = _JSON_FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _top_level_object_spans(text: str) -> list[str]:
    """Find each top-level JSON value span (``{...}`` or ``[...]``) in ``text``, in order.

    Scans for the next ``{`` or ``[`` -- whichever comes first -- and asks
    ``json.JSONDecoder.raw_decode`` to parse one full value starting there.
    Deferring to the real parser for where a value ends means a literal
    brace inside a string value can never be mistaken for a structural one:
    raw_decode understands JSON string quoting and backslash escapes
    natively, so it can't fragment a well-formed object the way a naive
    ``{``/``}`` counter can.

    Searching for ``[`` as well as ``{`` (not just ``{``) matters: when
    raw_decode is asked to parse starting at a ``[``, it consumes the
    ENTIRE array, including anything nested inside it, and scanning resumes
    only after the array's end. That means a ``{`` nested inside a
    top-level array is never independently found and unwrapped into its
    own object candidate -- the array always comes back as one whole span.
    This holds no matter where the array sits in the text (leading, or
    buried in prose), not only when it opens the whole scope; a check that
    only looked at whether the scope *starts* with ``[`` would miss the
    latter.

    A location where raw_decode can't produce a full value (e.g. a stray
    ``{`` that never closes) is skipped and scanning resumes one character
    later, so side-by-side top-level values are still returned as separate
    spans rather than one merged, invalid span.
    """
    spans: list[str] = []
    decoder = json.JSONDecoder()
    i = 0
    while True:
        starts = [p for p in (text.find("{", i), text.find("[", i)) if p != -1]
        if not starts:
            return spans
        start = min(starts)
        try:
            _value, end = decoder.raw_decode(text, start)
        except ValueError:
            # AC-922: retrying one character later is O(n^2) on degenerate
            # repetitive input (e.g. '{"a": ' * n); load-bearing for
            # correctness (it's what lets side-by-side spans still separate
            # after a stray unclosed brace), so don't "optimize" it away here.
            i = start + 1
            continue
        spans.append(text[start:end])
        i = end


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
    whole text. There, unlike the fenced case, multiple candidate top-level
    values are tried in the order they appear, returning the first Mapping
    that parses -- this is what lets bare, unfenced JSON embedded in prose
    still be recovered, including when a competitor lists more than one
    option (e.g. "Option A: {...} Option B: {...}"): loose prose makes no
    claim to a single payload the way a fence does, so picking the first
    valid one is the closest match to what a human reader would take as the
    answer.

    A scope that opens with ``[`` -- fenced or not -- is exempt from any
    rescue attempt: if it parses, it's a decisive, terminal answer about the
    model's output shape (see the wrong-type note below), not a detour on
    the way to finding an object; if it fails to parse (e.g. a truncated
    array cut off mid-token), that failure is terminal too, rather than a
    cue to brace-scan or span-scan into the array's interior and unwrap
    whatever complete object happens to be sitting inside it.

    ``on_failure`` controls what happens when no JSON object is found:
    ``"raise"`` (default) re-raises the underlying parse error; ``"none"``
    returns ``None`` instead.
    """
    fence_match = _JSON_FENCE_RE.search(text)
    has_fence = fence_match is not None
    scope = fence_match.group(1).strip() if fence_match else text.strip()

    candidates = [scope]
    # scope opening with `[` means the model was producing an array, and this
    # check must run BEFORE looking at has_fence (not nested inside a fenced
    # `elif`, the way an earlier version of this had it), because a fenced
    # scope can open with `[` too, and the fenced branch's brace-scan below
    # is just as able to reach into a truncated array's interior as the
    # no-fence span scan is. A complete array is caught by the wrong-type-
    # terminal rule further down once it parses, but a TRUNCATED array (no
    # closing bracket) never reaches that rule: json.loads(scope) raises
    # JSONDecodeError instead of returning a list, so without this check the
    # loop would fall through to a rescue candidate below and unwrap
    # whatever complete object happens to sit inside the unterminated array
    # -- the same silent-unwrap shape the wrong-type rule exists to prevent,
    # reached through malformed syntax instead of a successful parse.
    # Skipping the rescue candidates entirely means an array-shaped scope is
    # terminal whether it parses or not, regardless of which branch below
    # would otherwise have produced them.
    if not scope.startswith("["):
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
