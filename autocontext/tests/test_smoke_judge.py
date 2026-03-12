"""Smoke test: single-round judge eval (MTS-29).

Validates basic wiring: judge scores, parses, and returns correctly
on a canned prompt+output with a mock provider.
"""

from __future__ import annotations

import json

from autocontext.execution.judge import JudgeResult, LLMJudge
from autocontext.providers.base import CompletionResult, LLMProvider


class _MockProvider(LLMProvider):
    def __init__(self, response_text: str) -> None:
        self._response = response_text

    def complete(self, system_prompt: str, user_prompt: str, model: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 4096) -> CompletionResult:
        return CompletionResult(text=self._response, model=model or "mock-v1")

    def default_model(self) -> str:
        return "mock-v1"


CANNED_PROMPT = "Write a one-paragraph summary of what AutoContext does"
CANNED_OUTPUT = (
    "AutoContext is an iterative strategy generation system that uses multi-agent "
    "collaboration to evolve strategies through tournament matches and LLM "
    "judge evaluation with Elo-based progression gating."
)
RUBRIC = "Evaluate on: accuracy (factual correctness), clarity (readability), completeness (coverage of key concepts)"


def _make_judge_response(
    score: float = 0.85,
    dims: dict[str, float] | None = None,
) -> str:
    data = {
        "score": score,
        "reasoning": "The summary accurately captures the core AutoContext loop.",
        "dimensions": dims or {"accuracy": 0.9, "clarity": 0.85, "completeness": 0.8},
    }
    return f"<!-- JUDGE_RESULT_START -->\n{json.dumps(data)}\n<!-- JUDGE_RESULT_END -->"


class TestSmokeJudgeEval:
    """MTS-29: Validate judge returns valid result with score, dimensions, reasoning."""

    def test_judge_returns_valid_result(self) -> None:
        provider = _MockProvider(_make_judge_response())
        judge = LLMJudge(model="mock-v1", rubric=RUBRIC, provider=provider)
        result = judge.evaluate(CANNED_PROMPT, CANNED_OUTPUT)
        assert isinstance(result, JudgeResult)
        assert 0 <= result.score <= 1
        assert result.score == 0.85

    def test_all_three_dimensions_scored(self) -> None:
        provider = _MockProvider(_make_judge_response())
        judge = LLMJudge(model="mock-v1", rubric=RUBRIC, provider=provider)
        result = judge.evaluate(CANNED_PROMPT, CANNED_OUTPUT)
        assert len(result.dimension_scores) == 3
        assert "accuracy" in result.dimension_scores
        assert "clarity" in result.dimension_scores
        assert "completeness" in result.dimension_scores

    def test_dimension_scores_independent(self) -> None:
        provider = _MockProvider(_make_judge_response(dims={"accuracy": 0.9, "clarity": 0.7, "completeness": 0.5}))
        judge = LLMJudge(model="mock-v1", rubric=RUBRIC, provider=provider)
        result = judge.evaluate(CANNED_PROMPT, CANNED_OUTPUT)
        assert result.dimension_scores["accuracy"] == 0.9
        assert result.dimension_scores["clarity"] == 0.7
        assert result.dimension_scores["completeness"] == 0.5

    def test_reasoning_non_empty(self) -> None:
        provider = _MockProvider(_make_judge_response())
        judge = LLMJudge(model="mock-v1", rubric=RUBRIC, provider=provider)
        result = judge.evaluate(CANNED_PROMPT, CANNED_OUTPUT)
        assert len(result.reasoning) > 0
        assert "AutoContext" in result.reasoning

    def test_parse_succeeds_first_attempt(self) -> None:
        provider = _MockProvider(_make_judge_response())
        judge = LLMJudge(model="mock-v1", rubric=RUBRIC, provider=provider)
        result = judge.evaluate(CANNED_PROMPT, CANNED_OUTPUT)
        assert result.parse_method in ("markers", "raw_json")  # depends on parser strategy order
