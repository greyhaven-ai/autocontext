from __future__ import annotations

from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.judge import LLMJudge


def _fake_llm(_system: str, _user: str) -> str:
    return '{"score": 0.8, "reasoning": "ok"}'


def test_judge_result_carries_evaluator_epoch() -> None:
    judge = LLMJudge(model="claude-sonnet-4-5", rubric="score correctness 0-1", llm_fn=_fake_llm)
    result = judge.evaluate("task", "output")
    expected = compute_evaluator_epoch("score correctness 0-1", judge.provider.name, "claude-sonnet-4-5").epoch_id
    assert result.evaluator_epoch == expected


def test_different_rubric_changes_epoch() -> None:
    j1 = LLMJudge(model="claude-sonnet-4-5", rubric="rubric one", llm_fn=_fake_llm)
    j2 = LLMJudge(model="claude-sonnet-4-5", rubric="rubric two", llm_fn=_fake_llm)
    assert j1.evaluate("t", "o").evaluator_epoch != j2.evaluate("t", "o").evaluator_epoch
