"""`autoctx train-r1` CLI: the R1 recipe (distillation cold-start -> RLVR) as one command.

Pins the CLI contract without running MLX: the pipeline function is patched and we assert
the command surfaces both stage scores and the resumed adapter.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from autocontext.cli import app

runner = CliRunner()


def test_train_r1_json_success() -> None:
    from unittest.mock import patch

    fake = {
        "distill": {"avg_score": 0.30, "valid_rate": 1.0},
        "rlvr": {"avg_score": 0.50, "valid_rate": 1.0},
        "resume_adapter_file": "/runs/r1/distill/adapters/adapters.safetensors",
        "avg_score": 0.50,
        "valid_rate": 1.0,
    }
    with patch("autocontext.cli_train._run_r1", return_value=fake):
        result = runner.invoke(
            app,
            ["train-r1", "--json", "--scenario", "antichain", "--data", "d.jsonl", "--output-dir", "runs/r1"],
        )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["avg_score"] == 0.50
    assert data["distill"]["avg_score"] == 0.30
    assert data["rlvr"]["avg_score"] == 0.50
    assert data["resume_adapter_file"].endswith("distill/adapters/adapters.safetensors")


def test_train_r1_json_error_exits_1() -> None:
    from unittest.mock import patch

    with patch("autocontext.cli_train._run_r1", side_effect=RuntimeError("rlvr exploded")):
        result = runner.invoke(
            app,
            ["train-r1", "--json", "--scenario", "antichain", "--data", "d.jsonl", "--output-dir", "runs/r1"],
        )

    assert result.exit_code == 1


def test_run_r1_forwards_to_pipeline_with_stage_dirs() -> None:
    """The thin _run_r1 wrapper forwards CLI args to run_r1_pipeline verbatim."""
    from unittest.mock import patch

    from autocontext import cli_train

    captured: dict = {}

    def fake_pipeline(**kw):
        captured.update(kw)
        return {"distill": {}, "rlvr": {}, "avg_score": 0.0, "valid_rate": 0.0, "resume_adapter_file": None}

    with patch("autocontext.cli_train.run_r1_pipeline", fake_pipeline):
        cli_train._run_r1(
            scenario_name="antichain",
            data_path="d.jsonl",
            output_dir="runs/r1",
            base_model="mlx-community/Qwen2.5-3B-Instruct-4bit",
            variant="gspo",
            register_import="",
        )

    assert captured["scenario_name"] == "antichain"
    assert captured["output_dir"] == "runs/r1"
    assert captured["base_model"].endswith("Qwen2.5-3B-Instruct-4bit")
    # variant is an RLVR-stage option
    assert captured["rlvr_kwargs"]["variant"] == "gspo"
