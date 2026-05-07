from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.agents.provider_bridge import RuntimeBridgeClient, wrap_runtime_session_client
from autocontext.config.settings import AppSettings
from autocontext.loop.stage_types import GenerationContext
from autocontext.loop.stages import stage_agent_generation
from autocontext.prompts.templates import build_prompt_bundle
from autocontext.runtimes.base import AgentOutput
from autocontext.scenarios.base import Observation
from autocontext.session.runtime_events import RuntimeSessionEventStore, RuntimeSessionEventType
from autocontext.session.runtime_session_ids import runtime_session_id_for_run


class _DeterministicAgentRuntime:
    name = "DeterministicAgentRuntime"

    def __init__(self) -> None:
        self._client = DeterministicDevClient()

    def generate(self, prompt: str) -> AgentOutput:
        response = self._client.generate(
            model="runtime-model",
            prompt=prompt,
            max_tokens=4096,
            temperature=0.0,
        )
        return AgentOutput(
            text=response.text,
            model=response.usage.model,
            metadata={"prompt_length": len(prompt)},
        )


class _FailingAgentRuntime:
    name = "FailingAgentRuntime"

    def generate(self, prompt: str) -> AgentOutput:
        del prompt
        raise RuntimeError("runtime down")


class _ClosableAgentRuntime(_DeterministicAgentRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _PromptCapturingAgentRuntime:
    name = "PromptCapturingAgentRuntime"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> AgentOutput:
        self.prompts.append(prompt)
        return AgentOutput(text="captured", model="runtime-model", metadata={})


def _prompts():
    return build_prompt_bundle(
        scenario_rules="Capture the flag while preserving defense.",
        strategy_interface='{"aggression": float, "defense": float, "path_bias": float}',
        evaluation_criteria="Score valid balanced strategies.",
        previous_summary="best: 0.0",
        observation=Observation(narrative="A compact grid is visible.", state={}, constraints=[]),
        current_playbook="Keep a defensive anchor.",
        available_tools="",
    )


def test_orchestrator_records_run_scoped_runtime_session_for_runtime_bridge(tmp_path: Path) -> None:
    settings = AppSettings(agent_provider="deterministic", db_path=tmp_path / "events.db")
    orchestrator = AgentOrchestrator(
        client=RuntimeBridgeClient(_DeterministicAgentRuntime()),
        settings=settings,
    )
    scenario = MagicMock()
    scenario.describe_rules.return_value = "Capture the flag while preserving defense."
    scenario.validate_actions.return_value = (True, "")
    ctx = GenerationContext(
        run_id="run-abc",
        scenario_name="grid_ctf",
        scenario=scenario,
        generation=1,
        settings=settings,
        previous_best=0.0,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="",
        replay_narrative="",
        prompts=_prompts(),
        strategy_interface='{"aggression": float, "defense": float, "path_bias": float}',
    )
    artifacts = MagicMock()
    artifacts.persist_tools.return_value = []

    result = stage_agent_generation(ctx, orchestrator=orchestrator, artifacts=artifacts, sqlite=MagicMock())

    assert result.outputs is not None
    assert result.outputs.strategy
    store = RuntimeSessionEventStore(settings.db_path)
    try:
        log = store.load(runtime_session_id_for_run("run-abc"))
    finally:
        store.close()

    assert log is not None
    assert log.metadata["runId"] == "run-abc"
    assert log.metadata["scenario"] == "grid_ctf"
    prompt_roles = {
        event.payload.get("role")
        for event in log.events
        if event.event_type == RuntimeSessionEventType.PROMPT_SUBMITTED
    }
    assert {"competitor", "translator", "analyst", "coach", "architect"}.issubset(prompt_roles)
    assistant_events = [
        event for event in log.events if event.event_type == RuntimeSessionEventType.ASSISTANT_MESSAGE
    ]
    assert assistant_events
    assert all(event.payload.get("metadata", {}).get("runtimeSessionId") == log.session_id for event in assistant_events)


def test_resolve_role_runtime_records_run_scoped_runtime_session(tmp_path: Path, monkeypatch) -> None:
    from autocontext.cli_role_runtime import resolve_role_runtime

    class _ResolvedOrchestrator:
        def resolve_role_execution(self, *args, **kwargs):
            del args, kwargs
            return RuntimeBridgeClient(_DeterministicAgentRuntime()), "runtime-model"

    monkeypatch.setattr(
        "autocontext.cli_role_runtime.AgentOrchestrator.from_settings",
        lambda *args, **kwargs: _ResolvedOrchestrator(),
    )
    settings = AppSettings(agent_provider="deterministic", db_path=tmp_path / "events.db")

    provider, model = resolve_role_runtime(
        settings,
        role="competitor",
        scenario_name="grid_ctf",
        run_id="solve-123",
        sqlite=object(),
        artifacts=object(),
    )

    result = provider.complete(
        system_prompt="Complete the task precisely.",
        user_prompt="Describe your strategy as JSON.",
        model=model,
    )

    assert result.text
    store = RuntimeSessionEventStore(settings.db_path)
    try:
        log = store.load(runtime_session_id_for_run("solve-123"))
    finally:
        store.close()

    assert log is not None
    assert log.metadata["runId"] == "solve-123"
    assert log.metadata["scenario"] == "grid_ctf"
    assert [event.event_type for event in log.events] == [
        RuntimeSessionEventType.PROMPT_SUBMITTED,
        RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]
    assert log.events[0].payload["role"] == "competitor"


def test_runtime_session_recording_preserves_runtime_failure_semantics(tmp_path: Path) -> None:
    from autocontext.session import RuntimeSession

    store = RuntimeSessionEventStore(tmp_path / "events.db")
    try:
        session = RuntimeSession.create(
            session_id=runtime_session_id_for_run("run-failing"),
            goal="autoctx run failing",
            event_store=store,
        )
        client = wrap_runtime_session_client(
            RuntimeBridgeClient(_FailingAgentRuntime()),
            session=session,
            role="competitor",
            cwd="/workspace",
        )

        with pytest.raises(RuntimeError, match="runtime down"):
            client.generate(
                model="runtime-model",
                prompt="Describe your strategy.",
                max_tokens=4096,
                temperature=0.0,
            )

        log = store.load(session.session_id)
    finally:
        store.close()

    assert log is not None
    assert [event.event_type for event in log.events] == [
        RuntimeSessionEventType.PROMPT_SUBMITTED,
        RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]
    assert log.events[1].payload["isError"] is True
    assert log.events[1].payload["error"] == "runtime down"


def test_runtime_session_recording_closes_budgeted_runtime_client(tmp_path: Path, monkeypatch) -> None:
    from autocontext.session import RuntimeSession

    runtime = _ClosableAgentRuntime()
    role_client = RuntimeBridgeClient(runtime)
    monkeypatch.setattr("autocontext.agents.provider_bridge.create_role_client", lambda *args, **kwargs: role_client)
    monkeypatch.setattr("autocontext.agents.role_runtime_overrides.time.monotonic", lambda: 100.0)

    settings = AppSettings(
        agent_provider="pi-rpc",
        db_path=tmp_path / "events.db",
        pi_timeout=900.0,
        pi_rpc_persistent=True,
    )
    orchestrator = AgentOrchestrator(client=DeterministicDevClient(), settings=settings)
    store = RuntimeSessionEventStore(settings.db_path)
    try:
        orchestrator._active_runtime_session = RuntimeSession.create(
            session_id=runtime_session_id_for_run("run-budgeted"),
            goal="autoctx budgeted runtime",
            event_store=store,
        )

        with orchestrator._use_role_runtime(
            "analyst",
            orchestrator.analyst,
            generation=1,
            scenario_name="grid_ctf",
            generation_deadline=520.0,
        ):
            assert orchestrator.analyst.runtime.client is not role_client
            orchestrator.analyst.runtime.client.generate(
                model="runtime-model",
                prompt="describe the strategy",
                max_tokens=4096,
                temperature=0.0,
                role="analyst",
            )
            assert runtime.closed is False
    finally:
        store.close()

    assert runtime.closed is True
    assert id(role_client) not in orchestrator._disposable_client_ids


def test_runtime_session_recording_logs_hook_mutated_provider_prompt(tmp_path: Path) -> None:
    from autocontext.extensions import HookBus, HookedLanguageModelClient, HookEvents, HookResult
    from autocontext.session import RuntimeSession

    runtime = _PromptCapturingAgentRuntime()
    hook_bus = HookBus()

    def mutate_prompt(event):
        return HookResult(payload={"prompt": f"{event.payload['prompt']} plus hook"})

    hook_bus.on(HookEvents.BEFORE_PROVIDER_REQUEST, mutate_prompt)
    store = RuntimeSessionEventStore(tmp_path / "events.db")
    try:
        session = RuntimeSession.create(
            session_id=runtime_session_id_for_run("run-hooked"),
            goal="autoctx hooked runtime",
            event_store=store,
        )
        client = HookedLanguageModelClient(
            RuntimeBridgeClient(runtime),
            hook_bus,
            provider_name="runtime:analyst",
        )
        recorded_client = wrap_runtime_session_client(
            client,
            session=session,
            role="analyst",
            cwd="/workspace",
        )

        response = recorded_client.generate(
            model="runtime-model",
            prompt="original prompt",
            max_tokens=4096,
            temperature=0.0,
            role="analyst",
        )
        log = store.load(session.session_id)
    finally:
        store.close()

    assert response.text == "captured"
    assert runtime.prompts == ["original prompt plus hook"]
    assert log is not None
    prompt_events = [event for event in log.events if event.event_type == RuntimeSessionEventType.PROMPT_SUBMITTED]
    assert prompt_events[0].payload["prompt"] == "original prompt plus hook"
