"""AC-928: Anthropic honors ``output_schema`` via a forced tool call.

Anthropic has no ``response_format``. The equivalent is declaring a tool whose
``input_schema`` is the role schema and pinning ``tool_choice`` to it, so the
model fills the schema in a ``tool_use`` block instead of writing prose.

The contract these pin is the same one ``openai_compat`` keeps: ``constrained``
describes what actually happened, never what was requested. A caller reads that
flag to decide between the strict parser and the markdown scrape, so a result
that claims ``constrained=True`` without a validated payload behind it would
route unvalidated text into a parser that assumes otherwise.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.providers.anthropic import AnthropicProvider
from autocontext.providers.base import OutputSchema

PAYLOAD = {"answer": "ok"}


class _Messages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.calls.append(request)
        return self.response


def _tool_use_block(name: str = "answer", payload: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(type="tool_use", name=name, input=payload if payload is not None else PAYLOAD)


def _text_block(text: str) -> Any:
    return SimpleNamespace(type="text", text=text)


def _response(blocks: list[Any], stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
        stop_reason=stop_reason,
    )


def _provider(response: Any) -> tuple[AnthropicProvider, _Messages]:
    messages = _Messages(response)
    provider = object.__new__(AnthropicProvider)
    provider._default_model = "claude-stub"
    provider._client = SimpleNamespace(messages=messages)
    return provider, messages


def _schema() -> OutputSchema:
    return OutputSchema(
        name="answer",
        schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )


def test_schema_forces_the_tool_and_returns_its_payload() -> None:
    provider, messages = _provider(_response([_tool_use_block()], stop_reason="tool_use"))

    result = provider.complete("sys", "user", output_schema=_schema())

    request = messages.calls[0]
    assert request["tools"][0]["name"] == "answer"
    assert request["tools"][0]["input_schema"] == _schema().schema
    # Pinned, not "auto": an unforced tool is one the model may decline to call,
    # which would silently produce prose on the path a strict parser reads.
    assert request["tool_choice"] == {"type": "tool", "name": "answer"}
    assert result.constrained is True
    assert json.loads(result.text) == PAYLOAD


def test_no_schema_sends_no_tool_and_reports_unconstrained() -> None:
    """The default path must be byte-identical to pre-AC-928 behavior.

    Anthropic is the shipped default provider, so a change that leaked into
    unschema'd calls would alter every existing user's runs.
    """
    provider, messages = _provider(_response([_text_block("plain prose")]))

    result = provider.complete("sys", "user")

    assert "tools" not in messages.calls[0]
    assert "tool_choice" not in messages.calls[0]
    assert result.constrained is False
    assert result.text == "plain prose"


def test_missing_tool_use_block_degrades_to_unconstrained(caplog: pytest.LogCaptureFixture) -> None:
    """The failure that would otherwise be invisible.

    If the model answers in prose despite the forced tool, reporting
    ``constrained=True`` would hand that prose to a parser that assumes JSON.
    Fall back to the text and say so.
    """
    provider, _ = _provider(_response([_text_block("I would rather explain.")]))

    with caplog.at_level(logging.WARNING):
        result = provider.complete("sys", "user", output_schema=_schema())

    assert result.constrained is False
    assert result.text == "I would rather explain."
    assert any("no tool_use block" in record.message for record in caplog.records)


def test_tool_use_block_for_a_different_tool_is_not_accepted() -> None:
    """Name-matched on purpose.

    Taking the first tool_use block regardless of name would accept a payload
    shaped by some other schema and label it constrained against this one.
    """
    provider, _ = _provider(_response([_tool_use_block(name="something_else", payload={"other": 1})]))

    result = provider.complete("sys", "user", output_schema=_schema())

    assert result.constrained is False


def test_tool_use_block_is_found_after_leading_text() -> None:
    """Anthropic may emit a text block before the tool call.

    The pre-AC-928 reader took ``content[0]`` and stopped, so a preamble would
    have hidden the payload entirely.
    """
    provider, _ = _provider(_response([_text_block("Let me structure that."), _tool_use_block()]))

    result = provider.complete("sys", "user", output_schema=_schema())

    assert result.constrained is True
    assert json.loads(result.text) == PAYLOAD


def test_unconstrained_text_is_found_after_a_non_text_block() -> None:
    """Same defect on the unconstrained path, which also read only content[0]."""
    provider, _ = _provider(_response([SimpleNamespace(type="thinking"), _text_block("the answer")]))

    result = provider.complete("sys", "user")

    assert result.text == "the answer"


def test_a_real_analyst_run_forces_the_tool_on_the_anthropic_wire() -> None:
    """The capability has to FIRE, not merely exist.

    The tests above drive ``complete()`` directly, which cannot catch a role
    path that never passes a schema down -- exactly the AC-913 near-miss where
    constrained decoding compiled, passed, and would never have run. This goes
    through the real AnalystRunner and asserts on the request that reached the
    Anthropic client, so the whole chain is covered by something executable.
    """
    from autocontext.agents.analyst import AnalystRunner
    from autocontext.agents.provider_bridge import ProviderBridgeClient
    from autocontext.harness.core.subagent import SubagentRuntime

    payload = {"findings": ["f"], "root_causes": ["r"], "recommendations": ["rec"]}
    provider, messages = _provider(_response([_tool_use_block(name="analyst_output", payload=payload)], stop_reason="tool_use"))

    runtime = SubagentRuntime(ProviderBridgeClient(provider))
    execution = AnalystRunner(runtime, model="claude-stub").run("analyze this", system="you are the analyst")

    request = messages.calls[0]
    assert request["tool_choice"] == {"type": "tool", "name": "analyst_output"}
    assert request["tools"][0]["name"] == "analyst_output"
    # And the loop believes it, so parsing takes the strict path rather than
    # scraping markdown out of a JSON payload.
    assert execution.metadata.get("constrained") is True
