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
    "bare_json_with_prose": _Raises(json.JSONDecodeError),
    "json_array_not_object": _Raises(ValueError),
    "empty_string": _Raises(json.JSONDecodeError),
    "invalid_json_in_fence": _Raises(json.JSONDecodeError),
    "brace_in_string_value": {"code": "if (x) { return 1; }", "ok": True},
    "trailing_stray_brace_no_fence": _Raises(json.JSONDecodeError),
    "architect_valid_tools_block": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "whitespace_only": _Raises(json.JSONDecodeError),
    "fenced_with_leading_prose_and_trailing_prose": {"a": 1},
    "curator_rating_shape": {"actionability": 4, "specificity": 5, "correctness": 2, "rationale": "solid"},
    "hint_feedback_shape": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
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
# Extractor 3: agents.architect.parse_architect_tool_specs
# ---------------------------------------------------------------------------

PARSE_ARCHITECT_TOOL_SPECS_EXPECTED: dict[str, list[dict[str, Any]]] = {
    "normal_fenced_block": [],
    "fence_no_language": [],  # requires the literal "```json" tag; plain fence never matches
    "fence_single_line": [],
    "two_fenced_blocks": [],  # rfind("```") grabs the SECOND block's closing fence -> corrupt span -> JSONDecodeError -> []
    "truncated_block": [],
    "bare_json_with_prose": [],
    "json_array_not_object": [],
    "empty_string": [],
    "invalid_json_in_fence": [],
    "brace_in_string_value": [],  # valid JSON, valid dict, but no "tools" key
    "trailing_stray_brace_no_fence": [],
    "architect_valid_tools_block": [{"name": "n", "description": "d", "code": "c"}],
    "whitespace_only": [],
    "fenced_with_leading_prose_and_trailing_prose": [],
    "curator_rating_shape": [],
    "hint_feedback_shape": [],
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_parse_architect_tool_specs(name: str, text: str) -> None:
    assert parse_architect_tool_specs(text) == PARSE_ARCHITECT_TOOL_SPECS_EXPECTED[name]


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
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_curator_rate_analyst_output(name: str, text: str) -> None:
    assert _curator_rate(text) == CURATOR_RATE_EXPECTED[name]


# ---------------------------------------------------------------------------
# Extractor 6: agents.translator_simplification.extract_strategy_deterministic
# ---------------------------------------------------------------------------

EXTRACT_STRATEGY_DETERMINISTIC_EXPECTED: dict[str, dict[str, Any] | None] = {
    "normal_fenced_block": {"a": 1},
    "fence_no_language": {"a": 1},
    "fence_single_line": {"a": 1},  # fence regex requires a literal \n, but the bare-object regex fallback still finds it
    "two_fenced_blocks": {"a": 1},
    "truncated_block": None,
    "bare_json_with_prose": {"a": 1},  # bare-object regex fallback
    "json_array_not_object": None,  # not a dict; fenced+bare-object+whole-text all reject
    "empty_string": None,
    "invalid_json_in_fence": None,
    "brace_in_string_value": {"code": "if (x) { return 1; }", "ok": True},
    "trailing_stray_brace_no_fence": None,  # naive bare-object regex still fails to reconstruct valid JSON here
    "architect_valid_tools_block": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "whitespace_only": None,
    "fenced_with_leading_prose_and_trailing_prose": {"a": 1},
    "curator_rating_shape": {"actionability": 4, "specificity": 5, "correctness": 2, "rationale": "solid"},
    "hint_feedback_shape": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
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

EXTRACT_JSON_OBJECT_EXPECTED: dict[str, dict[str, Any] | None] = {
    "normal_fenced_block": {"a": 1},
    "fence_no_language": {"a": 1},
    "fence_single_line": {"a": 1},
    "two_fenced_blocks": {"a": 1},
    "truncated_block": None,
    "bare_json_with_prose": {"a": 1},  # naive find("{")/rfind("}") fallback happens to work here
    "json_array_not_object": None,  # no "{" in the text at all; fenced regex requires one too
    "empty_string": None,
    "invalid_json_in_fence": None,
    "brace_in_string_value": {"code": "if (x) { return 1; }", "ok": True},  # fenced regex backtracks past the inner "}"
    "trailing_stray_brace_no_fence": None,  # naive rfind("}") picks up the LATER stray "}" from "{2}", corrupting the span
    "architect_valid_tools_block": {"tools": [{"name": "n", "description": "d", "code": "c"}]},
    "whitespace_only": None,
    "fenced_with_leading_prose_and_trailing_prose": {"a": 1},
    "curator_rating_shape": {"actionability": 4, "specificity": 5, "correctness": 2, "rationale": "solid"},
    "hint_feedback_shape": {"helpful": ["h1"], "misleading": ["m1"], "missing": ["mi1"]},
}


@pytest.mark.parametrize("name,text", CORPUS, ids=CORPUS_IDS)
def test_action_filter_extract_json_object(name: str, text: str) -> None:
    assert ActionFilterHarness._extract_json_object(text) == EXTRACT_JSON_OBJECT_EXPECTED[name]
