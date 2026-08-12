from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.providers.anthropic import AnthropicProvider
from autocontext.providers.base import ProviderError


class _Messages:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.calls.append(request)
        return self.outcomes.pop(0)


def _tool_block(thoughts: Any, call_id: str = "toolu-1") -> Any:
    return SimpleNamespace(type="tool_use", id=call_id, name="deep_think", input=thoughts)


def _response(*blocks: Any, tokens: tuple[int, int] = (3, 2), stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=list(blocks),
        model="claude-stub",
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
        stop_reason=stop_reason,
    )


def _provider(outcomes: list[Any]) -> tuple[AnthropicProvider, _Messages]:
    messages = _Messages(outcomes)
    provider = object.__new__(AnthropicProvider)
    provider._default_model = "claude-stub"
    provider._client = SimpleNamespace(messages=messages)
    return provider, messages


def test_anthropic_deep_think_is_ordered_and_separate_from_final_text() -> None:
    provider, messages = _provider(
        [
            _response(_tool_block({"thoughts": "establish invariant"}), tokens=(5, 7), stop_reason="tool_use"),
            _response(SimpleNamespace(type="text", text='{"answer":"done"}'), tokens=(11, 13)),
        ]
    )

    result = provider.complete_with_thinking("system", "user")

    assert result.text == '{"answer":"done"}'
    assert result.thinking_stream == ["establish invariant"]
    assert result.thinking_tool == "deep_think"
    assert result.thinking_capture == "tool"
    assert result.usage == {"input_tokens": 16, "output_tokens": 20}
    assert messages.calls[0]["tool_choice"] == {
        "type": "tool",
        "name": "deep_think",
        "disable_parallel_tool_use": True,
    }
    assert messages.calls[1]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    tool_result = messages.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "establish invariant" not in tool_result["content"]
    assert tool_result["content"] == '{"recorded":1}'


def test_claude_5_deep_think_uses_only_the_explicit_scratchpad() -> None:
    provider, messages = _provider(
        [
            _response(_tool_block({"thoughts": "bounded"}), stop_reason="tool_use"),
            _response(SimpleNamespace(type="text", text="final")),
        ]
    )
    provider._default_model = "claude-sonnet-5"

    provider.complete_with_thinking("system", "user", temperature=0.4)

    assert all("temperature" not in request for request in messages.calls)
    assert all(request["thinking"] == {"type": "disabled"} for request in messages.calls)


def test_anthropic_deep_think_fails_closed_on_invalid_tool_input() -> None:
    provider, _ = _provider([_response(_tool_block({"unexpected": "shape"}))])

    with pytest.raises(ProviderError, match="Invalid deep_think arguments"):
        provider.complete_with_thinking("system", "user")


def test_anthropic_deep_think_fails_when_required_call_is_ignored() -> None:
    provider, _ = _provider([_response(SimpleNamespace(type="text", text="uncaptured"))])

    with pytest.raises(ProviderError, match="did not honor required deep_think"):
        provider.complete_with_thinking("system", "user")


def test_anthropic_deep_think_fails_closed_at_turn_limit() -> None:
    provider, _ = _provider(
        [
            _response(_tool_block({"thoughts": "first turn"}, "toolu-1")),
            _response(_tool_block({"thoughts": "still working"}, "toolu-2")),
        ]
    )

    with pytest.raises(ProviderError, match="exceeded 1 deep_think tool turns"):
        provider.complete_with_thinking("system", "user", max_tool_turns=1)


def test_anthropic_allows_final_answer_after_last_tool_turn() -> None:
    provider, messages = _provider(
        [
            _response(_tool_block({"thoughts": "bounded thought"})),
            _response(SimpleNamespace(type="text", text="final")),
        ]
    )

    result = provider.complete_with_thinking("system", "user", max_tool_turns=1)

    assert result.text == "final"
    assert result.thinking_stream == ["bounded thought"]
    assert len(messages.calls) == 2


def test_anthropic_advertises_native_thinking_support() -> None:
    provider, _ = _provider([])
    assert provider.supports_thinking_stream is True
