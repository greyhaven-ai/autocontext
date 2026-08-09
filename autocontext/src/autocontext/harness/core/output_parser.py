"""Generic structured output extraction from LLM text."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

# The `tag` group is what makes a ```json fence distinguishable from a bare
# ``` or a ```python one. It is a named group so that adding it cannot silently
# renumber `body` out from under strip_json_fences, which captured group(1)
# before this group existed.
_JSON_FENCE_RE = re.compile(r"```(?P<tag>json)?\s*\n?(?P<body>.*?)\n?\s*```", re.DOTALL)

# U+FEFF (BOM / zero-width no-break space). `str.strip()` does NOT remove it
# -- it is a format character, not whitespace, so `.isspace()` on it is False
# -- and neither does the fence regex's `\s*`, so a BOM survives both ways of
# producing a scope in extract_json. That matters because the array-shape
# check there is a `startswith("[")` on the scope: a leading BOM shifts the
# "[" off index 0, the check reads False, and a truncated array falls through
# to the rescue candidates it is supposed to be exempt from. Spelled with
# chr() rather than pasted in, so it is visible in a diff and in an editor.
_BOM = chr(0xFEFF)


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
    """Strip markdown code fences, returning inner content.

    Takes the FIRST fence of any language, unlike extract_json below, which
    prefers a ```json-tagged one. That difference is deliberate and pinned:
    this is a general-purpose fence stripper with callers that are not
    extracting JSON at all, so it has no basis for preferring a json tag.
    Do not "fix" it to match extract_json -- if a caller of this function
    wants JSON, it should call extract_json.
    """
    match = _JSON_FENCE_RE.search(text)
    return match.group("body").strip() if match else text.strip()


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


def extract_json(text: str, *, on_failure: str = "raise") -> dict[str, Any] | None:
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
        # scope opening with `[` means the model was producing an array, and
        # this check must run BEFORE looking at has_fence (not nested inside a
        # fenced `elif`, the way an earlier version of this had it), because a
        # fenced scope can open with `[` too, and the fenced branch's
        # brace-scan below is just as able to reach into a truncated array's
        # interior as the no-fence span scan is. A complete array is caught by
        # the wrong-type-terminal rule further down once it parses, but a
        # TRUNCATED array (no closing bracket) never reaches that rule:
        # json.loads(scope) raises JSONDecodeError instead of returning a
        # list, so without this check the loop would fall through to a rescue
        # candidate below and unwrap whatever complete object happens to sit
        # inside the unterminated array -- the same silent-unwrap shape the
        # wrong-type rule exists to prevent, reached through malformed syntax
        # instead of a successful parse.
        # Skipping the rescue candidates entirely means an array-shaped scope
        # is terminal whether it parses or not, regardless of which branch
        # below would otherwise have produced them. `scope` is BOM-normalized
        # by _scope_text precisely so this check cannot be walked past by a
        # leading U+FEFF -- see that constant's comment.
        if scope.startswith("["):
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
