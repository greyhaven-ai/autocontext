"""Characterization tests for the seven ways autocontext pulls JSON out of LLM text.

AC-910 plan 3 consolidated these onto a single parser. This file pinned what
EACH extractor did BEFORE that, against a shared corpus of realistic LLM
outputs, so the consolidation could be proven not to change observable
behavior; the tables now record the post-consolidation results, with the rows
that moved annotated in place with why.

This is a characterization test suite: every assertion records the *current*
result, including ``None``, ``[]``, ``{}``, defaults, and raised exceptions. It
does not judge whether a result is correct. Where two extractors disagree on the
same input, that disagreement is the point -- see the report at
``.superpowers/sdd/2026-08-08-ac-910-plan3-model-json-consolidation/task-1-report.md``
for the full disagreement table.

The call sites characterized here. ``strip_json_fences`` is retained as a
compatibility wrapper for external imports, but has zero production callers;
all production JSON parsing routes through the seven sites below:

1. ``harness.core.output_parser.extract_json`` -- the consolidation target.
   It had zero callers when this file was written; every site below that
   still parses JSON now routes through it.
2. ``agents.architect.parse_architect_tool_specs`` -- manual
   ``find("```json")`` / ``rfind("```")``, no regex, schema-specific
   (``{"tools": [...]}"``).
3. ``agents.translator.StrategyTranslator.translate`` -- the fail-hard site:
   strips fences then ``json.loads``, raising a specific ``ValueError`` when
   the result isn't an object. AC-910 Task 4 migrated this one onto
   ``extract_json(text)`` (default ``on_failure="raise"``) rather than
   ``on_failure="none"``, since this is the strategy actually scored by the
   generation loop -- a parse failure here must raise, never silently
   become an empty dict.
4. ``agents.curator.KnowledgeCurator.rate_analyst_output`` -- the fail-soft
   counterpart to (3). Originally stripped fences then ran its own
   ``json.loads``, swallowing ``JSONDecodeError`` and silently discarding
   non-dict results. AC-910 Task 5 migrated it onto
   ``extract_json(..., on_failure="none")``, and the branch's final cleanup
   added the schema guard that makes "degrades to defaults, never raises"
   true of the WHOLE site rather than only of its parse step -- see
   ``test_curator_rate_analyst_output_degrades_on_schema_mismatch``.
5. ``agents.translator_simplification.extract_strategy_deterministic`` -- its
   own fence regex (this one required a literal newline after the fence,
   unlike the shared ``_JSON_FENCE_RE``'s optional-newline one) with a
   bare-JSON-object regex fallback and a whole-text last resort.
6. ``agents.hint_feedback.parse_hint_feedback`` -- feeds a schema-specific
   payload. Originally its own private fence regex requiring a literal
   newline; AC-910 Task 5 migrated it onto (1), which recovers a single-line
   fence this site used to silently miss (see
   ``hint_feedback_shape_single_line_fence`` below).
7. ``execution.action_filter.ActionFilterHarness._extract_json_object`` --
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
import logging
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
from autocontext.harness.core.output_parser import extract_json
from autocontext.harness.core.types import RoleExecution, RoleUsage

# ---------------------------------------------------------------------------
# Corpus: (case name, raw LLM text). Reused by every extractor's test below,
# and by later AC-910 tasks that build the consolidated parser against it.
# ---------------------------------------------------------------------------

# U+FEFF, spelled with chr() and concatenated into the corpus rows below
# rather than pasted in as a literal, because a pasted BOM is invisible in
# an editor and in a diff -- the corpus rows it appears in would read as
# duplicates of the plain ones they exist to contrast with.
_BOM = chr(0xFEFF)

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
        # JSON fence tags are case-insensitive. This corpus row is the simple
        # control; a separate priority test below proves an uppercase tag is
        # recognized rather than merely recovered by a brace scan.
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
        #
        # NOTE what this row does and does not reach today: the scope opens
        # with "[", so the array-shaped-scope check skips the rescue
        # candidates and json.loads rejects the whole scope with "Extra data".
        # It is terminal via THAT rule, not via the wrong-type rule -- see
        # EXTRACT_JSON_EXPECTED's comment on this row.
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
        # The wrong-type rule's TERMINALITY, which nothing else here pinned.
        # `array_of_objects_in_prose_no_fence` above reaches the wrong-type
        # rule, but its array is the LAST candidate, so stopping there and
        # continuing past it are indistinguishable -- and every other row
        # that reaches the rule has the same shape. Changing the `break` at
        # output_parser.py:188 to `continue` left the entire suite green.
        #
        # Here the array span is followed by a LATER, well-formed object
        # span, so the two differ: `break` raises (the array settles what the
        # model produced), `continue` reaches past it and returns {"b": 2} --
        # the same silently-plausible wrong answer as AC-921, one nesting
        # level inward. See test_extract_json_wrong_type_candidate_is_terminal
        # for the two other discriminating shapes (an array reached after an
        # earlier malformed span, and a non-array wrong type).
        "array_span_then_later_object_no_fence",
        'Tools: [{"a": 1}] and {"b": 2}',
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
        # the fence tag and, matching nothing, gave up entirely -- falling to
        # all-empty defaults. What recovers it now is not extract_json's
        # optional newline but the fact that extract_json has a fallback at
        # all; see HINT_FEEDBACK_EXPECTED's comment below.
        "hint_feedback_shape_single_line_fence",
        '```json{"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]}```',
    ),
    (
        # BOM + truncated array, UNFENCED. `truncated_array_one_object`
        # above is the same payload without the BOM, and it is terminal
        # because extract_json's array-shape check sees scope[0] == "[".
        # U+FEFF is not whitespace, so `.strip()` leaves it in place, the
        # "[" lands at index 1, the check reads False, and before the fix
        # this fell through to the span scan and unwrapped the inner
        # {"a": 1} -- the exact silent-wrong-answer shape that check exists
        # to prevent, reached by one invisible character.
        "bom_truncated_array_no_fence",
        _BOM + '[{"a": 1}',
    ),
    (
        # Same defect through the FENCED path. The fence regex's `\s*` does
        # not consume U+FEFF either, so the BOM survives into the captured
        # group and the fenced brace scan reaches into the array's interior
        # just as the unfenced span scan did. Both shapes are pinned because
        # the two scopes are produced by different code (`fence_match.group(1)`
        # vs `text`) and an earlier round of this work fixed one path and
        # left the other.
        "bom_fenced_truncated_array",
        "```json\n" + _BOM + '[{"a": 1}\n```',
    ),
    (
        # Control for the two above: the BOM fix must not turn a BOM-prefixed
        # ORDINARY object into a failure. This parsed before the fix too, but
        # only by accident -- json.loads choked on the BOM and the span-scan
        # rescue recovered the object. Now the normalized scope parses
        # directly, first candidate, no rescue involved.
        "bom_object_no_fence",
        _BOM + '{"a": 1}',
    ),
    (
        # C1, a REGRESSION this branch introduced and these three rows pin.
        # extract_json committed to `_JSON_FENCE_RE.search(text)`'s first hit
        # -- the first fence of ANY language -- and confined every recovery
        # path to it. A model that reasons in a plain ``` block before
        # answering therefore had its answer made unreachable: architect
        # returned [] and logged "possibly truncated", which is AC-920's exact
        # user-visible symptom reintroduced at AC-920's own call site. The
        # pre-consolidation architect matched the literal "```json" and
        # skipped a preceding fence harmlessly; the migration lost that.
        #
        # Unpinned in BOTH directions before this: `two_fenced_blocks` uses
        # two ```json blocks, so it structurally cannot see which fence is
        # chosen, and no corpus row had a non-JSON fence at all.
        "plain_fence_preamble_then_json_fence",
        (
            "Let me think about this.\n```\nreasoning scratch\n```\n"
            'Here is the tool:\n```json\n{"tools": [{"name": "n", "description": "d", "code": "c"}]}\n```'
        ),
    ),
    (
        # Same defect, and the shape that made it a SILENT WRONG ANSWER rather
        # than a missing one. The preamble is a ```python block whose code
        # contains a dict literal, so the wrong scope PARSED: extract_json
        # returned {"draft": 1} -- the model's scratch work -- as the payload.
        # At the migrated sites that meant extract_strategy_deterministic and
        # action_filter handing scratch work back as a strategy, which is
        # worse than the [] the architect row above produced.
        #
        # This is also why the fence choice keys on the `json` TAG and not on
        # "does the block contain a brace": the brace test alone would pick
        # this python block. The tag is the model's own designation.
        "python_fence_preamble_then_json_fence",
        (
            'Let me think.\n```python\nplan = {"draft": 1}\n```\n'
            'Here is the tool:\n```json\n{"tools": [{"name": "n", "description": "d", "code": "c"}]}\n```'
        ),
    ),
    (
        # The third C1 shape: a brace-free reasoning fence and NO json-tagged
        # block at all, with the payload bare in the prose after it. Nothing
        # fenced can be holding an object here, so there is no fenced payload
        # to be confined to and the whole-text span scan runs. Distinct from
        # ac_921_corrupt_fence_with_decoy_json, where the fence DOES contain a
        # brace and so is a payload claim whose failure must stay terminal --
        # that row is what stops this fallback from being widened into AC-921.
        "braceless_fence_preamble_then_bare_json",
        'Thinking:\n```\nscratch\n```\nResult: {"a": 1}',
    ),
]

CORPUS_IDS = [name for name, _ in CORPUS]


class _Raises:
    """Sentinel marking that an extractor is pinned to raise on this case.

    The pinned type is matched EXACTLY (``type(exc) is exc_type``), not by
    ``isinstance``. ``json.JSONDecodeError`` subclasses ``ValueError``, so an
    ``isinstance`` check cannot tell "nothing parsed at all" from "parsed
    fine, but to the wrong type" -- and those are two different outcomes of
    two different rules in extract_json, reached through different branches.
    A row that silently swapped one for the other would still pass a loose
    check. That is not hypothetical: ``array_then_separate_object_no_fence``
    below was pinned to ``ValueError`` and documented as covering the
    wrong-type rule, while it actually raises ``JSONDecodeError`` and never
    reaches that rule at all. The subclass relationship is the only reason
    the mislabel survived.
    """

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
# Extractor 1: output_parser.extract_json (dead code today; consolidation target)
# ---------------------------------------------------------------------------

EXTRACT_JSON_EXPECTED: dict[str, Any] = {
    "normal_fenced_block": {"a": 1},
    "fence_no_language": {"a": 1},
    "fence_single_line": {"a": 1},
    "two_fenced_blocks": {"a": 1},
    "truncated_block": _Raises(json.JSONDecodeError),
    # CHANGED by the Step 2 strengthening: this row has no fence at all, just
    # an object in prose, so extract_json now span-scans the bare text -- same
    # fallback action_filter already uses -- and recovers the object. (The
    # "no fence because the fence is unterminated" case is `truncated_block`
    # directly above; it reaches the same no-fence branch by a different
    # route and still fails, since there's nothing complete to recover.)
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
    # JSON tags are now matched case-insensitively, so this parses directly
    # from the designated fence rather than succeeding through brace recovery.
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
    # AC-910 Task 3 review Critical 2 fix: the scope OPENS with "[", so the
    # direct-array check skips the rescue candidates entirely and
    # the only candidate left is the whole scope -- which json.loads rejects
    # with "Extra data" at the trailing `, {"b": 2}`. So this is terminal via
    # the array-SHAPED-SCOPE rule and a JSONDecodeError, NOT via the
    # wrong-type rule; the wrong-type `break` at output_parser.py:188 is never
    # reached on this input. It was pinned to `_Raises(ValueError)` and
    # commented as covering "a top-level array candidate is terminal even
    # when it isn't the whole scope" -- neither claim is true of this row, and
    # it only passed because JSONDecodeError subclasses ValueError. The row
    # that actually covers a terminal array candidate mid-scope is
    # `array_of_objects_in_prose_no_fence` just below (array not at index 0,
    # so the rescue candidates DO run and the array parses to a list), and
    # `array_span_then_later_object_no_fence` after it (which additionally
    # pins that the scan STOPS there).
    "array_then_separate_object_no_fence": _Raises(json.JSONDecodeError),
    # Same Critical 2 fix, array embedded in prose rather than leading: it is
    # isolated as the first container candidate and parses to a list ->
    # wrong-type ValueError.
    "array_of_objects_in_prose_no_fence": _Raises(ValueError),
    # Wrong-type TERMINALITY (the `break`, not merely the raise): an array
    # span with a well-formed object span AFTER it. See CORPUS.
    "array_span_then_later_object_no_fence": _Raises(ValueError),
    # String-aware span scan recovers this whole, valid object; there's no
    # decoy here to fall through to, so this also passed before the fix via
    # the whole-scope candidate -- pinned as the no-decoy companion to
    # critical1 above.
    "brace_in_string_value_no_fence_no_decoy": {"note": "step 3} done", "a": 1},
    # AC-910 Task 5 Step 1c(i) fix: the first container is "[", so a failed
    # parse (JSONDecodeError, since the array is unterminated) is terminal --
    # no object-rescue candidates are tried -- instead of unwrapping the inner
    # {"a": 1}.
    "truncated_array_one_object": _Raises(json.JSONDecodeError),
    "truncated_array_two_objects": _Raises(json.JSONDecodeError),
    # Step 1c(iv) accepted residual: no fence, so each malformed candidate is
    # skipped in favor of the next one that parses. See the CORPUS comment.
    "unfenced_earlier_malformed_then_valid": {"x": 2},
    # hint_feedback.py migration: a single-line fence recovers fine here --
    # via the fence regex's optional newline, and, were that newline
    # required, via the unfenced span scan instead (verified by requiring it
    # and re-running). This row's significance is only visible at the
    # hint_feedback site.
    "hint_feedback_shape_single_line_fence": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
    # BOM fix: the scope is BOM-normalized before the array-shape check, so
    # these two now behave exactly like their BOM-less twins
    # (truncated_array_one_object / the fenced case) instead of returning the
    # unwrapped inner {"a": 1}.
    "bom_truncated_array_no_fence": _Raises(json.JSONDecodeError),
    "bom_fenced_truncated_array": _Raises(json.JSONDecodeError),
    # Control: same normalization, opposite direction -- still parses.
    "bom_object_no_fence": {"a": 1},
    # C1 fix: the ```json-tagged block is preferred over the preamble fence,
    # so the real payload is recovered instead of the reasoning scratch.
    # Before the fix: row 1 raised JSONDecodeError (scope was "reasoning
    # scratch"), and row 2 returned {"draft": 1} -- the scratch dict, silently
    # substituted for the answer.
    "plain_fence_preamble_then_json_fence": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "python_fence_preamble_then_json_fence": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    # No json-tagged block and the only fence is brace-free, so there is no
    # fenced payload at all and the whole-text span scan recovers the object.
    "braceless_fence_preamble_then_bare_json": {"a": 1},
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_extract_json(name: str, text: str) -> None:
    expected = EXTRACT_JSON_EXPECTED[name]
    if isinstance(expected, _Raises):
        with pytest.raises(expected.exc_type) as excinfo:
            extract_json(text)
        # Exact type, not isinstance -- see _Raises' docstring.
        assert type(excinfo.value) is expected.exc_type
    else:
        assert extract_json(text) == expected


# ---------------------------------------------------------------------------
# extract_json's strengthened fallback and on_failure policy (AC-910 task 2).
# ---------------------------------------------------------------------------


def test_extract_json_same_line_fence_parses() -> None:
    assert extract_json('```json{"a": 1}```') == {"a": 1}


def test_extract_json_prose_wrapped_bare_json_parses_via_brace_scan() -> None:
    assert extract_json('Here is the result: {"a": 1} -- hope that helps!') == {"a": 1}


@pytest.mark.parametrize(
    "text",
    (
        'Use [draft] while reasoning, then return {"a": 1}',
        'See [working notes](https://example.test/notes), then return {"a": 1}',
        'Sources [1, 2] support the result {"a": 1}',
    ),
)
def test_extract_json_skips_bracket_prose_before_object(text: str) -> None:
    assert extract_json(text) == {"a": 1}


@pytest.mark.parametrize("decoder_error", (ValueError, RecursionError))
def test_extract_json_bounds_failed_structural_recovery(
    monkeypatch: pytest.MonkeyPatch,
    decoder_error: type[Exception],
) -> None:
    from autocontext.harness.core import output_parser

    class AlwaysFailDecoder:
        calls = 0

        def raw_decode(self, text: str, start: int):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise decoder_error

    decoder = AlwaysFailDecoder()
    monkeypatch.setattr(output_parser.json, "JSONDecoder", lambda: decoder)

    assert output_parser._top_level_object_spans('{"a": ' * 1000) == []
    assert decoder.calls == output_parser._MAX_FAILED_DECODE_ATTEMPTS


def test_extract_json_require_unique_rejects_competing_objects() -> None:
    text = 'Draft: {"answer": 1}\nFinal: {"answer": 2}'

    assert extract_json(text) == {"answer": 1}
    assert extract_json(text, on_failure="none", require_unique=True) is None
    with pytest.raises(ValueError, match="unambiguous"):
        extract_json(text, require_unique=True)


def test_extract_json_required_keys_skip_unrelated_mapping() -> None:
    text = 'Metadata: {"request_id": "abc"}\nVerdict: {"score": 0.1}'

    assert extract_json(text) == {"request_id": "abc"}
    assert extract_json(text, required_keys=("score",)) == {"score": 0.1}
    assert extract_json(text, required_keys=("score",), require_unique=True) == {"score": 0.1}


def test_extract_json_require_unique_trusts_tagged_answer() -> None:
    text = '```\n{"answer": 1}\n```\n```json\n{"answer": 2}\n```'

    assert extract_json(text, require_unique=True) == {"answer": 2}


def test_extract_json_uppercase_json_fence_tag_is_recognized() -> None:
    assert extract_json('```JSON\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_matches_json_fence_tag_exactly_case_insensitively() -> None:
    expected = {"answer": 2}

    # If the uppercase tag leaked into its body, both fences would look
    # untagged and the earlier python dict would win.
    uppercase_answer = '```python\nscratch = {"draft": 1}\n```\n```JsOn\n{"answer": 2}\n```'
    assert extract_json(uppercase_answer) == expected
    assert ActionFilterHarness._extract_json_object(uppercase_answer) == expected

    # Prefixes are not tags. If any of these gained JSON priority, its scratch
    # object would beat the later, genuinely JSON-tagged answer.
    for info_string in ("jsonl", "json5", "jsonnet"):
        response = f'```{info_string}\n{{"draft": 1}}\n```\n```json\n{{"answer": 2}}\n```'
        assert extract_json(response) == expected


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
    # path.
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


@pytest.mark.parametrize(
    "text",
    [
        'Final: [{"aggression": 0.7}',
        '```\nFinal: [{"aggression": 0.7}\n```',
        '```JSON\n[{"aggression": 0.7}\n```',
        '```jsonnet\n[{"aggression": 0.7}\n```',
    ],
)
def test_extract_json_prefixed_truncated_array_is_terminal(text: str) -> None:
    """Prose or fence info before ``[`` must not expose an inner object."""
    with pytest.raises(json.JSONDecodeError):
        extract_json(text)
    assert extract_json(text, on_failure="none") is None
    assert ActionFilterHarness._extract_json_object(text) is None


def test_translator_rejects_prefixed_truncated_array() -> None:
    """The fail-hard production translator must reject, not score, the fragment."""
    with pytest.raises(json.JSONDecodeError):
        _translate('Final: [{"aggression": 0.7}')


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


def test_extract_json_prefers_json_tagged_fence_over_preceding_fence() -> None:
    """C1: a reasoning or code block before the answer must not capture the scan.

    extract_json used to commit to `_JSON_FENCE_RE.search(text)`'s first hit --
    the first fence of ANY language -- and confine every recovery path to it,
    so a model that thinks out loud in a ``` block before emitting its
    ```json block had its answer made unreachable. The pre-consolidation
    architect matched the literal "```json" and skipped a preceding fence
    harmlessly; the migration lost that, reintroducing AC-920's user-visible
    symptom (proposal dropped, warning blaming truncation) at AC-920's own
    call site.

    Three preamble shapes, because they failed differently:
    - plain ``` prose -> the wrong scope did not parse -> raised.
    - ```python containing a dict literal -> the wrong scope DID parse ->
      returned the scratch dict, a silent wrong answer.
    - a fence with a brace-free body and no ```json block after it -> nothing
      fenced can hold an object, so the whole-text scan must run.

    See test_parse_architect_tool_specs_c1_preamble_fence_does_not_drop_tools
    for the same defect at the call site whose symptom it reproduced.
    """
    payload = '{"tools": [{"name": "n", "description": "d", "code": "c"}]}'
    expected = {"tools": [{"name": "n", "description": "d", "code": "c"}]}

    plain_preamble = f"Let me think about this.\n```\nreasoning scratch\n```\nHere is the tool:\n```json\n{payload}\n```"
    assert extract_json(plain_preamble) == expected

    # The ```python body contains a dict literal, so a "does this fence hold a
    # brace" heuristic alone would still pick it. The `json` TAG is what
    # decides, which is the point of this case.
    code_preamble = f'Let me think.\n```python\nplan = {{"draft": 1}}\n```\nHere is the tool:\n```json\n{payload}\n```'
    assert extract_json(code_preamble) == expected

    # No json-tagged block anywhere and the only fence is brace-free: there is
    # no fenced payload to be confined to, so the whole-text span scan runs.
    assert extract_json('Thinking:\n```\nscratch\n```\nResult: {"a": 1}') == {"a": 1}

    # Controls, so preferring the tagged fence cannot be over-read.
    # 1. AC-921 is untouched: a corrupt TAGGED fence still fails closed rather
    #    than reaching the decoy in the trailing prose.
    assert extract_json('```json\n{a: 1, "b":}\n```  also see config: {"x": 2}', on_failure="none") is None
    # 2. An untagged fence that DOES hold a brace is still a payload claim, so
    #    its failure stays terminal too -- this is what stops the brace-free
    #    fallback above from widening into AC-921.
    assert extract_json('```\n{a: 1, "b":}\n```  also see config: {"x": 2}', on_failure="none") is None
    # 3. AC-920 still holds: with two tagged fences the FIRST one wins.
    assert extract_json('```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```') == {"a": 1}


def test_parse_architect_tool_specs_c1_preamble_fence_does_not_drop_tools() -> None:
    """C1 at the call site whose symptom it reproduced.

    A reasoning block before the ```json block made this return [] and log
    "possibly truncated" -- a real architect proposal silently discarded, with
    the warning pointing at the wrong cause. Byte-for-byte AC-920's symptom,
    reintroduced through a different door at AC-920's own call site.
    """
    payload = '{"tools": [{"name": "n", "description": "d", "code": "c"}]}'
    expected = [{"name": "n", "description": "d", "code": "c"}]

    assert parse_architect_tool_specs(f"Let me think.\n```\nscratch\n```\nHere:\n```json\n{payload}\n```") == expected
    assert (
        parse_architect_tool_specs(f'Thinking.\n```python\nx = {{"draft": 1}}\n```\nHere:\n```json\n{payload}\n```') == expected
    )
    # Control: the same payload with no preamble worked throughout, so the
    # rows above isolate the preamble as the cause.
    assert parse_architect_tool_specs(f"```json\n{payload}\n```") == expected


def test_extract_json_wrong_type_candidate_is_terminal() -> None:
    """A candidate that PARSES to a non-Mapping stops the whole scan.

    This pins the `break` (not `continue`) at output_parser.py:188 -- the
    plan's strongest structural claim about extract_json, and until now
    entirely untested. Every earlier corpus row that reached that line had the
    wrong-type candidate LAST, where breaking and continuing were
    indistinguishable; direct-array rows skipped rescue candidates before
    reaching it. These inputs tell the two apart -- each has a well-formed
    OBJECT candidate after the wrong-type one, so `continue` would return it
    instead of raising:

        'Tools: [{"a": 1}] and {"b": 2}'                    -> would return {"b": 2}
        'garbage: {not valid json,} [{"mid": 1}] {"c": 3}'  -> would return {"c": 3}
        '"empty {} here"'                                   -> would return {}

    Returning any of those would be the AC-921 failure shape one nesting
    level inward: a plausible object lifted from somewhere the model never
    designated, substituted for an answer the parser already had. The array
    (or, in the third case, the string) IS the model's answer about what it
    produced; the fact that an object-shaped fragment sits nearby is not a
    reason to keep looking.

    The three shapes are deliberately different, since they enter the
    wrong-type branch by different routes: an array reached as the first
    rescue candidate; an array reached only AFTER an earlier malformed span
    was skipped (proving the break isn't an artifact of being the first
    rescue tried); and a non-array wrong type (a bare JSON string as the
    whole scope, terminal from the scope candidate itself, with the object
    fragment found by the span scan behind it).
    """
    # 1. Array span, then a later object span. Terminal at the array.
    with pytest.raises(ValueError) as excinfo:
        extract_json('Tools: [{"a": 1}] and {"b": 2}')
    assert type(excinfo.value) is ValueError  # not JSONDecodeError; see _Raises
    assert "got list" in str(excinfo.value)

    # 2. Same, but the array is only reached after an earlier candidate fails
    #    to parse -- so the scan is demonstrably still running when it hits
    #    the wrong type, and still stops there.
    with pytest.raises(ValueError) as excinfo:
        extract_json('garbage: {not valid json,} [{"mid": 1}] {"c": 3}')
    assert type(excinfo.value) is ValueError
    assert "got list" in str(excinfo.value)

    # 3. Non-array wrong type: the scope is a bare JSON string, which parses
    #    (to `str`) as the FIRST candidate. The span scan still found a `{}`
    #    inside it, so a `continue` would return that empty dict.
    with pytest.raises(ValueError) as excinfo:
        extract_json('"empty {} here"')
    assert type(excinfo.value) is ValueError
    assert "got str" in str(excinfo.value)

    # on_failure="none" must fail closed on all three too, not fall through to
    # the trailing object -- this is the shape the migrated call sites use.
    assert extract_json('Tools: [{"a": 1}] and {"b": 2}', on_failure="none") is None
    assert extract_json('garbage: {not valid json,} [{"mid": 1}] {"c": 3}', on_failure="none") is None
    assert extract_json('"empty {} here"', on_failure="none") is None

    # Control: an earlier candidate that FAILS to parse is still a reason to
    # keep scanning (the Step 1c(iv) residual). Only a successful parse to a
    # wrong type is terminal, so the break must not be over-read as "stop on
    # any bad candidate".
    assert extract_json('first: {bad json,} then: {"x": 2}') == {"x": 2}


def test_extract_json_bom_does_not_walk_past_the_array_shape_check() -> None:
    """A leading U+FEFF must not defeat the truncated-array rule.

    The original rule used `scope.startswith("[")`, while `.strip()` does not
    remove U+FEFF. A BOM therefore shifted the "[" to index 1 and exposed the
    inner object to rescue. Scope normalization and first-container detection
    now make the BOM-prefixed payload follow the same path as its plain twin.

    Both scope-producing paths are pinned, not just one: the fenced scope
    (`fence_match.group(1)`) and the unfenced one (`text`) are separate
    expressions, the fence regex's `\\s*` does not consume U+FEFF either, and
    an earlier round of this work fixed one path while leaving the other --
    so a single-shape test here would repeat that mistake.
    """
    with pytest.raises(json.JSONDecodeError):
        extract_json(_BOM + '[{"a": 1}')
    with pytest.raises(json.JSONDecodeError):
        extract_json("```json\n" + _BOM + '[{"a": 1}\n```')
    # Control: normalizing the BOM away must not newly reject a BOM-prefixed
    # ordinary object. This one succeeded before the fix too, but only via
    # the span-scan rescue (json.loads rejects the BOM); now the first
    # candidate parses directly.
    assert extract_json(_BOM + '{"a": 1}') == {"a": 1}
    assert extract_json("```json\n" + _BOM + '{"a": 1}\n```') == {"a": 1}


# ---------------------------------------------------------------------------
# Extractor 2: agents.architect.parse_architect_tool_specs
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
    "uppercase_json_fence_tag": [],  # parses directly; still no "tools" key
    # scope parses to a list -> terminal ValueError -> None -> [] with a
    # warning (previously [] via no "```json" tag found at all, no warning)
    "bare_array_of_objects": [],
    "fenced_array_of_objects": [],  # same terminal-list rule -> None -> [] with a warning
    "fenced_mixed_array_with_object": [],  # same terminal-list rule -> None -> [] with a warning
    "two_bare_json_objects_no_fence": [],  # extract_json recovers Option A's dict, but it has no "tools" key
    "critical1_brace_in_string_with_decoy": [],  # extract_json recovers the correct object, but no "tools" key
    "array_then_separate_object_no_fence": [],  # extract_json now raises (Critical 2 fix) -> None -> []
    "array_of_objects_in_prose_no_fence": [],  # same Critical 2 fix -> None -> []
    "array_span_then_later_object_no_fence": [],  # wrong-type rule is terminal -> raises -> None -> []
    "brace_in_string_value_no_fence_no_decoy": [],  # valid dict recovered, but no "tools" key
    "truncated_array_one_object": [],  # extract_json now raises (Step 1c(i) fix) -> None -> []
    "truncated_array_two_objects": [],  # same
    "unfenced_earlier_malformed_then_valid": [],  # extract_json recovers {"x": 2}, but no "tools" key
    "hint_feedback_shape_single_line_fence": [],  # recovers the dict, but no "tools" key
    "bom_truncated_array_no_fence": [],  # extract_json now raises (BOM fix) -> None -> []
    "bom_fenced_truncated_array": [],  # same
    "bom_object_no_fence": [],  # valid dict recovered, but no "tools" key
    # C1 fix, and THIS is the row that matters most in this table: the
    # regression's user-visible symptom was exactly an architect proposal
    # being silently dropped ([] plus a "possibly truncated" warning) because
    # a reasoning block preceded the ```json block. Both now recover the spec,
    # matching the pre-consolidation find("```json") behavior.
    "plain_fence_preamble_then_json_fence": [{"name": "n", "description": "d", "code": "c"}],
    "python_fence_preamble_then_json_fence": [{"name": "n", "description": "d", "code": "c"}],
    "braceless_fence_preamble_then_bare_json": [],  # recovers {"a": 1}, but no "tools" key
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


def test_parse_architect_tool_specs_only_warns_when_there_was_a_payload(caplog: pytest.LogCaptureFixture) -> None:
    """The "possibly truncated" warning stays scoped to inputs that could hold an object.

    Before this site was migrated onto extract_json it looked for a literal
    "```json" and returned [] SILENTLY when there wasn't one, so an empty
    string, bare prose, or an array-shaped answer logged nothing. Routing
    every unparseable input through one warning turned all of those into log
    volume, and the message they print -- "possibly truncated" -- is wrong
    about them: nothing was truncated, there was no object to find.

    The rule pinned here is the payload test, not the phrasing: warn when the
    content holds a "{" (extract_json can only succeed by returning a Mapping,
    so a "{" is what makes failure informative), stay silent otherwise.
    """
    caplog.set_level(logging.WARNING, logger="autocontext.agents.architect")

    for silent in ("", "   ", "No tools this round, the harness already covers it.", "```json\n[1, 2, 3]\n```"):
        caplog.clear()
        assert parse_architect_tool_specs(silent) == []
        assert caplog.records == [], f"expected no warning for {silent!r}"

    # A real truncated proposal is the case the message is FOR, and it still warns.
    caplog.clear()
    assert parse_architect_tool_specs('```json\n{"tools": [{"name": "n", "descrip') == []
    assert [r.message for r in caplog.records] == [
        "architect output JSON block unparseable (possibly truncated); treating as no proposal"
    ]


# ---------------------------------------------------------------------------
# Extractor 3: agents.translator.StrategyTranslator.translate (fail-hard site)
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
        with pytest.raises(expected.exc_type) as excinfo:
            _translate(text)
        # Exact type, not isinstance -- see _Raises' docstring. translate()
        # re-raises the wrong-type case as its own bare ValueError and lets a
        # JSONDecodeError through untouched, so the exact types match
        # extract_json's row for row.
        assert type(excinfo.value) is expected.exc_type
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
# Extractor 4: agents.curator.KnowledgeCurator.rate_analyst_output
# Fail-soft: a parse failure and a decoded value that isn't an object BOTH
# fall back to AnalystRating defaults (actionability=specificity=correctness=3,
# rationale=""), which is why most rows below look identical regardless of
# whether parsing "succeeded."
#
# "Never raises" is what this comment used to claim, and it was FALSE both
# before and after the consolidation: a payload that parses as JSON but not as
# a rating (e.g. {"actionability": "not-a-number"}) reached
# AnalystRating.from_dict and came back out as a pydantic ValidationError.
# None of the corpus rows carry a schema-INVALID payload, so nothing in this
# table ever observed it. The site now guards from_dict and degrades to
# defaults, which is what makes the claim true for the first time; the input
# that used to raise is pinned in
# test_curator_rate_analyst_output_degrades_on_schema_mismatch below rather
# than added to the corpus, since it only discriminates at this one site.
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
    "uppercase_json_fence_tag": _CURATOR_DEFAULT,  # parses directly, but has no rating keys
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
    # extract_json raises (wrong-type rule is terminal) -> None -> default.
    # The {"b": 2} a `continue` would reach has no rating keys either, so this
    # row cannot discriminate the break -- only EXTRACT_JSON_EXPECTED's can.
    "array_span_then_later_object_no_fence": _CURATOR_DEFAULT,
    "brace_in_string_value_no_fence_no_decoy": _CURATOR_DEFAULT,  # parses fine, no matching keys
    "truncated_array_one_object": _CURATOR_DEFAULT,  # extract_json raises -> None -> default
    "truncated_array_two_objects": _CURATOR_DEFAULT,  # same
    "unfenced_earlier_malformed_then_valid": _CURATOR_DEFAULT,  # recovers {"x": 2}, but no matching rating keys
    "hint_feedback_shape_single_line_fence": _CURATOR_DEFAULT,  # recovers the dict, no matching rating keys
    "bom_truncated_array_no_fence": _CURATOR_DEFAULT,  # extract_json raises (BOM fix) -> None -> default
    "bom_fenced_truncated_array": _CURATOR_DEFAULT,  # same
    "bom_object_no_fence": _CURATOR_DEFAULT,  # parses fine, no matching rating keys
    # C1 fix: recovers the right dict now, but none of these payloads carry
    # rating keys, so this table cannot observe the fix. Pinned for
    # completeness -- EXTRACT_JSON_EXPECTED and the architect table are where
    # the C1 rows discriminate.
    "plain_fence_preamble_then_json_fence": _CURATOR_DEFAULT,
    "python_fence_preamble_then_json_fence": _CURATOR_DEFAULT,
    "braceless_fence_preamble_then_bare_json": _CURATOR_DEFAULT,
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_curator_rate_analyst_output(name: str, text: str) -> None:
    assert _curator_rate(text) == CURATOR_RATE_EXPECTED[name]


def test_curator_rate_analyst_output_degrades_on_schema_mismatch() -> None:
    """A payload that parses as JSON but not as a rating degrades, it does not raise.

    This site is fail-soft by contract: the rating is feedback for the next
    generation's analyst prompt, not the artifact the loop scores, and its
    caller (_maybe_rate_analyst_output) runs mid-generation with no handler,
    so an exception here costs a whole generation to save a cosmetic score.

    Both rows below used to raise pydantic ValidationError out of
    rate_analyst_output. The FENCED one raised before the consolidation too --
    strip_json_fences + json.loads decoded it to a dict just fine, and the bad
    value went straight into AnalystRating.from_dict. What the migration onto
    extract_json changed is that the UNFENCED one now reaches from_dict as
    well: json.loads(strip_json_fences(prose)) raised JSONDecodeError and was
    swallowed into the default rating, whereas extract_json recovers the
    object out of surrounding prose. So the raise is pre-existing and the set
    of inputs reaching it got wider -- both are closed here.
    """
    unfenced = 'Rating: {"actionability": "not-a-number"} done'
    fenced = '```json\n{"actionability": "not-a-number"}\n```'

    assert _curator_rate(unfenced) == _CURATOR_DEFAULT
    assert _curator_rate(fenced) == _CURATOR_DEFAULT

    # Control: a schema-VALID payload in the same unfenced prose shape still
    # flows through, so the guard above degrades only on real mismatches
    # rather than swallowing every recovered payload.
    assert _curator_rate('Rating: {"actionability": 5} done') == _CuratorRatingShape(
        actionability=5, specificity=3, correctness=3, rationale=""
    )


# ---------------------------------------------------------------------------
# Extractor 5: agents.translator_simplification.extract_strategy_deterministic
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
    "uppercase_json_fence_tag": {"a": 1},  # exact case-insensitive JSON tag
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
    # Critical 2 fix: extract_json now raises -> None. Via the array-shaped-
    # scope rule for the first (JSONDecodeError on the whole scope), the
    # wrong-type rule for the second and third; the third additionally pins
    # that the scan stops rather than adopting the later {"b": 2}.
    "array_then_separate_object_no_fence": None,
    "array_of_objects_in_prose_no_fence": None,
    "array_span_then_later_object_no_fence": None,
    "brace_in_string_value_no_fence_no_decoy": {"note": "step 3} done", "a": 1},
    # Step 1c(i) fix (see EXTRACT_JSON_EXPECTED comment): truncated array
    # scopes are now terminal on failure, not a cue to unwrap a nested object.
    "truncated_array_one_object": None,
    "truncated_array_two_objects": None,
    # Step 1c(iv) accepted residual: recovers the later well-formed candidate.
    "unfenced_earlier_malformed_then_valid": {"x": 2},
    "hint_feedback_shape_single_line_fence": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
    # BOM fix (see EXTRACT_JSON_EXPECTED): a BOM no longer walks past
    # extract_json's array-shape check, so these fail closed -> None.
    "bom_truncated_array_no_fence": None,
    "bom_fenced_truncated_array": None,
    "bom_object_no_fence": {"a": 1},
    # C1 fix: the ```json block is preferred over the preamble fence. Row 2 is
    # the one that mattered at THIS site -- before the fix it returned
    # {"draft": 1}, the model's python scratch dict, as a strategy/selection.
    # A lost recovery is bad; returning scratch work as the answer is worse.
    "plain_fence_preamble_then_json_fence": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "python_fence_preamble_then_json_fence": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "braceless_fence_preamble_then_bare_json": {"a": 1},
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_extract_strategy_deterministic(name: str, text: str) -> None:
    assert extract_strategy_deterministic(text) == EXTRACT_STRATEGY_DETERMINISTIC_EXPECTED[name]


# ---------------------------------------------------------------------------
# Extractor 6: agents.hint_feedback.parse_hint_feedback
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
# would have been indistinguishable from the base case).
#
# What recovers it is NOT, as an earlier version of this comment claimed,
# extract_json's optional-newline fence regex. Requiring the newline there
# and re-running leaves this row passing: with no fence matched, the scope
# becomes the whole text and the unfenced span scan finds the payload
# anyway. The optional newline is one of two independent routes; the real
# difference is that extract_json has a fallback at all, where
# hint_feedback's single regex either matched or gave up. That distinction
# matters because it says what would actually regress this row -- removing
# the fallback scan, not tightening the fence pattern.
#
# Either way this is the intended union-of-behaviors direction this whole
# plan has been strengthening extract_json toward, not a regression.
HINT_FEEDBACK_EXPECTED["hint_feedback_shape_single_line_fence"] = _HintFeedbackShape(
    helpful=("h1",), misleading=("m1",), missing=("mi1",)
)


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_parse_hint_feedback(name: str, text: str) -> None:
    assert _hint_feedback(text) == HINT_FEEDBACK_EXPECTED[name]


# ---------------------------------------------------------------------------
# Extractor 7: execution.action_filter.ActionFilterHarness._extract_json_object
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
    "uppercase_json_fence_tag": {"a": 1},  # exact case-insensitive JSON tag
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
    # Critical 2 fix: extract_json now raises -> None. Via the array-shaped-
    # scope rule for the first (JSONDecodeError on the whole scope), the
    # wrong-type rule for the second and third; the third additionally pins
    # that the scan stops rather than adopting the later {"b": 2}.
    "array_then_separate_object_no_fence": None,
    "array_of_objects_in_prose_no_fence": None,
    "array_span_then_later_object_no_fence": None,
    "brace_in_string_value_no_fence_no_decoy": {"note": "step 3} done", "a": 1},
    # Step 1c(i) fix (see EXTRACT_JSON_EXPECTED comment): truncated array
    # scopes are now terminal on failure, not a cue to unwrap a nested object.
    "truncated_array_one_object": None,
    "truncated_array_two_objects": None,
    # Step 1c(iv) accepted residual: recovers the later well-formed candidate.
    "unfenced_earlier_malformed_then_valid": {"x": 2},
    "hint_feedback_shape_single_line_fence": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
    # BOM fix (see EXTRACT_JSON_EXPECTED): a BOM no longer walks past
    # extract_json's array-shape check, so these fail closed -> None.
    "bom_truncated_array_no_fence": None,
    "bom_fenced_truncated_array": None,
    "bom_object_no_fence": {"a": 1},
    # C1 fix: the ```json block is preferred over the preamble fence. Row 2 is
    # the one that mattered at THIS site -- before the fix it returned
    # {"draft": 1}, the model's python scratch dict, as a strategy/selection.
    # A lost recovery is bad; returning scratch work as the answer is worse.
    "plain_fence_preamble_then_json_fence": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "python_fence_preamble_then_json_fence": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "braceless_fence_preamble_then_bare_json": {"a": 1},
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
#   agents/translator.py:118             re.search  -- python code block
#   agents/translator.py:121             re.search  -- generic fence
#   execution/harness_synthesizer.py:233 re.search  -- python code block
#   execution/policy_refinement.py:71    re.findall -- python code block
#
# policy_refinement.py was MISSED by the first version of this guard, and by
# two independent evasions at once, either of which alone would have hidden
# it: `findall` was not in the constructor set below, and its pattern is a
# function-local variable passed by name rather than an inline literal. Both
# holes are closed now (see `_fence_regex_offenders`), which is the only
# reason this comment can claim four sites rather than three.
#
# translator.py, harness_synthesizer.py and policy_refinement.py are a
# permanent exemption, not a to-do: all three regexes extract PYTHON
# source code from a fence, not JSON. extract_json is a JSON parser (it feeds
# candidates to json.loads); forcing a Python-code extraction onto it would be
# wrong, not incomplete, so these are deliberately out of scope for migration.
#
# This is EXACT-SET equality, not an allow-list: it fails both when a NEW
# offender appears (someone adds a fence regex) and when a known one
# disappears (for example, a file is deleted) -- so a stale exemption can't
# survive unnoticed the way a permissive allow-list would let it.
_KNOWN_OFFENDERS = frozenset(
    {
        # Python code extraction, not JSON -- permanently out of scope.
        "agents/translator.py",
        "execution/harness_synthesizer.py",
        # Python code extraction, not JSON -- same permanent exemption as the
        # two above. Missed by the original guard (re.findall + a pattern
        # bound to a function-local variable); listed here only once the
        # guard below could actually see it.
        "execution/policy_refinement.py",
    }
)

# re.* callables whose first positional argument, if it resolves to a string
# containing a markdown fence, marks the call as a fence-regex offender.
# "compile" catches the pattern this guard was originally written for
# (`_JSON_FENCE_RE = re.compile(...)`, reused across multiple calls); the
# rest catch a pattern built inline at each call site instead
# (`re.search(r"```...", text)`), which is exactly the shape translator.py
# and harness_synthesizer.py use and the original "compile"-only check
# missed entirely. "findall"/"split"/"sub" were added after `findall`'s
# absence turned out to be one of the two reasons policy_refinement.py went
# undetected; they are the remaining module-level re.* functions that take a
# pattern first and are plausible ways to pull content out of a fence.
_FENCE_REGEX_CONSTRUCTORS = frozenset({"compile", "search", "match", "finditer", "findall", "split", "sub"})


def _guarded_python_files() -> list[Path]:
    src_root = Path(__file__).resolve().parents[1] / "src" / "autocontext"
    files: list[Path] = []
    for dirname in _GUARDED_SRC_DIRS:
        files.extend(sorted((src_root / dirname).rglob("*.py")))
    return files


def _string_literal_bindings(tree: ast.Module) -> dict[str, list[str]]:
    """Map each name bound to a plain string literal anywhere in ``tree``.

    Collected by walking the WHOLE module, so function-local bindings count
    the same as module-level ones. That is load-bearing rather than
    thorough-for-its-own-sake: execution/policy_refinement.py's offender is a
    function-local ``pattern = r"```(?:python)?\\s*\\n(.*?)```"`` handed to
    ``re.findall(pattern, ...)`` on the next line, and a module-level-only
    scan cannot see it.

    Scope is deliberately ignored and no reachability is computed -- this is
    a name-to-literal lookup, not dataflow. Consequences, both accepted:
    a name bound in one function and a same-named binding in another are
    conflated (over-approximating, which for a guard means a spurious
    failure someone must look at, never a silent miss), and a name rebound
    several times maps to every literal it took, any one of which containing
    a fence is enough to flag the call site.
    """
    bindings: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        target_names: list[str] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            target_names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            # `pattern: str = r"```..."` -- same shape, different node type.
            target_names = [node.target.id]
            value = node.value
        if value is None or not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for name in target_names:
            bindings.setdefault(name, []).append(value.value)
    return bindings


def _fence_regex_offenders() -> set[str]:
    """Return the set of guarded-directory-relative paths (e.g.
    "agents/hint_feedback.py") that call one of ``re.compile``, ``re.search``,
    ``re.match``, ``re.finditer``, ``re.findall``, ``re.split`` or ``re.sub``
    with a first positional argument that resolves to a string containing
    three backticks.

    What this DOES catch:
    - A fence pattern passed inline as a string literal to any of those seven
      `re` module functions, e.g. ``re.compile(r"```(?:json)?...")`` or
      ``re.search(r"```python\\s*\\n(.*?)```", text)``.
    - A fence pattern bound to a NAME anywhere in the same module (any scope,
      module-level or function-local) and passed by that name, e.g.
      ``pattern = r"```(?:python)?..."`` then ``re.findall(pattern, text)``
      -- which is exactly the shape execution/policy_refinement.py uses, and
      which this guard used to miss for two independent reasons at once
      (``findall`` was not in the constructor set, and the pattern was not
      inline). Resolution is by name only, via `_string_literal_bindings`;
      see that function for what "resolves" does and does not mean.

    Either way, however many such calls are in one file, the file is one
    entry in the returned set.

    What this still does NOT catch, by construction of the AST check below.
    The first two were demonstrated live against this guard; all are real
    holes, not theoretical ones:
    - ``import re as _r`` / ``from re import compile as _c`` (any aliased
      import) followed by ``_r.compile(...)`` / ``_c(...)`` -- the check
      requires the call's object to be the bare name ``re``.
    - A pattern built by string concatenation or an f-string rather than
      being a single literal: neither the inline check nor the name
      resolution above looks at anything but ``ast.Constant`` strings, so
      ``re.search("`" * 3 + "(.*?)", text)`` or an f-string pattern passes
      straight through.
    - A pattern reached through anything other than a direct name: an
      attribute (``_PATTERNS.fence``), a subscript (``_PATTERNS["fence"]``),
      a function's return value, or a name bound from another module's
      constant.
    - The compiled-object call shape ``_P = re.compile(r"```...")`` followed
      by ``_P.search(text)`` IS caught, but only because of the
      ``re.compile`` call itself -- the ``_P.search`` site is invisible, so
      a pattern compiled in a different module and imported is not seen.
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
        bindings = _string_literal_bindings(tree)
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
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                candidates = [first_arg.value]
            elif isinstance(first_arg, ast.Name):
                candidates = bindings.get(first_arg.id, [])
            else:
                continue
            if any("```" in candidate for candidate in candidates):
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
    detects, and nothing more: a fence pattern reaching `re.compile` /
    `re.search` / `re.match` / `re.finditer` / `re.findall` / `re.split` /
    `re.sub` either as an inline string literal, or as a name bound to a
    string literal somewhere in the same module (any scope -- module-level
    or function-local).

    It still does NOT catch a pattern reached through an aliased `re` import
    (`import re as _r`, `from re import compile as _c`), one built by
    concatenation or an f-string rather than being a single literal, or one
    reached by anything other than a bare name (an attribute, a subscript, a
    call's return value, a constant imported from another module). See that
    docstring for why each is out of scope rather than an oversight. A guard
    that implied broader coverage than it has would be worse than one whose
    limits are written down: the false confidence is what let
    execution/policy_refinement.py sit undetected while an earlier version
    of this docstring claimed the coverage was complete for what it
    described.

    That miss is the reason for the current wording. The first version of
    this guard checked four `re.*` functions and inline literals only, and
    policy_refinement.py evaded it twice over -- `re.findall` was not in the
    set, AND its pattern is a function-local variable passed by name. Either
    hole alone would have hidden it. Both are closed now; the remaining
    holes listed above are not.

    Exact-set equality against `_KNOWN_OFFENDERS`, not a plain "no offenders"
    assertion and not a permissive allow-list: a NEW offender fails it (the
    regression this guard exists to catch), and so does a known one
    disappearing (so migrating one of the tracked files forces this test's
    expected set to be updated deliberately, rather than carrying a stale
    exemption forever).

    Proven to fire in all three directions this guard cares about:
    1. The current tree passes with exactly `_KNOWN_OFFENDERS` (four files).
    2. A deliberately reintroduced fence regex added to an agents/ module
       that already imports `re` -- specifically the shape that used to slip
       through, `pattern = r"```...(.*?)```"` then `re.findall(pattern, ...)`
       in agents/curator.py -> fails, naming that file. Removed -> passes.
       (A module that does NOT already import `re`, e.g. agents/coach.py,
       fails at import instead of tripping the guard -- that failure looks
       like a passing proof but tests the wrong thing, so the file used for
       this check must already import `re`.)
    3. `_KNOWN_OFFENDERS` missing one of the four tracked files while that
       file still offends -> fails. Restored -> passes.
    """
    assert _fence_regex_offenders() == _KNOWN_OFFENDERS


def test_degenerate_repetition_honors_none_failure_policy() -> None:
    """Deep malformed output is a parse failure on every supported Python."""
    from autocontext.harness.core.output_parser import extract_json

    text = "Here is my verdict:\n" + '{"a": ' * 2000
    assert extract_json(text, on_failure="none") is None
