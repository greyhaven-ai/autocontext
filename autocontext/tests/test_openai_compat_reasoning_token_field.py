from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.providers.base import ProviderError
from autocontext.providers.openai_compat import DEEP_THINK_TOOL_NAME, OpenAICompatibleProvider


class _Completions:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.calls.append(deepcopy(request))
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
    [
        "gpt-5", "gpt-5.5", "gpt-5.4-mini", "gpt-5.6-terra", "gpt-6",
        "o1", "o3", "o4-mini", "openai/o3", "openai/gpt-5.4-mini",
        "ft:gpt-5.4-mini:org:custom", "ft:o4-mini:org:custom", "OPENAI/GPT-5.5",
    ],
)
def test_reasoning_models_use_max_completion_tokens(model: str) -> None:
    provider, completions = _provider([_response()], model)

    provider.complete("system", "user", max_tokens=123)

    assert completions.calls[0]["max_completion_tokens"] == 123
    assert "max_tokens" not in completions.calls[0]


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4.1-mini", "gpt-5ish", "gpt-5.4ish", "o3ish", "qwen3"])
def test_non_reasoning_models_keep_max_tokens(model: str) -> None:
    provider, completions = _provider([_response()], model)

    provider.complete("system", "user")

    assert completions.calls[0]["max_tokens"] == 4096
    assert "max_completion_tokens" not in completions.calls[0]


@pytest.mark.parametrize(
    ("default_model", "override", "field"),
    [("gpt-4o", "o3", "max_completion_tokens"), ("o3", "gpt-4o", "max_tokens")],
)
def test_token_field_follows_explicit_model_override(default_model: str, override: str, field: str) -> None:
    provider, completions = _provider([_response()], default_model)

    provider.complete("system", "user", model=override, max_tokens=123)

    assert completions.calls[0]["model"] == override
    assert completions.calls[0][field] == 123
    assert ("max_tokens" in completions.calls[0]) != ("max_completion_tokens" in completions.calls[0])


@pytest.mark.parametrize("model", ["gpt-5.4-mini", "o3", "openai/o4-mini"])
def test_thinking_calls_use_max_completion_tokens_on_every_turn(model: str) -> None:
    tool_response = _response()
    tool_response.choices[0].message.tool_calls = [SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name=DEEP_THINK_TOOL_NAME, arguments='{"thoughts":"check the budget"}'),
    )]
    provider, completions = _provider([tool_response, _response("final")], model)

    result = provider.complete_with_thinking("system", "user", max_tokens=123)

    assert result.text == "final"
    assert result.thinking_stream == ["check the budget"]
    assert len(completions.calls) == 2
    for request in completions.calls:
        assert request["max_completion_tokens"] == 123
        assert "max_tokens" not in request


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


def test_single_dispatch_does_not_negotiate_legacy_output_token_field() -> None:
    provider, completions = _provider(
        [_RejectedRequest("unknown parameter max_completion_tokens")], "o3",
    )
    provider._single_dispatch = True

    with pytest.raises(ProviderError, match="max_completion_tokens"):
        provider.complete("system", "user")

    assert len(completions.calls) == 1
    assert completions.calls[0]["max_completion_tokens"] == 4096


@pytest.mark.parametrize("status", [401, 429, 500])
def test_token_field_mentioned_in_non_rejection_error_does_not_trigger_fallback(status: int) -> None:
    error = _RejectedRequest("unrecognized max_completion_tokens")
    error.status_code = status
    provider, completions = _provider([error], "o3")

    with pytest.raises(ProviderError, match="max_completion_tokens"):
        provider.complete("system", "user")

    assert len(completions.calls) == 1
