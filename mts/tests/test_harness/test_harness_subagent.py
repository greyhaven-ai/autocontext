"""Tests for mts.harness.core.subagent — SubagentRuntime, SubagentTask."""

from __future__ import annotations

import re

from mts.harness.core.llm_client import LanguageModelClient
from mts.harness.core.subagent import SubagentRuntime, SubagentTask
from mts.harness.core.types import ModelResponse, RoleExecution, RoleUsage


class FakeClient(LanguageModelClient):
    def generate(self, *, model: str, prompt: str, max_tokens: int, temperature: float, role: str = "") -> ModelResponse:
        return ModelResponse(
            text="  fake output  ",
            usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=50, model=model),
        )


def test_subagent_task_fields() -> None:
    task = SubagentTask(role="analyst", model="m", prompt="p", max_tokens=100, temperature=0.5)
    assert task.role == "analyst"
    assert task.model == "m"
    assert task.prompt == "p"
    assert task.max_tokens == 100
    assert task.temperature == 0.5


def test_subagent_runtime_calls_client() -> None:
    client = FakeClient()
    runtime = SubagentRuntime(client)
    task = SubagentTask(role="competitor", model="m", prompt="p", max_tokens=100, temperature=0.0)
    result = runtime.run_task(task)
    assert isinstance(result, RoleExecution)


def test_subagent_runtime_returns_role_execution() -> None:
    client = FakeClient()
    runtime = SubagentRuntime(client)
    task = SubagentTask(role="coach", model="m", prompt="p", max_tokens=100, temperature=0.0)
    result = runtime.run_task(task)
    assert result.role == "coach"
    assert result.status == "completed"


def test_subagent_runtime_generates_subagent_id() -> None:
    client = FakeClient()
    runtime = SubagentRuntime(client)
    task = SubagentTask(role="analyst", model="m", prompt="p", max_tokens=100, temperature=0.0)
    result = runtime.run_task(task)
    assert re.match(r"analyst-[0-9a-f]+", result.subagent_id)


def test_subagent_runtime_strips_whitespace() -> None:
    client = FakeClient()
    runtime = SubagentRuntime(client)
    task = SubagentTask(role="analyst", model="m", prompt="p", max_tokens=100, temperature=0.0)
    result = runtime.run_task(task)
    assert result.content == "fake output"  # leading/trailing whitespace stripped
