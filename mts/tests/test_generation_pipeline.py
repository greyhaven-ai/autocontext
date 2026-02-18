"""Tests for GenerationPipeline — composed stage orchestrator."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mts.config.settings import AppSettings
from mts.loop.generation_runner import GenerationRunner


class TestGenerationPipelineFlag:
    def test_flag_default_off(self) -> None:
        settings = AppSettings(agent_provider="deterministic")
        assert settings.use_generation_pipeline is False

    def test_flag_enabled(self) -> None:
        settings = AppSettings(agent_provider="deterministic", use_generation_pipeline=True)
        assert settings.use_generation_pipeline is True


class TestGenerationPipelineIntegration:
    def test_pipeline_runs_one_generation(self, tmp_path: Path) -> None:
        """Pipeline path executes a full generation with deterministic client."""
        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            use_generation_pipeline=True,
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=1, run_id="pipe_test")
        assert summary.generations_executed == 1
        assert summary.best_score >= 0.0

    def test_monolith_still_works(self, tmp_path: Path) -> None:
        """With flag off, monolithic path still works."""
        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            use_generation_pipeline=False,
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=1, run_id="mono_test")
        assert summary.generations_executed == 1
        assert summary.best_score >= 0.0

    def test_pipeline_multi_generation(self, tmp_path: Path) -> None:
        """Pipeline handles multiple generations correctly."""
        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            use_generation_pipeline=True,
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=2, run_id="pipe_multi")
        assert summary.generations_executed == 2

    def test_pipeline_produces_equivalent_scores(self, tmp_path: Path) -> None:
        """Pipeline and monolith produce same scores with deterministic client."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        settings_a = AppSettings(
            agent_provider="deterministic",
            db_path=dir_a / "test.sqlite3",
            runs_root=dir_a / "runs",
            knowledge_root=dir_a / "knowledge",
            skills_root=dir_a / "skills",
            claude_skills_path=dir_a / ".claude" / "skills",
            use_generation_pipeline=False,
            curator_enabled=False,
        )
        runner_a = GenerationRunner(settings_a)
        runner_a.migrate(Path("migrations"))
        summary_a = runner_a.run("grid_ctf", generations=1, run_id="equiv_a")

        settings_b = AppSettings(
            agent_provider="deterministic",
            db_path=dir_b / "test.sqlite3",
            runs_root=dir_b / "runs",
            knowledge_root=dir_b / "knowledge",
            skills_root=dir_b / "skills",
            claude_skills_path=dir_b / ".claude" / "skills",
            use_generation_pipeline=True,
            curator_enabled=False,
        )
        runner_b = GenerationRunner(settings_b)
        runner_b.migrate(Path("migrations"))
        summary_b = runner_b.run("grid_ctf", generations=1, run_id="equiv_b")

        assert summary_a.best_score == pytest.approx(summary_b.best_score, abs=1e-6)
        assert summary_a.current_elo == pytest.approx(summary_b.current_elo, abs=1e-6)


class TestPipelineControllerCheckpoints:
    def test_pipeline_accepts_controller(self) -> None:
        """GenerationPipeline constructor accepts an optional controller parameter."""
        from mts.harness.core.controller import LoopController
        from mts.loop.generation_pipeline import GenerationPipeline

        controller = LoopController()
        pipeline = GenerationPipeline(
            orchestrator=MagicMock(),
            tournament_runner=MagicMock(),
            gate=MagicMock(),
            artifacts=MagicMock(),
            sqlite=MagicMock(),
            trajectory_builder=MagicMock(),
            events=MagicMock(),
            curator=None,
            controller=controller,
        )
        assert pipeline._controller is controller

    def test_pipeline_gate_override_applied(self, tmp_path: Path) -> None:
        """When controller has a gate override, it's applied after stage_tournament."""
        from mts.harness.core.controller import LoopController

        controller = LoopController()
        controller.set_gate_override("advance")

        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            use_generation_pipeline=True,
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        runner.controller = controller
        summary = runner.run("grid_ctf", generations=1, run_id="override_test")
        assert summary.generations_executed == 1
