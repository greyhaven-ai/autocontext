"""Tests for factual_confidence dimension support (MTS-50)."""

from __future__ import annotations

from mts.execution.judge import LLMJudge
from mts.providers.callable_wrapper import CallableProvider


def _make_judge(rubric: str = "Evaluate quality.") -> LLMJudge:
    """Create a judge with a deterministic provider."""

    def llm_fn(system: str, user: str) -> str:
        return (
            "<!-- JUDGE_RESULT_START -->\n"
            '{"score": 0.7, "reasoning": "decent", "dimensions": '
            '{"factual_accuracy": 0.8, "factual_confidence": 0.9, "clarity": 0.6}}\n'
            "<!-- JUDGE_RESULT_END -->"
        )

    provider = CallableProvider(llm_fn, model_name="test")
    return LLMJudge(model="test", rubric=rubric, provider=provider)


def _make_judge_without_confidence(rubric: str = "Evaluate quality.") -> LLMJudge:
    """Create a judge whose response doesn't include factual_confidence."""

    def llm_fn(system: str, user: str) -> str:
        return (
            "<!-- JUDGE_RESULT_START -->\n"
            '{"score": 0.7, "reasoning": "ok", "dimensions": '
            '{"factual_accuracy": 0.8, "clarity": 0.6}}\n'
            "<!-- JUDGE_RESULT_END -->"
        )

    provider = CallableProvider(llm_fn, model_name="test")
    return LLMJudge(model="test", rubric=rubric, provider=provider)


def test_factual_confidence_returned_when_judge_provides_it() -> None:
    """When the judge provides factual_confidence, it appears in results."""
    judge = _make_judge()
    result = judge.evaluate(
        task_prompt="Summarize the report.",
        agent_output="The report says X.",
        reference_context="The actual report content.",
    )
    assert "factual_confidence" in result.dimension_scores
    assert result.dimension_scores["factual_confidence"] == 0.9


def test_factual_confidence_defaulted_when_missing() -> None:
    """When the judge omits factual_confidence but reference context is
    provided, a default of 0.5 is inserted."""
    judge = _make_judge_without_confidence()
    result = judge.evaluate(
        task_prompt="Summarize the report.",
        agent_output="The report says X.",
        reference_context="The actual report content.",
    )
    assert "factual_confidence" in result.dimension_scores
    assert result.dimension_scores["factual_confidence"] == 0.5


def test_no_factual_confidence_without_reference_context() -> None:
    """Without reference context, factual_confidence is not auto-injected."""

    def llm_fn(system: str, user: str) -> str:
        return (
            "<!-- JUDGE_RESULT_START -->\n"
            '{"score": 0.7, "reasoning": "ok", "dimensions": '
            '{"clarity": 0.6, "creativity": 0.8}}\n'
            "<!-- JUDGE_RESULT_END -->"
        )

    provider = CallableProvider(llm_fn, model_name="test")
    judge = LLMJudge(model="test", rubric="Evaluate quality.", provider=provider)
    result = judge.evaluate(
        task_prompt="Write a poem.",
        agent_output="Roses are red.",
    )
    assert "factual_confidence" not in result.dimension_scores
    assert "factual_accuracy" not in result.dimension_scores


def test_system_prompt_includes_confidence_instruction() -> None:
    """When reference_context is provided, the system prompt mentions
    factual_confidence."""
    captured: list[str] = []

    def llm_fn(system: str, user: str) -> str:
        captured.append(system)
        return (
            "<!-- JUDGE_RESULT_START -->\n"
            '{"score": 0.5, "reasoning": "ok", "dimensions": {}}\n'
            "<!-- JUDGE_RESULT_END -->"
        )

    provider = CallableProvider(llm_fn, model_name="test")
    judge = LLMJudge(model="test", rubric="Check facts.", provider=provider)
    judge.evaluate(
        task_prompt="Summarize.",
        agent_output="Output.",
        reference_context="Source doc.",
    )
    assert len(captured) == 1
    assert "factual_confidence" in captured[0]
