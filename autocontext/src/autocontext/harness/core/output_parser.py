"""Generic structured output extraction from LLM text."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

# The `tag` group is what makes a ```json fence distinguishable from a bare
# ``` or a ```python one. JSON is case-insensitive, but it must be the complete
# tag: jsonl/json5/jsonnet are different info strings and must not gain JSON's
# priority merely because they share its prefix. ``{``/``[`` in the lookahead
# preserves the supported single-line form (```json{"a": 1}```), while U+FEFF
# lets the normal scope cleanup handle a BOM between the tag and payload.
_JSON_FENCE_RE = re.compile(
    r"```(?P<tag>json(?=\s|[\[{\ufeff]))?\s*\n?(?P<body>.*?)\n?\s*```",
    re.DOTALL | re.IGNORECASE,
)

# U+FEFF (BOM / zero-width no-break space). `str.strip()` does NOT remove it
# -- it is a format character, not whitespace, so `.isspace()` on it is False
# -- and neither does the fence regex's `\s*`, so a BOM survives both ways of
# producing a scope in extract_json. Normalizing it here keeps structural
# checks and direct parsing aligned, rather than making the same payload take
# a recovery path solely because an invisible format character precedes it.
# Spelled with chr() rather than pasted in, so it is visible in a diff and in
# an editor.
_BOM = chr(0xFEFF)

# Each failed raw_decode may scan the remaining suffix. Bounding failures keeps
# adversarial repetition linear in input size while retaining generous recovery
# for ordinary prose with a handful of stray braces (AC-922).
_MAX_FAILED_DECODE_ATTEMPTS = 64
_JSON_NUMBER_AT_ARRAY_START_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?(?=\s*(?:[,\]]|$))")
_NUMERIC_CITATION_RE = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]")


def _scope_text(raw: str) -> str:
    """Normalize a candidate scope: strip surrounding whitespace and any leading BOM.

    The trailing `.strip()` handles a BOM followed by whitespace, which the
    leading one stops at.
    """
    return raw.strip().lstrip(_BOM).strip()


# A plain json.JSONDecoder (no object_hook etc) exposes raw_decode: given a
# starting index, it parses exactly one JSON value from there using the real
# parser and reports where that value ended. _top_level_object_spans uses it
# to find top-level value boundaries instead of hand-rolling a brace counter
# that reimplements JSON string-quoting/escaping rules on its own -- a
# hand-rolled version was tried and worked, but it cannot help disagreeing
# with json.loads at the margins (that disagreement is exactly how the two
# holes this function now closes got in) where the real parser can't.


def strip_json_fences(text: str) -> str:
    """Strip the first markdown code fence, returning its inner content.

    This compatibility wrapper intentionally keeps the historical first-fence
    behavior. JSON-parsing callers should use :func:`extract_json`, which can
    distinguish a designated JSON answer from an earlier reasoning fence.
    """
    match = _JSON_FENCE_RE.search(text)
    return match.group("body").strip() if match else text.strip()


def _plausible_json_array_start(text: str, start: int) -> bool:
    """Distinguish a truncated JSON array from Markdown/prose brackets."""
    cursor = start + 1
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor == len(text):
        return True
    if text[cursor] in {'"', "{", "[", "]"}:
        return True
    suffix = text[cursor:]
    if _JSON_NUMBER_AT_ARRAY_START_RE.match(suffix):
        return True
    return any(
        suffix.startswith(literal) and (len(suffix) == len(literal) or suffix[len(literal)] in " \t\r\n,]")
        for literal in ("true", "false", "null")
    )


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

    A malformed object start is skipped and scanning resumes one character
    later, so side-by-side top-level objects are still returned separately.
    Recovery attempts are capped because each failed ``raw_decode`` may scan
    the remaining suffix; the cap makes degenerate repetition bounded rather
    than quadratic. A malformed *plausible JSON array* is terminal so its
    nested objects cannot be promoted, while Markdown/prose brackets such as
    ``[draft]`` are skipped like ordinary text.
    """
    spans: list[str] = []
    decoder = json.JSONDecoder()
    i = 0
    failed_attempts = 0
    while True:
        starts = [p for p in (text.find("{", i), text.find("[", i)) if p != -1]
        if not starts:
            return spans
        start = min(starts)
        try:
            _value, end = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            failed_attempts += 1
            if text[start] == "[" and _plausible_json_array_start(text, start):
                # A truncated array can still contain complete object values.
                # Treat the whole remainder as one failing candidate instead
                # of walking into it and promoting one of those nested values.
                spans.append(text[start:])
                return spans
            if failed_attempts >= _MAX_FAILED_DECODE_ATTEMPTS:
                return spans
            i = start + 1
            continue
        spans.append(text[start:end])
        i = end


def _fenced_payload_scopes(text: str) -> list[str] | None:
    """Return the fenced scopes to search, or None to search the whole text.

    Picking the right fence is the whole job here, and committing to
    ``_JSON_FENCE_RE.search(text)``'s first hit -- the first fence of ANY
    language -- was a regression (C1). A model that emits a reasoning block
    before its answer:

        Let me think.
        ```
        reasoning scratch
        ```
        Here is the tool:
        ```json
        {"tools": [...]}
        ```

    put the scratch block in front of the payload, and `search` handed that
    scratch block over as THE scope. Every recovery path is confined to the
    scope, so the real payload became unreachable: architect returned [] and
    logged "possibly truncated", which is AC-920's exact user-visible symptom
    reintroduced at AC-920's own call site. The pre-consolidation architect
    searched for the literal "```json" and skipped a preceding fence
    harmlessly; the migration lost that. Worse, when the preamble was a
    ```python block whose code happened to contain a dict literal, the scope
    PARSED and extract_json returned the model's scratch work as the answer --
    a silent wrong answer, not a missing one.

    The tag is the model's own designation, so it decides:

    1. Any ```json-tagged fence -> the FIRST one is the sole scope, and it is
       terminal. This is what makes the preamble cases work regardless of what
       the preamble contains (prose, code, braces, valid JSON), and it keeps
       both existing fence rules intact: first block wins when there are two
       (AC-920), and a corrupt tagged block fails closed rather than
       substituting JSON found elsewhere in the text (AC-921).
    2. Otherwise, untagged fences that could plausibly hold an object -- ones
       containing a ``{`` or a ``[`` -- are tried in order. An untagged fence
       is a weaker claim than a tagged one, but it is still a claim, so this
       stays confined to the fences and does NOT fall back to the surrounding
       prose; that fallback is precisely AC-921's failure shape.
    3. If every fence is brace-free (pure prose or code -- the reasoning-
       scratch shape with no json block after it), there is no fenced payload
       at all. Return None so the caller scans the whole text, which is what
       recovers an object sitting in the prose outside those fences.

    The brace test in 2/3 is deliberately crude, and it is a fallback for
    untagged fences only -- rule 1 short-circuits before it whenever a tag is
    present, so a ```python block containing a dict literal cannot capture the
    scan away from a real ```json block. It only decides between untagged
    fences, where there is no better signal available.
    """
    matches = list(_JSON_FENCE_RE.finditer(text))
    if not matches:
        return None
    tagged = [m for m in matches if m.group("tag")]
    if tagged:
        return [_scope_text(tagged[0].group("body"))]
    scopes = [_scope_text(m.group("body")) for m in matches]
    plausible = [scope for scope in scopes if "{" in scope or "[" in scope]
    return plausible or None


def _has_multiple_mapping_candidates(text: str) -> bool:
    """Return whether an LLM response contains competing JSON objects.

    A tagged JSON fence is authoritative, matching ``extract_json``'s normal
    fence-selection rule. Without one, scan the whole response so an object in
    an untagged reasoning fence cannot hide a different final object outside
    that fence. Callers that must fail closed on ambiguity can opt into this
    check without changing the established first-object behavior elsewhere.
    """
    tagged = next((match for match in _JSON_FENCE_RE.finditer(text) if match.group("tag")), None)
    scope = _scope_text(tagged.group("body")) if tagged is not None else _scope_text(text)
    mapping_count = 0
    for span in _top_level_object_spans(scope):
        try:
            decoded = json.loads(span)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(decoded, Mapping):
            mapping_count += 1
            if mapping_count > 1:
                return True
    return False


def extract_json(
    text: str,
    *,
    on_failure: str = "raise",
    require_unique: bool = False,
) -> dict[str, Any] | None:
    """Extract a JSON object from LLM text.

    Tries fenced code blocks first. WHICH fence is not "the first one":
    ``_fenced_payload_scopes`` picks it, preferring a ```json-tagged block
    over any untagged block that precedes it, so a reasoning or code block
    emitted before the answer cannot capture the scan. See that function --
    reading the wrong fence was a real regression (C1) with a silent
    wrong-answer mode, and the rule is stated there once rather than twice.

    If the chosen fence's content does not parse directly, this scans for a
    ``{...}`` span WITHIN that fenced content only -- never the surrounding
    prose. A broken fence is evidence the model intended that block to be the
    payload; silently substituting unrelated JSON found elsewhere in the text
    would return a wrong answer instead of no answer (see AC-921), so it is
    never attempted. A fence containing more than one object side by side
    still fails outright for the same reason: the fence is the model's claim
    about its payload, and more than one object inside it is a real conflict,
    not something to guess through.

    Only when there is no fenced payload at all -- no fences, or only
    brace-free ones that cannot be holding an object -- does the scan fall
    back to the whole text. There, unlike the fenced case, multiple candidate top-level
    values are tried in the order they appear, returning the first Mapping
    that parses -- this is what lets bare, unfenced JSON embedded in prose
    still be recovered, including when a competitor lists more than one
    option (e.g. "Option A: {...} Option B: {...}"): loose prose makes no
    claim to a single payload the way a fence does, so picking the first
    valid one is the closest match to what a human reader would take as the
    answer.

    A scope whose first plausible JSON container is ``[`` -- fenced or not,
    and even when prose precedes it -- is exempt from object rescue. If it
    parses, it is a decisive answer about the model's output shape (see the
    wrong-type note below); if it is truncated, that parse failure is terminal
    too, rather than a cue to scan into the array and unwrap a nested object.
    Markdown/prose brackets are skipped unless they look like JSON values.

    ``on_failure`` controls what happens when no JSON object is found:
    ``"raise"`` (default) re-raises the underlying parse error; ``"none"``
    returns ``None`` instead.

    ``require_unique`` rejects responses with more than one top-level JSON
    object candidate. A tagged JSON fence remains authoritative, so JSON-shaped
    scratch work in an earlier untagged fence cannot invalidate an explicitly
    designated answer.
    """
    fenced_scopes = _fenced_payload_scopes(text)
    has_fence = fenced_scopes is not None
    scopes = fenced_scopes if fenced_scopes is not None else [_scope_text(text)]

    # One flat candidate list across every scope, in scope order and, within a
    # scope, whole-scope before recovered sub-span. Flat rather than a loop per
    # scope because the wrong-type rule below stops the ENTIRE scan, not just
    # the current scope: a candidate that parses to a non-Mapping settles what
    # the model produced no matter which fence it came from.
    candidates: list[str] = []
    for scope in scopes:
        candidates.append(scope)
        # The first structural JSON token, not scope[0], decides whether the
        # model is producing an array: prose and non-JSON fence info can sit in
        # front of it. Parse a complete array as its own candidate so the
        # wrong-type rule below remains decisive; for a truncated array,
        # _top_level_object_spans returns its whole remainder as one failing
        # candidate. Either way, do not add any object-rescue candidates from
        # inside or after that array.
        array_start = scope.find("[")
        object_start = scope.find("{")
        if array_start != -1 and (object_start == -1 or array_start < object_start):
            # Keep the established direct-array behavior: the whole scope is
            # already the exact candidate, including any trailing material
            # whose Extra-data JSONDecodeError is part of the public outcome.
            if array_start == 0:
                continue
            structural_candidates = _top_level_object_spans(scope)
            if structural_candidates:
                first_object_index = next(
                    (index for index, candidate in enumerate(structural_candidates) if candidate.startswith("{")),
                    None,
                )
                citation_prefix = (
                    array_start > 0
                    and first_object_index is not None
                    and first_object_index > 0
                    and all(_NUMERIC_CITATION_RE.fullmatch(candidate) for candidate in structural_candidates[:first_object_index])
                )
                if citation_prefix:
                    assert first_object_index is not None
                    candidates.append(structural_candidates[first_object_index])
                    continue
                first_candidate = structural_candidates[0]
                if first_candidate != scope:
                    candidates.append(first_candidate)
            continue
        if has_fence:
            # NOTE: this crude first-`{`-to-last-`}` rescue is weaker than the
            # string-aware span scan the no-fence branch uses below, so
            # 'blah {oops} and {"a": 1}' recovers unfenced but not fenced.
            # That divergence predates C1 and is left alone here rather than
            # widened: unifying it would also change the deliberate rule that
            # a fence holding two side-by-side objects is a conflict, which is
            # a separate decision from which fence to read.
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
        except (json.JSONDecodeError, RecursionError) as exc:
            last_exc = exc
            continue
        if isinstance(decoded, Mapping):
            if require_unique:
                try:
                    has_multiple = _has_multiple_mapping_candidates(text)
                except (ValueError, RecursionError) as exc:
                    last_exc = exc
                    break
                if has_multiple:
                    last_exc = ValueError("Expected one unambiguous JSON object, got multiple candidates")
                    break
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
