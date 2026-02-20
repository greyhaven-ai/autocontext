"""Tests for GenerationPipeline — composed stage orchestrator."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from mts.config.settings import AppSettings
from mts.loop.generation_runner import GenerationRunner


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
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=1, run_id="pipe_test")
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
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=2, run_id="pipe_multi")
        assert summary.generations_executed == 2


class TestPipelineMetaOptimizer:
    def test_pipeline_accepts_meta_optimizer(self) -> None:
        """GenerationPipeline constructor accepts an optional meta_optimizer parameter."""
        from mts.loop.generation_pipeline import GenerationPipeline

        meta = MagicMock()
        pipeline = GenerationPipeline(
            orchestrator=MagicMock(),
            supervisor=MagicMock(),
            gate=MagicMock(),
            artifacts=MagicMock(),
            sqlite=MagicMock(),
            trajectory_builder=MagicMock(),
            events=MagicMock(),
            curator=None,
            meta_optimizer=meta,
        )
        assert pipeline._meta_optimizer is meta

    def test_pipeline_meta_optimizer_defaults_none(self) -> None:
        """MetaOptimizer defaults to None when not provided."""
        from mts.loop.generation_pipeline import GenerationPipeline

        pipeline = GenerationPipeline(
            orchestrator=MagicMock(),
            supervisor=MagicMock(),
            gate=MagicMock(),
            artifacts=MagicMock(),
            sqlite=MagicMock(),
            trajectory_builder=MagicMock(),
            events=MagicMock(),
            curator=None,
        )
        assert pipeline._meta_optimizer is None


class TestPipelineControllerCheckpoints:
    def test_pipeline_accepts_controller(self) -> None:
        """GenerationPipeline constructor accepts an optional controller parameter."""
        from mts.harness.core.controller import LoopController
        from mts.loop.generation_pipeline import GenerationPipeline

        controller = LoopController()
        pipeline = GenerationPipeline(
            orchestrator=MagicMock(),
            supervisor=MagicMock(),
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
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        runner.controller = controller
        summary = runner.run("grid_ctf", generations=1, run_id="override_test")
        assert summary.generations_executed == 1
