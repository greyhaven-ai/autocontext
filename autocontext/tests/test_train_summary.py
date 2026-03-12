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
