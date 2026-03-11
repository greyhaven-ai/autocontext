"""MTS training package — optional MLX-based distillation and autoresearch."""
from __future__ import annotations

__all__ = ["HAS_MLX"]

try:
    import mlx.core  # type: ignore[import-not-found]  # noqa: F401
    import mlx.nn  # type: ignore[import-not-found]  # noqa: F401

    HAS_MLX = True
except ImportError:
    HAS_MLX = False
