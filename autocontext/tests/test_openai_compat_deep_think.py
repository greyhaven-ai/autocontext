from __future__ import annotations

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
        self.calls.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _BadRequest(Exception):
    status_code = 400


def _tool_call(arguments: str, call_id: str = "call-1") -> Any:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=DEEP_THINK_TOOL_NAME, arguments=arguments),
    )


def _response(*, text: str | None = None, tool_calls: list[Any] | None = None, tokens: tuple[int, int] = (3, 2)) -> Any:
    choice = SimpleNamespace(
        message=SimpleNamespace(content=text, tool_calls=tool_calls),
        finish_reason="tool_calls" if tool_calls else "stop",
    )
    usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1])
    return SimpleNamespace(choices=[choice], usage=usage)


def _provider(outcomes: list[Any]) -> tuple[OpenAICompatibleProvider, _Completions]:
    completions = _Completions(outcomes)
    provider = object.__new__(OpenAICompatibleProvider)
    provider._default_model = "stub"
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


def test_deep_think_calls_are_ordered_and_separate_from_final_text() -> None:
    provider, completions = _provider(
        [
            _response(tool_calls=[_tool_call('{"thoughts":"check the invariant"}')], tokens=(5, 7)),
            _response(text='{"answer":"done"}', tokens=(11, 13)),
        ]
    )

    result = provider.complete_with_thinking("system", "user", reasoning_effort="none")

    assert result.text == '{"answer":"done"}'
    assert result.thinking_stream == ["check the invariant"]
    assert result.thinking_tool == "deep_think"
    assert result.thinking_capture == "tool"
    assert result.usage == {"input_tokens": 16, "output_tokens": 20}
    assert completions.calls[0]["tool_choice"] == "required"
    assert completions.calls[1]["tool_choice"] == "auto"
    assert completions.calls[0]["parallel_tool_calls"] is False
    assert completions.calls[0]["reasoning_effort"] == "none"
    assert completions.calls[0]["tools"][0]["function"]["name"] == "deep_think"
    assert completions.calls[1]["messages"][-1]["role"] == "tool"
    assert "check the invariant" not in completions.calls[1]["messages"][-1]["content"]


def test_deep_think_preserves_malformed_arguments_instead_of_dropping_them() -> None:
    provider, _ = _provider(
        [
            _response(tool_calls=[_tool_call("unparseable scratchpad")]),
            _response(text="final"),
        ]
    )

    result = provider.complete_with_thinking("system", "user")

    assert result.thinking_stream == ["unparseable scratchpad"]


def test_deep_think_fails_when_required_first_call_is_ignored() -> None:
    provider, _ = _provider([_response(text="uncaptured final")])

    with pytest.raises(ProviderError, match="did not honor required deep_think"):
        provider.complete_with_thinking("system", "user")


def test_deep_think_fails_closed_at_tool_turn_limit() -> None:
    provider, _ = _provider([_response(tool_calls=[_tool_call('{"thoughts":"still working"}')])])

    with pytest.raises(ProviderError, match="exceeded 1 deep_think tool turns"):
        provider.complete_with_thinking("system", "user", max_tool_turns=1)


def test_deep_think_negotiates_only_unsupported_reasoning_effort() -> None:
    provider, completions = _provider(
        [
            _BadRequest("unknown parameter reasoning_effort"),
            _response(tool_calls=[_tool_call('{"thoughts":"portable tools"}')]),
            _response(text="final"),
        ]
    )

    result = provider.complete_with_thinking("system", "user")

    assert result.thinking_stream == ["portable tools"]
    assert completions.calls[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in completions.calls[1]
    assert "reasoning_effort" not in completions.calls[2]


def test_openai_compat_advertises_native_thinking_support() -> None:
    provider, _ = _provider([])
    assert provider.supports_thinking_stream is True
