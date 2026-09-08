"""Call-count, dependency scheduling, and role isolation regressions."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.agents.types import RoleExecution, RoleUsage
from autocontext.config.settings import AppSettings
from autocontext.prompts.templates import PromptBundle


def execution(role: str) -> RoleExecution:
    return RoleExecution(role, f"{role} result", RoleUsage(1, 1, 1, role), "test", "ok")


def prompts() -> PromptBundle:
    return PromptBundle(
        competitor="Describe your strategy reasoning and recommend specific parameter values.",
        analyst="Analyze strengths/failures and return Findings, Root Causes, Actionable Recommendations.",
        coach="Update the playbook.",
        architect="Propose infrastructure/tooling improvements.",
    )


@pytest.mark.parametrize("pipeline", [False, True])
def test_cadence_eliminates_calls_but_retains_zero_cost_records(pipeline: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator(DeterministicDevClient(), AppSettings(agent_provider="deterministic", use_pipeline_engine=pipeline))
    calls = MagicMock(wraps=orch.architect.run)
    monkeypatch.setattr(orch.architect, "run", calls)
    for generation in (1, 2, 3):
        output = orch.run_generation(prompts(), generation)
        architect = next(result for result in output.role_executions if result.role == "architect")
        if generation < 3:
            assert architect.status == "skipped"
            assert architect.usage.input_tokens == architect.usage.output_tokens == architect.usage.latency_ms == 0
            assert architect.metadata["reason"] == "architect_cadence"
            assert output.architect_tools == []
            assert output.architect_harness_specs == []
        else:
            assert architect.status != "skipped"
    assert calls.call_count == 1


@pytest.mark.parametrize("pipeline", [False, True])
def test_architect_overlaps_analyst_and_coach_does_not_wait_for_architect(
    pipeline: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator(DeterministicDevClient(), AppSettings(agent_provider="deterministic", use_pipeline_engine=pipeline))
    both_started = threading.Barrier(2)
    coach_started = threading.Event()

    def analyst(prompt: str, **kwargs: object) -> RoleExecution:
        both_started.wait(timeout=5)
        return execution("analyst")

    def architect(prompt: str, **kwargs: object) -> RoleExecution:
        both_started.wait(timeout=5)
        assert coach_started.wait(timeout=5), "coach waited for independent architect"
        return execution("architect")

    def coach(prompt: str, **kwargs: object) -> RoleExecution:
        assert "analyst result" in prompt
        coach_started.set()
        return execution("coach")

    monkeypatch.setattr(orch.analyst, "run", analyst)
    monkeypatch.setattr(orch.architect, "run", architect)
    monkeypatch.setattr(orch.coach, "run", coach)
    orch.run_generation(prompts(), 3)


@pytest.mark.parametrize("pipeline", [False, True])
def test_parallel_roles_keep_their_resolved_clients(pipeline: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator(DeterministicDevClient(), AppSettings(agent_provider="deterministic", use_pipeline_engine=pipeline))
    clients = {role: DeterministicDevClient() for role in ("competitor", "translator", "analyst", "architect", "coach")}
    barrier = threading.Barrier(2)

    def resolve(role: str, **kwargs: object) -> tuple[DeterministicDevClient, None]:
        return clients[role], None

    def role_call(role: str):
        def run(prompt: str, **kwargs: object) -> RoleExecution:
            barrier.wait(timeout=5)
            assert getattr(orch, role).runtime.client is clients[role]
            return execution(role)
        return run

    monkeypatch.setattr(orch, "_resolve_role_execution", resolve)
    monkeypatch.setattr(orch.analyst, "run", role_call("analyst"))
    monkeypatch.setattr(orch.architect, "run", role_call("architect"))
    orch.run_generation(prompts(), 3)
    assert orch.analyst.runtime.client is orch.client
    assert orch.architect.runtime.client is orch.client


@pytest.mark.parametrize("generation, expected_roles", [(1, ["analyst"]), (3, ["analyst", "architect"])])
def test_rlm_cadence_skips_session_and_context_loading(
    generation: int, expected_roles: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator(DeterministicDevClient(), AppSettings(agent_provider="deterministic"))
    loader = MagicMock()
    orch._rlm_loader = loader
    calls: list[str] = []

    def session(*, role: str, **kwargs: object) -> tuple[RoleExecution, list[object]]:
        calls.append(role)
        return execution(role), []

    monkeypatch.setattr(orch, "_run_single_rlm_session", session)
    _, architect = orch._run_rlm_roles("run", "grid_ctf", generation, {}, "architect prompt")
    assert calls == expected_roles
    assert loader.load_for_architect.call_count == (generation == 3)
    if generation == 1:
        assert architect.status == "skipped"


@pytest.mark.parametrize("pipeline", [False, True])
def test_failed_analyst_does_not_start_coach_and_restores_bindings(
    pipeline: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator(DeterministicDevClient(), AppSettings(agent_provider="deterministic", use_pipeline_engine=pipeline))
    both_started = threading.Barrier(2)
    coach = MagicMock(side_effect=AssertionError("coach must not run after failed analyst"))

    def analyst(prompt: str, **kwargs: object) -> RoleExecution:
        both_started.wait(timeout=5)
        raise ValueError("analyst failed")

    def architect(prompt: str, **kwargs: object) -> RoleExecution:
        both_started.wait(timeout=5)
        return execution("architect")

    monkeypatch.setattr(orch.analyst, "run", analyst)
    monkeypatch.setattr(orch.architect, "run", architect)
    monkeypatch.setattr(orch.coach, "run", coach)
    with pytest.raises(ValueError, match="analyst failed"):
        orch.run_generation(prompts(), 3)
    coach.assert_not_called()
    assert orch.analyst.runtime.client is orch.client
    assert orch.architect.runtime.client is orch.client
