"""Settings-backed output-token budgets + model-aware clamp (AC-905)."""

from __future__ import annotations

from autocontext.config.settings import AppSettings
from autocontext.providers.token_caps import clamp_output_tokens


class TestBudgetSettings:
    def test_defaults_match_previous_literals(self) -> None:
        settings = AppSettings()
        assert settings.competitor_max_tokens == 800
        assert settings.translator_max_tokens == 1024  # deliberate floor raise from 400/200
        assert settings.analyst_max_tokens == 1200
        assert settings.coach_max_tokens == 2000
        assert settings.architect_max_tokens == 1600
        assert settings.curator_max_tokens == 3000
        assert settings.curator_rating_max_tokens == 1200
        assert settings.curator_consolidation_max_tokens == 4000
        assert settings.skeptic_max_tokens == 2000
        assert settings.scenario_designer_max_tokens == 3000
        assert settings.solve_designer_max_tokens == 1200
        assert settings.train_codegen_max_tokens == 8000

    def test_env_override(self, monkeypatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_COACH_MAX_TOKENS", "4096")
        assert load_settings().coach_max_tokens == 4096


class TestClampOutputTokens:
    def test_known_capped_model_clamps(self) -> None:
        assert clamp_output_tokens(100_000, "claude-3-haiku-20240307") == 4096

    def test_requested_below_cap_passes(self) -> None:
        assert clamp_output_tokens(2000, "claude-3-haiku-20240307") == 2000

    def test_unknown_model_passes_through(self) -> None:
        assert clamp_output_tokens(100_000, "future-model-9000") == 100_000

    def test_none_model_passes_through(self) -> None:
        assert clamp_output_tokens(5000, None) == 5000


class TestAgentWiring:
    """Each runner passes its constructed budget into SubagentTask (AC-905)."""

    def _capture(self):
        from autocontext.agents.subagent_runtime import SubagentRuntime
        from autocontext.harness.core.types import RoleExecution, RoleUsage

        captured: list[int] = []

        class CapturingRuntime(SubagentRuntime):
            def __init__(self) -> None:
                pass

            def run_task(self, task):  # type: ignore[no-untyped-def]
                captured.append(task.max_tokens)
                return RoleExecution(
                    role=task.role,
                    content="<!-- CURATOR_DECISION: accept -->",
                    usage=RoleUsage(model="m", input_tokens=0, output_tokens=0, latency_ms=0),
                    subagent_id="s",
                    status="ok",
                )

        return CapturingRuntime(), captured

    def test_coach_budget_flows(self) -> None:
        from autocontext.agents.coach import CoachRunner

        runtime, captured = self._capture()
        CoachRunner(runtime, "m", max_tokens=2222).run("prompt")
        assert captured == [2222]

    def test_curator_three_budgets_flow(self) -> None:
        from autocontext.agents.curator import KnowledgeCurator

        runtime, captured = self._capture()
        curator = KnowledgeCurator(runtime, "m", max_tokens=3100, rating_max_tokens=1300, consolidation_max_tokens=4100)
        curator.assess_playbook_quality("old", "new", "trajectory", "analysis")
        assert captured == [3100]

    def test_orchestrator_wires_settings(self, monkeypatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_COACH_MAX_TOKENS", "2323")
        monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
        settings = load_settings()
        assert settings.coach_max_tokens == 2323
        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.agents.orchestrator import AgentOrchestrator

        orchestrator = AgentOrchestrator(build_client_from_settings(settings), settings)
        assert orchestrator.coach.max_tokens == 2323
        assert orchestrator.translator.max_tokens == settings.translator_max_tokens
