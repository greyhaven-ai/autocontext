"""Tests for train.py format_summary (runs without MLX)."""

from __future__ import annotations


def test_format_summary_no_mlx() -> None:
    """format_summary works even without MLX installed."""
    from autocontext.training.autoresearch.train import format_summary

    result = format_summary(
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
    # val_loss omitted when not provided (keeps existing callers unchanged)
    assert "val_loss" not in result


def test_format_summary_includes_val_loss_when_provided() -> None:
    """When val_loss is provided it is emitted so the runner/CLI can surface it."""
    from autocontext.training.autoresearch.train import format_summary

    result = format_summary(
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
    """TrainingRunner.parse_summary surfaces the val_loss line from the block."""
    from autocontext.training.autoresearch.train import format_summary
    from autocontext.training.runner import TrainingRunner

    block = format_summary(
        avg_score=0.5,
        valid_rate=1.0,
        training_seconds=1.0,
        peak_memory_mb=10.0,
        num_steps=3,
        num_params_m=0.1,
        depth=4,
        val_loss=0.789,
    )
    runner = TrainingRunner.__new__(TrainingRunner)  # parse_summary is pure; skip __init__
    parsed = TrainingRunner.parse_summary(runner, block)
    assert parsed is not None
    assert parsed["val_loss"] == 0.789
