"""autocontext training package — optional MLX-based distillation and autoresearch."""
from __future__ import annotations

from autocontext.training.types import MatchRecord, TrainingRecord

__all__ = ["HAS_MLX", "MatchRecord", "TrainingRecord"]

try:
    import mlx.core  # noqa: F401
    import mlx.nn  # noqa: F401

    HAS_MLX = True
except ImportError:
    HAS_MLX = False
