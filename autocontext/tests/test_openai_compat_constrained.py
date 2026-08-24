from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.providers.base import OutputSchema, ProviderError
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


def _response(text: str = '{"answer":"ok"}') -> Any:
    choice = SimpleNamespace(
        message=SimpleNamespace(content=text),
        finish_reason="stop",
    )
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage)


def _provider(outcomes: list[Any]) -> tuple[OpenAICompatibleProvider, _Completions]:
    completions = _Completions(outcomes)
    provider = object.__new__(OpenAICompatibleProvider)
    provider._default_model = "stub"
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


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


def test_transient_failure_does_not_retry_without_schema() -> None:
    provider, completions = _provider([TimeoutError("timed out"), _response("unconstrained")])

    with pytest.raises(ProviderError, match="timed out"):
        provider.complete("system", "user", output_schema=_schema())

    assert len(completions.calls) == 1
    assert "response_format" in completions.calls[0]


def test_explicit_response_format_rejection_retries_unconstrained() -> None:
    provider, completions = _provider(
        [_RejectedRequest("response_format json_schema is not supported"), _response("fallback")]
    )

    result = provider.complete("system", "user", output_schema=_schema())

    assert result.text == "fallback"
    assert result.constrained is False
    assert len(completions.calls) == 2
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]


def test_single_dispatch_mode_does_not_redispatch_compatibility_fallback() -> None:
    provider, completions = _provider(
        [_RejectedRequest("response_format json_schema is not supported"), _response("fallback")]
    )
    provider._single_dispatch = True

    with pytest.raises(ProviderError, match="response_format"):
        provider.complete("system", "user", output_schema=_schema())

    assert len(completions.calls) == 1


def test_successful_schema_request_is_reported_as_constrained() -> None:
    provider, completions = _provider([_response()])

    result = provider.complete("system", "user", output_schema=_schema())

    assert result.constrained is True
    assert completions.calls[0]["response_format"]["json_schema"]["strict"] is True


def test_gpt_56_ordinary_completion_disables_implicit_reasoning() -> None:
    provider, completions = _provider([_response("plain")])
    provider._default_model = "gpt-5.6-terra"

    provider.complete("system", "user")

    assert completions.calls[0]["reasoning_effort"] == "none"
    assert completions.calls[0]["max_completion_tokens"] == 4096
    assert "max_tokens" not in completions.calls[0]


def test_gpt_56_negotiates_unsupported_reasoning_control() -> None:
    provider, completions = _provider(
        [_RejectedRequest("unknown parameter reasoning_effort"), _response("portable")]
    )
    provider._default_model = "gpt-5.6-terra"

    result = provider.complete("system", "user")

    assert result.text == "portable"
    assert completions.calls[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in completions.calls[1]


def test_gpt_56_negotiates_legacy_output_token_field() -> None:
    provider, completions = _provider(
        [_RejectedRequest("unknown parameter max_completion_tokens"), _response("portable")]
    )
    provider._default_model = "gpt-5.6-terra"

    result = provider.complete("system", "user")

    assert result.text == "portable"
    assert completions.calls[0]["max_completion_tokens"] == 4096
    assert completions.calls[1]["max_tokens"] == 4096
    assert "max_completion_tokens" not in completions.calls[1]


@pytest.mark.parametrize("model", ["gemini-3.6-flash", "google/gemini-3.5-flash-lite"])
def test_new_gemini_models_omit_ignored_temperature(model: str) -> None:
    provider, completions = _provider([_response("plain")])
    provider._default_model = model

    provider.complete("system", "user", temperature=0.4)

    assert "temperature" not in completions.calls[0]
