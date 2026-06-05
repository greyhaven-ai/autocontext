"""CUDA backend routing tests for autoresearch training."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from autocontext.training.autoresearch import cuda as cuda_module
from autocontext.training.autoresearch import train as train_module


def _summary_metrics() -> dict[str, float]:
    return {
        "avg_score": 0.1,
        "valid_rate": 0.2,
        "training_seconds": 1.0,
        "peak_memory_mb": 32.0,
        "num_steps": 2.0,
        "num_params_m": 0.5,
        "depth": 4.0,
    }


def test_parser_accepts_cuda_backend() -> None:
    args = train_module._build_parser().parse_args(
        [
            "--scenario",
            "grid_ctf",
            "--data",
            "training.jsonl",
            "--output-dir",
            "out",
            "--backend",
            "cuda",
        ]
    )

    assert args.backend == "cuda"


def test_run_training_routes_cuda_backend(tmp_path: Path) -> None:
    # Routing/dispatch must work without the cuda extras installed: the dependency
    # preflight runs inside run_cuda_training (patched here), not in run_training.
    with patch.object(cuda_module, "run_cuda_training", return_value=_summary_metrics()) as run_cuda:
        result = train_module.run_training(
            scenario_name="grid_ctf",
            data_path=tmp_path / "training.jsonl",
            output_dir=tmp_path / "out",
            time_budget=1,
            memory_limit_mb=1024,
            backend="cuda",
            assess_temperature=1.2,
            assess_top_k=20,
        )

    assert result["num_steps"] == 2.0
    run_cuda.assert_called_once()
    # sampling params are forwarded to the CUDA backend, not silently dropped
    fwd = run_cuda.call_args.kwargs
    assert fwd["assess_temperature"] == 1.2
    assert fwd["assess_top_k"] == 20


def test_run_training_rejects_val_select_on_cuda(tmp_path: Path) -> None:
    # val_select is MLX-only; the cuda path must reject it explicitly (not silently drop)
    with pytest.raises(ValueError, match="MLX-only"):
        train_module.run_training(
            scenario_name="grid_ctf",
            data_path=tmp_path / "training.jsonl",
            output_dir=tmp_path / "out",
            time_budget=1,
            memory_limit_mb=1024,
            backend="cuda",
            val_select=True,
        )


def test_run_training_rejects_loss_weighting_on_mlxlm(tmp_path: Path) -> None:
    # Reward-weighted regression is mlx/cuda-only; the mlxlm guard fires before the
    # mlx_lm import, so this holds without MLX/torch/mlx_lm installed.
    with pytest.raises(NotImplementedError, match="mlx/cuda-only"):
        train_module.run_training(
            scenario_name="grid_ctf",
            data_path=tmp_path / "training.jsonl",
            output_dir=tmp_path / "out",
            time_budget=1,
            memory_limit_mb=1024,
            backend="mlxlm",
            loss_weight_mode="linear",
        )


def test_run_training_rejects_custom_vocab_size_on_mlxlm(tmp_path: Path) -> None:
    # --vocab-size only applies to the from-scratch BPE backends; mlxlm uses the
    # pretrained model's tokenizer. The guard fires before the mlx_lm import.
    with pytest.raises(NotImplementedError, match="pretrained model's tokenizer"):
        train_module.run_training(
            scenario_name="grid_ctf",
            data_path=tmp_path / "training.jsonl",
            output_dir=tmp_path / "out",
            time_budget=1,
            memory_limit_mb=1024,
            backend="mlxlm",
            vocab_size=4096,
        )


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"elite_fraction": 1.5}, "elite_fraction"),
        ({"elite_fraction": 0.0}, "elite_fraction"),
        ({"elite_fraction": -1.0}, "elite_fraction"),
        ({"dedupe_near_threshold": 0.0}, "dedupe_near_threshold"),
        ({"dedupe_near_threshold": 1.5}, "dedupe_near_threshold"),
        # vocab_size lower bound is enforced in run_training (not just the Typer wrapper), so
        # direct Python callers + `python train.py --vocab-size 100` are guarded too.
        ({"vocab_size": 100}, "vocab_size must be >= 256"),
        ({"vocab_size": 0}, "vocab_size must be >= 256"),
    ],
)
def test_run_training_rejects_out_of_range_curation(tmp_path: Path, kwargs: dict, match: str) -> None:
    # Validation happens before backend dispatch, so this holds without MLX/torch.
    with pytest.raises(ValueError, match=match):
        train_module.run_training(
            scenario_name="grid_ctf",
            data_path=tmp_path / "training.jsonl",
            output_dir=tmp_path / "out",
            time_budget=1,
            memory_limit_mb=1024,
            backend="mlx",
            **kwargs,
        )


def test_run_training_rejects_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported training backend"):
        train_module.run_training(
            scenario_name="grid_ctf",
            data_path=tmp_path / "training.jsonl",
            output_dir=tmp_path / "out",
            time_budget=1,
            memory_limit_mb=1024,
            backend="not-real",
        )


def test_require_torch_cuda_accepts_cuda_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert cuda_module.require_torch_cuda() is fake_torch


def test_require_torch_cuda_rejects_unavailable_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    with pytest.raises(RuntimeError, match="torch.cuda.is_available"):
        cuda_module.require_torch_cuda()
