"""Tests for train.py format_summary (runs without MLX)."""

from __future__ import annotations

import importlib


def _train_module():
    return importlib.import_module("autocontext.training.autoresearch.train")


def _runner_module():
    return importlib.import_module("autocontext.training.runner")


def test_format_summary_no_mlx() -> None:
    """format_summary works even without MLX installed."""
    result = _train_module().format_summary(
        avg_score=0.85,
        valid_rate=0.99,
        training_seconds=60.0,
        peak_memory_mb=512.0,
        num_steps=500,
        num_params_m=2.0,
        depth=4,
    )
    assert "avg_score: 0.8500" in result
    assert "valid_rate: 0.9900" in result
    assert "depth: 4" in result
    assert "val_loss" not in result


def test_format_summary_includes_val_loss_when_provided() -> None:
    result = _train_module().format_summary(
        avg_score=0.85,
        valid_rate=0.99,
        training_seconds=60.0,
        peak_memory_mb=512.0,
        num_steps=500,
        num_params_m=2.0,
        depth=4,
        val_loss=1.2345,
    )
    assert "val_loss: 1.2345" in result


def test_parse_summary_picks_up_val_loss() -> None:
    train = _train_module()
    runner_mod = _runner_module()
    block = train.format_summary(
        avg_score=0.5,
        valid_rate=1.0,
        training_seconds=1.0,
        peak_memory_mb=10.0,
        num_steps=3,
        num_params_m=0.1,
        depth=4,
        val_loss=0.789,
    )
    runner = runner_mod.TrainingRunner.__new__(runner_mod.TrainingRunner)
    parsed = runner_mod.TrainingRunner.parse_summary(runner, block)
    assert parsed is not None
    assert parsed["val_loss"] == 0.789


def test_format_summary_includes_token_pressure_metrics_when_provided() -> None:
    result = _train_module().format_summary(
        avg_score=0.5,
        valid_rate=1.0,
        training_seconds=1.0,
        peak_memory_mb=10.0,
        num_steps=3,
        num_params_m=0.1,
        depth=4,
        token_pressure_positive_ratio=0.75,
        token_pressure_negative_ratio=0.25,
        token_pressure_shock_spike_count=2,
    )

    assert "token_pressure_positive_ratio: 0.7500" in result
    assert "token_pressure_negative_ratio: 0.2500" in result
    assert "token_pressure_shock_spike_count: 2" in result


def test_default_train_steps_distinguishes_from_scratch_vs_adapter() -> None:
    train = _train_module()
    assert train._default_train_steps("mlx") == 8
    assert train._default_train_steps("cuda") == 8
    for adapter in ("mlxlm", "opd", "grpo", "trl"):
        assert train._default_train_steps(adapter) == 100, adapter


def test_default_learning_rate_per_backend() -> None:
    train = _train_module()
    assert train._default_learning_rate("mlx") == 1e-3
    assert train._default_learning_rate("cuda") == 1e-3
    assert train._default_learning_rate("mlxlm") == 1e-4
    for rlvr in ("opd", "grpo", "trl"):
        assert train._default_learning_rate(rlvr) == 1e-5, rlvr
