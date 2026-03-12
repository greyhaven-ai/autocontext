"""Tests for training package import guards and CLI error handling (MTS-181)."""
from __future__ import annotations

import subprocess
import sys

from autocontext.training import HAS_MLX


def test_training_has_mlx_flag_exists() -> None:
    """training/__init__.py exports HAS_MLX boolean."""
    from autocontext.training import HAS_MLX

    assert isinstance(HAS_MLX, bool)


def test_mts_train_runs_successfully() -> None:
    """Running `autoctx train` either trains or fails honestly when MLX is unavailable."""
    result = subprocess.run(
        [sys.executable, "-m", "autocontext.cli", "train"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = result.stdout + result.stderr
    if HAS_MLX:
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}:\n{combined}"
        assert "training summary" in combined.lower(), (
            f"Expected training summary in output, got:\n{combined}"
        )
    else:
        assert result.returncode == 1, f"Expected honest failure without MLX, got {result.returncode}:\n{combined}"
        assert "mlx is required" in combined.lower(), f"Expected MLX guidance in output, got:\n{combined}"
