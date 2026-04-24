"""CUDA backend routing tests for autoresearch training."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

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
    with patch.object(train_module, "_run_cuda_training", return_value=_summary_metrics()) as run_cuda:
        result = train_module.run_training(
            scenario_name="grid_ctf",
            data_path=tmp_path / "training.jsonl",
            output_dir=tmp_path / "out",
            time_budget=1,
            memory_limit_mb=1024,
            backend="cuda",
        )

    assert result["num_steps"] == 2.0
    run_cuda.assert_called_once()


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

    assert train_module._require_torch_cuda() is fake_torch


def test_require_torch_cuda_rejects_unavailable_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    with pytest.raises(RuntimeError, match="torch.cuda.is_available"):
        train_module._require_torch_cuda()
