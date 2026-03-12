"""Tests for MLX GPT training model (MTS-176).

All tests are skipped when MLX is not installed (CI-safe).
Note: mx.eval() is MLX's lazy evaluation trigger, not Python's eval().
"""
from __future__ import annotations

import pytest

from autocontext.training import HAS_MLX

pytestmark = pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")


def test_model_instantiation() -> None:
    """GPTModel can be instantiated with default hyperparameters."""
    from autocontext.training.autoresearch.prepare import BASE_VOCAB_SIZE, SPECIAL_TOKEN_STRINGS
    from autocontext.training.autoresearch.train import GPTModel, ModelConfig

    cfg = ModelConfig()
    model = GPTModel(cfg)
    assert model is not None
    # Verify key config values
    assert cfg.depth == 4
    assert cfg.vocab_size == BASE_VOCAB_SIZE + len(SPECIAL_TOKEN_STRINGS)
    assert cfg.seq_len == 2048


def test_forward_pass_shape() -> None:
    """Forward pass produces logits with correct shape [batch, seq, vocab]."""
    import mlx.core as mx  # type: ignore[import-not-found]

    from autocontext.training.autoresearch.train import GPTModel, ModelConfig

    cfg = ModelConfig()
    model = GPTModel(cfg)
    batch_size = 2
    seq_len = 32  # shorter for test speed
    x = mx.zeros((batch_size, seq_len), dtype=mx.int32)
    logits = model(x)
    assert logits.shape == (batch_size, seq_len, cfg.vocab_size)


def test_training_step_reduces_loss() -> None:
    """A few training steps should reduce loss from the initial value."""
    import mlx.core as mx  # type: ignore[import-not-found]
    import mlx.nn as nn  # type: ignore[import-not-found]
    import mlx.optimizers as optim  # type: ignore[import-not-found]

    from autocontext.training.autoresearch.train import GPTModel, ModelConfig, compute_loss

    cfg = ModelConfig()
    model = GPTModel(cfg)

    optimizer = optim.AdamW(learning_rate=1e-3)
    loss_and_grad = nn.value_and_grad(model, compute_loss)

    # Generate random data
    rng = mx.random.key(42)
    x = mx.random.randint(0, cfg.vocab_size, shape=(4, 64), key=rng)
    y = mx.random.randint(0, cfg.vocab_size, shape=(4, 64), key=mx.random.key(99))

    # Initial loss — mx.eval triggers MLX lazy computation (not Python eval)
    initial_loss = compute_loss(model, x, y)
    mx.eval(initial_loss)  # noqa: S307 — MLX array materialization, not Python eval
    initial_val = initial_loss.item()

    # Train a few steps
    for _ in range(5):
        loss, grads = loss_and_grad(model, x, y)
        optimizer.update(model, grads)
        mx.eval(model.parameters(), optimizer.state, loss)  # noqa: S307

    final_loss = compute_loss(model, x, y)
    mx.eval(final_loss)  # noqa: S307
    assert final_loss.item() < initial_val, f"Loss did not decrease: {initial_val} -> {final_loss.item()}"


def test_summary_block_format() -> None:
    """format_summary() produces the expected summary block with required fields."""
    from autocontext.training.autoresearch.train import format_summary

    summary = format_summary(
        avg_score=0.75,
        valid_rate=0.95,
        training_seconds=120.5,
        peak_memory_mb=1024.0,
        num_steps=1000,
        num_params_m=1.5,
        depth=4,
    )
    assert "avg_score" in summary
    assert "valid_rate" in summary
    assert "training_seconds" in summary
    assert "peak_memory_mb" in summary
    assert "num_steps" in summary
    assert "num_params_M" in summary
    assert "depth" in summary
    assert "0.75" in summary or "0.7500" in summary


def test_checkpoint_save_load(tmp_path: str) -> None:
    """Model weights can be saved and loaded from a checkpoint."""
    from pathlib import Path

    import mlx.core as mx  # type: ignore[import-not-found]

    from autocontext.training.autoresearch.train import GPTModel, ModelConfig, load_checkpoint, save_checkpoint

    cfg = ModelConfig()
    model = GPTModel(cfg)

    # Forward pass to ensure parameters are realized
    x = mx.zeros((1, 16), dtype=mx.int32)
    _ = model(x)
    mx.eval(model.parameters())  # noqa: S307 — MLX lazy evaluation trigger

    ckpt_path = Path(tmp_path) / "checkpoint.safetensors"
    save_checkpoint(model, ckpt_path)
    assert ckpt_path.exists()

    # Load into a fresh model
    model2 = GPTModel(cfg)
    load_checkpoint(model2, ckpt_path)

    # Verify parameters match
    x_test = mx.ones((1, 16), dtype=mx.int32)
    out1 = model(x_test)
    out2 = model2(x_test)
    mx.eval(out1, out2)  # noqa: S307
    assert mx.allclose(out1, out2).item(), "Loaded model produces different output"
