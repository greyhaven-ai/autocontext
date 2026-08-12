"""AC-924: what the judge's 4-tier score parser actually does, pinned by execution.

The issue framed this as consolidating a duplicate fence regex. Characterizing
first -- the same discipline that found five defects in the Plan 3 extractors --
turned up two behaviors that matter more than the duplication, because a
mis-parsed judge score is not a crash. It is a wrong number entering the loop's
ranking, so the run keeps going and reports a result nobody can tell is wrong.

**Tier order is not what the method docstrings say.** `_parse_judge_response`
calls markers, then raw_json, then code_block, then plaintext. The individual
methods label themselves "Strategy 2: code block" and "Strategy 3: raw JSON",
i.e. the reverse of the order they run in. The class docstring has it right.
AC-924's own description inherited the wrong version.

That inversion is what causes the first defect: `_try_raw_json_parse` scans the
WHOLE response with `re.finditer(r'\\{[^{}]*"score"[^{}]*\\}', ...)` and takes
the first match, so it reaches inside fences and reasoning blocks and wins
before the fence-aware tier is ever tried.

These tests pin today's behavior including both defects. The fix commit changes
the recorded values, so the diff is the evidence.
"""

from __future__ import annotations

from typing import Any

import pytest

from autocontext.execution.judge import _RESULT_END, _RESULT_START, LLMJudge


def _parse(response: str) -> tuple[float, str, dict[str, float], str]:
    """Drive the real parser without constructing a judge (no provider needed)."""
    judge = object.__new__(LLMJudge)
    return judge._parse_judge_response(response)


@pytest.mark.parametrize(
    "label,response,score,method",
    [
        ("markers win outright", f'{_RESULT_START}\n{{"score":0.9}}\n{_RESULT_END}', 0.9, "markers"),
        (
            "markers beat stray prose JSON",
            f'Aside {{"score":0.15}}\n{_RESULT_START}\n{{"score":0.85}}\n{_RESULT_END}',
            0.85,
            "markers",
        ),
        ("bare object", '{"score": 0.6}', 0.6, "raw_json"),
        ("fenced object is read by the RAW tier", '```json\n{"score": 0.8}\n```', 0.8, "raw_json"),
        ("untagged fence, same", '```\n{"score": 0.7}\n```', 0.7, "raw_json"),
        ("prose then object", 'Verdict:\n{"score": 0.55}', 0.55, "raw_json"),
        ("plain text score", "Overall score: 0.45", 0.45, "plaintext"),
        ("x / 1.0 form", "I would rate this 0.35 / 1.0", 0.35, "plaintext"),
        ("nothing scoreable", "This response cannot be scored.", 0.0, "none"),
    ],
)
def test_uncontested_responses_parse_as_recorded(label: str, response: str, score: float, method: str) -> None:
    """The cases where every tier would agree. These must not move."""
    got_score, _reasoning, _dims, got_method = _parse(response)
    assert (got_score, got_method) == (score, method), label


def test_code_block_tier_is_only_reachable_past_two_nesting_levels() -> None:
    """Why `code_block` almost never fires, which the tier order hides.

    `raw_json` runs first and its regex handles at most one level of nesting.
    A fenced payload nested deeper falls through to the fence-aware tier -- the
    only routine way to reach it at all.
    """
    score, _reasoning, _dims, method = _parse('```json\n{"score": 0.65, "dimensions": {"a": {"b": 1}}}\n```')
    assert (score, method) == (0.65, "code_block")


def test_deeply_nested_unfenced_payload_falls_all_the_way_to_plaintext() -> None:
    """DEFECT (recorded, not endorsed): dimensions are silently dropped.

    Two levels of nesting defeats the raw tier, there is no fence for the fence
    tier, so a well-formed JSON object is scraped by a plaintext regex. The
    score survives by luck because the regex finds `"score": 0.64`; every
    per-dimension score in the same object is lost without a signal.
    """
    score, _reasoning, dims, method = _parse('{"score": 0.64, "dimensions": {"a": {"b": 1}}}')
    assert (score, method) == (0.64, "plaintext")
    assert dims == {}, "dimensions survived; this test is out of date"


@pytest.mark.parametrize(
    "label,response,wrong,right",
    [
        (
            "a discarded draft beats the final answer",
            'I considered {"score": 0.2} but settled on {"score": 0.9}',
            0.2,
            0.9,
        ),
        (
            "prose draft beats the fenced answer",
            'Draft thought {"score": 0.1}\n```json\n{"score": 0.95}\n```',
            0.1,
            0.95,
        ),
        (
            "a reasoning block beats the answer",
            '```\nthinking out loud, maybe {"score": 0.05}\n```\n```json\n{"score": 0.88}\n```',
            0.05,
            0.88,
        ),
    ],
)
def test_first_score_shaped_object_anywhere_wins(label: str, response: str, wrong: float, right: float) -> None:
    """DEFECT (recorded, not endorsed): the earliest match wins, wherever it is.

    The third case is the one that matters in practice. Emitting a reasoning
    block before the answer is ordinary open-weight behavior -- it is the exact
    shape AC-926 fixed for the advise gate -- and here it scores the run 0.05
    instead of 0.88. Nothing errors; a wrong number just enters the ranking.
    """
    score, _reasoning, _dims, _method = _parse(response)
    assert score == wrong, f"{label}: expected the recorded (wrong) value"
    assert score != right, f"{label}: behavior improved; re-record this file"


def test_tier_order_contradicts_the_method_docstrings() -> None:
    """Pins the naming inversion so the fix has to resolve it deliberately.

    `_try_code_block_parse` documents itself as Strategy 2 and `_try_raw_json_parse`
    as Strategy 3, while `_parse_judge_response` runs raw_json second and
    code_block third. Asserted on observable behavior rather than on docstring
    text: a fenced, singly-nested payload is reported as `raw_json`, which is
    only possible if the raw tier runs first.
    """
    _score, _reasoning, _dims, method = _parse('```json\n{"score": 0.5, "dimensions": {"a": 1}}\n```')
    assert method == "raw_json"


def test_dimensions_survive_a_single_nesting_level() -> None:
    """The one path that delivers per-dimension scores today."""
    _score, _reasoning, dims, method = _parse('{"score": 0.75, "dimensions": {"a": 1}}')
    assert method == "raw_json"
    assert dims == {"a": 1.0}


def test_marker_tier_ignores_malformed_json_and_falls_through() -> None:
    """Markers are preferred but not trusted blindly."""
    response: Any = f'{_RESULT_START}\nnot json\n{_RESULT_END}\n{{"score": 0.42}}'
    score, _reasoning, _dims, method = _parse(response)
    assert (score, method) == (0.42, "raw_json")
