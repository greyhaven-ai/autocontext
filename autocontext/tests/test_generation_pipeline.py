"""Tests for GenerationPipeline — composed stage orchestrator."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autocontext.config.settings import AppSettings
from autocontext.loop.generation_runner import GenerationRunner


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
        from autocontext.loop.generation_pipeline import GenerationPipeline

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
        from autocontext.loop.generation_pipeline import GenerationPipeline

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
        from autocontext.harness.core.controller import LoopController
        from autocontext.loop.generation_pipeline import GenerationPipeline

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
        from autocontext.harness.core.controller import LoopController

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


class TestPipelineMetaHookCalls:
    def test_pipeline_records_llm_calls(self, tmp_path: Path) -> None:
        """After stage_agent_generation, record_llm_call is called for each role execution."""
        meta = MagicMock()
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
        runner._meta_optimizer = meta
        runner.run("grid_ctf", generations=1, run_id="meta_llm_test")
        assert meta.record_llm_call.call_count >= 1

    def test_pipeline_records_gate_decision(self, tmp_path: Path) -> None:
        """After gate evaluation, record_gate_decision is called."""
        meta = MagicMock()
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
        runner._meta_optimizer = meta
        runner.run("grid_ctf", generations=1, run_id="meta_gate_test")
        meta.record_gate_decision.assert_called_once()
        args = meta.record_gate_decision.call_args
        assert args[0][0] in ("advance", "retry", "rollback")
        assert isinstance(args[0][1], float)
        assert args[0][2] == 1

    def test_pipeline_records_generation(self, tmp_path: Path) -> None:
        """After stage_persistence, record_generation is called with full metrics."""
        meta = MagicMock()
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
        runner._meta_optimizer = meta
        runner.run("grid_ctf", generations=1, run_id="meta_gen_test")
        meta.record_generation.assert_called_once()
        kwargs = meta.record_generation.call_args[1]
        assert kwargs["generation"] == 1
        assert isinstance(kwargs["role_usages"], dict)
        assert isinstance(kwargs["gate_decision"], str)
        assert isinstance(kwargs["score_delta"], float)

    def test_pipeline_meta_error_does_not_crash(self, tmp_path: Path) -> None:
        """If MetaOptimizer raises, the generation still completes."""
        meta = MagicMock()
        meta.record_llm_call.side_effect = RuntimeError("meta boom")
        meta.record_gate_decision.side_effect = RuntimeError("meta boom")
        meta.record_generation.side_effect = RuntimeError("meta boom")
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
        runner._meta_optimizer = meta
        summary = runner.run("grid_ctf", generations=1, run_id="meta_err_test")
        assert summary.generations_executed == 1


class TestPipelineMetaIntegration:
    def test_full_generation_with_meta_enabled(self, tmp_path: Path) -> None:
        """Full generation with audit and cost tracking enabled produces data."""
        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            curator_enabled=False,
            audit_enabled=True,
            audit_log_path=tmp_path / "audit.ndjson",
            cost_tracking_enabled=True,
            meta_profiling_enabled=True,
            meta_min_observations=1,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=1, run_id="meta_e2e")
        assert summary.generations_executed == 1

        # Verify audit log was written
        audit_path = tmp_path / "audit.ndjson"
        assert audit_path.exists()
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) >= 1

        # Verify cost summary is available
        cost = runner._meta_optimizer.cost_summary()
        assert cost is not None
        assert cost.total_cost >= 0.0

        # Verify profiler has data
        profiles = runner._meta_optimizer.profiles()
        assert len(profiles) >= 1

        # Verify report is non-empty
        report = runner._meta_optimizer.report()
        assert "Meta-Optimization Report" in report
        assert "Cost" in report

    def test_meta_disabled_no_side_effects(self, tmp_path: Path) -> None:
        """With all meta features disabled, no audit files or data produced."""
        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            curator_enabled=False,
            audit_enabled=False,
            audit_log_path=tmp_path / "audit.ndjson",
            cost_tracking_enabled=False,
            meta_profiling_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=1, run_id="meta_off")
        assert summary.generations_executed == 1

        # No audit file created
        audit_path = tmp_path / "audit.ndjson"
        assert not audit_path.exists()

        # Cost summary is None
        assert runner._meta_optimizer.cost_summary() is None

        # Profiles are empty
        assert runner._meta_optimizer.profiles() == {}

    def test_multi_gen_accumulates_metrics(self, tmp_path: Path) -> None:
        """Multiple generations accumulate cost records correctly."""
        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            curator_enabled=False,
            audit_enabled=True,
            audit_log_path=tmp_path / "audit.ndjson",
            cost_tracking_enabled=True,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=2, run_id="meta_multi")
        assert summary.generations_executed == 2

        cost = runner._meta_optimizer.cost_summary()
        assert cost is not None
        assert cost.records_count >= 2
