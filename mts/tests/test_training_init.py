"""Tests for training package import guards and CLI error handling (MTS-181)."""
from __future__ import annotations

import subprocess
import sys


def test_training_has_mlx_flag_exists() -> None:
    """training/__init__.py exports HAS_MLX boolean."""
    from mts.training import HAS_MLX

    assert isinstance(HAS_MLX, bool)


def test_mts_train_runs_successfully() -> None:
    """Running `mts train` sets up workspace and exits cleanly."""
    result = subprocess.run(
        [sys.executable, "-m", "mts.cli", "train"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}:\n{combined}"
    assert "training summary" in combined.lower(), (
        f"Expected training summary in output, got:\n{combined}"
    )
