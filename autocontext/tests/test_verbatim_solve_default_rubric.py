"""AC-734 follow-up — default verbatim rubric must not fight the judge.

Reviewer P2: the previous default rubric ended with "Output ONLY the
score as a decimal number." LLMJudge's system prompt asks for JSON
inside <!-- JUDGE_RESULT_START --> / <!-- JUDGE_RESULT_END --> markers;
a model following the rubric's instruction would emit a bare decimal
like ``0.8``, and the judge's plaintext fallback does not parse that
shape — turning a successful evaluation into a parse failure.

These tests pin: (1) the default rubric describes the scoring criteria
only, (2) it does NOT include any output-format directive that
contradicts the judge's own marker/JSON contract.
"""

from __future__ import annotations

from autocontext.knowledge.verbatim_solve import (
    _DEFAULT_VERBATIM_JUDGE_RUBRIC,
    VerbatimSolveRequest,
)


class TestDefaultVerbatimRubric:
    def test_rubric_is_nonempty(self) -> None:
        assert _DEFAULT_VERBATIM_JUDGE_RUBRIC.strip() != ""

    def test_rubric_describes_scoring_criteria(self) -> None:
        body = _DEFAULT_VERBATIM_JUDGE_RUBRIC.lower()
        # Must still tell the judge how to score (range + criterion).
        assert "0.0" in body and "1.0" in body
        assert "task prompt" in body or "requirement" in body

    def test_rubric_does_not_force_bare_decimal_output(self) -> None:
        body = _DEFAULT_VERBATIM_JUDGE_RUBRIC.lower()
        # The judge's system prompt requires JUDGE_RESULT markers + JSON.
        # The rubric must not contradict that contract.
        assert "output only" not in body
        assert "decimal number" not in body
        assert "no other text" not in body

    def test_rubric_does_not_mention_markers_either(self) -> None:
        # The rubric should describe scoring; the judge prompt handles
        # output format. Mentioning markers in the rubric would be
        # redundant noise (and a maintenance hazard).
        body = _DEFAULT_VERBATIM_JUDGE_RUBRIC.lower()
        assert "judge_result" not in body


class TestVerbatimSolveRequestUsesDefault:
    def test_empty_judge_rubric_is_replaced_with_default(self) -> None:
        req = VerbatimSolveRequest(description="x", task_prompt="hello world")
        assert req.judge_rubric == _DEFAULT_VERBATIM_JUDGE_RUBRIC

    def test_explicit_judge_rubric_is_preserved(self) -> None:
        req = VerbatimSolveRequest(
            description="x",
            task_prompt="hello world",
            judge_rubric="Custom: score 0.5 if it compiles, 1.0 if all proofs close.",
        )
        assert req.judge_rubric.startswith("Custom:")
