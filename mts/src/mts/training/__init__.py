"""MTS training package — optional MLX-based distillation and autoresearch."""
from __future__ import annotations

from mts.training.types import MatchRecord, TrainingRecord

__all__ = ["HAS_MLX", "MatchRecord", "TrainingRecord"]

try:
    import mlx.core  # type: ignore[import-not-found]  # noqa: F401
    import mlx.nn  # type: ignore[import-not-found]  # noqa: F401

    HAS_MLX = True
except ImportError:
    HAS_MLX = False
