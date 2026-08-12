from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.agents.panel_runtime import (
    PanelLanguageModelClient,
    compare_panel_benchmark,
    panel_config_for_role,
)
from autocontext.config.settings import AppSettings
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleUsage
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.runtimes.base import AgentOutput, AgentRuntime


class _CostRuntime(AgentRuntime):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "CostRuntime"

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        del prompt, system, schema
        self.calls += 1
        return AgentOutput(text="runtime participant", model="runtime-model", cost_usd=0.05)

    def revise(
        self,
        prompt: str,
        previous_output: str,
        feedback: str,
        system: str | None = None,
    ) -> AgentOutput:
        del prompt, previous_output, feedback, system
        return AgentOutput(text="revised", model="runtime-model")


class _FakeProvider(LLMProvider):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model_name: str = "fake",
    ) -> None:
        del api_key, base_url
        self.default_model_name = default_model_name

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        del system_prompt, user_prompt, temperature, max_tokens
        return CompletionResult(text="ok", model=model or self.default_model_name)

    def default_model(self) -> str:
        return self.default_model_name


class _CostProvider(LLMProvider):
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        del system_prompt, user_prompt, temperature, max_tokens
        return CompletionResult(
            text="provider participant",
            model=model or "provider-model",
            usage={"input_tokens": 4, "output_tokens": 5},
            cost_usd=0.42,
        )

    def default_model(self) -> str:
        return "provider-model"


class _FakeClient(LanguageModelClient):
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.calls: list[dict[str, str]] = []

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        del max_tokens, temperature
        self.calls.append({"model": model, "prompt": prompt, "role": role})
        return ModelResponse(
            text=f"{self.prefix}:{model}",
            usage=RoleUsage(input_tokens=3, output_tokens=2, latency_ms=7, model=model),
            metadata={"cost_usd": 0.01},
        )


def test_openrouter_fusion_can_be_configured_as_single_provider_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.providers import openai_compat
    from autocontext.providers.registry import create_provider

    monkeypatch.setattr(openai_compat, "OpenAICompatibleProvider", _FakeProvider)

    provider = create_provider("openrouter", api_key="or-test", model="openrouter/fusion")

    assert provider.default_model() == "openrouter/fusion"


def test_panel_config_for_role_is_opt_in_and_parses_provider_model_pairs() -> None:
    assert panel_config_for_role(AppSettings(), "competitor") is None

    config = panel_config_for_role(
        AppSettings(
            panel_roles="competitor,coach",
            panel_participants="competitor=openai:gpt-4.1,anthropic:claude-3;coach=ollama:llama3",
            panel_synthesizer_provider="anthropic",
            panel_synthesizer_model="claude-opus",
        ),
        "competitor",
    )

    assert config is not None
    assert [(p.provider, p.model) for p in config.participants] == [
        ("openai", "gpt-4.1"),
        ("anthropic", "claude-3"),
    ]
    assert config.synthesizer_provider == "anthropic"
    assert config.synthesizer_model == "claude-opus"


def test_panel_language_model_client_preserves_final_output_and_participant_metadata() -> None:
    base = _FakeClient("synth")
    participant_clients = {
        ("openai", "gpt-4.1"): _FakeClient("openai"),
        ("anthropic", "claude-3"): _FakeClient("anthropic"),
    }
    config = panel_config_for_role(
        AppSettings(
            panel_roles="competitor",
            panel_participants="competitor=openai:gpt-4.1,anthropic:claude-3",
        ),
        "competitor",
    )
    assert config is not None

    client = PanelLanguageModelClient(
        role="competitor",
        base_client=base,
        config=config,
        client_factory=lambda provider, model: participant_clients[(provider, model)],
    )
    response = client.generate(
        model="fallback-model",
        prompt="build a strategy",
        max_tokens=100,
        temperature=0.2,
        role="competitor",
    )

    assert response.text == "synth:fallback-model"
    assert base.calls[0]["role"] == "competitor:panel_synthesizer"
    assert "build a strategy" in base.calls[0]["prompt"]
    assert "openai:gpt-4.1" in base.calls[0]["prompt"]
    assert response.metadata["panel_runtime"] is True
    assert response.metadata["panel_role"] == "competitor"
    participants = response.metadata["panel_participants"]
    assert participants[0]["provider"] == "openai"
    assert participants[0]["model"] == "gpt-4.1"
    assert participants[0]["content"] == "openai:gpt-4.1"
    assert response.metadata["panel_synthesizer"]["model"] == "fallback-model"
    assert response.metadata["panel_estimated_cost_usd"] == 0.03


def test_panel_counts_provider_bridge_costs() -> None:
    from autocontext.agents.provider_bridge import ProviderBridgeClient

    config = panel_config_for_role(
        AppSettings(
            panel_roles="competitor",
            panel_participants="competitor=openai:gpt-4.1",
        ),
        "competitor",
    )
    assert config is not None

    def client_factory(provider: str, _model: str) -> LanguageModelClient:
        if provider == "openai":
            return ProviderBridgeClient(_CostProvider())
        return _FakeClient("unused")

    client = PanelLanguageModelClient(
        role="competitor",
        base_client=_FakeClient("synth"),
        config=config,
        client_factory=client_factory,
    )

    response = client.generate(
        model="fallback-model",
        prompt="build a strategy",
        max_tokens=100,
        temperature=0.2,
        role="competitor",
    )

    participant = response.metadata["panel_participants"][0]
    assert participant["estimated_cost_usd"] == 0.42
    assert response.metadata["panel_estimated_cost_usd"] == 0.43


def test_orchestrator_records_provider_prefixed_panel_runtime_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.agents.provider_bridge import RuntimeBridgeClient
    from autocontext.session import RuntimeSession
    from autocontext.session.runtime_events import RuntimeSessionEventStore, RuntimeSessionEventType
    from autocontext.session.runtime_session_ids import runtime_session_id_for_run

    runtime = _CostRuntime()
    role_client = RuntimeBridgeClient(runtime)
    monkeypatch.setattr(
        "autocontext.agents.provider_bridge.create_role_client",
        lambda *args, **kwargs: role_client,
    )
    settings = AppSettings(
        agent_provider="deterministic",
        db_path=tmp_path / "events.db",
        panel_roles="analyst",
        panel_participants="analyst=pi:participant-model",
    )
    orch = AgentOrchestrator(_FakeClient("synth"), settings)
    store = RuntimeSessionEventStore(settings.db_path)
    try:
        object.__setattr__(
            orch,
            "_active_runtime_session",
            RuntimeSession.create(
                session_id=runtime_session_id_for_run("run-panel"),
                goal="panel runtime recording",
                event_store=store,
            ),
        )
        with orch._use_role_runtime("analyst", orch.analyst, generation=1, scenario_name="grid_ctf"):
            execution = orch.analyst.run("review this strategy")
        log = store.load(runtime_session_id_for_run("run-panel"))
    finally:
        store.close()

    assert runtime.calls == 1
    assert execution.metadata["panel_participants"][0]["estimated_cost_usd"] == 0.05
    assert log is not None
    prompt_roles = [
        event.payload.get("role")
        for event in log.events
        if event.event_type == RuntimeSessionEventType.PROMPT_SUBMITTED
    ]
    assert "analyst:panel_participant" in prompt_roles
    assistant_metadata = [
        event.payload.get("metadata", {})
        for event in log.events
        if event.event_type == RuntimeSessionEventType.ASSISTANT_MESSAGE
    ]
    assert assistant_metadata[0]["cost_usd"] == 0.05


def test_orchestrator_role_runtime_executes_panel_and_returns_role_output() -> None:
    from autocontext.agents.orchestrator import AgentOrchestrator

    settings = AppSettings(
        agent_provider="deterministic",
        panel_roles="analyst",
        panel_participants="analyst=deterministic:fast,deterministic:careful",
    )
    orch = AgentOrchestrator(_FakeClient("synth"), settings)

    with orch._use_role_runtime("analyst", orch.analyst, generation=1):
        execution = orch.analyst.run("review this strategy")

    assert execution.role == "analyst"
    assert execution.content == "synth:claude-sonnet-5"
    assert execution.metadata["panel_runtime"] is True
    assert len(execution.metadata["panel_participants"]) == 2


def test_panel_benchmark_comparison_reports_deltas() -> None:
    assert compare_panel_benchmark(
        single_score=0.4,
        panel_score=0.55,
        single_latency_ms=100,
        panel_latency_ms=180,
        single_cost_usd=0.02,
        panel_cost_usd=0.05,
    ) == {
        "score_delta": 0.15,
        "latency_ms_delta": 80.0,
        "cost_usd_delta": 0.03,
        "score_per_cost_delta": -9.0,
    }
