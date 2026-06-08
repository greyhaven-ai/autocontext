"""Pure (mlx-free, torch-free) helpers shared by the distillation backends.

Lives apart from ``on_policy_distill`` (which imports ``mlx.core`` at module scope) so the
cross-platform ``trl`` backend can reuse these without pulling in MLX on a non-Mac host.
"""

from __future__ import annotations

import math


def assert_vocab_compatible(student_vocab: int, teacher_vocab: int) -> None:
    """Reject a teacher/student tokenizer mismatch up front with a clear message.

    On-policy distillation compares per-token distributions, so the two models must share
    a vocabulary; mismatched sizes otherwise surface as an opaque logit-shape error deep in
    the loss after both (large) models have loaded.
    """
    if student_vocab != teacher_vocab:
        raise ValueError(
            "on-policy distillation requires a shared tokenizer: student vocab "
            f"{student_vocab} != teacher vocab {teacher_vocab}. Use teacher and student "
            "from the same model family."
        )


def build_trl_metrics(
    *,
    training_loss: float,
    num_steps: float,
    training_seconds: float,
    peak_memory_mb: float,
) -> dict[str, float]:
    """Assemble TRL backend summary metrics with a FINITE keep/discard score.

    The HF checkpoint is not assessed in-scenario here, so ``avg_score`` is a loss-based
    proxy: negative training loss (lower loss -> higher score), finite so the training
    runner's ``avg_score > best`` keep/discard comparison actually selects a checkpoint
    (a NaN score compares false against every baseline and would discard everything).
    """
    score = -training_loss if math.isfinite(training_loss) else 0.0
    return {
        "avg_score": score,
        "valid_rate": 1.0,
        "training_loss": training_loss,
        "val_loss": float("nan"),
        "training_seconds": training_seconds,
        "peak_memory_mb": peak_memory_mb,
        "num_steps": num_steps,
        "num_params_m": 0.0,
        "depth": 0.0,
    }
