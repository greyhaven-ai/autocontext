"""Tests for autocontext.harness.core.types — RoleUsage, RoleExecution, ModelResponse."""

from __future__ import annotations

from autocontext.harness.core.types import ModelResponse, RoleExecution, RoleUsage


def test_role_usage_construction() -> None:
    usage = RoleUsage(input_tokens=100, output_tokens=50, latency_ms=200, model="test-model")
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.latency_ms == 200
    assert usage.model == "test-model"


def test_role_usage_slots() -> None:
    assert hasattr(RoleUsage, "__slots__")


def test_role_execution_construction() -> None:
    usage = RoleUsage(input_tokens=10, output_tokens=5, latency_ms=50, model="m")
    exe = RoleExecution(role="analyst", content="hello", usage=usage, subagent_id="analyst-abc", status="completed")
    assert exe.role == "analyst"
    assert exe.content == "hello"
    assert exe.usage is usage
    assert exe.subagent_id == "analyst-abc"
    assert exe.status == "completed"


def test_role_execution_slots() -> None:
    assert hasattr(RoleExecution, "__slots__")


def test_model_response_construction() -> None:
    usage = RoleUsage(input_tokens=10, output_tokens=5, latency_ms=50, model="m")
    resp = ModelResponse(text="output text", usage=usage)
    assert resp.text == "output text"
    assert resp.usage is usage


def test_model_response_slots() -> None:
    assert hasattr(ModelResponse, "__slots__")
