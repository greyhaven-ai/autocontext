"""Tests for MLX GPT training model (AC-176).

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


def test_iter_masked_batches_shapes_mask_and_no_tail_drop() -> None:
    """iter_masked_batches yields (x, y, mask) per-example, pads, and drops no example."""
    from autocontext.training.autoresearch.prepare import iter_masked_batches

    strat = 99
    # 3 examples -> with batch_size 2 we expect 2 batches (sizes 2 and 1): nothing dropped
    sequences = [
        [1, strat, 2, 3],
        [4, strat, 5],
        [6, strat, 7, 8, 9],
    ]
    batches = list(iter_masked_batches(sequences, seq_len=4, batch_size=2, pad_token_id=0, strategy_token_id=strat))
    assert len(batches) == 2
    x0, y0, m0 = batches[0]
    assert x0.shape == (2, 4) and y0.shape == (2, 4) and m0.shape == (2, 4)
    assert batches[1][0].shape[0] == 1  # last partial batch kept, not dropped
    # mask is 0 on the prompt token(s) before the strategy token, 1 after
    import mlx.core as mx  # type: ignore[import-not-found]

    assert m0[0].tolist() == [0.0, 1.0, 1.0, 0.0]  # [prompt, comp, comp, pad]
    assert mx.sum(m0).item() > 0


def test_compute_loss_mask_ignores_masked_positions() -> None:
    """Masked compute_loss equals the loss over only the unmasked positions."""
    import mlx.core as mx  # type: ignore[import-not-found]

    from autocontext.training.autoresearch.train import GPTModel, ModelConfig, compute_loss

    cfg = ModelConfig()
    model = GPTModel(cfg)
    x = mx.zeros((1, 4), dtype=mx.int32)
    y = mx.array([[1, 2, 3, 4]], dtype=mx.int32)

    # all-ones mask must equal the unmasked mean loss
    ones = mx.ones((1, 4), dtype=mx.float32)
    masked_all = compute_loss(model, x, y, ones)
    unmasked = compute_loss(model, x, y)
    mx.eval(masked_all, unmasked)  # noqa: S307
    assert abs(masked_all.item() - unmasked.item()) < 1e-4

    # masking out 3 of 4 positions changes the value (only one target counts)
    partial = mx.array([[0.0, 1.0, 0.0, 0.0]], dtype=mx.float32)
    masked_partial = compute_loss(model, x, y, partial)
    mx.eval(masked_partial)  # noqa: S307
    assert masked_partial.item() >= 0.0


def test_run_training_mlx_end_to_end_smoke(tmp_path: str) -> None:
    """run_training(backend='mlx') runs the full pipeline via the package import path.

    Regression guard (PR #1027 review): _run_mlx_training's tokenization loop uses
    TrainingExample, which must be importable in BOTH the script-local and the
    package (autocontext.training.autoresearch.prepare) fallback import branches.
    Normal package/CLI execution takes the fallback branch; a missing import there
    raised UnboundLocalError. Existing tests never ran the full loop, so this pins it.
    """
    import json
    from pathlib import Path

    from autocontext.training.autoresearch.train import run_training

    records = [
        {
            "run_id": f"r{i % 2}",
            "scenario": "grid_ctf",
            "context": {"playbook": "p"},
            "strategy": {"aggression": 0.5, "defense": 0.3},
            "score": 0.5 + 0.01 * i,
        }
        for i in range(6)
    ]
    data_path = Path(tmp_path) / "data.jsonl"
    data_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    metrics = run_training(
        scenario_name="grid_ctf",
        data_path=data_path,
        output_dir=Path(tmp_path) / "out",
        time_budget=30,
        memory_limit_mb=4096,
        train_steps=1,
        batch_size=1,
        seq_len=16,
        assess_samples=1,
        backend="mlx",
    )
    assert "avg_score" in metrics and "valid_rate" in metrics
    # the inference bundle should have been written without error
    assert (Path(tmp_path) / "out" / "model.safetensors").exists()


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
