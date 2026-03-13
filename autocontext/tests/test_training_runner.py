"""Tests for training loop runner (AC-179) and CLI command (AC-180)."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from autocontext.config.settings import AppSettings
from autocontext.training.runner import (
    ExperimentOutcome,
    ExperimentResult,
    TrainingConfig,
    TrainingResult,
    TrainingRunner,
)

# ---------------------------------------------------------------------------
# TrainingConfig
# ---------------------------------------------------------------------------


class TestTrainingConfig:
    def test_defaults(self) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=Path("data.jsonl"))
        assert cfg.scenario == "grid_ctf"
        assert cfg.data_path == Path("data.jsonl")
        assert cfg.time_budget == 300
        assert cfg.max_experiments == 0
        assert cfg.memory_limit_mb == 16384
        assert cfg.agent_provider == "anthropic"
        assert cfg.agent_model == ""

    def test_custom_values(self) -> None:
        cfg = TrainingConfig(
            scenario="othello",
            data_path=Path("/tmp/train.jsonl"),
            time_budget=600,
            max_experiments=50,
            memory_limit_mb=8192,
            agent_provider="deterministic",
            agent_model="custom-model",
        )
        assert cfg.scenario == "othello"
        assert cfg.time_budget == 600
        assert cfg.max_experiments == 50
        assert cfg.memory_limit_mb == 8192


# ---------------------------------------------------------------------------
# ExperimentResult
# ---------------------------------------------------------------------------


class TestExperimentResult:
    def test_kept_result(self) -> None:
        r = ExperimentResult(
            experiment_index=1,
            avg_score=0.85,
            valid_rate=0.95,
            peak_memory_mb=4096.0,
            training_seconds=120.5,
            outcome=ExperimentOutcome.KEPT,
        )
        assert r.outcome == ExperimentOutcome.KEPT
        assert r.avg_score == 0.85

    def test_discarded_result(self) -> None:
        r = ExperimentResult(
            experiment_index=2,
            avg_score=0.50,
            valid_rate=0.80,
            peak_memory_mb=2048.0,
            training_seconds=60.0,
            outcome=ExperimentOutcome.DISCARDED,
        )
        assert r.outcome == ExperimentOutcome.DISCARDED

    def test_error_result(self) -> None:
        r = ExperimentResult(
            experiment_index=3,
            avg_score=0.0,
            valid_rate=0.0,
            peak_memory_mb=0.0,
            training_seconds=0.0,
            outcome=ExperimentOutcome.ERROR,
            error_message="timeout",
        )
        assert r.outcome == ExperimentOutcome.ERROR
        assert r.error_message == "timeout"


# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------


class TestWorkspaceSetup:
    def test_copies_template_files(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        runner.setup_workspace()

        workspace = tmp_path / "workspace"
        assert (workspace / "train.py").exists()
        assert (workspace / "prepare.py").exists()
        assert (workspace / "program.md").exists()

    def test_creates_git_branch(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=workspace)
        runner.setup_workspace()

        # Runner should have initialized its own git repo and created a branch
        assert (workspace / ".git").exists()
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
        )
        branch = result.stdout.strip()
        assert branch.startswith("autocontext-train/grid_ctf/")

    def test_renders_program_md_with_scenario(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        runner.setup_workspace()

        program_md = (tmp_path / "workspace" / "program.md").read_text()
        assert "grid_ctf" in program_md
        assert "300" in program_md  # time_budget
        assert "16384" in program_md  # memory_limit

    def test_initializes_results_tsv(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        runner.setup_workspace()

        tsv_path = tmp_path / "workspace" / "results.tsv"
        assert tsv_path.exists()
        header = tsv_path.read_text().strip().split("\n")[0]
        assert "experiment" in header
        assert "avg_score" in header
        assert "outcome" in header


# ---------------------------------------------------------------------------
# Git state machine
# ---------------------------------------------------------------------------


class TestGitStateMachine:
    @pytest.fixture()
    def git_workspace(self, tmp_path: Path) -> tuple[TrainingRunner, Path]:
        """Create a runner with an initialized git workspace."""
        workspace = tmp_path / "workspace"
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=workspace)
        runner.setup_workspace()
        return runner, workspace

    def test_keep_preserves_commit(self, git_workspace: tuple[TrainingRunner, Path]) -> None:
        runner, workspace = git_workspace
        # Simulate an experiment: modify train.py and commit
        (workspace / "train.py").write_text("# improved v1\n")
        runner._git_commit("experiment 1")

        commit_before = runner._git_head_sha()
        runner.keep_experiment()
        commit_after = runner._git_head_sha()

        # HEAD should still be the same commit (keep = do nothing to git)
        assert commit_before == commit_after

    def test_discard_resets_head(self, git_workspace: tuple[TrainingRunner, Path]) -> None:
        runner, workspace = git_workspace
        head_before = runner._git_head_sha()

        # Simulate an experiment: modify and commit
        (workspace / "train.py").write_text("# bad experiment\n")
        runner._git_commit("bad experiment")
        assert runner._git_head_sha() != head_before

        runner.discard_experiment()
        assert runner._git_head_sha() == head_before

    def test_record_result_appends_to_tsv(self, git_workspace: tuple[TrainingRunner, Path]) -> None:
        runner, workspace = git_workspace
        result = ExperimentResult(
            experiment_index=0,
            avg_score=0.75,
            valid_rate=0.90,
            peak_memory_mb=4096.0,
            training_seconds=100.0,
            outcome=ExperimentOutcome.KEPT,
        )
        runner.record_result(result)

        tsv_path = workspace / "results.tsv"
        lines = tsv_path.read_text().strip().split("\n")
        assert len(lines) == 2  # header + 1 result
        assert "0.75" in lines[1]
        assert "kept" in lines[1]


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


class TestConstraints:
    def test_max_experiments_respected(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            max_experiments=3,
        )
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        assert runner.should_stop(experiment_count=3)
        assert not runner.should_stop(experiment_count=2)

    def test_max_experiments_zero_means_unlimited(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            max_experiments=0,
        )
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        assert not runner.should_stop(experiment_count=1000)

    def test_time_budget_subprocess_timeout(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            time_budget=10,
        )
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        # Subprocess timeout should be 2x the time budget (safety margin)
        assert runner.subprocess_timeout == 20

    def test_convergence_nudge_after_consecutive_discards(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        # Not enough discards yet
        assert not runner.needs_convergence_nudge(consecutive_discards=9)
        # Exactly 10 triggers nudge
        assert runner.needs_convergence_nudge(consecutive_discards=10)
        # More than 10 also triggers
        assert runner.needs_convergence_nudge(consecutive_discards=15)


# ---------------------------------------------------------------------------
# Summary parsing
# ---------------------------------------------------------------------------


class TestSummaryParsing:
    def test_parse_valid_summary(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        stdout = textwrap.dedent("""\
            Some training output...
            === TRAINING SUMMARY ===
            avg_score: 0.8500
            valid_rate: 0.9500
            training_seconds: 120.5
            peak_memory_mb: 4096.0
            num_steps: 500
            num_params_M: 12.50
            depth: 4
            ========================
        """)
        result = runner.parse_summary(stdout)
        assert result is not None
        assert result["avg_score"] == pytest.approx(0.85)
        assert result["valid_rate"] == pytest.approx(0.95)
        assert result["peak_memory_mb"] == pytest.approx(4096.0)

    def test_parse_missing_summary_returns_none(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        assert runner.parse_summary("no summary here") is None


# ---------------------------------------------------------------------------
# TrainingResult
# ---------------------------------------------------------------------------


class TestTrainingResult:
    def test_best_checkpoint_from_results(self) -> None:
        results = [
            ExperimentResult(0, 0.5, 0.9, 4096, 100, ExperimentOutcome.KEPT),
            ExperimentResult(1, 0.8, 0.95, 4096, 110, ExperimentOutcome.KEPT),
            ExperimentResult(2, 0.6, 0.92, 4096, 105, ExperimentOutcome.DISCARDED),
        ]
        tr = TrainingResult(
            scenario="grid_ctf",
            total_experiments=3,
            kept_count=2,
            discarded_count=1,
            best_score=0.8,
            best_experiment_index=1,
            checkpoint_path=Path("/tmp/checkpoint"),
            results=results,
        )
        assert tr.best_score == 0.8
        assert tr.best_experiment_index == 1
        assert tr.kept_ratio == pytest.approx(2 / 3)

    def test_empty_results(self) -> None:
        tr = TrainingResult(
            scenario="grid_ctf",
            total_experiments=0,
            kept_count=0,
            discarded_count=0,
            best_score=0.0,
            best_experiment_index=-1,
            checkpoint_path=None,
            results=[],
        )
        assert tr.kept_ratio == 0.0
        assert tr.checkpoint_path is None


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------


class TestTrainingLoop:
    def test_run_raises_on_failed_baseline(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl", max_experiments=1)
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        failed = ExperimentResult(
            experiment_index=0,
            avg_score=0.0,
            valid_rate=0.0,
            peak_memory_mb=0.0,
            training_seconds=0.0,
            outcome=ExperimentOutcome.ERROR,
            error_message="MLX is required",
        )

        with patch.object(runner, "_execute_experiment", return_value=failed):
            with pytest.raises(RuntimeError, match="MLX is required"):
                runner.run()

    def test_run_keeps_best_and_discards_regressions(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl", max_experiments=3)
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        baseline = ExperimentResult(
            experiment_index=0,
            avg_score=0.5,
            valid_rate=1.0,
            peak_memory_mb=1024.0,
            training_seconds=1.0,
            outcome=ExperimentOutcome.KEPT,
            checkpoint_path=tmp_path / "workspace" / "checkpoints" / "exp_0",
        )
        regressed = ExperimentResult(
            experiment_index=1,
            avg_score=0.4,
            valid_rate=1.0,
            peak_memory_mb=1024.0,
            training_seconds=1.0,
            outcome=ExperimentOutcome.DISCARDED,
        )
        improved = ExperimentResult(
            experiment_index=2,
            avg_score=0.8,
            valid_rate=1.0,
            peak_memory_mb=1024.0,
            training_seconds=1.0,
            outcome=ExperimentOutcome.KEPT,
            checkpoint_path=tmp_path / "workspace" / "checkpoints" / "exp_2",
        )

        with (
            patch.object(runner, "_execute_experiment", side_effect=[baseline, regressed, improved]),
            patch.object(runner, "_build_agent_client", return_value=object()),  # type: ignore[arg-type]
            patch.object(
                runner,
                "_propose_train_py",
                side_effect=["# experiment 1\n", "# experiment 2\n"],
            ),
            patch.object(runner, "discard_experiment") as mock_discard,
        ):
            result = runner.run()

        assert result.total_experiments == 3
        assert result.kept_count == 2
        assert result.discarded_count == 1
        assert result.best_score == pytest.approx(0.8)
        assert result.best_experiment_index == 2
        assert result.checkpoint_path == improved.checkpoint_path
        mock_discard.assert_called_once()

    def test_propose_train_py_uses_competitor_model_when_agent_model_empty(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            agent_provider="anthropic",
            agent_model="",
        )
        (tmp_path / "data.jsonl").write_text("{}\n")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "train.py").write_text("print('hello')\n", encoding="utf-8")
        (workspace / "program.md").write_text("program", encoding="utf-8")
        (workspace / "results.tsv").write_text("experiment\tavg_score\n", encoding="utf-8")
        runner = TrainingRunner(cfg, work_dir=workspace)

        class StubClient:
            def __init__(self) -> None:
                self.model: str | None = None

            def generate(
                self,
                *,
                model: str,
                prompt: str,
                max_tokens: int,
                temperature: float,
                role: str = "",
            ) -> object:
                del prompt, max_tokens, temperature, role
                self.model = model

                class Response:
                    text = "```python\nprint('updated')\n```"

                return Response()

        client = StubClient()
        with patch(
            "autocontext.training.runner.load_settings",
            return_value=AppSettings(model_competitor="fallback-competitor"),
        ):
            updated = runner._propose_train_py(client, experiment_index=1, consecutive_discards=0)

        assert client.model == "fallback-competitor"
        assert "updated" in updated


# ---------------------------------------------------------------------------
# CLI (AC-180)
# ---------------------------------------------------------------------------


class TestTrainCLI:
    def test_parses_all_arguments(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli._run_training") as mock_run:
            mock_run.return_value = TrainingResult(
                scenario="grid_ctf",
                total_experiments=5,
                kept_count=3,
                discarded_count=2,
                best_score=0.85,
                best_experiment_index=3,
                checkpoint_path=Path("/tmp/best"),
                results=[],
            )
            result = runner.invoke(
                app,
                [
                    "train",
                    "--scenario", "grid_ctf",
                    "--data", "data.jsonl",
                    "--time-budget", "600",
                    "--max-experiments", "50",
                    "--memory-limit", "8192",
                    "--agent-provider", "deterministic",
                    "--agent-model", "custom-model",
                ],
            )
            assert result.exit_code == 0, result.output
            mock_run.assert_called_once()
            call_cfg = mock_run.call_args[0][0]
            assert isinstance(call_cfg, TrainingConfig)
            assert call_cfg.scenario == "grid_ctf"
            assert call_cfg.time_budget == 600
            assert call_cfg.max_experiments == 50
            assert call_cfg.memory_limit_mb == 8192
            assert call_cfg.agent_provider == "deterministic"
            assert call_cfg.agent_model == "custom-model"

    def test_missing_data_uses_default(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli._run_training") as mock_run:
            mock_run.return_value = TrainingResult(
                scenario="grid_ctf",
                total_experiments=0,
                kept_count=0,
                discarded_count=0,
                best_score=0.0,
                best_experiment_index=-1,
                checkpoint_path=None,
                results=[],
            )
            result = runner.invoke(app, ["train", "--scenario", "grid_ctf"])
            assert result.exit_code == 0, result.output

    def test_keyboard_interrupt_handled(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli._run_training") as mock_run:
            mock_run.side_effect = KeyboardInterrupt()
            result = runner.invoke(app, ["train", "--scenario", "grid_ctf"])
            # Should not crash — graceful exit
            assert result.exit_code in (0, 1), result.output
            assert "interrupted" in result.output.lower() or "best" in result.output.lower() or result.exit_code == 1
