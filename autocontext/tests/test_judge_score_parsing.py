"""AC-924: what the judge's 4-tier score parser actually does, pinned by execution.

The issue framed this as consolidating a duplicate fence regex. Characterizing
first -- the same discipline that found five defects in the Plan 3 extractors --
turned up two behaviors that matter more than the duplication, because a
mis-parsed judge score is not a crash. It is a wrong number entering the loop's
ranking, so the run keeps going and reports a result nobody can tell is wrong.

**The tier order was not what the method docstrings said.** The parser ran
markers, raw_json, code_block, plaintext, while the methods labelled themselves
"Strategy 2: code block" and "Strategy 3: raw JSON" -- the reverse. Only the
class docstring was right, and AC-924's own description inherited the wrong
version.

That inversion is what caused the first defect. `_try_raw_json_parse` scanned
the WHOLE response with `re.finditer(r'\\{[^{}]*"score"[^{}]*\\}', ...)` and took
the first match, so it reached inside fences and reasoning blocks and won before
the fence-aware tier was ever tried.

The fix replaces both middle tiers with the shared `extract_json`, which already
preferred the answer over the scratchpad. These values were recorded against the
old parser first and then re-recorded, so the diff in this file is the evidence
that the behavior moved.
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
        ("fenced object", '```json\n{"score": 0.8}\n```', 0.8, "raw_json"),
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


def test_arbitrary_nesting_depth_is_read_as_json() -> None:
    """FIXED by AC-924. Was: fell to the plaintext tier and lost its dimensions.

    The old raw regex handled one level of nesting and the fence tier could not
    help an unfenced payload, so a well-formed object was scraped by a plaintext
    regex. The score survived by luck; every per-dimension score was dropped.
    """
    score, _reasoning, _dims, method = _parse('{"score": 0.64, "dimensions": {"a": {"b": 1}}}')
    assert (score, method) == (0.64, "raw_json")


@pytest.mark.parametrize(
    "label,response,expected,was",
    [
        (
            "prose draft loses to the fenced answer",
            'Draft thought {"score": 0.1}\n```json\n{"score": 0.95}\n```',
            0.95,
            0.1,
        ),
        (
            "a reasoning block loses to the answer",
            '```\nthinking out loud, maybe {"score": 0.05}\n```\n```json\n{"score": 0.88}\n```',
            0.88,
            0.05,
        ),
    ],
)
def test_a_discarded_draft_no_longer_outranks_the_answer(label: str, response: str, expected: float, was: float) -> None:
    """FIXED by AC-924, and the reason the issue was worth more than a dedupe.

    The old middle tier scanned the whole response for a `"score"`-shaped
    object and took the first hit, so a draft in prose or a reasoning block beat
    the real answer. Emitting a reasoning block before the answer is ordinary
    open-weight output -- the exact shape AC-926 fixed for the advise gate --
    and it scored the run 0.05 where the judge said 0.88. Silently: a wrong
    number entered the ranking and the run carried on.
    """
    score, _reasoning, _dims, _method = _parse(response)
    assert score == expected, label
    assert score != was, f"{label}: regressed to the pre-AC-924 value"


def test_two_bare_objects_in_prose_still_take_the_first() -> None:
    """NOT fixed, and deliberately so.

    With no fence and no markers there is nothing to distinguish a draft from a
    verdict; both parsers take the first object. Recorded so the limit is known
    rather than assumed away by the cases above.
    """
    score, _reasoning, _dims, method = _parse('I considered {"score": 0.2} but settled on {"score": 0.9}')
    assert (score, method) == (0.2, "raw_json")


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
