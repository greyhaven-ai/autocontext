"""Bounded LLM review gate for ambient advise proposals (AC-900)."""

from __future__ import annotations

import pytest
from autocontext.ambient.advise_gate import AdviseGateDecision, run_advise_gate
from pydantic import ValidationError

from autocontext.ambient.charter import (
    AdviseGateConfig,
    Charter,
    CharterBudgets,
    CharterSource,
    CharterTarget,
)
from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError


class _StubProvider(LLMProvider):
    def __init__(self, text: str = "", error: bool = False) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        self.calls.append({"model": model, "max_tokens": max_tokens, "user_prompt": user_prompt})
        if self.error:
            raise ProviderError("gate provider down")
        return CompletionResult(text=self.text)

    def default_model(self) -> str:
        return "stub"


def _charter(**overrides: object) -> Charter:
    base: dict[str, object] = dict(
        tier="oss",
        control_surface="local",
        autonomy="propose",
        sources=[CharterSource(name="native", kind="autocontext", enabled=True)],
        targets=[
            CharterTarget(
                name="competitor-grid",
                kind="role",
                selector="competitor@grid_ctf",
                base_model="Qwen/Qwen2.5-3B-Instruct",
                method="sft-distill",
                min_dataset_records=500,
                eval_suite="grid_ctf_holdout",
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=8.0, window_hours=24, disk_quota_gb=200.0),
    )
    base.update(overrides)
    return Charter(**base)  # type: ignore[arg-type]


class TestAdviseGateConfig:
    def test_validates_and_round_trips_on_charter(self) -> None:
        config = AdviseGateConfig(model="judge-model")
        assert config.max_output_tokens == 512
        charter = _charter(advise_gate=config)
        reloaded = Charter.model_validate(charter.model_dump(mode="json"))
        assert reloaded.advise_gate is not None
        assert reloaded.advise_gate.model == "judge-model"
        assert _charter().advise_gate is None

    def test_rejects_empty_model_and_bad_bounds(self) -> None:
        with pytest.raises(ValidationError):
            AdviseGateConfig(model="")
        with pytest.raises(ValidationError):
            AdviseGateConfig(model="m", max_output_tokens=10)
        with pytest.raises(ValidationError):
            AdviseGateConfig(model="m", max_output_tokens=100_000)


class TestRunAdviseGate:
    def test_parses_clean_verdict(self) -> None:
        provider = _StubProvider('{"should_propose": true, "rationale": "durable evidence"}')
        decision = run_advise_gate(provider, "judge-model", "evidence", max_output_tokens=256)
        assert decision == AdviseGateDecision(should_propose=True, rationale="durable evidence")
        assert provider.calls[0]["max_tokens"] == 256
        assert provider.calls[0]["model"] == "judge-model"

    def test_parses_fenced_verdict(self) -> None:
        provider = _StubProvider('```json\n{"should_propose": false, "rationale": "one-off noise"}\n```')
        decision = run_advise_gate(provider, "m", "evidence", max_output_tokens=512)
        assert decision is not None and decision.should_propose is False

    def test_garbage_returns_none(self) -> None:
        provider = _StubProvider("mid-sentence trunca")
        assert run_advise_gate(provider, "m", "evidence", max_output_tokens=512) is None

    def test_provider_error_returns_none(self) -> None:
        provider = _StubProvider(error=True)
        assert run_advise_gate(provider, "m", "evidence", max_output_tokens=512) is None
