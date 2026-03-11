"""Baseline MLX GPT model and training loop for autoresearch distillation.

All MLX code is behind import guards so the module can be imported
(for type checking, tests, etc.) even when MLX is not installed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mts.training import HAS_MLX

if HAS_MLX:
    import mlx.core as mx  # type: ignore[import-not-found]
    import mlx.nn as nn  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ModelConfig:
    """Hyperparameters for the baseline GPT model."""

    depth: int = 4
    aspect_ratio: int = 64
    head_dim: int = 64
    n_kv_heads: int = 4
    vocab_size: int = 8192
    seq_len: int = 2048

    @property
    def d_model(self) -> int:
        return self.depth * self.aspect_ratio

    @property
    def n_heads(self) -> int:
        return self.d_model // self.head_dim


# ---------------------------------------------------------------------------
# Model components (only defined when MLX is available)
# ---------------------------------------------------------------------------

if HAS_MLX:

    class RMSNorm(nn.Module):  # type: ignore[misc]
        """Root Mean Square Layer Normalization."""

        def __init__(self, dims: int, eps: float = 1e-6) -> None:
            super().__init__()
            self.weight = mx.ones((dims,))
            self.eps = eps

        def __call__(self, x: Any) -> Any:
            norm = mx.rsqrt(mx.mean(x * x, axis=-1, keepdims=True) + self.eps)
            return x * norm * self.weight

    def _rotary_embedding(x: Any, offset: int = 0) -> Any:
        """Apply Rotary Position Embedding (RoPE) to input tensor."""
        _, seq_len, n_heads, head_dim = x.shape
        positions = mx.arange(offset, offset + seq_len, dtype=mx.float32)
        freqs = 1.0 / (10000.0 ** (mx.arange(0, head_dim, 2, dtype=mx.float32) / head_dim))
        angles = mx.expand_dims(positions, axis=-1) * mx.expand_dims(freqs, axis=0)
        cos_vals = mx.cos(angles)
        sin_vals = mx.sin(angles)
        # Reshape for broadcasting: [1, seq, 1, head_dim//2]
        cos_vals = mx.expand_dims(mx.expand_dims(cos_vals, axis=0), axis=2)
        sin_vals = mx.expand_dims(mx.expand_dims(sin_vals, axis=0), axis=2)
        x1 = x[..., ::2]
        x2 = x[..., 1::2]
        rotated = mx.concatenate([x1 * cos_vals - x2 * sin_vals, x1 * sin_vals + x2 * cos_vals], axis=-1)
        return rotated

    class Attention(nn.Module):  # type: ignore[misc]
        """Grouped-query attention with RoPE and optional sliding window."""

        def __init__(self, cfg: ModelConfig) -> None:
            super().__init__()
            self.n_heads = cfg.n_heads
            self.n_kv_heads = cfg.n_kv_heads
            self.head_dim = cfg.head_dim
            self.d_model = cfg.d_model
            self.scale = 1.0 / math.sqrt(self.head_dim)

            self.q_proj = nn.Linear(self.d_model, self.n_heads * self.head_dim, bias=False)
            self.k_proj = nn.Linear(self.d_model, self.n_kv_heads * self.head_dim, bias=False)
            self.v_proj = nn.Linear(self.d_model, self.n_kv_heads * self.head_dim, bias=False)
            self.o_proj = nn.Linear(self.n_heads * self.head_dim, self.d_model, bias=False)

        def __call__(self, x: Any) -> Any:
            batch, seq_len, _ = x.shape

            q = self.q_proj(x).reshape(batch, seq_len, self.n_heads, self.head_dim)
            k = self.k_proj(x).reshape(batch, seq_len, self.n_kv_heads, self.head_dim)
            v = self.v_proj(x).reshape(batch, seq_len, self.n_kv_heads, self.head_dim)

            # Apply RoPE
            q = _rotary_embedding(q)
            k = _rotary_embedding(k)

            # Repeat KV heads for grouped-query attention
            if self.n_kv_heads < self.n_heads:
                repeat_factor = self.n_heads // self.n_kv_heads
                k = mx.repeat(k, repeat_factor, axis=2)
                v = mx.repeat(v, repeat_factor, axis=2)

            # Transpose to [batch, n_heads, seq, head_dim]
            q = mx.transpose(q, (0, 2, 1, 3))
            k = mx.transpose(k, (0, 2, 1, 3))
            v = mx.transpose(v, (0, 2, 1, 3))

            # Scaled dot-product attention with causal mask
            scores = (q @ mx.transpose(k, (0, 1, 3, 2))) * self.scale
            # Causal mask
            mask = mx.triu(mx.full((seq_len, seq_len), float("-inf")), k=1)
            scores = scores + mask
            weights = mx.softmax(scores, axis=-1)
            out = weights @ v

            # Transpose back and project
            out = mx.transpose(out, (0, 2, 1, 3)).reshape(batch, seq_len, -1)
            return self.o_proj(out)

    class FeedForward(nn.Module):  # type: ignore[misc]
        """Feed-forward network with ReLU-squared activation."""

        def __init__(self, d_model: int) -> None:
            super().__init__()
            hidden = d_model * 4
            self.gate = nn.Linear(d_model, hidden, bias=False)
            self.up = nn.Linear(d_model, hidden, bias=False)
            self.down = nn.Linear(hidden, d_model, bias=False)

        def __call__(self, x: Any) -> Any:
            # ReLU-squared: (ReLU(x))^2
            gate_out = mx.maximum(self.gate(x), 0.0)
            return self.down(gate_out * gate_out * self.up(x))

    class TransformerBlock(nn.Module):  # type: ignore[misc]
        """Single transformer block with pre-norm and per-layer residual scalars."""

        def __init__(self, cfg: ModelConfig) -> None:
            super().__init__()
            self.norm1 = RMSNorm(cfg.d_model)
            self.attn = Attention(cfg)
            self.norm2 = RMSNorm(cfg.d_model)
            self.ff = FeedForward(cfg.d_model)
            # Per-layer residual scalars (initialized to 1.0)
            self.attn_scale = mx.array(1.0)
            self.ff_scale = mx.array(1.0)

        def __call__(self, x: Any) -> Any:
            x = x + self.attn_scale * self.attn(self.norm1(x))
            x = x + self.ff_scale * self.ff(self.norm2(x))
            return x

    class GPTModel(nn.Module):  # type: ignore[misc]
        """Baseline GPT model for strategy distillation."""

        def __init__(self, cfg: ModelConfig) -> None:
            super().__init__()
            self.cfg = cfg
            self.embed = nn.Embedding(cfg.vocab_size, cfg.d_model)
            self.layers = [TransformerBlock(cfg) for _ in range(cfg.depth)]
            self.norm = RMSNorm(cfg.d_model)
            self.head = nn.Linear(cfg.d_model, cfg.vocab_size, bias=False)

        def __call__(self, x: Any) -> Any:
            h = self.embed(x)
            for layer in self.layers:
                h = layer(h)
            h = self.norm(h)
            return self.head(h)

    def compute_loss(model: GPTModel, x: Any, y: Any) -> Any:
        """Cross-entropy loss for next-token prediction."""
        logits = model(x)
        # Reshape for cross entropy: [batch*seq, vocab]
        batch, seq_len, vocab = logits.shape
        logits_flat = logits.reshape(-1, vocab)
        targets_flat = y.reshape(-1)
        return mx.mean(nn.losses.cross_entropy(logits_flat, targets_flat))

    def save_checkpoint(model: GPTModel, path: Path) -> None:
        """Save model weights to safetensors format."""
        import numpy as np  # noqa: I001
        import safetensors.numpy  # type: ignore[import-not-found]

        weights: dict[str, Any] = {}
        flat = model.parameters()
        _flatten_params(flat, "", weights)

        np_weights = {k: np.array(v) for k, v in weights.items()}
        safetensors.numpy.save_file(np_weights, str(path))

    def load_checkpoint(model: GPTModel, path: Path) -> None:
        """Load model weights from safetensors format."""
        import safetensors.numpy  # type: ignore[import-not-found]

        np_weights = safetensors.numpy.load_file(str(path))
        mx_weights = {k: mx.array(v) for k, v in np_weights.items()}

        # Unflatten and load
        nested = _unflatten_params(mx_weights)
        model.update(nested)

    def _flatten_params(params: Any, prefix: str, out: dict[str, Any]) -> None:
        """Recursively flatten nested parameter dict/list."""
        if isinstance(params, dict):
            for k, v in params.items():
                new_prefix = f"{prefix}.{k}" if prefix else k
                _flatten_params(v, new_prefix, out)
        elif isinstance(params, list):
            for i, v in enumerate(params):
                new_prefix = f"{prefix}.{i}" if prefix else str(i)
                _flatten_params(v, new_prefix, out)
        else:
            out[prefix] = params

    def _unflatten_params(flat: dict[str, Any]) -> dict[str, Any]:
        """Reconstruct nested parameter structure from flat dict."""
        result: dict[str, Any] = {}
        for key, value in flat.items():
            parts = key.split(".")
            current = result
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value

        converted = _convert_numeric_keys(result)
        assert isinstance(converted, dict)  # top-level is always a dict
        return converted

    def _convert_numeric_keys(d: dict[str, Any]) -> dict[str, Any] | list[Any]:
        """Convert dicts with all-numeric keys to lists."""
        if all(k.isdigit() for k in d):
            max_idx = max(int(k) for k in d)
            lst: list[Any] = [None] * (max_idx + 1)
            for k, v in d.items():
                val = _convert_numeric_keys(v) if isinstance(v, dict) else v
                lst[int(k)] = val
            return lst
        converted: dict[str, Any] = {}
        for k, v in d.items():
            converted[k] = _convert_numeric_keys(v) if isinstance(v, dict) else v
        return converted

else:
    # Stubs when MLX is not available
    class ModelConfig:  # type: ignore[no-redef]
        """Hyperparameters for the baseline GPT model (stub)."""

        depth: int = 4
        aspect_ratio: int = 64
        head_dim: int = 64
        n_kv_heads: int = 4
        vocab_size: int = 8192
        seq_len: int = 2048

    class GPTModel:  # type: ignore[no-redef]
        """GPT model stub when MLX is not installed."""

        def __init__(self, cfg: ModelConfig) -> None:
            raise ImportError("MLX is required. Install with: uv sync --group dev --extra mlx")


# ---------------------------------------------------------------------------
# Summary formatting (always available)
# ---------------------------------------------------------------------------


def format_summary(
    *,
    avg_score: float,
    valid_rate: float,
    training_seconds: float,
    peak_memory_mb: float,
    num_steps: int,
    num_params_m: float,
    depth: int,
) -> str:
    """Format the training results summary block.

    This block is printed to stdout and parsed by the autoresearch agent.
    """
    return (
        "=== TRAINING SUMMARY ===\n"
        f"avg_score: {avg_score:.4f}\n"
        f"valid_rate: {valid_rate:.4f}\n"
        f"training_seconds: {training_seconds:.1f}\n"
        f"peak_memory_mb: {peak_memory_mb:.1f}\n"
        f"num_steps: {num_steps}\n"
        f"num_params_M: {num_params_m:.2f}\n"
        f"depth: {depth}\n"
        "========================"
    )
