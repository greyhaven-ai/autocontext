"""Stage 2 of ERP-67 — the transport seam for structural role isolation.

`SubagentTask` gains an optional `system` field. When it is set, `run_task`
delivers the task as a system turn + a user turn via `generate_multiturn`
(so role-capable backends keep untrusted content out of the system turn). When
it is empty — every call site today — `run_task` uses the single-prompt
`generate` path exactly as before, so behaviour is unchanged until a later
stage populates `system`.
"""

from __future__ import annotations

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.subagent import SubagentRuntime, SubagentTask
from autocontext.harness.core.types import ModelResponse, RoleUsage


class _RecordingClient(LanguageModelClient):
    """Captures which transport method was called and with what."""

    def __init__(self) -> None:
        self.generate_calls: list[dict[str, object]] = []
        self.multiturn_calls: list[dict[str, object]] = []

    def generate(self, *, model, prompt, max_tokens, temperature, role=""):  # type: ignore[no-untyped-def]
        self.generate_calls.append({"prompt": prompt, "role": role})
        return ModelResponse(text="single", usage=RoleUsage(0, 0, 0, model))

    def generate_multiturn(self, *, model, system, messages, max_tokens, temperature, role=""):  # type: ignore[no-untyped-def]
        self.multiturn_calls.append({"system": system, "messages": messages, "role": role})
        return ModelResponse(text="multi", usage=RoleUsage(0, 0, 0, model))


def _task(**overrides: object) -> SubagentTask:
    base: dict[str, object] = {
        "role": "competitor",
        "model": "m",
        "prompt": "USER-BODY untrusted reference here",
        "max_tokens": 100,
        "temperature": 0.0,
    }
    base.update(overrides)
    return SubagentTask(**base)  # type: ignore[arg-type]


def test_task_without_system_uses_single_prompt_path() -> None:
    client = _RecordingClient()
    exec_result = SubagentRuntime(client).run_task(_task())
    assert len(client.generate_calls) == 1
    assert not client.multiturn_calls
    assert client.generate_calls[0]["prompt"] == "USER-BODY untrusted reference here"
    assert exec_result.content == "single"


def test_task_with_system_uses_multiturn_isolated_path() -> None:
    client = _RecordingClient()
    exec_result = SubagentRuntime(client).run_task(_task(system="SYSTEM trusted instructions", prompt="UNTRUSTED body"))
    assert len(client.multiturn_calls) == 1
    assert not client.generate_calls
    call = client.multiturn_calls[0]
    assert call["system"] == "SYSTEM trusted instructions"
    assert call["messages"] == [{"role": "user", "content": "UNTRUSTED body"}]
    assert exec_result.content == "multi"


def test_role_and_metadata_preserved_on_both_paths() -> None:
    client = _RecordingClient()
    single = SubagentRuntime(client).run_task(_task(role="analyst"))
    multi = SubagentRuntime(client).run_task(_task(role="coach", system="S"))
    assert single.role == "analyst"
    assert multi.role == "coach"
    assert client.generate_calls[0]["role"] == "analyst"
    assert client.multiturn_calls[0]["role"] == "coach"


def test_system_defaults_empty_so_existing_call_sites_are_unchanged() -> None:
    # Positional construction (as existing call sites do) must still work and
    # leave system empty → single-prompt path.
    task = SubagentTask("competitor", "m", "p", 100, 0.0)
    assert task.system == ""
    client = _RecordingClient()
    SubagentRuntime(client).run_task(task)
    assert len(client.generate_calls) == 1 and not client.multiturn_calls
