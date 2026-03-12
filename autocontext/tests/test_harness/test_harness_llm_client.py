"""Tests for autocontext.harness.core.llm_client — LanguageModelClient base class."""

from __future__ import annotations

import pytest

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage


def test_base_client_generate_raises() -> None:
    client = LanguageModelClient()
    with pytest.raises(NotImplementedError):
        client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0)


def test_base_client_multiturn_concatenates() -> None:
    """Default multiturn should fall back to generate with concatenated messages."""
    calls: list[dict[str, object]] = []

    class Spy(LanguageModelClient):
        def generate(self, *, model: str, prompt: str, max_tokens: int, temperature: float, role: str = "") -> ModelResponse:
            calls.append({"model": model, "prompt": prompt})
            return ModelResponse(
                text="ok",
                usage=RoleUsage(input_tokens=1, output_tokens=1, latency_ms=1, model=model),
            )

    client = Spy()
    result = client.generate_multiturn(
        model="m",
        system="sys",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "bye"},
        ],
        max_tokens=100,
        temperature=0.0,
    )
    assert result.text == "ok"
    assert len(calls) == 1
    # Should concatenate system + user messages
    assert "sys" in str(calls[0]["prompt"])
    assert "hello" in str(calls[0]["prompt"])
    assert "bye" in str(calls[0]["prompt"])


def test_base_client_accepts_role_param() -> None:
    """The role parameter should flow through to generate."""
    received_role: list[str] = []

    class Spy(LanguageModelClient):
        def generate(self, *, model: str, prompt: str, max_tokens: int, temperature: float, role: str = "") -> ModelResponse:
            received_role.append(role)
            return ModelResponse(
                text="ok",
                usage=RoleUsage(input_tokens=1, output_tokens=1, latency_ms=1, model=model),
            )

    client = Spy()
    client.generate(model="m", prompt="p", max_tokens=100, temperature=0.0, role="analyst")
    assert received_role == ["analyst"]
