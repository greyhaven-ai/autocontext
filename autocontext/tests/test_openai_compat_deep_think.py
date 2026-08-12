from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.providers.base import ProviderError, ThinkingUnsupportedError
from autocontext.providers.openai_compat import DEEP_THINK_TOOL_NAME, OpenAICompatibleProvider
from autocontext.providers.retry import RetryProvider


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


def _provider(
    outcomes: list[Any], *, model: str = "stub"
) -> tuple[OpenAICompatibleProvider, _Completions]:
    completions = _Completions(outcomes)
    provider = object.__new__(OpenAICompatibleProvider)
    provider._default_model = model
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
    assert completions.calls[0]["tools"][0]["function"]["strict"] is True
    assert completions.calls[1]["messages"][-1]["role"] == "tool"
    assert "check the invariant" not in completions.calls[1]["messages"][-1]["content"]
    assert completions.calls[1]["messages"][-1]["content"] == '{"recorded":1}'


def test_deep_think_fails_closed_on_malformed_arguments() -> None:
    provider, _ = _provider(
        [
            _response(tool_calls=[_tool_call("unparseable scratchpad")]),
        ]
    )

    with pytest.raises(ProviderError, match="Invalid deep_think arguments"):
        provider.complete_with_thinking("system", "user")


def test_deep_think_fails_when_required_first_call_is_ignored() -> None:
    provider, _ = _provider([_response(text="uncaptured final")])

    with pytest.raises(ProviderError, match="did not honor required deep_think"):
        provider.complete_with_thinking("system", "user")


def test_deep_think_reports_endpoint_level_tool_rejection_as_unsupported() -> None:
    provider, _ = _provider([_BadRequest("tools are not supported")])

    with pytest.raises(ThinkingUnsupportedError, match="does not support thinking tools"):
        provider.complete_with_thinking("system", "user")


def test_deep_think_fails_closed_at_tool_turn_limit() -> None:
    provider, _ = _provider(
        [
            _response(tool_calls=[_tool_call('{"thoughts":"first turn"}', "call-1")]),
            _response(tool_calls=[_tool_call('{"thoughts":"still working"}', "call-2")]),
        ]
    )

    with pytest.raises(ProviderError, match="exceeded 1 deep_think tool turns"):
        provider.complete_with_thinking("system", "user", max_tool_turns=1)


def test_deep_think_allows_final_answer_after_last_tool_turn() -> None:
    provider, completions = _provider(
        [
            _response(tool_calls=[_tool_call('{"thoughts":"bounded thought"}')]),
            _response(text="final"),
        ]
    )

    result = provider.complete_with_thinking("system", "user", max_tool_turns=1)

    assert result.text == "final"
    assert result.thinking_stream == ["bounded thought"]
    assert len(completions.calls) == 2


def test_retry_preserves_usage_from_successful_turns_before_transient_failure() -> None:
    provider, _ = _provider(
        [
            _response(tool_calls=[_tool_call('{"thoughts":"first attempt"}')], tokens=(5, 7)),
            ProviderError("connection reset"),
            _response(tool_calls=[_tool_call('{"thoughts":"retry"}')], tokens=(11, 13)),
            _response(text="final", tokens=(17, 19)),
        ]
    )
    retrying = RetryProvider(provider, max_retries=1, base_delay=0)

    result = retrying.complete_with_thinking("system", "user")

    assert result.text == "final"
    assert result.usage == {"input_tokens": 33, "output_tokens": 39}


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


def test_deep_think_uses_lowest_gateway_level_when_none_is_rejected() -> None:
    provider, completions = _provider(
        [
            _BadRequest('level "none" not supported, valid levels: low, medium, high'),
            _response(tool_calls=[_tool_call('{"thoughts":"portable scratchpad"}')]),
            _response(text="final"),
        ]
    )

    result = provider.complete_with_thinking("system", "user", reasoning_effort="high")

    assert result.thinking_stream == ["portable scratchpad"]
    assert completions.calls[0]["reasoning_effort"] == "none"
    assert completions.calls[1]["reasoning_effort"] == "low"
    assert completions.calls[2]["reasoning_effort"] == "low"


def test_deep_think_negotiates_strict_flag_but_validates_arguments_locally() -> None:
    provider, completions = _provider(
        [
            _BadRequest("unknown field tools[0].function.strict"),
            _response(tool_calls=[_tool_call('{"thoughts":"portable schema"}')]),
            _response(text="final"),
        ]
    )

    result = provider.complete_with_thinking("system", "user")

    assert result.thinking_stream == ["portable schema"]
    assert completions.calls[0]["tools"][0]["function"]["strict"] is True
    assert "strict" not in completions.calls[1]["tools"][0]["function"]
    assert "strict" not in completions.calls[2]["tools"][0]["function"]


def test_deep_think_maps_gpt_56_external_effort_to_numeric_juice() -> None:
    provider, completions = _provider(
        [
            _response(tool_calls=[_tool_call('{"thoughts":"budgeted scratchpad"}')]),
            _response(text="final"),
        ],
        model="openai-codex/gpt-5.6-sol",
    )

    provider.complete_with_thinking("system", "user", reasoning_effort="high")

    assert completions.calls[0]["reasoning_effort"] == "none"
    assert completions.calls[0]["messages"][0]["content"].endswith("# Juice: 48 !important")


def test_openai_compat_advertises_native_thinking_support() -> None:
    provider, _ = _provider([])
    assert provider.supports_thinking_stream is True
