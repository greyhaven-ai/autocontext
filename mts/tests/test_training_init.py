"""Tests for training package import guards and CLI error handling (MTS-181)."""
from __future__ import annotations

import subprocess
import sys

from mts.training import HAS_MLX


def test_training_has_mlx_flag_exists() -> None:
    """training/__init__.py exports HAS_MLX boolean."""
    from mts.training import HAS_MLX

    assert isinstance(HAS_MLX, bool)


def test_mts_train_without_mlx_gives_clear_error() -> None:
    """Running `mts train` exits with an honest message for the current environment."""
    result = subprocess.run(
        [sys.executable, "-m", "mts.cli", "train"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0

    if not HAS_MLX:
        assert "mlx" in combined.lower() or "uv sync" in combined.lower(), (
            f"Expected helpful MLX install message, got:\n{combined}"
        )
    else:
        assert "runner" in combined.lower() or "not wired" in combined.lower(), (
            f"Expected clear scaffold-not-wired message, got:\n{combined}"
        )
