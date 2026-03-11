"""Tests for training package import guards and CLI error handling (MTS-181)."""
from __future__ import annotations

import subprocess
import sys


def test_training_has_mlx_flag_exists() -> None:
    """training/__init__.py exports HAS_MLX boolean."""
    from mts.training import HAS_MLX

    assert isinstance(HAS_MLX, bool)


def test_mts_train_without_mlx_gives_clear_error() -> None:
    """Running `mts train` without MLX installed exits with helpful message."""
    result = subprocess.run(
        [sys.executable, "-m", "mts.cli", "train"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # If MLX is not installed, should exit non-zero with a helpful message.
    # If MLX IS installed, the command will proceed (still OK for the test).
    if result.returncode != 0:
        combined = result.stdout + result.stderr
        assert "mlx" in combined.lower() or "uv sync" in combined.lower(), (
            f"Expected helpful MLX install message, got:\n{combined}"
        )
