from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.providers.openai_compat import OpenAICompatibleProvider


class _Completions:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.calls.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _RejectedRequest(Exception):
    status_code = 400


def _response(text: str = "plain") -> Any:
    choice = SimpleNamespace(
        message=SimpleNamespace(content=text),
        finish_reason="stop",
    )
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage)


def _provider(outcomes: list[Any], model: str) -> tuple[OpenAICompatibleProvider, _Completions]:
    completions = _Completions(outcomes)
    provider = object.__new__(OpenAICompatibleProvider)
    provider._default_model = model
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


@pytest.mark.parametrize(
    "model",
    ["gpt-5", "gpt-5.5", "gpt-5.4-mini", "o1", "o3", "o4-mini", "openai/o3"],
)
def test_reasoning_models_use_max_completion_tokens(model: str) -> None:
    provider, completions = _provider([_response()], model)

    provider.complete("system", "user")

    assert completions.calls[0]["max_completion_tokens"] == 4096
    assert "max_tokens" not in completions.calls[0]


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4.1-mini"])
def test_non_reasoning_models_keep_max_tokens(model: str) -> None:
    provider, completions = _provider([_response()], model)

    provider.complete("system", "user")

    assert completions.calls[0]["max_tokens"] == 4096
    assert "max_completion_tokens" not in completions.calls[0]


def test_reasoning_model_negotiates_legacy_output_token_field() -> None:
    provider, completions = _provider(
        [_RejectedRequest("unknown parameter max_completion_tokens"), _response("portable")],
        "gpt-5.4-mini",
    )

    result = provider.complete("system", "user")

    assert result.text == "portable"
    assert completions.calls[0]["max_completion_tokens"] == 4096
    assert completions.calls[1]["max_tokens"] == 4096
    assert "max_completion_tokens" not in completions.calls[1]
