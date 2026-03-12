"""Tests for Competitor RLM — extending REPL-loop mode to the Competitor role.

Covers: config field, context loader, prompts, RLM session integration, orchestrator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.config.settings import AppSettings, load_settings
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleExecution, RoleUsage
from autocontext.harness.repl.types import RlmContext
from autocontext.rlm.repl_worker import ReplWorker
from autocontext.rlm.session import RlmSession

# Locate migrations directory relative to the test file
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_artifacts(tmp_path: Path) -> Any:
    """Create an ArtifactStore pointed at tmp directories."""
    from autocontext.storage.artifacts import ArtifactStore

    runs = tmp_path / "runs"
    knowledge = tmp_path / "knowledge"
    skills = tmp_path / "skills"
    claude_skills = tmp_path / ".claude" / "skills"
    runs.mkdir()
    knowledge.mkdir()
    skills.mkdir()
    claude_skills.mkdir(parents=True)
    return ArtifactStore(
        runs_root=runs,
        knowledge_root=knowledge,
        skills_root=skills,
        claude_skills_path=claude_skills,
    )


@pytest.fixture()
def tmp_sqlite(tmp_path: Path) -> Any:
    """Create a SQLiteStore with migrations applied."""
    from autocontext.storage.sqlite_store import SQLiteStore

    db_path = tmp_path / "test.db"
    store = SQLiteStore(db_path)
    store.migrate(_MIGRATIONS_DIR)
    return store


@pytest.fixture()
def context_loader(tmp_artifacts: Any, tmp_sqlite: Any) -> Any:
    """Create a ContextLoader."""
    from autocontext.rlm.context_loader import ContextLoader

    return ContextLoader(tmp_artifacts, tmp_sqlite)


@pytest.fixture()
def seeded_artifacts(tmp_artifacts: Any, tmp_path: Path) -> Any:
    """Artifact store with some data seeded for tests."""
    scenario = "grid_ctf"
    run_id = "test_run"

    # Write a playbook
    tmp_artifacts.write_playbook(scenario, "## Strategy\n\n- Be aggressive.")

    # Write hints
    tmp_artifacts.write_hints(scenario, "- Try aggression=0.6.")

    # Create replays directory with a replay
    gen_dir = tmp_artifacts.generation_dir(run_id, 1)
    replay_dir = gen_dir / "replays"
    replay_dir.mkdir(parents=True)
    (replay_dir / "grid_ctf_1.json").write_text(
        json.dumps({"score": 0.7, "moves": [1, 2, 3]}), encoding="utf-8",
    )

    # Create metrics
    (gen_dir / "metrics.json").write_text(
        json.dumps({"elo": 1200, "win_rate": 0.6}), encoding="utf-8",
    )

    # Create analysis
    analysis_dir = tmp_artifacts.knowledge_root / scenario / "analysis"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "gen_1.md").write_text(
        "## Findings\n\n- Score improved.", encoding="utf-8",
    )

    return tmp_artifacts


# ===========================================================================
# 1. Context Loader Tests
# ===========================================================================


class TestContextLoaderCompetitor:
    def test_load_for_competitor_populates_replays(
        self, context_loader: Any, seeded_artifacts: Any, tmp_sqlite: Any,
    ) -> None:
        """Replays loaded from run artifacts."""
        ctx = context_loader.load_for_competitor(
            run_id="test_run", scenario_name="grid_ctf", generation=1,
        )
        assert isinstance(ctx, RlmContext)
        assert isinstance(ctx.variables["replays"], list)
        assert len(ctx.variables["replays"]) == 1
        assert ctx.variables["replays"][0]["score"] == 0.7

    def test_load_for_competitor_populates_metrics(
        self, context_loader: Any, seeded_artifacts: Any, tmp_sqlite: Any,
    ) -> None:
        """Metrics history loaded."""
        ctx = context_loader.load_for_competitor(
            run_id="test_run", scenario_name="grid_ctf", generation=1,
        )
        assert isinstance(ctx.variables["metrics_history"], list)
        assert len(ctx.variables["metrics_history"]) == 1
        assert ctx.variables["metrics_history"][0]["elo"] == 1200

    def test_load_for_competitor_populates_match_scores(
        self, context_loader: Any, seeded_artifacts: Any, tmp_sqlite: Any,
    ) -> None:
        """Match scores from DB (empty list when no matches recorded)."""
        ctx = context_loader.load_for_competitor(
            run_id="test_run", scenario_name="grid_ctf", generation=1,
        )
        assert isinstance(ctx.variables["match_scores"], list)

    def test_load_for_competitor_populates_playbook(
        self, context_loader: Any, seeded_artifacts: Any, tmp_sqlite: Any,
    ) -> None:
        """Playbook string loaded."""
        ctx = context_loader.load_for_competitor(
            run_id="test_run", scenario_name="grid_ctf", generation=1,
        )
        assert isinstance(ctx.variables["playbook"], str)
        assert "aggressive" in ctx.variables["playbook"].lower()

    def test_load_for_competitor_populates_coach_hints(
        self, context_loader: Any, seeded_artifacts: Any, tmp_sqlite: Any,
    ) -> None:
        """Hints loaded."""
        ctx = context_loader.load_for_competitor(
            run_id="test_run", scenario_name="grid_ctf", generation=1,
        )
        assert isinstance(ctx.variables["coach_hints"], str)
        assert "aggression" in ctx.variables["coach_hints"].lower()

    def test_load_for_competitor_populates_scenario_context(
        self, context_loader: Any, seeded_artifacts: Any, tmp_sqlite: Any,
    ) -> None:
        """Rules + interface + current strategy populated."""
        ctx = context_loader.load_for_competitor(
            run_id="test_run",
            scenario_name="grid_ctf",
            generation=1,
            scenario_rules="Capture the flag.",
            strategy_interface='{"aggression": float}',
            current_strategy={"aggression": 0.5},
        )
        assert ctx.variables["scenario_rules"] == "Capture the flag."
        assert ctx.variables["strategy_interface"] == '{"aggression": float}'
        assert ctx.variables["current_strategy"] == {"aggression": 0.5}

    def test_load_for_competitor_summary_format(
        self, context_loader: Any, seeded_artifacts: Any, tmp_sqlite: Any,
    ) -> None:
        """Summary string has all variable names."""
        ctx = context_loader.load_for_competitor(
            run_id="test_run", scenario_name="grid_ctf", generation=1,
        )
        summary = ctx.summary
        expected_vars = [
            "replays", "metrics_history", "match_scores", "playbook",
            "coach_hints", "scenario_rules", "strategy_interface",
            "current_strategy", "prior_analyses", "operational_lessons",
        ]
        for var in expected_vars:
            assert var in summary, f"Expected '{var}' in summary"


# ===========================================================================
# 2. Prompt Tests
# ===========================================================================


class TestCompetitorPrompts:
    def test_competitor_rlm_system_has_placeholders(self) -> None:
        """exec-backend prompt has required format placeholders."""
        from autocontext.rlm.prompts import COMPETITOR_RLM_SYSTEM

        for placeholder in ["{max_turns}", "{max_stdout_chars}", "{variable_summary}"]:
            assert placeholder in COMPETITOR_RLM_SYSTEM, (
                f"Missing placeholder {placeholder}"
            )

    def test_competitor_rlm_constrained_has_constraints(self) -> None:
        """Constrained variant has constraint bullets."""
        from autocontext.rlm.prompts import COMPETITOR_RLM_SYSTEM_CONSTRAINED

        assert "Constraints" in COMPETITOR_RLM_SYSTEM_CONSTRAINED

    def test_competitor_monty_rlm_has_state_instructions(self) -> None:
        """Monty variant explains state[] persistence."""
        from autocontext.rlm.prompts import COMPETITOR_MONTY_RLM_SYSTEM

        assert "state" in COMPETITOR_MONTY_RLM_SYSTEM
        for placeholder in ["{max_turns}", "{max_stdout_chars}", "{variable_summary}"]:
            assert placeholder in COMPETITOR_MONTY_RLM_SYSTEM

    def test_competitor_monty_constrained_has_constraints(self) -> None:
        """Monty constrained variant has constraint bullets."""
        from autocontext.rlm.prompts import COMPETITOR_MONTY_RLM_SYSTEM_CONSTRAINED

        assert "Constraints" in COMPETITOR_MONTY_RLM_SYSTEM_CONSTRAINED


# ===========================================================================
# 3. Config Tests
# ===========================================================================


class TestConfigRlmCompetitor:
    def test_config_rlm_competitor_default_false(self) -> None:
        """Defaults to disabled."""
        settings = AppSettings()
        assert settings.rlm_competitor_enabled is False

    def test_config_rlm_competitor_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AUTOCONTEXT_RLM_COMPETITOR_ENABLED=true activates the setting."""
        monkeypatch.setenv("AUTOCONTEXT_RLM_COMPETITOR_ENABLED", "true")
        # Clear any preset env var that might interfere
        monkeypatch.delenv("AUTOCONTEXT_PRESET", raising=False)
        settings = load_settings()
        assert settings.rlm_competitor_enabled is True


# ===========================================================================
# 4. Integration Tests (mock LLM client)
# ===========================================================================


class _CompetitorReadyClient(LanguageModelClient):
    """Client that produces a JSON strategy via the answer protocol."""

    def __init__(self) -> None:
        self._turn = 0

    def generate_multiturn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        self._turn += 1
        if self._turn == 1:
            text = '<code>\nprint(len(replays))\nprint(scenario_rules)\n</code>'
        else:
            text = (
                '<code>\n'
                'answer["content"] = \'{"aggression": 0.65, "defense": 0.55}\'\n'
                'answer["ready"] = True\n'
                '</code>'
            )
        return ModelResponse(
            text=text,
            usage=RoleUsage(input_tokens=50, output_tokens=30, latency_ms=2, model=model),
        )


class _NeverReadyClient(LanguageModelClient):
    """Client that never sets answer['ready']."""

    def generate_multiturn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        return ModelResponse(
            text='<code>\nprint("exploring...")\n</code>',
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=1, model=model),
        )


class TestCompetitorRlmSession:
    def test_competitor_rlm_session_produces_strategy(self) -> None:
        """Session runs, answer extracted as JSON strategy text."""
        client = _CompetitorReadyClient()
        namespace: dict[str, Any] = {
            "replays": [{"score": 0.7}],
            "scenario_rules": "Capture the flag",
            "strategy_interface": "{}",
            "current_strategy": {},
            "metrics_history": [],
            "match_scores": [],
            "playbook": "",
            "coach_hints": "",
            "prior_analyses": [],
            "operational_lessons": "",
        }
        worker = ReplWorker(namespace=namespace)
        session = RlmSession(
            client=client,
            worker=worker,
            role="competitor",
            model="test-model",
            system_prompt="You are a competitor.",
            max_turns=5,
        )
        result = session.run()
        assert result.status == "completed"
        assert result.role == "competitor"
        # answer["content"] should contain the JSON strategy
        parsed = json.loads(result.content)
        assert parsed["aggression"] == 0.65

    def test_competitor_rlm_answer_protocol(self) -> None:
        """answer['content'] = JSON, answer['ready'] = True works."""
        client = _CompetitorReadyClient()
        namespace: dict[str, Any] = {
            "replays": [],
            "scenario_rules": "",
            "strategy_interface": "",
            "current_strategy": {},
            "metrics_history": [],
            "match_scores": [],
            "playbook": "",
            "coach_hints": "",
            "prior_analyses": [],
            "operational_lessons": "",
        }
        worker = ReplWorker(namespace=namespace)
        session = RlmSession(
            client=client,
            worker=worker,
            role="competitor",
            model="m",
            system_prompt="s",
            max_turns=5,
        )
        result = session.run()
        assert result.status == "completed"
        assert "aggression" in result.content

    def test_competitor_rlm_turn_limit(self) -> None:
        """Stops at max_turns when answer is never ready."""
        client = _NeverReadyClient()
        worker = ReplWorker(namespace={"replays": []})
        session = RlmSession(
            client=client,
            worker=worker,
            role="competitor",
            model="m",
            system_prompt="s",
            max_turns=3,
        )
        result = session.run()
        assert result.status == "truncated"
        assert len(session.execution_history) == 3

    def test_competitor_rlm_exec_backend(self) -> None:
        """Uses ReplWorker (exec backend) for competitor."""
        client = _CompetitorReadyClient()
        worker = ReplWorker(namespace={"replays": [], "scenario_rules": "test"})
        assert hasattr(worker, "run_code")
        assert hasattr(worker, "namespace")
        session = RlmSession(
            client=client,
            worker=worker,
            role="competitor",
            model="m",
            system_prompt="s",
            max_turns=5,
        )
        result = session.run()
        assert result.status == "completed"

    def test_competitor_rlm_monty_backend(self) -> None:
        """Uses MontyReplWorker when available, else skip."""
        try:
            from autocontext.harness.repl.monty_worker import MontyReplWorker
        except ImportError:
            pytest.skip("pydantic-monty not installed")

        client = _CompetitorReadyClient()
        worker = MontyReplWorker(namespace={"replays": [], "scenario_rules": "test"})
        session = RlmSession(
            client=client,
            worker=worker,
            role="competitor",
            model="m",
            system_prompt="s",
            max_turns=5,
        )
        result = session.run()
        assert result.role == "competitor"


# ===========================================================================
# 5. Orchestrator Integration Tests
# ===========================================================================


class TestOrchestratorCompetitorRlm:
    def test_orchestrator_uses_rlm_when_enabled(
        self, tmp_artifacts: Any, tmp_sqlite: Any, seeded_artifacts: Any,
    ) -> None:
        """When rlm_competitor_enabled=True, run_generation uses RLM path for competitor."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.prompts.templates import PromptBundle

        settings = AppSettings(
            agent_provider="deterministic",
            rlm_enabled=True,
            rlm_competitor_enabled=True,
            rlm_max_turns=5,
            curator_enabled=False,
        )
        orch = AgentOrchestrator(
            client=DeterministicDevClient(),
            settings=settings,
            artifacts=seeded_artifacts,
            sqlite=tmp_sqlite,
        )
        prompts = PromptBundle(
            competitor="Describe your strategy for grid_ctf.",
            analyst="Analyze strengths/failures.",
            coach="You are the playbook coach. Update the playbook.",
            architect="Propose tools.",
        )
        outputs = orch.run_generation(
            prompts,
            generation_index=1,
            run_id="test_run",
            scenario_name="grid_ctf",
        )
        # The strategy should be produced (may be empty dict if DeterministicDevClient
        # does not produce valid JSON in competitor RLM mode, but the path should execute)
        assert outputs.strategy is not None
        # Should have role_executions including competitor
        roles = [r.role for r in outputs.role_executions]
        assert "competitor" in roles

    def test_orchestrator_skips_rlm_when_disabled(
        self, tmp_artifacts: Any, tmp_sqlite: Any,
    ) -> None:
        """When rlm_competitor_enabled=False, normal single-shot path is used."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.prompts.templates import PromptBundle

        settings = AppSettings(
            agent_provider="deterministic",
            rlm_enabled=True,
            rlm_competitor_enabled=False,
            curator_enabled=False,
        )
        orch = AgentOrchestrator(
            client=DeterministicDevClient(),
            settings=settings,
            artifacts=tmp_artifacts,
            sqlite=tmp_sqlite,
        )
        prompts = PromptBundle(
            competitor="Describe your strategy for grid_ctf.",
            analyst="Analyze strengths/failures.",
            coach="You are the playbook coach. Update the playbook.",
            architect="Propose tools.",
        )
        outputs = orch.run_generation(
            prompts,
            generation_index=1,
            run_id="test_run",
            scenario_name="grid_ctf",
        )
        assert outputs.strategy is not None
        # With rlm_competitor_enabled=False, the normal competitor.run() path is used.
        # The competitor execution should still be present.
        roles = [r.role for r in outputs.role_executions]
        assert "competitor" in roles

    def test_orchestrator_passes_scenario_rules_and_strategy(
        self, tmp_artifacts: Any, tmp_sqlite: Any, seeded_artifacts: Any,
    ) -> None:
        """scenario_rules and current_strategy reach the context loader."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.prompts.templates import PromptBundle

        settings = AppSettings(
            agent_provider="deterministic",
            rlm_enabled=True,
            rlm_competitor_enabled=True,
            rlm_max_turns=5,
            curator_enabled=False,
        )
        orch = AgentOrchestrator(
            client=DeterministicDevClient(),
            settings=settings,
            artifacts=seeded_artifacts,
            sqlite=tmp_sqlite,
        )
        prompts = PromptBundle(
            competitor="Describe your strategy.",
            analyst="Analyze.",
            coach="Coach.",
            architect="Propose.",
        )

        captured_kwargs: dict[str, Any] = {}
        original_load = orch._rlm_loader.load_for_competitor  # type: ignore[union-attr]

        def _spy_load(*args: Any, **kwargs: Any) -> Any:
            captured_kwargs.update(kwargs)
            return original_load(*args, **kwargs)

        with patch.object(orch._rlm_loader, "load_for_competitor", side_effect=_spy_load):  # type: ignore[union-attr]
            orch.run_generation(
                prompts,
                generation_index=1,
                run_id="test_run",
                scenario_name="grid_ctf",
                scenario_rules="Capture the flag on a 5x5 grid.",
                current_strategy={"aggression": 0.5},
            )

        assert captured_kwargs.get("scenario_rules") == "Capture the flag on a 5x5 grid."
        assert captured_kwargs.get("current_strategy") == {"aggression": 0.5}

    def test_orchestrator_passes_scenario_rules_to_analyst_and_architect(
        self, tmp_artifacts: Any, tmp_sqlite: Any, seeded_artifacts: Any,
    ) -> None:
        """scenario_rules reaches the analyst and architect context loaders."""
        from autocontext.agents.orchestrator import AgentOrchestrator
        from autocontext.prompts.templates import PromptBundle

        settings = AppSettings(
            agent_provider="deterministic",
            rlm_enabled=True,
            rlm_competitor_enabled=False,  # Use normal competitor path
            rlm_max_turns=5,
            curator_enabled=False,
        )
        orch = AgentOrchestrator(
            client=DeterministicDevClient(),
            settings=settings,
            artifacts=seeded_artifacts,
            sqlite=tmp_sqlite,
        )
        prompts = PromptBundle(
            competitor="Describe your strategy.",
            analyst="Analyze.",
            coach="Coach.",
            architect="Propose.",
        )

        analyst_kwargs: dict[str, Any] = {}
        architect_kwargs: dict[str, Any] = {}
        original_analyst = orch._rlm_loader.load_for_analyst  # type: ignore[union-attr]
        original_architect = orch._rlm_loader.load_for_architect  # type: ignore[union-attr]

        def _spy_analyst(*args: Any, **kwargs: Any) -> Any:
            analyst_kwargs.update(kwargs)
            return original_analyst(*args, **kwargs)

        def _spy_architect(*args: Any, **kwargs: Any) -> Any:
            architect_kwargs.update(kwargs)
            return original_architect(*args, **kwargs)

        with (
            patch.object(orch._rlm_loader, "load_for_analyst", side_effect=_spy_analyst),  # type: ignore[union-attr]
            patch.object(orch._rlm_loader, "load_for_architect", side_effect=_spy_architect),  # type: ignore[union-attr]
        ):
            orch.run_generation(
                prompts,
                generation_index=1,
                run_id="test_run",
                scenario_name="grid_ctf",
                scenario_rules="Capture the flag on a 5x5 grid.",
            )

        assert analyst_kwargs.get("scenario_rules") == "Capture the flag on a 5x5 grid."
        assert architect_kwargs.get("scenario_rules") == "Capture the flag on a 5x5 grid."


# ===========================================================================
# 6. RLM Trial Summary & Experiment Log Tests (MTS-100)
# ===========================================================================


class TestBuildTrialSummary:
    def test_summary_includes_generation_and_turns(self) -> None:
        """Trial summary contains generation number and turn count."""
        from autocontext.agents.orchestrator import _build_trial_summary
        from autocontext.harness.repl.types import ExecutionRecord

        history = [
            ExecutionRecord(turn=1, code="x = 1", stdout="", error=None, answer_ready=False),
            ExecutionRecord(turn=2, code='answer["ready"] = True', stdout="", error=None, answer_ready=True),
        ]
        role_exec = RoleExecution(
            role="competitor", content="done",
            usage=RoleUsage(input_tokens=100, output_tokens=50, latency_ms=500, model="test"),
            subagent_id="abc", status="completed",
        )
        summary = _build_trial_summary(3, history, role_exec)
        assert "Generation 3" in summary
        assert "Turns: 2" in summary
        assert "code executions: 2" in summary
        assert "500ms" in summary

    def test_summary_counts_errors(self) -> None:
        """Error count reflects turns with errors."""
        from autocontext.agents.orchestrator import _build_trial_summary
        from autocontext.harness.repl.types import ExecutionRecord

        history = [
            ExecutionRecord(turn=1, code="bad()", stdout="", error="NameError", answer_ready=False),
            ExecutionRecord(turn=2, code="ok()", stdout="ok", error=None, answer_ready=True),
        ]
        role_exec = RoleExecution(
            role="competitor", content="done",
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=100, model="t"),
            subagent_id="x", status="completed",
        )
        summary = _build_trial_summary(1, history, role_exec)
        assert "errors: 1" in summary
        assert "[ERROR]" in summary

    def test_summary_shows_ready_flag(self) -> None:
        """Turns where answer_ready=True are marked [READY]."""
        from autocontext.agents.orchestrator import _build_trial_summary
        from autocontext.harness.repl.types import ExecutionRecord

        history = [
            ExecutionRecord(turn=1, code='answer["ready"] = True', stdout="", error=None, answer_ready=True),
        ]
        role_exec = RoleExecution(
            role="competitor", content="done",
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=50, model="t"),
            subagent_id="x", status="completed",
        )
        summary = _build_trial_summary(1, history, role_exec)
        assert "[READY]" in summary


class TestExperimentLog:
    def test_build_experiment_log_empty_when_no_trials(self, tmp_sqlite: Any) -> None:
        """Returns empty string when no RLM trial data."""
        from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder

        builder = ScoreTrajectoryBuilder(tmp_sqlite)
        result = builder.build_experiment_log("nonexistent-run")
        assert result == ""

    @staticmethod
    def _seed_run(sqlite: Any, run_id: str, generations: list[int]) -> None:
        """Create run + generation rows so FK constraints pass."""
        sqlite.create_run(run_id, "grid_ctf", len(generations), "local")
        for gen in generations:
            sqlite.upsert_generation(run_id, gen, mean_score=0.5, best_score=0.5, elo=1500.0,
                                     wins=0, losses=0, gate_decision="advance", status="completed")

    def test_build_experiment_log_collects_summaries(self, tmp_sqlite: Any) -> None:
        """Collects stored trial summaries across generations."""
        from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder

        self._seed_run(tmp_sqlite, "run-1", [1, 2])
        tmp_sqlite.append_agent_output("run-1", 1, "competitor_rlm_trials", "### Gen 1 trial")
        tmp_sqlite.append_agent_output("run-1", 2, "competitor_rlm_trials", "### Gen 2 trial")

        builder = ScoreTrajectoryBuilder(tmp_sqlite)
        log = builder.build_experiment_log("run-1")
        assert "RLM Experiment Log" in log
        assert "Gen 1 trial" in log
        assert "Gen 2 trial" in log

    def test_build_experiment_log_ignores_other_roles(self, tmp_sqlite: Any) -> None:
        """Only collects competitor_rlm_trials, not other roles."""
        from autocontext.knowledge.trajectory import ScoreTrajectoryBuilder

        self._seed_run(tmp_sqlite, "run-1", [1])
        tmp_sqlite.append_agent_output("run-1", 1, "competitor", '{"aggression": 0.5}')
        tmp_sqlite.append_agent_output("run-1", 1, "competitor_rlm_trials", "### Gen 1 trial")

        builder = ScoreTrajectoryBuilder(tmp_sqlite)
        log = builder.build_experiment_log("run-1")
        assert "Gen 1 trial" in log
        assert "aggression" not in log


class TestRlmTrialStorage:
    def test_rlm_competitor_stores_trial_summary(
        self, tmp_artifacts: Any, tmp_sqlite: Any, seeded_artifacts: Any,
    ) -> None:
        """Running competitor via RLM stores a trial summary in sqlite."""
        from autocontext.agents.orchestrator import AgentOrchestrator

        settings = AppSettings(
            agent_provider="deterministic",
            rlm_enabled=True,
            rlm_competitor_enabled=True,
            rlm_max_turns=5,
            curator_enabled=False,
        )
        orch = AgentOrchestrator(
            client=DeterministicDevClient(),
            settings=settings,
            artifacts=seeded_artifacts,
            sqlite=tmp_sqlite,
        )

        # Seed run + generation so FK constraints pass
        tmp_sqlite.create_run("trial-test", "grid_ctf", 1, "local")
        tmp_sqlite.upsert_generation("trial-test", 1, mean_score=0.5, best_score=0.5,
                                     elo=1500.0, wins=0, losses=0, gate_decision="advance",
                                     status="completed")

        orch._run_rlm_competitor(
            run_id="trial-test",
            scenario_name="grid_ctf",
            generation_index=1,
        )

        rows = tmp_sqlite.get_agent_outputs_by_role("trial-test", "competitor_rlm_trials")
        assert len(rows) == 1
        content = rows[0]["content"]
        assert "Generation 1" in content
        assert "RLM competitor trial" in content
