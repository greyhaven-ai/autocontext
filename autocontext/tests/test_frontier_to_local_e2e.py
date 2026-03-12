"""AC-185: Milestone acceptance test — frontier-to-local export/train/route flow.

Validates the full pipeline end to end:
1. Run deterministic generation loop → SQLite data
2. Export training data → TrainingRecords
3. Train local model (mocked subprocess) → checkpoint
4. Deploy MLXProvider (mocked _load_model_and_tokenizer) → strategy generation
5. Validate strategy + execute match → meaningful score
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner
from autocontext.providers.base import CompletionResult
from autocontext.scenarios.grid_ctf import GridCtfScenario
from autocontext.training.export import export_training_data
from autocontext.training.runner import (
    ExperimentOutcome,
    ExperimentResult,
    TrainingConfig,
    TrainingRunner,
)
from autocontext.training.types import TrainingRecord

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path, **overrides: Any) -> AppSettings:
    """Deterministic settings rooted in tmp_path."""
    defaults: dict[str, Any] = dict(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


def _fake_tokenizer(*, end_token_id: int = 8196) -> MagicMock:
    """Build a mock tokenizer with encode/decode returning valid JSON."""
    tok = MagicMock()
    tok.end_token_id = end_token_id
    tok.vocab_size = 8197

    def _encode(text: str, **kwargs: Any) -> list[int]:
        return list(range(min(len(text), 50)))

    def _decode(token_ids: list[int]) -> str:
        return json.dumps({"aggression": 0.55, "defense": 0.35, "path_bias": 0.45})

    tok.encode.side_effect = _encode
    tok.decode.side_effect = _decode
    return tok


def _fake_model(*, vocab_size: int = 8197, seq_len: int = 2048) -> MagicMock:
    """Build a mock model that returns logits."""
    model = MagicMock()
    cfg = MagicMock()
    cfg.vocab_size = vocab_size
    cfg.seq_len = seq_len
    model.cfg = cfg
    return model


def _write_fake_checkpoint(model_dir: Path) -> None:
    """Write a minimal fake checkpoint structure."""
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "config.json").write_text(json.dumps({
        "depth": 4,
        "aspect_ratio": 64,
        "head_dim": 64,
        "n_kv_heads": 4,
        "vocab_size": 8197,
        "seq_len": 2048,
    }))
    (model_dir / "model.safetensors").write_bytes(b"FAKE_WEIGHTS")
    (model_dir / "tokenizer.json").write_text(json.dumps({"type": "BPE"}))


def _run_deterministic_loop(tmp_path: Path, *, generations: int = 3, run_id: str = "e2e_run") -> GenerationRunner:
    """Run a deterministic generation loop and return the runner (with sqlite/artifacts)."""
    settings = _make_settings(tmp_path)
    runner = GenerationRunner(settings)
    runner.migrate(_MIGRATIONS_DIR)
    runner.run(scenario_name="grid_ctf", generations=generations, run_id=run_id)
    return runner


# ---------------------------------------------------------------------------
# Stage 1: Discover & Generate
# ---------------------------------------------------------------------------


class TestStage1DeterministicRun:
    def test_produces_sqlite_data(self, tmp_path: Path) -> None:
        """Deterministic 3-gen run produces real SQLite data."""
        settings = _make_settings(tmp_path)
        runner = GenerationRunner(settings)
        runner.migrate(_MIGRATIONS_DIR)
        summary = runner.run(scenario_name="grid_ctf", generations=3, run_id="stage1_run")

        assert summary.generations_executed == 3
        assert summary.best_score > 0

        metrics = runner.sqlite.get_generation_metrics("stage1_run")
        assert len(metrics) == 3


# ---------------------------------------------------------------------------
# Stage 2: Export Training Data
# ---------------------------------------------------------------------------


class TestStage2Export:
    def test_export_training_data_from_run(self, tmp_path: Path) -> None:
        """Export training records from a completed run."""
        runner = _run_deterministic_loop(tmp_path, generations=3, run_id="export_run")

        records = list(export_training_data(
            runner.sqlite,
            runner.artifacts,
            run_id="export_run",
        ))

        assert len(records) >= 1
        for rec in records:
            assert isinstance(rec, TrainingRecord)
            assert rec.run_id == "export_run"
            assert rec.scenario == "grid_ctf"
            assert rec.strategy  # non-empty
            assert rec.score >= 0
            assert rec.gate_decision in ("advance", "retry", "rollback")
            assert "playbook" in rec.context
            assert "hints" in rec.context
            assert "trajectory" in rec.context


# ---------------------------------------------------------------------------
# Stage 3: Train (mocked subprocess)
# ---------------------------------------------------------------------------


class TestStage3Training:
    def test_training_runner_baseline(self, tmp_path: Path) -> None:
        """Training runner with mocked experiment returns a valid result."""
        data_path = tmp_path / "train_data.jsonl"
        data_path.write_text("{}\n")
        work_dir = tmp_path / "train_work"

        config = TrainingConfig(
            scenario="grid_ctf",
            data_path=data_path,
            max_experiments=1,
            agent_provider="deterministic",
        )
        runner = TrainingRunner(config, work_dir=work_dir)

        kept_result = ExperimentResult(
            experiment_index=0,
            avg_score=0.72,
            valid_rate=0.95,
            peak_memory_mb=2048.0,
            training_seconds=45.0,
            outcome=ExperimentOutcome.KEPT,
            checkpoint_path=work_dir / "checkpoints" / "exp_0",
        )

        with patch.object(runner, "_execute_experiment", return_value=kept_result):
            result = runner.run()

        assert result.total_experiments == 1
        assert result.kept_count == 1
        assert result.best_score == 0.72


# ---------------------------------------------------------------------------
# Stage 4: Deploy MLXProvider (mocked)
# ---------------------------------------------------------------------------


class TestStage4MLXDeploy:
    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_mlx_provider_generates_strategy(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """MLXProvider with mocked model produces a completion result."""
        model_dir = tmp_path / "checkpoint"
        _write_fake_checkpoint(model_dir)

        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        from autocontext.providers.mlx_provider import MLXProvider

        provider = MLXProvider(model_path=str(model_dir))

        strategy_json = json.dumps({"aggression": 0.55, "defense": 0.35, "path_bias": 0.45})
        with patch.object(provider, "_generate", return_value=strategy_json):
            result = provider.complete("", "Generate a grid_ctf strategy")

        assert isinstance(result, CompletionResult)
        assert result.text == strategy_json
        assert result.model == str(model_dir)


# ---------------------------------------------------------------------------
# Stage 5: Validate & Execute
# ---------------------------------------------------------------------------


class TestStage5ValidateExecute:
    def test_strategy_executes_in_scenario(self) -> None:
        """A valid strategy produces a match with a non-negative score."""
        scenario = GridCtfScenario()
        strategy = {"aggression": 0.55, "defense": 0.35, "path_bias": 0.45}
        result = scenario.execute_match(strategy, seed=42)

        assert isinstance(result.score, float)
        assert result.score >= 0
        assert not result.validation_errors


# ---------------------------------------------------------------------------
# Full Pipeline E2E
# ---------------------------------------------------------------------------


class TestFullPipeline:
    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_full_frontier_to_local_pipeline(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """Chain all 5 stages: generate → export → train → deploy → execute."""
        # Stage 1: Run deterministic generation loop
        runner = _run_deterministic_loop(tmp_path, generations=3, run_id="pipeline_run")

        # Stage 2: Export training data
        records = list(export_training_data(
            runner.sqlite,
            runner.artifacts,
            run_id="pipeline_run",
        ))
        assert len(records) >= 1

        # Stage 3: Write records to JSONL + run training (mocked)
        data_path = tmp_path / "exported_data.jsonl"
        with open(data_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps({
                    "run_id": rec.run_id,
                    "scenario": rec.scenario,
                    "generation_index": rec.generation_index,
                    "strategy": rec.strategy,
                    "score": rec.score,
                }) + "\n")

        train_work = tmp_path / "train_work"
        train_config = TrainingConfig(
            scenario="grid_ctf",
            data_path=data_path,
            max_experiments=1,
            agent_provider="deterministic",
        )
        train_runner = TrainingRunner(train_config, work_dir=train_work)

        checkpoint_dir = train_work / "checkpoints" / "exp_0"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        _write_fake_checkpoint(checkpoint_dir)

        kept = ExperimentResult(
            experiment_index=0,
            avg_score=0.72,
            valid_rate=0.95,
            peak_memory_mb=2048.0,
            training_seconds=45.0,
            outcome=ExperimentOutcome.KEPT,
            checkpoint_path=checkpoint_dir,
        )
        with patch.object(train_runner, "_execute_experiment", return_value=kept):
            train_result = train_runner.run()

        assert train_result.best_score > 0
        assert train_result.checkpoint_path is not None

        # Stage 4: Deploy MLXProvider from checkpoint
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        from autocontext.providers.mlx_provider import MLXProvider

        provider = MLXProvider(model_path=str(train_result.checkpoint_path))

        strategy_json = json.dumps({"aggression": 0.55, "defense": 0.35, "path_bias": 0.45})
        with patch.object(provider, "_generate", return_value=strategy_json):
            completion = provider.complete("", "Generate a grid_ctf strategy")
        assert completion.text

        # Stage 5: Parse output → validate → execute match
        strategy = json.loads(completion.text)

        scenario = GridCtfScenario()
        match_result = scenario.execute_match(strategy, seed=42)

        assert match_result.score >= 0
        assert isinstance(match_result.score, float)


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_export_kept_only_filters_rollbacks(self, tmp_path: Path) -> None:
        """kept_only=True filters out non-advance generations."""
        runner = _run_deterministic_loop(tmp_path, generations=3, run_id="kept_run")

        all_records = list(export_training_data(
            runner.sqlite, runner.artifacts, run_id="kept_run",
        ))
        kept_records = list(export_training_data(
            runner.sqlite, runner.artifacts, run_id="kept_run", kept_only=True,
        ))

        assert len(kept_records) <= len(all_records)
        for rec in kept_records:
            assert isinstance(rec, TrainingRecord)
            assert rec.gate_decision == "advance"

    def test_training_runner_max_experiments_one_runs_baseline_only(self, tmp_path: Path) -> None:
        """max_experiments=1 runs exactly one experiment (baseline only)."""
        data_path = tmp_path / "data.jsonl"
        data_path.write_text("{}\n")
        work_dir = tmp_path / "work"

        config = TrainingConfig(
            scenario="grid_ctf",
            data_path=data_path,
            max_experiments=1,
            agent_provider="deterministic",
        )
        runner = TrainingRunner(config, work_dir=work_dir)

        result_obj = ExperimentResult(
            experiment_index=0,
            avg_score=0.65,
            valid_rate=0.90,
            peak_memory_mb=1024.0,
            training_seconds=30.0,
            outcome=ExperimentOutcome.KEPT,
        )
        with patch.object(runner, "_execute_experiment", return_value=result_obj):
            result = runner.run()

        assert result.total_experiments == 1
        assert result.best_experiment_index == 0

    @patch("autocontext.providers.mlx_provider._load_model_and_tokenizer")
    def test_mlx_client_adapter_routes_correctly(self, mock_load: MagicMock, tmp_path: Path) -> None:
        """build_client_from_settings with agent_provider='mlx' returns MLXClient."""
        model_dir = tmp_path / "model"
        _write_fake_checkpoint(model_dir)
        mock_load.return_value = (_fake_model(), _fake_tokenizer())

        from autocontext.agents.llm_client import MLXClient, build_client_from_settings

        settings = AppSettings(
            agent_provider="mlx",
            mlx_model_path=str(model_dir),
        )
        client = build_client_from_settings(settings)
        assert isinstance(client, MLXClient)
