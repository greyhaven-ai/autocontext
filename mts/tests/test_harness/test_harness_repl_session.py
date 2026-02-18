"""Tests for mts.harness.repl.session — RlmSession, make_llm_batch."""

from __future__ import annotations

from mts.harness.core.llm_client import LanguageModelClient
from mts.harness.core.types import ModelResponse, RoleExecution, RoleUsage
from mts.harness.repl.session import RlmSession, make_llm_batch
from mts.harness.repl.worker import ReplWorker


class FakeClient(LanguageModelClient):
    """Client that returns code blocks or finalization."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def generate(self, *, model: str, prompt: str, max_tokens: int, temperature: float, role: str = "") -> ModelResponse:
        text = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return ModelResponse(text=text, usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model=model))

    def generate_multiturn(
        self, *, model: str, system: str, messages: list[dict[str, str]], max_tokens: int, temperature: float, role: str = ""
    ) -> ModelResponse:
        text = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return ModelResponse(text=text, usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model=model))


def test_session_runs_single_turn() -> None:
    client = FakeClient(['<code>\nanswer["content"] = "done"\nanswer["ready"] = True\n</code>'])
    worker = ReplWorker()
    session = RlmSession(client=client, worker=worker, role="analyst", model="m", system_prompt="test")
    result = session.run()
    assert isinstance(result, RoleExecution)
    assert result.content == "done"


def test_session_stops_when_ready() -> None:
    client = FakeClient([
        '<code>\nx = 1\n</code>',
        '<code>\nanswer["content"] = "final"\nanswer["ready"] = True\n</code>',
    ])
    worker = ReplWorker()
    session = RlmSession(client=client, worker=worker, role="analyst", model="m", system_prompt="test")
    result = session.run()
    assert result.content == "final"
    assert len(session.execution_history) == 2


def test_session_respects_max_turns() -> None:
    client = FakeClient(['<code>\nx = 1\n</code>'] * 20)
    worker = ReplWorker()
    session = RlmSession(client=client, worker=worker, role="analyst", model="m", system_prompt="test", max_turns=3)
    result = session.run()
    assert result.status == "truncated"
    assert len(session.execution_history) == 3


def test_session_feeds_stdout_back() -> None:
    """REPL stdout should be fed back as the next user message."""
    messages_seen: list[list[dict[str, str]]] = []

    class SpyClient(LanguageModelClient):
        def __init__(self) -> None:
            self._call = 0

        def generate_multiturn(
            self, *, model: str, system: str, messages: list[dict[str, str]], max_tokens: int, temperature: float, role: str = ""
        ) -> ModelResponse:
            messages_seen.append(list(messages))
            self._call += 1
            if self._call == 1:
                text = '<code>\nprint("hello from repl")\n</code>'
            else:
                text = '<code>\nanswer["ready"] = True\n</code>'
            return ModelResponse(text=text, usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model=model))

    client = SpyClient()
    worker = ReplWorker()
    session = RlmSession(client=client, worker=worker, role="analyst", model="m", system_prompt="test")
    session.run()
    # Second call should have the stdout feedback in messages
    assert len(messages_seen) >= 2
    second_call_msgs = messages_seen[1]
    # Last user message should contain the stdout
    user_msgs = [m for m in second_call_msgs if m["role"] == "user"]
    assert any("hello from repl" in m["content"] for m in user_msgs)


def test_session_handles_code_errors() -> None:
    client = FakeClient([
        '<code>\n1/0\n</code>',
        '<code>\nanswer["ready"] = True\n</code>',
    ])
    worker = ReplWorker()
    session = RlmSession(client=client, worker=worker, role="analyst", model="m", system_prompt="test")
    result = session.run()
    assert session.execution_history[0].error is not None
    assert result.status == "completed"


def test_session_nudges_no_code_response() -> None:
    """When model doesn't emit code tags, session should nudge it."""
    messages_seen: list[list[dict[str, str]]] = []

    class SpyClient(LanguageModelClient):
        def __init__(self) -> None:
            self._call = 0

        def generate_multiturn(
            self, *, model: str, system: str, messages: list[dict[str, str]], max_tokens: int, temperature: float, role: str = ""
        ) -> ModelResponse:
            messages_seen.append(list(messages))
            self._call += 1
            if self._call == 1:
                text = "I will analyze the data..."  # No code tags
            else:
                text = '<code>\nanswer["ready"] = True\n</code>'
            return ModelResponse(text=text, usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model=model))

    client = SpyClient()
    worker = ReplWorker()
    session = RlmSession(client=client, worker=worker, role="analyst", model="m", system_prompt="test")
    session.run()
    # Second call should have a nudge message
    assert len(messages_seen) >= 2
    second_msgs = messages_seen[1]
    user_msgs = [m for m in second_msgs if m["role"] == "user"]
    assert any("code" in m["content"].lower() for m in user_msgs)


def test_session_returns_role_execution() -> None:
    client = FakeClient(['<code>\nanswer["content"] = "result"\nanswer["ready"] = True\n</code>'])
    worker = ReplWorker()
    session = RlmSession(client=client, worker=worker, role="architect", model="test-model", system_prompt="test")
    result = session.run()
    assert isinstance(result, RoleExecution)
    assert result.role == "architect"
    assert result.usage.model == "test-model"


def test_make_llm_batch_parallel() -> None:
    class CountingClient(LanguageModelClient):
        def __init__(self) -> None:
            self.call_count = 0

        def generate(self, *, model: str, prompt: str, max_tokens: int, temperature: float, role: str = "") -> ModelResponse:
            self.call_count += 1
            return ModelResponse(
                text=f"response to: {prompt}",
                usage=RoleUsage(input_tokens=1, output_tokens=1, latency_ms=1, model=model),
            )

    client = CountingClient()
    batch_fn = make_llm_batch(client, model="m")
    results = batch_fn(["prompt1", "prompt2", "prompt3"])
    assert client.call_count == 3
    assert len(results) == 3


def test_make_llm_batch_collects_results() -> None:
    class EchoClient(LanguageModelClient):
        def generate(self, *, model: str, prompt: str, max_tokens: int, temperature: float, role: str = "") -> ModelResponse:
            return ModelResponse(
                text=f"echo:{prompt}",
                usage=RoleUsage(input_tokens=1, output_tokens=1, latency_ms=1, model=model),
            )

    client = EchoClient()
    batch_fn = make_llm_batch(client, model="m")
    results = batch_fn(["a", "b"])
    assert results == ["echo:a", "echo:b"]


def test_make_llm_batch_empty_input() -> None:
    client = FakeClient([])
    batch_fn = make_llm_batch(client, model="m")
    results = batch_fn([])
    assert results == []
