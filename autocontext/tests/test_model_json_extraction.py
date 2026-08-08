"""Characterization tests for the seven ways autocontext pulls JSON out of LLM text.

AC-910 plan 3 consolidates these onto a single parser. Before that happens, this
file pins what EACH extractor does TODAY against a shared corpus of realistic LLM
outputs, so the consolidation can be proven not to change observable behavior.

This is a characterization test suite: every assertion records the *current*
result, including ``None``, ``[]``, ``{}``, defaults, and raised exceptions. It
does not judge whether a result is correct. Where two extractors disagree on the
same input, that disagreement is the point -- see the report at
``.superpowers/sdd/2026-08-08-ac-910-plan3-model-json-consolidation/task-1-report.md``
for the full disagreement table.

The seven call sites characterized here:

1. ``harness.core.output_parser.strip_json_fences`` -- generic fence stripper,
   the widest-used regex in the codebase.
2. ``harness.core.output_parser.extract_json`` -- fence-strip + ``json.loads``,
   currently DEAD CODE (zero callers), and the consolidation target.
3. ``agents.architect.parse_architect_tool_specs`` -- manual
   ``find("```json")`` / ``rfind("```")``, no regex, schema-specific
   (``{"tools": [...]}"``).
4. ``agents.translator.StrategyTranslator._strip_fences`` -- delegates
   verbatim to (1); pinned here to prove the delegation holds across the
   whole corpus, not just eyeballed from the source.
5. ``agents.curator.KnowledgeCurator.rate_analyst_output`` -- calls (1) then
   does its own ``json.loads``, swallowing ``JSONDecodeError`` and silently
   discarding non-dict results, both without raising.
6. ``agents.translator_simplification.extract_strategy_deterministic`` -- its
   own fence regex (this one requires a literal newline after the fence,
   unlike (1)'s optional-newline regex) with a bare-JSON-object regex
   fallback and a whole-text last resort.
7. ``agents.hint_feedback.parse_hint_feedback`` -- another private fence
   regex requiring a literal newline, feeding a schema-specific payload.
8. ``execution.action_filter.ActionFilterHarness._extract_json_object`` --
   tries a fenced-and-anchored regex, then falls back to naive
   first-``{``-to-last-``}`` scanning.

Curator and hint_feedback's own callables have effect-shaped return types
(``AnalystRating``, ``HintFeedback``) that route the extracted payload through
a domain-specific schema, which masks parse success on schema-mismatched
input (both quietly fall back to their all-defaults / all-empty shape). Two
corpus cases (``curator_rating_shape``, ``hint_feedback_shape``) use payloads
that satisfy that schema so the successful-parse path is actually observable,
rather than indistinguishable from the various failure paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from autocontext.agents.architect import parse_architect_tool_specs
from autocontext.agents.curator import KnowledgeCurator
from autocontext.agents.hint_feedback import parse_hint_feedback
from autocontext.agents.translator import StrategyTranslator
from autocontext.agents.translator_simplification import extract_strategy_deterministic
from autocontext.execution.action_filter import ActionFilterHarness
from autocontext.harness.core.output_parser import extract_json, strip_json_fences
from autocontext.harness.core.types import RoleExecution, RoleUsage

# ---------------------------------------------------------------------------
# Corpus: (case name, raw LLM text). Reused by every extractor's test below,
# and by later AC-910 tasks that build the consolidated parser against it.
# ---------------------------------------------------------------------------

CORPUS: list[tuple[str, str]] = [
    ("normal_fenced_block", '```json\n{"a": 1}\n```'),
    ("fence_no_language", '```\n{"a": 1}\n```'),
    ("fence_single_line", '```json{"a": 1}```'),
    (
        "two_fenced_blocks",
        'first:\n```json\n{"a": 1}\n```\nsecond:\n```json\n{"b": 2}\n```',
    ),
    ("truncated_block", '```json\n{"a": 1, "b": 2'),
    ("bare_json_with_prose", 'Here is the result: {"a": 1} -- hope that helps!'),
    ("json_array_not_object", "```json\n[1, 2, 3]\n```"),
    ("empty_string", ""),
    ("invalid_json_in_fence", '```json\n{a: 1, "b":}\n```'),
    (
        "brace_in_string_value",
        '```json\n{"code": "if (x) { return 1; }", "ok": true}\n```',
    ),
    (
        "trailing_stray_brace_no_fence",
        'Result: {"text": "value } here"} and also check ref {2} today.',
    ),
    (
        "architect_valid_tools_block",
        '```json\n{"tools": [{"name": "n", "description": "d", "code": "c"}]}\n```',
    ),
    ("whitespace_only", "   \n\t  "),
    (
        "fenced_with_leading_prose_and_trailing_prose",
        'Sure, here you go:\n```json\n{"a": 1}\n```\nLet me know if you need more.',
    ),
    (
        "curator_rating_shape",
        '```json\n{"actionability": 4, "specificity": 5, "correctness": 2, "rationale": "solid"}\n```',
    ),
    (
        "hint_feedback_shape",
        '```json\n{"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]}\n```',
    ),
    (
        # AC-921: a corrupt fenced block followed by unrelated JSON in the
        # trailing prose. extract_strategy_deterministic's bare-object
        # fallback finds the decoy {"x": 2} and returns it -- silently wrong,
        # not safe. This case guards the strengthened extract_json against
        # reintroducing that behavior.
        "ac_921_corrupt_fence_with_decoy_json",
        '```json\n{a: 1, "b":}\n```  also see config: {"x": 2}',
    ),
    (
        # strip_json_fences is case-sensitive: the "(?:json)?" tag group
        # only matches lowercase, so an uppercase ```JSON tag isn't consumed
        # as a tag and leaks into the "stripped" content instead.
        "uppercase_json_fence_tag",
        '```JSON\n{"a": 1}\n```',
    ),
    (
        # AC-910 Task 2 defect: a bare (unfenced) array of OBJECTS, not
        # scalars. json_array_not_object above ([1, 2, 3]) contains no
        # braces at all, so it never exercised the brace-scan fallback; this
        # one does, and used to have the fallback silently return the
        # array's first element instead of raising.
        "bare_array_of_objects",
        '[{"a": 1}]',
    ),
    (
        # Same defect, fenced: the scope itself parses successfully to a
        # list, which must be terminal -- not a cue to fall through to the
        # brace-scan candidate and grab the nested {"a": 1}.
        "fenced_array_of_objects",
        '```json\n[{"a": 1}]\n```',
    ),
    (
        # Same defect, mixed array: scalars AND an object. Confirms the fix
        # isn't specific to all-object arrays.
        "fenced_mixed_array_with_object",
        '```json\n[1, {"a": 1}, 2]\n```',
    ),
    (
        # AC-910 Task 3 regression case: two separate bare (unfenced) JSON
        # objects in prose, e.g. a competitor listing two strategy options.
        # extract_json's no-fence fallback must try each top-level candidate
        # in order and return the first that parses, not build one
        # first-"{"-to-last-"}" span (which would cross both objects and
        # fail). Also pinned directly against the real production path in
        # tests/test_translator_simplification.py::test_multiple_json_objects_extracts_first.
        "two_bare_json_objects_no_fence",
        'Option A: {"aggression": 0.9, "defense": 0.1}\nOption B: {"aggression": 0.5, "defense": 0.5}',
    ),
    (
        # AC-910 Task 3 review Critical 1: a literal "}" inside a JSON string
        # value used to fragment this well-formed object into an invalid
        # prefix, letting the naive scan fall through to the unrelated decoy
        # object below and silently return it. The string-aware span scan
        # must keep the whole first object intact and return it, never the
        # decoy.
        "critical1_brace_in_string_with_decoy",
        ('My rating: {"score": 5, "rationale": "matches rubric step 3}"} Ignore stale: {"score": 1, "rationale": "stale"}'),
    ),
    (
        # AC-910 Task 3 review Critical 2: a bare array followed by a
        # separate bare object, no fence. The array is a decisive answer
        # about what the model produced; the naive scan used to reach past
        # it and adopt the object dangling after it.
        "array_then_separate_object_no_fence",
        '[{"a": 1}], {"b": 2}',
    ),
    (
        # Same defect as above, realistic shape: array of objects embedded in
        # prose rather than at the very start of the text. This is the case
        # that a plain `scope.lstrip().startswith("[")` check would miss,
        # since the array isn't the first token in the scope.
        "array_of_objects_in_prose_no_fence",
        'Available tools: [{"name": "n1"}] let me know.',
    ),
    (
        # Companion to critical1: a brace inside a string value with NO decoy
        # after it. This must still parse successfully -- confirms the
        # string-aware fix isn't just failing safe on brace-in-string inputs,
        # it's recovering the correct object.
        "brace_in_string_value_no_fence_no_decoy",
        '{"note": "step 3} done", "a": 1}',
    ),
]

CORPUS_IDS = [name for name, _ in CORPUS]


class _Raises:
    """Sentinel marking that an extractor is pinned to raise on this case."""

    def __init__(self, exc_type: type[BaseException]) -> None:
        self.exc_type = exc_type

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"Raises({self.exc_type.__name__})"


# ---------------------------------------------------------------------------
# Helpers for the two extractors whose JSON-extraction step is embedded
# inside a larger method rather than exposed as a standalone function. Both
# are exercised through their REAL production code path: a stub runtime
# supplies canned model output, and the actual method under test parses it.
# ---------------------------------------------------------------------------


class _StubRuntime:
    """Minimal SubagentRuntime stand-in: returns fixed content, no LLM call."""

    def __init__(self, content: str) -> None:
        self._content = content

    def run_task(self, task: Any) -> RoleExecution:
        return RoleExecution(
            role="curator",
            content=self._content,
            usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="stub"),
            subagent_id="stub",
            status="completed",
        )


@dataclass(frozen=True, slots=True)
class _CuratorRatingShape:
    actionability: int
    specificity: int
    correctness: int
    rationale: str


def _curator_rate(text: str) -> _CuratorRatingShape:
    """Run KnowledgeCurator.rate_analyst_output's real parsing path on `text`."""
    curator = KnowledgeCurator(runtime=_StubRuntime(text), model="stub")
    rating, _execution = curator.rate_analyst_output(analyst_markdown="irrelevant", generation=7)
    return _CuratorRatingShape(
        actionability=rating.actionability,
        specificity=rating.specificity,
        correctness=rating.correctness,
        rationale=rating.rationale,
    )


@dataclass(frozen=True, slots=True)
class _HintFeedbackShape:
    helpful: tuple[str, ...]
    misleading: tuple[str, ...]
    missing: tuple[str, ...]


def _hint_feedback(text: str) -> _HintFeedbackShape:
    """Run parse_hint_feedback's real parsing path on `text` (pure function, no LLM)."""
    feedback = parse_hint_feedback(text, generation=7)
    return _HintFeedbackShape(
        helpful=tuple(feedback.helpful),
        misleading=tuple(feedback.misleading),
        missing=tuple(feedback.missing),
    )


# ---------------------------------------------------------------------------
# Extractor 1: output_parser.strip_json_fences
# ---------------------------------------------------------------------------

STRIP_JSON_FENCES_EXPECTED: dict[str, str] = {
    "normal_fenced_block": '{"a": 1}',
    "fence_no_language": '{"a": 1}',
    "fence_single_line": '{"a": 1}',
    "two_fenced_blocks": '{"a": 1}',
    "truncated_block": '```json\n{"a": 1, "b": 2',
    "bare_json_with_prose": 'Here is the result: {"a": 1} -- hope that helps!',
    "json_array_not_object": "[1, 2, 3]",
    "empty_string": "",
    "invalid_json_in_fence": '{a: 1, "b":}',
    "brace_in_string_value": '{"code": "if (x) { return 1; }", "ok": true}',
    "trailing_stray_brace_no_fence": 'Result: {"text": "value } here"} and also check ref {2} today.',
    "architect_valid_tools_block": '{"tools": [{"name": "n", "description": "d", "code": "c"}]}',
    "whitespace_only": "",
    "fenced_with_leading_prose_and_trailing_prose": '{"a": 1}',
    "curator_rating_shape": '{"actionability": 4, "specificity": 5, "correctness": 2, "rationale": "solid"}',
    "hint_feedback_shape": '{"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]}',
    "ac_921_corrupt_fence_with_decoy_json": '{a: 1, "b":}',
    "uppercase_json_fence_tag": 'JSON\n{"a": 1}',
    "bare_array_of_objects": '[{"a": 1}]',
    "fenced_array_of_objects": '[{"a": 1}]',
    "fenced_mixed_array_with_object": '[1, {"a": 1}, 2]',
    # No fence present -> passthrough unchanged, same as any other no-fence case.
    "two_bare_json_objects_no_fence": (
        'Option A: {"aggression": 0.9, "defense": 0.1}\nOption B: {"aggression": 0.5, "defense": 0.5}'
    ),
    "critical1_brace_in_string_with_decoy": (
        'My rating: {"score": 5, "rationale": "matches rubric step 3}"} Ignore stale: {"score": 1, "rationale": "stale"}'
    ),
    "array_then_separate_object_no_fence": '[{"a": 1}], {"b": 2}',
    "array_of_objects_in_prose_no_fence": 'Available tools: [{"name": "n1"}] let me know.',
    "brace_in_string_value_no_fence_no_decoy": '{"note": "step 3} done", "a": 1}',
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_strip_json_fences(name: str, text: str) -> None:
    assert strip_json_fences(text) == STRIP_JSON_FENCES_EXPECTED[name]


# ---------------------------------------------------------------------------
# Extractor 2: output_parser.extract_json (dead code today; consolidation target)
# ---------------------------------------------------------------------------

EXTRACT_JSON_EXPECTED: dict[str, Any] = {
    "normal_fenced_block": {"a": 1},
    "fence_no_language": {"a": 1},
    "fence_single_line": {"a": 1},
    "two_fenced_blocks": {"a": 1},
    "truncated_block": _Raises(json.JSONDecodeError),
    # CHANGED by the Step 2 strengthening: no fence is present (the fence is
    # unterminated), so extract_json now brace-scans the bare text -- same
    # fallback action_filter already uses -- and recovers the object.
    "bare_json_with_prose": {"a": 1},
    "json_array_not_object": _Raises(ValueError),
    "empty_string": _Raises(json.JSONDecodeError),
    "invalid_json_in_fence": _Raises(json.JSONDecodeError),
    "brace_in_string_value": {"code": "if (x) { return 1; }", "ok": True},
    # CHANGED by the AC-910 Task 3 review fix (Critical 1, string-aware
    # spans): the naive brace counter used to see the "}" inside the
    # "value } here" string as closing the object, leaving an invalid
    # `{"text": "value }` fragment and a dangling `{2}` decoy, neither of
    # which parses -> raise. The string-aware scan now sees the real,
    # well-formed `{"text": "value } here"}` object and recovers it.
    "trailing_stray_brace_no_fence": {"text": "value } here"},
    "architect_valid_tools_block": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "whitespace_only": _Raises(json.JSONDecodeError),
    "fenced_with_leading_prose_and_trailing_prose": {"a": 1},
    "curator_rating_shape": {"actionability": 4, "specificity": 5, "correctness": 2, "rationale": "solid"},
    "hint_feedback_shape": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
    # NOT changed: the decoy JSON lives outside the fenced span. The brace
    # scan is confined to the fence's own captured content, so it never
    # reaches the decoy -- this is the AC-921 guard.
    "ac_921_corrupt_fence_with_decoy_json": _Raises(json.JSONDecodeError),
    # CHANGED by the Step 2 strengthening: a fence IS present, so the fallback
    # stays confined to its captured content ('JSON\n{"a": 1}'), which itself
    # contains a recoverable {...} span once the leaked tag is scanned past.
    # This never reaches outside the fence, so it doesn't reintroduce AC-921.
    "uppercase_json_fence_tag": {"a": 1},
    # Defect fix (AC-910 Task 2 review): the scope parses successfully to a
    # list. That is now terminal -- raise -- rather than falling through to
    # the brace-scan candidate and silently returning the nested {"a": 1}.
    "bare_array_of_objects": _Raises(ValueError),
    "fenced_array_of_objects": _Raises(ValueError),
    "fenced_mixed_array_with_object": _Raises(ValueError),
    # AC-910 Task 3 fix: no fence, so multiple top-level candidates are tried
    # in order; Option A's object parses first and wins. Before the fix this
    # raised JSONDecodeError (the single first-"{"-to-last-"}" span crossed
    # both objects and the "Option B:" prose between them).
    "two_bare_json_objects_no_fence": {"aggression": 0.9, "defense": 0.1},
    # AC-910 Task 3 review Critical 1 fix: the brace inside the "rationale"
    # string no longer fragments the first object, so it recovers correctly
    # instead of falling through to the unrelated "stale" decoy object.
    "critical1_brace_in_string_with_decoy": {"score": 5, "rationale": "matches rubric step 3}"},
    # AC-910 Task 3 review Critical 2 fix: a top-level array candidate is
    # terminal even when it isn't the whole scope, so this no longer
    # unwraps {"a": 1} out of the array and ignores the trailing {"b": 2}.
    "array_then_separate_object_no_fence": _Raises(ValueError),
    # Same Critical 2 fix, array embedded in prose rather than leading.
    "array_of_objects_in_prose_no_fence": _Raises(ValueError),
    # String-aware span scan recovers this whole, valid object; there's no
    # decoy here to fall through to, so this also passed before the fix via
    # the whole-scope candidate -- pinned as the no-decoy companion to
    # critical1 above.
    "brace_in_string_value_no_fence_no_decoy": {"note": "step 3} done", "a": 1},
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_extract_json(name: str, text: str) -> None:
    expected = EXTRACT_JSON_EXPECTED[name]
    if isinstance(expected, _Raises):
        with pytest.raises(expected.exc_type):
            extract_json(text)
    else:
        assert extract_json(text) == expected


# ---------------------------------------------------------------------------
# extract_json's strengthened fallback and on_failure policy (AC-910 task 2).
# ---------------------------------------------------------------------------


def test_extract_json_same_line_fence_parses() -> None:
    assert extract_json('```json{"a": 1}```') == {"a": 1}


def test_extract_json_prose_wrapped_bare_json_parses_via_brace_scan() -> None:
    assert extract_json('Here is the result: {"a": 1} -- hope that helps!') == {"a": 1}


def test_extract_json_broken_fence_tag_recovers_via_brace_scan_within_fence() -> None:
    # The fenced content itself ('JSON\n{"a": 1}') doesn't parse directly
    # because of the leaked uppercase tag, but brace-scanning WITHIN that
    # captured span (not the wider text) recovers the object.
    assert extract_json('```JSON\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_json_array_raises_instead_of_being_coerced() -> None:
    with pytest.raises(ValueError):
        extract_json("```json\n[1, 2, 3]\n```")


def test_extract_json_on_failure_none_returns_none_instead_of_raising() -> None:
    assert extract_json("```json\n[1, 2, 3]\n```", on_failure="none") is None
    assert extract_json('```json\n{a: 1, "b":}\n```', on_failure="none") is None
    assert extract_json("", on_failure="none") is None


def test_extract_json_default_on_failure_still_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        extract_json('```json\n{a: 1, "b":}\n```')


def test_extract_json_ac_921_decoy_does_not_return_the_decoy_object() -> None:
    """A broken fence must FAIL, never silently substitute unrelated prose JSON.

    This is the specific input that made extract_strategy_deterministic return
    the wrong-but-plausible {"x": 2} (AC-921, characterized in the CORPUS row
    ac_921_corrupt_fence_with_decoy_json above). The strengthened extract_json
    must not reproduce that: it should fail, not return the decoy.
    """
    decoy_text = '```json\n{a: 1, "b":}\n```  also see config: {"x": 2}'
    assert extract_json(decoy_text, on_failure="none") is None
    with pytest.raises(json.JSONDecodeError):
        extract_json(decoy_text)


# ---------------------------------------------------------------------------
# Extractor 3: agents.architect.parse_architect_tool_specs
# ---------------------------------------------------------------------------

# AC-910 Task 3: parse_architect_tool_specs now delegates to the shared
# extract_json(..., on_failure="none") instead of its own find("```json") /
# rfind("```") span. Every row below still returns the same value as before
# the migration -- ARCH's [] here is (with one exception, noted per-row)
# still a schema mismatch (extract_json returns a dict, just not one with a
# "tools" list), not a parse failure. The one exception is `two_fenced_blocks`,
# which changes from a JSONDecodeError-driven [] to a schema-mismatch-driven
# [] -- same return value, different reason, not a value change (see the
# dedicated AC-920 test below for the case where this DOES change the
# return value).
PARSE_ARCHITECT_TOOL_SPECS_EXPECTED: dict[str, list[dict[str, Any]]] = {
    "normal_fenced_block": [],
    "fence_no_language": [],
    "fence_single_line": [],
    "two_fenced_blocks": [],  # extract_json grabs the FIRST block ({"a": 1}); still [] since it has no "tools" key
    # extract_json can't find a complete JSON object -> None -> [] (now logs
    # the warning; the old find/rfind short-circuited before ever trying to
    # parse, so it silently returned [] without logging)
    "truncated_block": [],
    "bare_json_with_prose": [],
    "json_array_not_object": [],
    "empty_string": [],
    "invalid_json_in_fence": [],  # still unparseable within the fenced scope -> None -> [] with a warning, as before
    "brace_in_string_value": [],  # valid JSON, valid dict, but no "tools" key
    "trailing_stray_brace_no_fence": [],
    "architect_valid_tools_block": [{"name": "n", "description": "d", "code": "c"}],
    "whitespace_only": [],
    "fenced_with_leading_prose_and_trailing_prose": [],
    "curator_rating_shape": [],
    "hint_feedback_shape": [],
    # still fails within the fenced scope (decoy sits outside it) -> None ->
    # [] with a warning, as before
    "ac_921_corrupt_fence_with_decoy_json": [],
    "uppercase_json_fence_tag": [],  # recovered via the within-scope brace scan despite the leaked tag; still no "tools" key
    # scope parses to a list -> terminal ValueError -> None -> [] with a
    # warning (previously [] via no "```json" tag found at all, no warning)
    "bare_array_of_objects": [],
    "fenced_array_of_objects": [],  # same terminal-list rule -> None -> [] with a warning
    "fenced_mixed_array_with_object": [],  # same terminal-list rule -> None -> [] with a warning
    "two_bare_json_objects_no_fence": [],  # extract_json recovers Option A's dict, but it has no "tools" key
    "critical1_brace_in_string_with_decoy": [],  # extract_json recovers the correct object, but no "tools" key
    "array_then_separate_object_no_fence": [],  # extract_json now raises (Critical 2 fix) -> None -> []
    "array_of_objects_in_prose_no_fence": [],  # same Critical 2 fix -> None -> []
    "brace_in_string_value_no_fence_no_decoy": [],  # valid dict recovered, but no "tools" key
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_parse_architect_tool_specs(name: str, text: str) -> None:
    assert parse_architect_tool_specs(text) == PARSE_ARCHITECT_TOOL_SPECS_EXPECTED[name]


def test_parse_architect_tool_specs_ac_920_two_tools_blocks_uses_first_block() -> None:
    """AC-920: an architect response offering an alternative (two fenced
    ``{"tools": [...]}`` blocks) used to be treated as no proposal at all.

    The old `find("```json")` / `rfind("```")` span took the LAST closing
    fence, splicing the first block's opening through the second block's
    opening into one corrupt, unparseable span -> JSONDecodeError -> [].
    extract_json's regex is non-greedy and takes the FIRST complete fenced
    block, so migrating onto it fixes this for free: the architect's first
    proposal is now recovered instead of silently discarded.
    """
    two_tools_blocks = (
        'Here is my primary proposal:\n```json\n{"tools": [{"name": "n1", "description": "d1", "code": "c1"}]}\n```\n'
        'And an alternative:\n```json\n{"tools": [{"name": "n2", "description": "d2", "code": "c2"}]}\n```'
    )
    assert parse_architect_tool_specs(two_tools_blocks) == [{"name": "n1", "description": "d1", "code": "c1"}]


# ---------------------------------------------------------------------------
# Extractor 4: agents.translator.StrategyTranslator._strip_fences
# Pinned identical to strip_json_fences on every case: it's a direct delegate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_translator_strip_fences_matches_output_parser(name: str, text: str) -> None:
    assert StrategyTranslator._strip_fences(text) == STRIP_JSON_FENCES_EXPECTED[name]


# ---------------------------------------------------------------------------
# Extractor 5: agents.curator.KnowledgeCurator.rate_analyst_output
# Never raises: JSONDecodeError is swallowed, non-dict decoded values are
# silently discarded. Both failure modes fall back to AnalystRating defaults
# (actionability=specificity=correctness=3, rationale=""), which is why most
# rows below look identical regardless of whether parsing "succeeded."
# ---------------------------------------------------------------------------

_CURATOR_DEFAULT = _CuratorRatingShape(actionability=3, specificity=3, correctness=3, rationale="")

CURATOR_RATE_EXPECTED: dict[str, _CuratorRatingShape] = {
    "normal_fenced_block": _CURATOR_DEFAULT,  # parses fine, but no actionability/specificity/etc keys
    "fence_no_language": _CURATOR_DEFAULT,
    "fence_single_line": _CURATOR_DEFAULT,
    "two_fenced_blocks": _CURATOR_DEFAULT,
    "truncated_block": _CURATOR_DEFAULT,  # JSONDecodeError swallowed
    "bare_json_with_prose": _CURATOR_DEFAULT,  # JSONDecodeError swallowed (no fence to strip first)
    "json_array_not_object": _CURATOR_DEFAULT,  # parses to a list; isinstance(decoded, dict) is False -> discarded silently
    "empty_string": _CURATOR_DEFAULT,
    "invalid_json_in_fence": _CURATOR_DEFAULT,  # JSONDecodeError swallowed
    "brace_in_string_value": _CURATOR_DEFAULT,  # parses fine, no matching keys
    "trailing_stray_brace_no_fence": _CURATOR_DEFAULT,
    "architect_valid_tools_block": _CURATOR_DEFAULT,  # parses fine, no matching keys
    "whitespace_only": _CURATOR_DEFAULT,
    "fenced_with_leading_prose_and_trailing_prose": _CURATOR_DEFAULT,
    "curator_rating_shape": _CuratorRatingShape(actionability=4, specificity=5, correctness=2, rationale="solid"),
    "hint_feedback_shape": _CURATOR_DEFAULT,
    "ac_921_corrupt_fence_with_decoy_json": _CURATOR_DEFAULT,  # JSONDecodeError swallowed
    "uppercase_json_fence_tag": _CURATOR_DEFAULT,  # leaky "JSON\n{...}" content isn't valid JSON; swallowed
    "bare_array_of_objects": _CURATOR_DEFAULT,  # parses to a list; isinstance(decoded, dict) is False -> discarded silently
    "fenced_array_of_objects": _CURATOR_DEFAULT,  # same: parses to a list, not a dict
    "fenced_mixed_array_with_object": _CURATOR_DEFAULT,  # same: parses to a list, not a dict
    "two_bare_json_objects_no_fence": _CURATOR_DEFAULT,  # recovers Option A's dict, but no matching rating keys
    # extract_json recovers {"score": 5, "rationale": "matches rubric step 3}"}.
    # "score" isn't a rating field and is ignored, but "rationale" IS a
    # rating field, so it flows through where the other rows' recovered
    # dicts have no field overlap at all.
    "critical1_brace_in_string_with_decoy": _CuratorRatingShape(
        actionability=3, specificity=3, correctness=3, rationale="matches rubric step 3}"
    ),
    "array_then_separate_object_no_fence": _CURATOR_DEFAULT,  # extract_json raises -> None -> default
    "array_of_objects_in_prose_no_fence": _CURATOR_DEFAULT,  # same
    "brace_in_string_value_no_fence_no_decoy": _CURATOR_DEFAULT,  # parses fine, no matching keys
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_curator_rate_analyst_output(name: str, text: str) -> None:
    assert _curator_rate(text) == CURATOR_RATE_EXPECTED[name]


# ---------------------------------------------------------------------------
# Extractor 6: agents.translator_simplification.extract_strategy_deterministic
# ---------------------------------------------------------------------------

# AC-910 Task 3: extract_strategy_deterministic now delegates to the shared
# extract_json(..., on_failure="none") instead of its own fence regex +
# bare-object regex + whole-text fallback chain. Two rows changed value as a
# result, both filed-bug fixes, not regressions:
#
# - `ac_921_corrupt_fence_with_decoy_json` ({"x": 2} -> None): this extractor
#   WAS the AC-921 bug -- its bare-object regex fallback scanned the whole
#   raw text and picked up the decoy object outside the corrupt fence.
#   extract_json's brace scan stays confined to the fence's own captured
#   content, so the decoy is never reached; the corrupt fence now fails
#   closed (None) instead of returning the wrong-but-plausible object.
# - `bare_array_of_objects` / `fenced_array_of_objects` /
#   `fenced_mixed_array_with_object` ({"a": 1} -> None): same array-coercion
#   defect Task 2 fixed inside extract_json itself (a successful parse to a
#   list is terminal, not a cue to keep hunting for a nested object) --
#   this extractor's own bare-object regex had the identical defect, and
#   migrating removes it here too.
#
# Every other row is unchanged: extract_json's fence + no-fence brace-scan
# fallbacks cover the same ground this extractor's three-layer chain did.
EXTRACT_STRATEGY_DETERMINISTIC_EXPECTED: dict[str, dict[str, Any] | None] = {
    "normal_fenced_block": {"a": 1},
    "fence_no_language": {"a": 1},
    "fence_single_line": {"a": 1},
    "two_fenced_blocks": {"a": 1},
    "truncated_block": None,
    "bare_json_with_prose": {"a": 1},
    "json_array_not_object": None,  # scope parses to a list -> terminal ValueError -> None
    "empty_string": None,
    "invalid_json_in_fence": None,
    "brace_in_string_value": {"code": "if (x) { return 1; }", "ok": True},
    # CHANGED by the AC-910 Task 3 review fix (Critical 1, string-aware
    # spans): see the matching comment on EXTRACT_JSON_EXPECTED above.
    "trailing_stray_brace_no_fence": {"text": "value } here"},
    "architect_valid_tools_block": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "whitespace_only": None,
    "fenced_with_leading_prose_and_trailing_prose": {"a": 1},
    "curator_rating_shape": {"actionability": 4, "specificity": 5, "correctness": 2, "rationale": "solid"},
    "hint_feedback_shape": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
    # AC-921 FIX (see comment above the table): was {"x": 2}, now None.
    "ac_921_corrupt_fence_with_decoy_json": None,
    "uppercase_json_fence_tag": {"a": 1},  # recovered via the within-scope brace scan on the leaked tag, not a bare-object regex
    # Array-coercion FIX (see comment above the table): were all {"a": 1}, now None.
    "bare_array_of_objects": None,
    "fenced_array_of_objects": None,
    "fenced_mixed_array_with_object": None,
    # Multi-object regression FIX (Task 3, output_parser.py): before the fix
    # this was None (the whole point of the regression report); now recovers
    # Option A, matching the old three-layer fallback chain this extractor
    # used to have. Net unchanged from the pre-migration baseline.
    "two_bare_json_objects_no_fence": {"aggression": 0.9, "defense": 0.1},
    # Critical 1 fix: recovers the correct first object, not the decoy.
    "critical1_brace_in_string_with_decoy": {"score": 5, "rationale": "matches rubric step 3}"},
    # Critical 2 fix: extract_json now raises on the array candidate -> None.
    "array_then_separate_object_no_fence": None,
    "array_of_objects_in_prose_no_fence": None,
    "brace_in_string_value_no_fence_no_decoy": {"note": "step 3} done", "a": 1},
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_extract_strategy_deterministic(name: str, text: str) -> None:
    assert extract_strategy_deterministic(text) == EXTRACT_STRATEGY_DETERMINISTIC_EXPECTED[name]


# ---------------------------------------------------------------------------
# Extractor 7: agents.hint_feedback.parse_hint_feedback
# Never raises. Like curator, its schema-specific return type masks parse
# success for corpus cases that don't use its "helpful"/"misleading"/"missing"
# keys, which is why almost every row is the same all-empty shape.
# ---------------------------------------------------------------------------

_HINT_FEEDBACK_DEFAULT = _HintFeedbackShape(helpful=(), misleading=(), missing=())

HINT_FEEDBACK_EXPECTED: dict[str, _HintFeedbackShape] = {name: _HINT_FEEDBACK_DEFAULT for name, _ in CORPUS}
HINT_FEEDBACK_EXPECTED["hint_feedback_shape"] = _HintFeedbackShape(helpful=("h1",), misleading=("m1",), missing=("mi1",))


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_parse_hint_feedback(name: str, text: str) -> None:
    assert _hint_feedback(text) == HINT_FEEDBACK_EXPECTED[name]


# ---------------------------------------------------------------------------
# Extractor 8: execution.action_filter.ActionFilterHarness._extract_json_object
# ---------------------------------------------------------------------------

# AC-910 Task 3: _extract_json_object now delegates to the shared
# extract_json(..., on_failure="none") instead of its own anchored fenced
# regex + naive first-"{"-to-last-"}" fallback. Three rows changed value,
# all the same array-coercion family Task 2 fixed inside extract_json
# itself (a successful parse to a list is terminal, not a cue to keep
# hunting for a nested object): `bare_array_of_objects`,
# `fenced_array_of_objects`, `fenced_mixed_array_with_object`, all
# {"a": 1} -> None. This extractor's naive find("{")/rfind("}") fallback had
# the identical defect (no type check at all on what it recovers); migrating
# removes it here too. `ac_921_corrupt_fence_with_decoy_json` does NOT
# change (still None both ways) -- this extractor's corrupted-fence
# candidate already failed to parse before reaching the naive whole-response
# scan that could have picked up the decoy, so it was accidentally already
# safe from AC-921, not by design.
EXTRACT_JSON_OBJECT_EXPECTED: dict[str, dict[str, Any] | None] = {
    "normal_fenced_block": {"a": 1},
    "fence_no_language": {"a": 1},
    "fence_single_line": {"a": 1},
    "two_fenced_blocks": {"a": 1},
    "truncated_block": None,
    "bare_json_with_prose": {"a": 1},
    "json_array_not_object": None,  # scope parses to a list -> terminal ValueError -> None
    "empty_string": None,
    "invalid_json_in_fence": None,
    "brace_in_string_value": {"code": "if (x) { return 1; }", "ok": True},
    # CHANGED by the AC-910 Task 3 review fix (Critical 1, string-aware
    # spans): see the matching comment on EXTRACT_JSON_EXPECTED above.
    "trailing_stray_brace_no_fence": {"text": "value } here"},
    "architect_valid_tools_block": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "whitespace_only": None,
    "fenced_with_leading_prose_and_trailing_prose": {"a": 1},
    "curator_rating_shape": {"actionability": 4, "specificity": 5, "correctness": 2, "rationale": "solid"},
    "hint_feedback_shape": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
    "ac_921_corrupt_fence_with_decoy_json": None,  # unchanged: fails within scope, decoy never reached, as before
    "uppercase_json_fence_tag": {
        "a": 1
    },  # recovered via the within-scope brace scan on the leaked tag now, not case-insensitive tag matching
    # Array-coercion FIX (see comment above the table): were all {"a": 1}, now None.
    "bare_array_of_objects": None,
    "fenced_array_of_objects": None,
    "fenced_mixed_array_with_object": None,
    # Multi-object regression FIX (Task 3, output_parser.py): this extractor
    # used to have its own two-stage candidate approach and would have
    # recovered Option A too before the extract_json migration; the parser
    # fix restores that.
    "two_bare_json_objects_no_fence": {"aggression": 0.9, "defense": 0.1},
    # Critical 1 fix: recovers the correct first object, not the decoy.
    "critical1_brace_in_string_with_decoy": {"score": 5, "rationale": "matches rubric step 3}"},
    # Critical 2 fix: extract_json now raises on the array candidate -> None.
    "array_then_separate_object_no_fence": None,
    "array_of_objects_in_prose_no_fence": None,
    "brace_in_string_value_no_fence_no_decoy": {"note": "step 3} done", "a": 1},
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_action_filter_extract_json_object(name: str, text: str) -> None:
    assert ActionFilterHarness._extract_json_object(text) == EXTRACT_JSON_OBJECT_EXPECTED[name]
