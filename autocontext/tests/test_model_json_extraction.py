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
4. ``agents.translator.StrategyTranslator.translate`` -- the fail-hard site:
   strips fences then ``json.loads``, raising a specific ``ValueError`` when
   the result isn't an object. AC-910 Task 4 migrated this one onto
   ``extract_json(text)`` (default ``on_failure="raise"``) rather than
   ``on_failure="none"``, since this is the strategy actually scored by the
   generation loop -- a parse failure here must raise, never silently
   become an empty dict.
5. ``agents.curator.KnowledgeCurator.rate_analyst_output`` -- calls (1) then
   does its own ``json.loads``, swallowing ``JSONDecodeError`` and silently
   discarding non-dict results, both without raising.
6. ``agents.translator_simplification.extract_strategy_deterministic`` -- its
   own fence regex (this one requires a literal newline after the fence,
   unlike (1)'s optional-newline regex) with a bare-JSON-object regex
   fallback and a whole-text last resort.
7. ``agents.hint_feedback.parse_hint_feedback`` -- feeds a schema-specific
   payload. Originally its own private fence regex requiring a literal
   newline; AC-910 Task 5 migrated it onto (2), which recovers a single-line
   fence this site used to silently miss (see
   ``hint_feedback_shape_single_line_fence`` below).
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

import ast
import json
from dataclasses import dataclass
from pathlib import Path
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
    (
        # AC-910 Task 5 Step 1c(i): a TRUNCATED array (no closing bracket)
        # containing exactly one complete object. `bare_array_of_objects`
        # above ('[{"a": 1}]', complete) is already terminal via the
        # successful-list-parse rule; this one is the residual the F1/Critical
        # 2 fixes didn't cover, because json.loads(scope) raises
        # JSONDecodeError on truncated input instead of returning a list, so
        # the terminal rule never got a chance to engage and the scan used to
        # unwrap the inner {"a": 1}. Must now raise, same as the complete case.
        "truncated_array_one_object",
        '[{"a": 1}',
    ),
    (
        # Same defect, two complete objects inside the truncated array. Both
        # are individually well-formed, which is exactly what let the old
        # no-fence multi-candidate scan find and adopt the first one.
        "truncated_array_two_objects",
        '[{"a": 1}, {"b": 2}',
    ),
    (
        # AC-910 Task 5 Step 1c(iv): the accepted residual. No fence is
        # present, so there is no designated payload -- unlike
        # ac_921_corrupt_fence_with_decoy_json above, where a fence exists
        # and a broken fence is evidence the model claimed that block as its
        # answer, so substituting a decoy found elsewhere would be a silent
        # wrong-answer (AC-921's failure shape). Here, with no fence, the
        # first candidate ("first: {bad json,}...") is malformed and the
        # scan tries the next top-level candidate in appearance order,
        # recovering {"x": 2}. This is DELIBERATE: try-each-candidate is the
        # only sensible policy for unfenced prose, and it matches
        # pre-consolidation behavior (see EXTRACT_JSON_EXPECTED's comment on
        # two_bare_json_objects_no_fence for the same policy applied to two
        # well-formed candidates instead of one malformed + one valid).
        "unfenced_earlier_malformed_then_valid",
        'first: {bad json,} then: {"x": 2}',
    ),
    (
        # AC-910 Task 5 hint_feedback.py migration: a single-line fence with
        # a payload matching HintFeedback's own schema (helpful/misleading/
        # missing), unlike `fence_single_line` above ({"a": 1}, no matching
        # keys). hint_feedback's own extraction step routes through a domain
        # type (HintFeedback) that masks parse success on schema-mismatched
        # input, so a corpus case with no matching keys can't observe this
        # site's behavior change -- this one can. Before the migration,
        # hint_feedback's own fence regex required a literal newline after
        # the fence tag and silently missed this (falling to all-empty
        # defaults); extract_json's optional-newline regex recovers it.
        "hint_feedback_shape_single_line_fence",
        '```json{"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]}```',
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


def _translate(text: str) -> dict[str, Any]:
    """Run StrategyTranslator.translate's real parsing path on `text`.

    `raw_output` is unrelated prose with no embedded JSON, so
    extract_strategy_deterministic always returns None on it and the LLM
    branch under test -- the one that parses `text` (the stubbed model
    response) via extract_json -- always runs.
    """
    translator = StrategyTranslator(runtime=_StubRuntime(text), model="stub")
    strategy, _execution = translator.translate(raw_output="no deterministic strategy here", strategy_interface="{}")
    return strategy


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
    "truncated_array_one_object": '[{"a": 1}',
    "truncated_array_two_objects": '[{"a": 1}, {"b": 2}',
    "unfenced_earlier_malformed_then_valid": 'first: {bad json,} then: {"x": 2}',
    "hint_feedback_shape_single_line_fence": '{"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]}',
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
    # AC-910 Task 5 Step 1c(i) fix: the scope opens with "[", so a failed
    # parse (JSONDecodeError, since the array is unterminated) is now
    # terminal -- no rescue candidates are tried -- instead of falling
    # through to _top_level_object_spans and unwrapping the inner {"a": 1}.
    "truncated_array_one_object": _Raises(json.JSONDecodeError),
    "truncated_array_two_objects": _Raises(json.JSONDecodeError),
    # Step 1c(iv) accepted residual: no fence, so each malformed candidate is
    # skipped in favor of the next one that parses. See the CORPUS comment.
    "unfenced_earlier_malformed_then_valid": {"x": 2},
    # hint_feedback.py migration: a single-line fence recovers fine here too
    # (extract_json's own regex already had the optional-newline form) --
    # this row's significance is only visible at the hint_feedback site.
    "hint_feedback_shape_single_line_fence": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
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


def test_extract_json_truncated_array_does_not_unwrap_inner_object() -> None:
    """AC-910 Task 5 Step 1c(i): a truncated (unterminated) array must fail
    the same way a complete one does, not silently unwrap its inner object.

    Before this fix: candidate 1 (the whole scope) raised JSONDecodeError
    because the array never closes, so the loop continued to
    _top_level_object_spans, which found the complete {"a": 1} sitting
    inside the unterminated array and returned it. A model hitting its
    token budget mid-array is a realistic truncation, not an exotic input
    (see AC-904/AC-905), so this silent unwrap mattered.

    This is the UNFENCED half of the guard; see
    test_extract_json_fenced_truncated_array_does_not_unwrap_inner_object
    for the fenced half, where the exact same defect survived one fix wave
    later because the array-shaped-scope check lived only in the no-fence
    branch.
    """
    with pytest.raises(json.JSONDecodeError):
        extract_json('[{"a": 1}')
    with pytest.raises(json.JSONDecodeError):
        extract_json('[{"a": 1}, {"b": 2}')
    # Complete array: already terminal via the successful-list-parse rule,
    # pinned here as the control case this fix must not disturb.
    with pytest.raises(ValueError):
        extract_json('[{"a": 1}]')
    # Plain object control: proves the array-only check doesn't over-tighten
    # and start blocking the ordinary no-fence brace-scan rescue path.
    assert extract_json('Here is the result: {"a": 1} -- hope that helps!') == {"a": 1}


def test_extract_json_fenced_truncated_array_does_not_unwrap_inner_object() -> None:
    """CRITICAL fix, AC-910 Task 5 fix wave: the array-shaped-scope check
    that test_extract_json_truncated_array_does_not_unwrap_inner_object pins
    for the UNFENCED case used to live only in that no-fence branch. The
    fenced branch brace-scanned its captured content unconditionally,
    regardless of whether that content opened with "[" -- so a truncated
    array behind a fence still silently unwrapped its inner object even
    after the unfenced case was hardened:

        extract_json('```json\\n[{"a": 1}\\n```')  used to return {'a': 1},
        silently wrong, while the unfenced extract_json('[{"a": 1}') already
        raised. Both must now raise identically, fenced or not.

    Fenced is the more likely real-world shape for a truncated array, not
    less: models are prompted to emit fenced code blocks, so a mid-array
    truncation is more often going to be behind a fence than bare in prose.
    """
    with pytest.raises(json.JSONDecodeError):
        extract_json('```json\n[{"a": 1}\n```')
    with pytest.raises(json.JSONDecodeError):
        extract_json('```json\n[{"a": 1}, {"b": 2}\n```')
    # Complete array, fenced: already terminal via the successful-list-parse
    # rule (also pinned in CORPUS as fenced_array_of_objects) -- pinned here
    # directly as the control case this fix must not disturb.
    with pytest.raises(ValueError):
        extract_json('```json\n[{"a": 1}]\n```')
    # Plain object control, fenced: proves the array-only check doesn't
    # over-tighten and start blocking the ordinary fenced brace-scan rescue
    # path (see test_extract_json_broken_fence_tag_recovers_via_brace_scan_within_fence).
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_unfenced_earlier_malformed_candidate_is_skipped() -> None:
    """AC-910 Task 5 Step 1c(iv): the accepted residual, pinned deliberately.

    With no fence, there is no designated payload, so trying each top-level
    candidate in order and returning the first that parses is the only
    sensible policy -- it is what recovers legitimate prose-plus-noise (see
    two_bare_json_objects_no_fence in the corpus) and it matches
    pre-consolidation behavior. This differs from the FENCED case
    (ac_921_corrupt_fence_with_decoy_json): there, a broken fence is
    evidence the model designated that block as its answer, so silently
    substituting a decoy found elsewhere would be a wrong answer, not a
    recovered one (AC-921). Without a fence, no such designation exists.
    """
    assert extract_json('first: {bad json,} then: {"x": 2}') == {"x": 2}


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
    "truncated_array_one_object": [],  # extract_json now raises (Step 1c(i) fix) -> None -> []
    "truncated_array_two_objects": [],  # same
    "unfenced_earlier_malformed_then_valid": [],  # extract_json recovers {"x": 2}, but no "tools" key
    "hint_feedback_shape_single_line_fence": [],  # recovers the dict, but no "tools" key
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
# Extractor 4: agents.translator.StrategyTranslator.translate (fail-hard site)
#
# AC-910 Task 4: translate() now calls extract_json(execution.content)
# directly -- its own _strip_fences + json.loads is gone -- instead of
# fence-stripping then parsing by hand. Every case matches
# EXTRACT_JSON_EXPECTED exactly, since the stubbed "model response" IS
# execution.content and nothing else touches it first (raw_output is a
# fixed non-JSON string across every case, so the deterministic fast path
# never intercepts; see _translate). The one behavioral seam: a successful
# parse to a non-Mapping (json_array_not_object, bare_array_of_objects,
# etc.) still raises ValueError, but translate() re-raises it with its own
# more specific "translator did not return a JSON object" message instead
# of extract_json's generic "Expected JSON object, got list" -- pinned
# separately below, since _Raises only checks the exception type, not the
# message. A genuine parse failure (bad syntax, e.g. truncated_block) is
# NOT rewritten: it's re-raised as the same JSONDecodeError extract_json
# raised, exactly as the pre-migration `json.loads` call would have let it
# propagate uncaught.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_translator_translate(name: str, text: str) -> None:
    expected = EXTRACT_JSON_EXPECTED[name]
    if isinstance(expected, _Raises):
        with pytest.raises(expected.exc_type):
            _translate(text)
    else:
        assert _translate(text) == expected


def test_translator_translate_wrong_type_uses_translator_specific_message() -> None:
    """A parse that succeeds but isn't an object gets translate()'s own,
    more specific message, not extract_json's generic one -- more useful to
    whoever reads the log.
    """
    with pytest.raises(ValueError, match="translator did not return a JSON object"):
        _translate("```json\n[1, 2, 3]\n```")


def test_translator_translate_parse_failure_stays_a_plain_json_decode_error() -> None:
    """A candidate that never parses at all (bad syntax) is not rewritten
    with the translator-specific message -- only the wrong-type case is.
    """
    with pytest.raises(json.JSONDecodeError):
        _translate('```json\n{a: 1, "b":}\n```')


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
    "truncated_array_one_object": _CURATOR_DEFAULT,  # extract_json raises -> None -> default
    "truncated_array_two_objects": _CURATOR_DEFAULT,  # same
    "unfenced_earlier_malformed_then_valid": _CURATOR_DEFAULT,  # recovers {"x": 2}, but no matching rating keys
    "hint_feedback_shape_single_line_fence": _CURATOR_DEFAULT,  # recovers the dict, no matching rating keys
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
    # Step 1c(i) fix (see EXTRACT_JSON_EXPECTED comment): truncated array
    # scopes are now terminal on failure, not a cue to unwrap a nested object.
    "truncated_array_one_object": None,
    "truncated_array_two_objects": None,
    # Step 1c(iv) accepted residual: recovers the later well-formed candidate.
    "unfenced_earlier_malformed_then_valid": {"x": 2},
    "hint_feedback_shape_single_line_fence": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
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
# AC-910 Task 5: CHANGED by the migration to extract_json. hint_feedback's
# own fence regex required a literal newline after the fence tag and missed
# this single-line fence, silently falling through to the all-empty default
# (identical to `_HINT_FEEDBACK_DEFAULT` above, so pre-migration this row
# would have been indistinguishable from the base case). extract_json's
# regex has always accepted the newline as optional, so migrating recovers
# the payload -- the intended union-of-behaviors direction this whole plan
# has been strengthening extract_json toward, not a regression.
HINT_FEEDBACK_EXPECTED["hint_feedback_shape_single_line_fence"] = _HintFeedbackShape(
    helpful=("h1",), misleading=("m1",), missing=("mi1",)
)


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
    # Step 1c(i) fix (see EXTRACT_JSON_EXPECTED comment): truncated array
    # scopes are now terminal on failure, not a cue to unwrap a nested object.
    "truncated_array_one_object": None,
    "truncated_array_two_objects": None,
    # Step 1c(iv) accepted residual: recovers the later well-formed candidate.
    "unfenced_earlier_malformed_then_valid": {"x": 2},
    "hint_feedback_shape_single_line_fence": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_action_filter_extract_json_object(name: str, text: str) -> None:
    assert ActionFilterHarness._extract_json_object(text) == EXTRACT_JSON_OBJECT_EXPECTED[name]


# ---------------------------------------------------------------------------
# AC-910 Task 5 Step 2: guard against a new duplicate fence extractor
# reappearing now that the seven originals are consolidated onto
# harness.core.output_parser.extract_json.
# ---------------------------------------------------------------------------

_GUARDED_SRC_DIRS = ("agents", "execution")
_ALLOWED_FENCE_REGEX_FILE = "output_parser.py"

# The four regex call sites this guard is aware of today, one per line,
# collapsed to three files below because two of them (translator.py's
# python-fence and generic-fence searches) live in the same module:
#
#   agents/translator.py:118            re.search  -- python code block
#   agents/translator.py:121            re.search  -- generic fence
#   execution/harness_synthesizer.py:233 re.search  -- python code block
#   execution/judge.py:555              re.compile -- JSON (AC-924)
#
# judge.py is tracked, unmigrated JSON extraction: `_try_code_block_parse`
# is one of FOUR ordered parsing strategies (marker-delimited, raw-JSON-
# with-"score"-key, code-block, plain-text) whose ordering is observable
# and entirely uncharacterized. Migrating it onto extract_json blind would
# be exactly the mistake this plan keeps proving is wrong -- characterize
# before you change. Tracked in AC-924, not fixed here.
#
# translator.py and harness_synthesizer.py are a DIFFERENT kind of
# exemption, not a to-do: both regexes extract PYTHON source code from a
# fence, not JSON. extract_json is a JSON parser (it feeds candidates to
# json.loads); forcing a Python-code extraction onto it would be wrong, not
# incomplete, so these are deliberately and permanently out of scope for
# migration, unlike judge.py's AC-924 debt.
#
# This is EXACT-SET equality, not an allow-list: it fails both when a NEW
# offender appears (someone adds a fence regex) and when a known one
# disappears (e.g. AC-924 lands and judge.py is migrated, or a file is
# deleted) -- so a stale exemption can't survive unnoticed the way a
# permissive allow-list would let it.
_KNOWN_OFFENDERS = frozenset(
    {
        "execution/judge.py",
        "agents/translator.py",
        "execution/harness_synthesizer.py",
    }
)

# re.* callables whose first positional argument, if a string literal
# containing a markdown fence, marks the call as a fence-regex offender.
# "compile" catches the pattern this guard was originally written for
# (`_JSON_FENCE_RE = re.compile(...)`, reused across multiple calls); the
# other three catch a pattern built inline at each call site instead
# (`re.search(r"```...", text)`), which is exactly the shape translator.py
# and harness_synthesizer.py use and the original "compile"-only check
# missed entirely.
_FENCE_REGEX_CONSTRUCTORS = frozenset({"compile", "search", "match", "finditer"})


def _guarded_python_files() -> list[Path]:
    src_root = Path(__file__).resolve().parents[1] / "src" / "autocontext"
    files: list[Path] = []
    for dirname in _GUARDED_SRC_DIRS:
        files.extend(sorted((src_root / dirname).rglob("*.py")))
    return files


def _fence_regex_offenders() -> set[str]:
    """Return the set of guarded-directory-relative paths (e.g.
    "agents/hint_feedback.py") containing a `re.compile(...)`, `re.search(...)`,
    `re.match(...)`, or `re.finditer(...)` call whose first positional
    argument is a string literal containing three backticks.

    What this DOES catch: a fence pattern passed directly as a string
    literal to one of those four `re` module functions, e.g.
    ``re.compile(r"```(?:json)?...")`` or ``re.search(r"```python\\s*\\n(.*?)```", text)``,
    however many are in one file (a file with two such calls is still one
    entry in the returned set).

    What this does NOT catch, by construction of the AST check below --
    each of these is a real, demonstrated way to defeat it, not a
    theoretical gap:
    - A pattern assigned to a module-level variable and passed by name
      (``_P = re.compile(...)`` then ``_P.search(text)`` elsewhere) --
      idiomatic Python, not evasion; a routine refactor would silently slip
      past this guard exactly the way a deliberate bypass would.
    - A pattern built by string concatenation or an f-string instead of a
      single literal.
    - ``from re import compile as _c`` (or any aliased import) followed by
      ``_c(...)`` -- the check requires the call's object to be the bare
      name ``re``.
    - A fence string embedded in ordinary text that is never passed to a
      `re.*` call at all (e.g. a prompt telling the model to emit fenced
      output, as in agents/llm_client.py) -- deliberately not flagged,
      since it isn't extracting anything and flagging it would just be
      noise that gets the guard disabled.
    """
    src_root = Path(__file__).resolve().parents[1] / "src" / "autocontext"
    offenders: set[str] = set()
    for path in _guarded_python_files():
        if path.name == _ALLOWED_FENCE_REGEX_FILE:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in _FENCE_REGEX_CONSTRUCTORS):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == "re"):
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
                continue
            if "```" in first_arg.value:
                offenders.add(str(path.relative_to(src_root)))
    return offenders


def test_no_new_markdown_fence_regex_outside_output_parser() -> None:
    """No module under agents/ or execution/ may define its own markdown-fence
    regex, beyond the known, tracked exceptions in `_KNOWN_OFFENDERS` --
    that duplication is exactly what this plan consolidated away.
    `output_parser.py` (the shared parser's home) is the sole allowed owner;
    it doesn't live under either guarded directory today, so excluding it by
    name is defense-in-depth rather than load-bearing, in case it ever moves.

    This enforces exactly what `_fence_regex_offenders`' docstring says it
    detects -- a fence pattern passed as a string literal directly to
    `re.compile` / `re.search` / `re.match` / `re.finditer` -- and nothing
    more. It does NOT catch a pattern assigned to a module-level variable
    and reused, built by concatenation or an f-string, or reached through
    an aliased `re` import; see that docstring for why each of those is
    deliberately out of scope rather than an oversight. A guard that
    implied broader coverage than that would be worse than one whose limits
    are written down, because the false confidence is what lets a routine
    refactor (moving a pattern to a module-level constant, which is
    idiomatic, not evasive) slip a new duplicate fence extractor past it
    unnoticed.

    Exact-set equality against `_KNOWN_OFFENDERS`, not a plain "no offenders"
    assertion and not a permissive allow-list: a NEW offender fails it (the
    regression this guard exists to catch), and so does a known one
    disappearing (so migrating one of the tracked files forces this test's
    expected set to be updated deliberately, rather than carrying a stale
    exemption forever).

    Proven to fire in all three directions this guard cares about (see
    task-5-fix-report.md for the pasted output):
    1. The current tree passes with exactly `_KNOWN_OFFENDERS`.
    2. A deliberately reintroduced fence regex added to an agents/ module
       that already imports `re` (e.g. `_BYPASS = re.compile(r"```...")` in
       agents/curator.py) -> fails, naming that file. Removed -> passes.
       (A module that does NOT already import `re`, e.g. agents/coach.py,
       fails at import instead of tripping the guard -- that failure looks
       like a passing proof but tests the wrong thing, so the file used for
       this check must already import `re`.)
    3. `_KNOWN_OFFENDERS` missing one of the three tracked files while that
       file still offends -> fails. Restored -> passes.

    A module-level-variable bypass (`_BYPASS = re.compile(...)` assigned in
    one place, called via `_BYPASS.search(...)` elsewhere) and an aliased-
    import bypass (`from re import compile as _c`) were both demonstrated
    live against this guard and neither is caught -- see this function's
    docstring and `_fence_regex_offenders`' docstring for why: closing them
    would require tracing assignments and import aliases, not just call
    shapes, which is a bigger change than this guard's AST walk is designed
    to do. Documented here rather than silently accepted.
    """
    assert _fence_regex_offenders() == _KNOWN_OFFENDERS
