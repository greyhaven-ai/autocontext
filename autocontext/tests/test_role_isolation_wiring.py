"""ERP-67 Stage 2b — role runners forward the trusted `system` turn.

When the orchestrator splits a role prompt (structural isolation on), it calls
`runner.run(prompt=untrusted, system=trusted)`. Each runner must forward that
into `SubagentTask.system`, so the runtime routes it as a system turn via
`generate_multiturn`. With no `system` (the default, and today's call sites) the
runner stays on the single-prompt path — byte-identical behaviour.
"""

from __future__ import annotations

import contextlib

from autocontext.agents.analyst import AnalystRunner
from autocontext.agents.architect import ArchitectRunner
from autocontext.agents.coach import CoachRunner
from autocontext.agents.competitor import CompetitorRunner
from autocontext.agents.subagent_runtime import SubagentRuntime
from autocontext.extensions.hooks import HookBus
from autocontext.extensions.llm import HookedLanguageModelClient
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage


class _RecordingClient(LanguageModelClient):
    def __init__(self) -> None:
        self.generate_calls: list[dict[str, object]] = []
        self.multiturn_calls: list[dict[str, object]] = []

    def generate(self, *, model, prompt, max_tokens, temperature, role=""):  # type: ignore[no-untyped-def]
        self.generate_calls.append({"prompt": prompt, "role": role})
        return ModelResponse(text="single", usage=RoleUsage(0, 0, 0, model))

    def generate_multiturn(self, *, model, system, messages, max_tokens, temperature, role=""):  # type: ignore[no-untyped-def]
        self.multiturn_calls.append({"system": system, "messages": messages, "role": role})
        return ModelResponse(text="multi", usage=RoleUsage(0, 0, 0, model))


def _runtime() -> tuple[SubagentRuntime, _RecordingClient]:
    client = _RecordingClient()
    return SubagentRuntime(client), client


def test_analyst_forwards_system_as_isolated_turn() -> None:
    runtime, client = _runtime()
    AnalystRunner(runtime, "m").run("UNTRUSTED body", system="TRUSTED system")
    assert not client.generate_calls
    call = client.multiturn_calls[0]
    assert call["system"] == "TRUSTED system"
    assert call["messages"] == [{"role": "user", "content": "UNTRUSTED body"}]
    assert call["role"] == "analyst"


def test_architect_forwards_system_as_isolated_turn() -> None:
    runtime, client = _runtime()
    ArchitectRunner(runtime, "m").run("UNTRUSTED body", system="TRUSTED system")
    call = client.multiturn_calls[0]
    assert call["system"] == "TRUSTED system"
    assert call["messages"] == [{"role": "user", "content": "UNTRUSTED body"}]
    assert call["role"] == "architect"


def test_competitor_forwards_system_and_keeps_tool_context_in_user_turn() -> None:
    runtime, client = _runtime()
    CompetitorRunner(runtime, "m").run("UNTRUSTED body", tool_context="TOOLS", system="TRUSTED system")
    call = client.multiturn_calls[0]
    assert call["system"] == "TRUSTED system"
    # tool context is appended to the user turn, never the system turn.
    user = call["messages"][0]["content"]
    assert "UNTRUSTED body" in user and "TOOLS" in user
    assert "TRUSTED system" not in user
    assert call["role"] == "competitor"


def test_runners_without_system_stay_on_single_prompt_path() -> None:
    runtime, client = _runtime()
    AnalystRunner(runtime, "m").run("just a flat prompt")
    assert len(client.generate_calls) == 1
    assert not client.multiturn_calls
    assert client.generate_calls[0]["prompt"] == "just a flat prompt"
    assert client.generate_calls[0]["role"] == "analyst"


class _CapableClient(_RecordingClient):
    supports_structural_isolation = True


class _FakeOrch:
    """Minimal orchestrator surface build_role_handler needs."""

    def __init__(self, client: LanguageModelClient) -> None:
        runtime = SubagentRuntime(client)
        self.competitor = CompetitorRunner(runtime, "m")
        self.analyst = AnalystRunner(runtime, "m")
        self.architect = ArchitectRunner(runtime, "m")

    @contextlib.contextmanager
    def _use_role_runtime(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        yield


def _handler(client: LanguageModelClient):  # type: ignore[no-untyped-def]
    from autocontext.agents.pipeline_adapter import build_role_handler

    return build_role_handler(
        _FakeOrch(client),  # type: ignore[arg-type]
        system_map={"analyst": "SYS trusted"},
        flat_map={"analyst": "FLAT legacy prompt"},
    )


def test_handler_isolates_only_for_capable_clients() -> None:
    client = _CapableClient()
    _handler(client)("analyst", "UNTRUSTED user turn", {})
    # capable backend → real system/user split, untrusted stays in the user turn.
    assert client.multiturn_calls and not client.generate_calls
    call = client.multiturn_calls[0]
    assert call["system"] == "SYS trusted"
    assert call["messages"] == [{"role": "user", "content": "UNTRUSTED user turn"}]


def test_handler_falls_back_to_flat_for_incapable_clients() -> None:
    client = _RecordingClient()  # supports_structural_isolation defaults False
    _handler(client)("analyst", "UNTRUSTED user turn", {})
    # incapable backend → exact legacy flat prompt, no system turn, no reordering.
    assert client.generate_calls and not client.multiturn_calls
    assert client.generate_calls[0]["prompt"] == "FLAT legacy prompt"


def test_coach_forwards_system_as_isolated_turn() -> None:
    runtime, client = _runtime()
    CoachRunner(runtime, "m").run("UNTRUSTED body", system="TRUSTED system")
    call = client.multiturn_calls[0]
    assert call["system"] == "TRUSTED system"
    assert call["messages"] == [{"role": "user", "content": "UNTRUSTED body"}]
    assert call["role"] == "coach"


def test_coach_without_system_stays_flat() -> None:
    runtime, client = _runtime()
    CoachRunner(runtime, "m").run("flat coach prompt")
    assert client.generate_calls and not client.multiturn_calls
    assert client.generate_calls[0]["prompt"] == "flat coach prompt"


def test_hook_wrapper_forwards_capability_so_production_isolates() -> None:
    # GenerationRunner always wraps the client for hooks; the wrapper must expose
    # the inner client's capability or every production role falls back to flat.
    capable = HookedLanguageModelClient(_CapableClient(), HookBus())
    assert capable.supports_structural_isolation is True
    incapable = HookedLanguageModelClient(_RecordingClient(), HookBus())
    assert incapable.supports_structural_isolation is False


def test_agent_sdk_client_advertises_structural_isolation() -> None:
    from autocontext.agents.agent_sdk_client import AgentSdkClient

    # Routes system via ClaudeAgentOptions.system_prompt → genuinely role-capable.
    assert AgentSdkClient.supports_structural_isolation is True
