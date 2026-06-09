"""Tests for training loop runner (AC-179) and CLI command (AC-180)."""

from __future__ import annotations

import json
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
        assert cfg.backend == "mlx"
        assert cfg.agent_provider == "anthropic"
        assert cfg.agent_model == ""
        assert cfg.val_select is False

    def test_custom_values(self) -> None:
        cfg = TrainingConfig(
            scenario="othello",
            data_path=Path("/tmp/train.jsonl"),
            time_budget=600,
            max_experiments=50,
            memory_limit_mb=8192,
            backend="cuda",
            agent_provider="deterministic",
            agent_model="custom-model",
        )
        assert cfg.scenario == "othello"
        assert cfg.time_budget == 600
        assert cfg.max_experiments == 50
        assert cfg.memory_limit_mb == 8192
        assert cfg.backend == "cuda"


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
        assert (workspace / "model.py").exists()  # architecture context for the agent loop
        assert (workspace / "program.md").exists()

    def test_deterministic_variant_tunes_model_shape_in_train_py(self, tmp_path: Path) -> None:
        """The deterministic agent path must still mutate model shape after the architecture
        moved to model.py: the MODEL_* knobs live in train.py exactly so this keeps working."""
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        source = "MODEL_DEPTH = 4\nMODEL_ASPECT_RATIO = 64\nMODEL_HEAD_DIM = 64\n"
        variant = runner._deterministic_train_py_variant(source, experiment_index=1)
        assert variant != source
        assert "MODEL_DEPTH = 5" in variant  # actually changed shape, not just appended a comment
        assert "# experiment-" not in variant

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

    def test_experiment_subprocess_receives_selected_backend(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            backend="cuda",
        )
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=completed) as mock_run:
            runner._run_experiment_subprocess(0)

        command = mock_run.call_args.args[0]
        backend_index = command.index("--backend")
        assert command[backend_index + 1] == "cuda"

    def test_experiment_subprocess_passes_loss_weight_flags(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            loss_weight_mode="softmax",
            loss_weight_temperature=0.5,
        )
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=completed) as mock_run:
            runner._run_experiment_subprocess(0)

        command = mock_run.call_args.args[0]
        mode_index = command.index("--loss-weight-by-score")
        assert command[mode_index + 1] == "softmax"
        temp_index = command.index("--loss-weight-temperature")
        assert command[temp_index + 1] == "0.5"

    def test_experiment_subprocess_passes_seed_flag(self, tmp_path: Path) -> None:
        """The trl seed must flow CLI -> TrainingConfig -> subprocess command (else seeded
        repeats via `autoctx train` can't differ)."""
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl", backend="trl", seed=7)
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=completed) as mock_run:
            runner._run_experiment_subprocess(0)

        command = mock_run.call_args.args[0]
        seed_index = command.index("--seed")
        assert command[seed_index + 1] == "7"

    def test_experiment_subprocess_omits_loss_weight_flags_when_uniform(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=completed) as mock_run:
            runner._run_experiment_subprocess(0)

        command = mock_run.call_args.args[0]
        assert "--loss-weight-by-score" not in command  # uniform default stays off the command line

    def test_experiment_subprocess_passes_train_steps_when_set(self, tmp_path: Path) -> None:
        """--train-steps must flow CLI -> TrainingConfig -> subprocess, else `autoctx train
        --backend mlxlm --train-steps 80` silently trains the adapter for the 8-step default."""
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl", backend="mlxlm", train_steps=80)
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=completed) as mock_run:
            runner._run_experiment_subprocess(0)

        command = mock_run.call_args.args[0]
        assert command[command.index("--train-steps") + 1] == "80"

    def test_experiment_subprocess_omits_train_steps_when_unset(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")  # train_steps=0
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=completed) as mock_run:
            runner._run_experiment_subprocess(0)

        # Unset stays off the command line so train.py applies its per-backend default.
        assert "--train-steps" not in mock_run.call_args.args[0]

    def test_experiment_subprocess_passes_learning_rate_when_set(self, tmp_path: Path) -> None:
        """--learning-rate must flow CLI -> TrainingConfig -> subprocess (the documented flag);
        else `autoctx train --backend mlxlm --learning-rate 1e-4` can't override the default."""
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl", backend="mlxlm", learning_rate=1e-4)
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=completed) as mock_run:
            runner._run_experiment_subprocess(0)

        command = mock_run.call_args.args[0]
        assert command[command.index("--learning-rate") + 1] == "0.0001"

    def test_experiment_subprocess_omits_learning_rate_when_unset(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl")  # learning_rate=0.0
        (tmp_path / "data.jsonl").write_text("{}\n")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=completed) as mock_run:
            runner._run_experiment_subprocess(0)

        assert "--learning-rate" not in mock_run.call_args.args[0]

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

    def test_build_training_result_publishes_best_checkpoint(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl", max_experiments=1)
        (tmp_path / "data.jsonl").write_text("{}\n{}\n", encoding="utf-8")
        checkpoint_path = tmp_path / "workspace" / "checkpoints" / "exp_0"
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        settings = AppSettings(
            knowledge_root=tmp_path / "knowledge",
            runs_root=tmp_path / "runs",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        best = ExperimentResult(
            experiment_index=0,
            avg_score=0.75,
            valid_rate=1.0,
            peak_memory_mb=1024.0,
            training_seconds=12.0,
            outcome=ExperimentOutcome.KEPT,
            checkpoint_path=checkpoint_path,
            summary_metrics={"num_params_M": 1.25, "num_steps": 8.0, "depth": 4.0},
        )

        with patch("autocontext.training.runner.load_settings", return_value=settings):
            result = runner.build_training_result([best])

        assert result.published_model_id is not None

        registry_path = settings.knowledge_root / "model_registry" / f"{result.published_model_id}.json"
        artifact_path = settings.knowledge_root / "_openclaw_artifacts" / f"{result.published_model_id}.json"
        assert registry_path.exists()
        assert artifact_path.exists()

        registry_record = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry_record["backend"] == "mlx"
        assert registry_record["runtime_types"] == ["provider", "pi"]

        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert artifact["artifact_type"] == "distilled_model"
        assert artifact["scenario"] == "grid_ctf"
        assert artifact["checkpoint_path"] == str(checkpoint_path)

    def test_build_training_result_respects_selected_backend(self, tmp_path: Path) -> None:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            max_experiments=1,
            backend="cuda",
        )
        (tmp_path / "data.jsonl").write_text("{}\n", encoding="utf-8")
        checkpoint_path = tmp_path / "workspace" / "checkpoints" / "exp_0"
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")

        settings = AppSettings(
            knowledge_root=tmp_path / "knowledge",
            runs_root=tmp_path / "runs",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        best = ExperimentResult(
            experiment_index=0,
            avg_score=0.75,
            valid_rate=1.0,
            peak_memory_mb=1024.0,
            training_seconds=12.0,
            outcome=ExperimentOutcome.KEPT,
            checkpoint_path=checkpoint_path,
            summary_metrics={"num_params_M": 1.25},
        )

        with patch("autocontext.training.runner.load_settings", return_value=settings):
            result = runner.build_training_result([best])

        registry_path = settings.knowledge_root / "model_registry" / f"{result.published_model_id}.json"
        registry_record = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry_record["backend"] == "cuda"
        assert registry_record["runtime_types"] == ["checkpoint"]

        from autocontext.training.model_registry import ModelRegistry, resolve_model

        registry = ModelRegistry(settings.knowledge_root)
        assert resolve_model(registry, scenario="grid_ctf", backend="cuda", runtime_type="provider") is None

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
        with patch("autocontext.cli_train._run_training") as mock_run:
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
                    "--scenario",
                    "grid_ctf",
                    "--data",
                    "data.jsonl",
                    "--time-budget",
                    "600",
                    "--max-experiments",
                    "50",
                    "--memory-limit",
                    "8192",
                    "--backend",
                    "cuda",
                    "--agent-provider",
                    "deterministic",
                    "--agent-model",
                    "custom-model",
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
            assert call_cfg.backend == "cuda"
            assert call_cfg.agent_provider == "deterministic"
            assert call_cfg.agent_model == "custom-model"

    def test_loss_weight_options_reach_config(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli_train._run_training") as mock_run:
            mock_run.return_value = TrainingResult(
                scenario="grid_ctf",
                total_experiments=1,
                kept_count=1,
                discarded_count=0,
                best_score=0.5,
                best_experiment_index=0,
                checkpoint_path=Path("/tmp/best"),
                results=[],
            )
            result = runner.invoke(
                app,
                ["train", "--scenario", "grid_ctf", "--loss-weight-by-score", "softmax", "--loss-weight-temperature", "0.5"],
            )
            assert result.exit_code == 0, result.output
            call_cfg = mock_run.call_args[0][0]
            assert call_cfg.loss_weight_mode == "softmax"
            assert call_cfg.loss_weight_temperature == 0.5

    def test_invalid_loss_weight_mode_rejected(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        # _run_training is patched so a non-zero exit can ONLY come from the BadParameter guard.
        with patch("autocontext.cli_train._run_training") as mock_run:
            result = runner.invoke(app, ["train", "--scenario", "grid_ctf", "--loss-weight-by-score", "bogus"])
        assert result.exit_code != 0
        mock_run.assert_not_called()

    def test_augmenter_option_reaches_config(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli_train._run_training") as mock_run:
            mock_run.return_value = TrainingResult(
                scenario="grid_ctf",
                total_experiments=1,
                kept_count=1,
                discarded_count=0,
                best_score=0.5,
                best_experiment_index=0,
                checkpoint_path=Path("/tmp/best"),
                results=[],
            )
            result = runner.invoke(app, ["train", "--scenario", "grid_ctf", "--augmenter", "pkg.mod:expand"])
            assert result.exit_code == 0, result.output
            assert mock_run.call_args[0][0].augmenter_spec == "pkg.mod:expand"

    def test_vocab_size_option_reaches_config(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli_train._run_training") as mock_run:
            mock_run.return_value = TrainingResult(
                scenario="grid_ctf",
                total_experiments=1,
                kept_count=1,
                discarded_count=0,
                best_score=0.5,
                best_experiment_index=0,
                checkpoint_path=Path("/tmp/best"),
                results=[],
            )
            result = runner.invoke(app, ["train", "--scenario", "grid_ctf", "--vocab-size", "4096"])
            assert result.exit_code == 0, result.output
            assert mock_run.call_args[0][0].vocab_size == 4096

    def test_too_small_vocab_size_rejected(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli_train._run_training") as mock_run:
            result = runner.invoke(app, ["train", "--scenario", "grid_ctf", "--vocab-size", "100"])
        assert result.exit_code != 0
        mock_run.assert_not_called()

    def test_softmax_zero_temperature_rejected(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli_train._run_training") as mock_run:
            result = runner.invoke(
                app,
                ["train", "--scenario", "grid_ctf", "--loss-weight-by-score", "softmax", "--loss-weight-temperature", "0"],
            )
        assert result.exit_code != 0
        mock_run.assert_not_called()

    def test_missing_data_uses_default(self) -> None:
        from autocontext.cli import app

        runner = CliRunner()
        with patch("autocontext.cli_train._run_training") as mock_run:
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
        with patch("autocontext.cli_train._run_training") as mock_run:
            mock_run.side_effect = KeyboardInterrupt()
            result = runner.invoke(app, ["train", "--scenario", "grid_ctf"])
            # Should not crash — graceful exit
            assert result.exit_code in (0, 1), result.output
            assert "interrupted" in result.output.lower() or "best" in result.output.lower() or result.exit_code == 1


class TestValSelectSubprocess:
    """val_select must reach the train.py subprocess (and stay off by default)."""

    def _capture_command(self, tmp_path: Path, *, val_select: bool) -> list[str]:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            val_select=val_select,
        )
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=fake) as mock_run:
            runner._run_experiment_subprocess(0)
        return list(mock_run.call_args.args[0])

    def test_val_select_appends_flag(self, tmp_path: Path) -> None:
        command = self._capture_command(tmp_path, val_select=True)
        assert "--val-select" in command

    def test_no_val_select_omits_flag(self, tmp_path: Path) -> None:
        command = self._capture_command(tmp_path, val_select=False)
        assert "--val-select" not in command


class TestCurationSubprocess:
    """Elite/dedup curation flags must reach the train.py subprocess."""

    def _capture_command(self, tmp_path: Path, **config_kwargs: object) -> list[str]:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            **config_kwargs,
        )
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=fake) as mock_run:
            runner._run_experiment_subprocess(0)
        return list(mock_run.call_args.args[0])

    def test_defaults_omit_curation_flags(self, tmp_path: Path) -> None:
        command = self._capture_command(tmp_path)
        assert "--elite-fraction" not in command
        assert "--dedupe" not in command
        assert "--dedupe-near-threshold" not in command

    def test_elite_fraction_appended(self, tmp_path: Path) -> None:
        command = self._capture_command(tmp_path, elite_fraction=0.25)
        assert "--elite-fraction" in command
        assert command[command.index("--elite-fraction") + 1] == "0.25"

    def test_dedupe_flags_appended(self, tmp_path: Path) -> None:
        command = self._capture_command(tmp_path, dedupe=True, dedupe_near_threshold=0.8)
        assert "--dedupe" in command
        assert "--dedupe-near-threshold" in command
        assert command[command.index("--dedupe-near-threshold") + 1] == "0.8"

    def test_augmenter_spec_appended(self, tmp_path: Path) -> None:
        command = self._capture_command(tmp_path, augmenter_spec="pkg.mod:expand")
        assert command[command.index("--augmenter") + 1] == "pkg.mod:expand"

    def test_augmenter_spec_omitted_by_default(self, tmp_path: Path) -> None:
        assert "--augmenter" not in self._capture_command(tmp_path)

    def test_vocab_size_appended_when_non_default(self, tmp_path: Path) -> None:
        command = self._capture_command(tmp_path, vocab_size=4096)
        assert command[command.index("--vocab-size") + 1] == "4096"

    def test_vocab_size_omitted_at_default(self, tmp_path: Path) -> None:
        assert "--vocab-size" not in self._capture_command(tmp_path)  # 8192 default stays off the command line

    def test_experiment_env_includes_invocation_cwd_for_consumer_augmenters(self, tmp_path: Path) -> None:
        """The subprocess PYTHONPATH must carry the invocation cwd so a consumer-repo
        augmenter (importable from where the user ran the command) resolves once the
        subprocess runs from the workspace, not just the autocontext repo root."""
        import os
        from pathlib import Path as _Path

        from autocontext.training.runner import _REPO_ROOT

        cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl", augmenter_spec="my_pkg:expand")
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        parts = runner._experiment_env()["PYTHONPATH"].split(os.pathsep)
        assert str(_REPO_ROOT) in parts  # autocontext's own modules still resolve
        assert str(_Path.cwd()) in parts  # and the caller's cwd (where the augmenter lives)


class TestDataStatsProvenance:
    """Published data_stats records the curation settings + raw/curated counts."""

    def test_data_stats_includes_curation_settings_and_counts(self, tmp_path: Path) -> None:
        data_path = tmp_path / "data.jsonl"
        data_path.write_text("{}\n{}\n{}\n{}\n", encoding="utf-8")  # 4 raw records
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=data_path,
            elite_fraction=0.5,
            dedupe=True,
            dedupe_near_threshold=0.9,
        )
        runner = TrainingRunner(cfg, work_dir=tmp_path / "workspace")
        best = ExperimentResult(
            experiment_index=0,
            avg_score=0.5,
            valid_rate=1.0,
            peak_memory_mb=1.0,
            training_seconds=1.0,
            outcome=ExperimentOutcome.KEPT,
            summary_metrics={"num_records": 2.0},  # curated count from the subprocess summary
        )
        stats = runner._data_stats(best)
        assert stats["elite_fraction"] == 0.5
        assert stats["dedupe"] == 1.0
        assert stats["dedupe_near_threshold"] == 0.9
        assert stats["records"] == 4.0  # raw JSONL line count
        assert stats["curated_records"] == 2.0  # records actually used after curation


def test_score_conditioned_flag_in_subprocess(tmp_path: Path) -> None:
    """--score-conditioned reaches the train.py subprocess only when enabled."""

    def capture(score_conditioned: bool) -> list[str]:
        cfg = TrainingConfig(
            scenario="grid_ctf",
            data_path=tmp_path / "data.jsonl",
            score_conditioned=score_conditioned,
        )
        runner = TrainingRunner(cfg, work_dir=tmp_path / "ws")
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("autocontext.training.runner.subprocess.run", return_value=fake) as mock_run:
            runner._run_experiment_subprocess(0)
        return list(mock_run.call_args.args[0])

    assert "--score-conditioned" in capture(True)
    assert "--score-conditioned" not in capture(False)
