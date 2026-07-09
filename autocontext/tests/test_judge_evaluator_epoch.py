from __future__ import annotations

from typing import Any

from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.judge import LLMJudge
from autocontext.extensions import HookBus, HookEvents
from autocontext.providers.base import CompletionResult, LLMProvider


def _fake_llm(_system: str, _user: str) -> str:
    return '{"score": 0.8, "reasoning": "ok"}'


class _RecordingProvider(LLMProvider):
    """Records the model each provider.complete call was actually invoked with."""

    def __init__(self) -> None:
        self.models_used: list[str | None] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        self.models_used.append(model)
        return CompletionResult(text='{"score": 0.8, "reasoning": "ok"}', model=model)

    def default_model(self) -> str:
        return "ctor-model"

    @property
    def name(self) -> str:
        return "recording"


def test_epoch_reflects_hook_switched_model() -> None:
    """A BEFORE_JUDGE hook that switches the model must make the stamped epoch
    reflect the model actually sent to the provider, not the constructor model.
    """
    provider = _RecordingProvider()
    bus = HookBus()

    def _switch_model(event: Any) -> dict[str, str]:
        return {"model": "hook-model"}

    bus.on(HookEvents.BEFORE_JUDGE, _switch_model)

    judge = LLMJudge(
        model="ctor-model",
        rubric="score correctness 0-1",
        provider=provider,
        hook_bus=bus,
    )
    result = judge.evaluate("task", "output")

    assert provider.models_used == ["hook-model"]
    expected = compute_evaluator_epoch("score correctness 0-1", "recording", "hook-model").epoch_id
    ctor_epoch = compute_evaluator_epoch("score correctness 0-1", "recording", "ctor-model").epoch_id
    assert result.evaluator_epoch == expected
    assert result.evaluator_epoch != ctor_epoch


def test_epoch_reflects_ctor_model_without_hook() -> None:
    """Backward compatibility: with no hook, the stamped epoch is the ctor-model epoch."""
    provider = _RecordingProvider()
    judge = LLMJudge(model="ctor-model", rubric="score correctness 0-1", provider=provider)
    result = judge.evaluate("task", "output")

    assert provider.models_used == ["ctor-model"]
    expected = compute_evaluator_epoch("score correctness 0-1", "recording", "ctor-model").epoch_id
    assert result.evaluator_epoch == expected


def test_judge_result_carries_evaluator_epoch() -> None:
    judge = LLMJudge(model="claude-sonnet-4-5", rubric="score correctness 0-1", llm_fn=_fake_llm)
    result = judge.evaluate("task", "output")
    expected = compute_evaluator_epoch("score correctness 0-1", judge.provider.name, "claude-sonnet-4-5").epoch_id
    assert result.evaluator_epoch == expected


def test_different_rubric_changes_epoch() -> None:
    j1 = LLMJudge(model="claude-sonnet-4-5", rubric="rubric one", llm_fn=_fake_llm)
    j2 = LLMJudge(model="claude-sonnet-4-5", rubric="rubric two", llm_fn=_fake_llm)
    assert j1.evaluate("t", "o").evaluator_epoch != j2.evaluate("t", "o").evaluator_epoch
