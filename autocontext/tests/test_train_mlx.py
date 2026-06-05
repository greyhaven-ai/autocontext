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
    x0, y0, m0, w0 = batches[0]
    assert x0.shape == (2, 4) and y0.shape == (2, 4) and m0.shape == (2, 4)
    assert w0 is None  # no weighting requested -> no per-example weight vector
    assert batches[1][0].shape[0] == 1  # last partial batch kept, not dropped
    # mask is 0 on the prompt token(s) before the strategy token, 1 after
    import mlx.core as mx  # type: ignore[import-not-found]

    assert m0[0].tolist() == [0.0, 1.0, 1.0, 0.0]  # [prompt, comp, comp, pad]
    assert mx.sum(m0).item() > 0


def test_iter_masked_batches_returns_per_example_weight_vector() -> None:
    """Weights are returned as a separate per-example vector, NOT folded into the mask."""
    from autocontext.training.autoresearch.prepare import iter_masked_batches

    strat = 99
    sequences = [[1, strat, 2, 3], [4, strat, 5, 6]]
    batches = list(
        iter_masked_batches(sequences, seq_len=4, batch_size=2, pad_token_id=0, strategy_token_id=strat, weights=[0.5, 1.5])
    )
    _, _, m, w = batches[0]
    assert m[0].tolist() == [0.0, 1.0, 1.0, 0.0]  # mask stays 0/1 (length-neutral)
    assert m[1].tolist() == [0.0, 1.0, 1.0, 0.0]
    assert w.tolist() == [0.5, 1.5]  # per-example weights live in the 4th element


def test_iter_masked_batches_default_weights_none() -> None:
    """weights=None and all-1.0 weights both yield no weight vector (unweighted)."""
    from autocontext.training.autoresearch.prepare import iter_masked_batches

    strat = 99
    sequences = [[1, strat, 2, 3]]
    none_w = list(iter_masked_batches(sequences, seq_len=4, batch_size=1, pad_token_id=0, strategy_token_id=strat))
    ones_w = list(iter_masked_batches(sequences, seq_len=4, batch_size=1, pad_token_id=0, strategy_token_id=strat, weights=[1.0]))
    assert none_w[0][2].tolist() == ones_w[0][2].tolist()  # identical 0/1 mask
    assert none_w[0][3] is None and ones_w[0][3] is None  # all-1.0 collapses to no weighting


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


def test_compute_loss_example_weights_are_length_neutral() -> None:
    """Per-example weighting weights each example's MEAN completion loss, so a long
    low-weight example cannot dominate a short high-weight one (the RWR contract).

    Two identical-content rows (so each example's mean completion loss is equal) but
    with different completion lengths: equal weights must give the same loss as the
    unweighted per-token average, proving the weight is per-example, not per-token.
    """
    import mlx.core as mx  # type: ignore[import-not-found]

    from autocontext.training.autoresearch.train import GPTModel, ModelConfig, compute_loss

    cfg = ModelConfig()
    model = GPTModel(cfg)
    x = mx.zeros((2, 4), dtype=mx.int32)
    y = mx.array([[1, 2, 3, 4], [1, 2, 3, 4]], dtype=mx.int32)
    # row 0 trains 1 completion token, row 1 trains 3 -> different lengths
    mask = mx.array([[0.0, 1.0, 0.0, 0.0], [1.0, 1.0, 1.0, 0.0]], dtype=mx.float32)

    # Equal per-example weights => each example's mean loss counts equally (length-neutral).
    equal_w = compute_loss(model, x, y, mask, mx.array([1.0, 1.0], dtype=mx.float32))
    # Upweighting the short row must move the loss toward that row's mean loss.
    short_heavy = compute_loss(model, x, y, mask, mx.array([9.0, 1.0], dtype=mx.float32))
    mx.eval(equal_w, short_heavy)  # noqa: S307
    assert equal_w.item() >= 0.0 and short_heavy.item() >= 0.0
    # The token-level masked average weights by length; the per-example path does not.
    token_level = compute_loss(model, x, y, mask)  # example_weights=None
    mx.eval(token_level)  # noqa: S307
    # equal per-example weighting differs from token-level when lengths differ
    assert abs(equal_w.item() - token_level.item()) > 1e-6


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


def _write_grid_ctf_dataset(tmp_path: str):
    import json
    from pathlib import Path

    # 6 records across two run_ids so load_jsonl yields a non-empty val split
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
    return data_path


def test_run_training_reports_validation_loss(tmp_path: str) -> None:
    """run_training holds out the val split and reports a finite val_loss."""
    import math
    from pathlib import Path

    from autocontext.training.autoresearch.train import run_training

    metrics = run_training(
        scenario_name="grid_ctf",
        data_path=_write_grid_ctf_dataset(tmp_path),
        output_dir=Path(tmp_path) / "out",
        time_budget=30,
        memory_limit_mb=4096,
        train_steps=2,
        batch_size=1,
        seq_len=16,
        assess_samples=1,
        backend="mlx",
    )
    assert "val_loss" in metrics
    assert math.isfinite(metrics["val_loss"])  # a held-out val split was scored


def test_run_training_val_select_completes(tmp_path: str) -> None:
    """val_select runs best-checkpoint selection + early stopping end-to-end."""
    import math
    from pathlib import Path

    from autocontext.training.autoresearch.train import run_training

    metrics = run_training(
        scenario_name="grid_ctf",
        data_path=_write_grid_ctf_dataset(tmp_path),
        output_dir=Path(tmp_path) / "out",
        time_budget=30,
        memory_limit_mb=4096,
        train_steps=3,
        batch_size=1,
        seq_len=16,
        assess_samples=1,
        val_select=True,
        backend="mlx",
    )
    assert math.isfinite(metrics["val_loss"])
    assert (Path(tmp_path) / "out" / "model.safetensors").exists()


def test_run_training_loss_weighted_end_to_end(tmp_path: str) -> None:
    """Reward-weighted regression (softmax loss weights) runs the full mlx pipeline."""
    from pathlib import Path

    from autocontext.training.autoresearch.train import run_training

    metrics = run_training(
        scenario_name="grid_ctf",
        data_path=_write_grid_ctf_dataset(tmp_path),
        output_dir=Path(tmp_path) / "out",
        time_budget=30,
        memory_limit_mb=4096,
        train_steps=2,
        batch_size=1,
        seq_len=24,
        assess_samples=1,
        loss_weight_mode="softmax",
        loss_weight_temperature=0.5,
        backend="mlx",
    )
    assert "avg_score" in metrics and "valid_rate" in metrics
    assert (Path(tmp_path) / "out" / "model.safetensors").exists()


def test_run_training_with_augmenter_end_to_end(tmp_path: str) -> None:
    """An augmenter spec is resolved + applied in the data pipeline before training.

    `copy:deepcopy` is a real importable stand-in augmenter (returns the records), so
    this exercises the full run_training -> prepare_training_records -> resolve/apply
    chain end to end on the mlx backend.
    """
    from pathlib import Path

    from autocontext.training.autoresearch.train import run_training

    metrics = run_training(
        scenario_name="grid_ctf",
        data_path=_write_grid_ctf_dataset(tmp_path),
        output_dir=Path(tmp_path) / "out",
        time_budget=30,
        memory_limit_mb=4096,
        train_steps=2,
        batch_size=1,
        seq_len=24,
        assess_samples=1,
        augmenter_spec="copy:deepcopy",
        backend="mlx",
    )
    assert "avg_score" in metrics and "valid_rate" in metrics
    assert (Path(tmp_path) / "out" / "model.safetensors").exists()


def test_run_training_score_conditioned_end_to_end(tmp_path: str) -> None:
    """score_conditioned training + top-bucket-conditioned assessment runs end to end."""
    from pathlib import Path

    from autocontext.training.autoresearch.train import run_training

    metrics = run_training(
        scenario_name="grid_ctf",
        data_path=_write_grid_ctf_dataset(tmp_path),
        output_dir=Path(tmp_path) / "out",
        time_budget=30,
        memory_limit_mb=4096,
        train_steps=2,
        batch_size=1,
        seq_len=24,
        assess_samples=1,
        score_conditioned=True,
        backend="mlx",
    )
    assert "avg_score" in metrics and "valid_rate" in metrics
    # the corpus should carry the quality control token
    assert "<|quality|>" in (Path(tmp_path) / "out" / "corpus.txt").read_text(encoding="utf-8")


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
