"""Fixed oracle for autoresearch: data loading, tokenizer training, assessment.

This module is READ-ONLY from the autoresearch agent's perspective.
It provides:
  1. JSONL data loading with train/val split by run_id
  2. BPE tokenizer training via rustbpe + tiktoken
  3. Dataloader yielding packed MLX arrays
  4. Assessment oracle for evaluating model-generated strategies

MLX-dependent code is behind import guards.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from autocontext.training import HAS_MLX

# The sequence-format contract is owned by sequence_format (pure domain module).
# Re-exported here for backward compatibility: existing callers / tests import these
# names from prepare. New code should import from sequence_format directly.
from autocontext.training.autoresearch.sequence_format import (  # noqa: F401
    _BPE_PAT,
    BASE_VOCAB_SIZE,
    SPECIAL_TOKEN_STRINGS,
    TrainingExample,
    build_generation_prompt,
    build_masked_example,
    build_special_tokens,
    completion_loss_mask,
    decodable_vocab_size,
    format_example,
    generation_logit_mask_values,
    score_loss_weights,
    total_vocab_size,
)
from autocontext.training.autoresearch.sequence_format import (
    extract_strategy as _extract_strategy_json,  # noqa: F401
)
from autocontext.training.autoresearch.sequence_format import (
    resolve_scenario_context as _resolve_scenario_context,  # noqa: F401
)
from autocontext.training.autoresearch.sequence_format import (
    resolve_scenario_name as _resolve_scenario_name,  # noqa: F401
)

logger = logging.getLogger(__name__)

if HAS_MLX:
    import mlx.core as mx  # type: ignore[import-not-found]


def serialize_tokenizer(tokenizer: Any) -> dict[str, Any]:
    """Serialize an AutoresearchTokenizer-compatible object to JSON data."""
    encoding = getattr(tokenizer, "_encoding", None)
    if encoding is None:
        raise ValueError("tokenizer is missing underlying encoding")
    mergeable_ranks = getattr(encoding, "_mergeable_ranks", None)
    if mergeable_ranks is None:
        raise ValueError("tokenizer encoding is missing mergeable ranks")

    pat_str = getattr(encoding, "_pat_str", _BPE_PAT)
    base_vocab_size = int(getattr(tokenizer, "base_vocab_size", BASE_VOCAB_SIZE))
    encoded_ranks = {base64.b64encode(token_bytes).decode("ascii"): token_id for token_bytes, token_id in mergeable_ranks.items()}
    return {
        "type": "BPE",
        "base_vocab_size": base_vocab_size,
        "include_quality": bool(getattr(tokenizer, "include_quality", False)),
        "pat_str": pat_str,
        "mergeable_ranks": encoded_ranks,
    }


def save_tokenizer_json(tokenizer: Any, path: Path) -> None:
    """Persist tokenizer metadata in the format expected by MLXProvider."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serialize_tokenizer(tokenizer), indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Data loading (no MLX dependency)
# ---------------------------------------------------------------------------


def load_jsonl(
    path: Path,
    val_fraction: float = 0.1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load JSONL records and split into train/val by run_id.

    The split is deterministic: run_ids are sorted and the last
    ``ceil(n_runs * val_fraction)`` are assigned to validation.

    Parameters
    ----------
    path:
        Path to a JSONL file where each line is a JSON object with at least
        ``run_id``, ``scenario``, ``strategy``, ``score``, ``context``.
    val_fraction:
        Fraction of unique run_ids to hold out for validation.

    Returns
    -------
    tuple[list, list]
        ``(train_records, val_records)``
    """
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # Deterministic split by run_id
    run_ids = sorted({r["run_id"] for r in records})
    n_val = max(1, int(len(run_ids) * val_fraction + 0.999))  # ceil
    val_run_ids = set(run_ids[-n_val:])

    train = [r for r in records if r["run_id"] not in val_run_ids]
    val = [r for r in records if r["run_id"] in val_run_ids]
    return train, val


# ---------------------------------------------------------------------------
# 2. Input formatting (no MLX dependency)
# ---------------------------------------------------------------------------


def extract_best_opponent(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the highest-scoring strategy from a list of records.

    Returns the strategy dict of the record with the highest score.
    """
    best = max(records, key=lambda r: r["score"])
    return dict(best["strategy"])


# ---------------------------------------------------------------------------
# 3. Tokenizer training (shared by MLX and CUDA backends)
# ---------------------------------------------------------------------------


class AutoresearchTokenizer:
    """Thin wrapper that preserves special-token metadata for training/inference."""

    def __init__(self, encoding: Any, *, base_vocab_size: int, include_quality: bool = False) -> None:
        self._encoding = encoding
        self.base_vocab_size = base_vocab_size
        self.include_quality = include_quality
        self.special_tokens = build_special_tokens(base_vocab_size, include_quality=include_quality)
        self.vocab_size = total_vocab_size(base_vocab_size, include_quality=include_quality)

    @property
    def end_token_id(self) -> int:
        return self.special_tokens["<|end|>"]

    def encode(self, text: str) -> list[int]:
        token_ids = self._encoding.encode(text, allowed_special=set(self.special_tokens))
        return cast(list[int], token_ids)

    def decode(self, token_ids: list[int]) -> str:
        return cast(str, self._encoding.decode(token_ids))


def train_tokenizer(
    corpus_path: Path, vocab_size: int = BASE_VOCAB_SIZE, *, score_conditioned: bool = False
) -> AutoresearchTokenizer:
    """Train a BPE tokenizer on the given corpus.

    Uses rustbpe for fast BPE training and wraps with tiktoken for
    encode/decode. When ``score_conditioned`` is set, the ``<|quality|>`` control
    token is registered as a special token (gated: omitted by default).

    Parameters
    ----------
    corpus_path:
        Path to a text file containing the training corpus.
    vocab_size:
        Target vocabulary size.
    score_conditioned:
        Whether to reserve/register the quality control token.

    Returns
    -------
    A tokenizer object with ``encode(text) -> list[int]`` and
    ``decode(tokens) -> str`` methods.
    """
    import rustbpe  # type: ignore[import-not-found]
    import tiktoken  # type: ignore[import-not-found]

    text = corpus_path.read_text(encoding="utf-8")
    tokenizer = rustbpe.Tokenizer()
    tokenizer.train_from_iterator([text], vocab_size=vocab_size)
    merges = {bytes(k): v for k, v in tokenizer.get_mergeable_ranks()}
    special_tokens = build_special_tokens(vocab_size, include_quality=score_conditioned)

    enc = tiktoken.Encoding(
        name="mts_autoresearch",
        pat_str=tokenizer.get_pattern(),
        mergeable_ranks=merges,
        special_tokens=special_tokens,
    )
    return AutoresearchTokenizer(enc, base_vocab_size=vocab_size, include_quality=score_conditioned)


if HAS_MLX:
    # -----------------------------------------------------------------------
    # 4. Dataloader (MLX arrays)
    # -----------------------------------------------------------------------

    def create_dataloader(
        token_ids: list[int],
        seq_len: int = 2048,
        batch_size: int = 4,
    ) -> Iterator[tuple[Any, Any]]:
        """Yield (x, y) batches from packed token IDs using best-fit cropping.

        Each batch contains ``batch_size`` sequences of length ``seq_len``.
        ``x`` is the input tokens and ``y`` is the targets (shifted by 1).

        Parameters
        ----------
        token_ids:
            Flat list of token IDs from the entire corpus.
        seq_len:
            Sequence length for each training example.
        batch_size:
            Number of sequences per batch.
        """
        # Best-fit crop: trim to largest multiple of (seq_len + 1) * batch_size
        stride = seq_len + 1
        total_seqs = len(token_ids) // stride
        usable_seqs = (total_seqs // batch_size) * batch_size
        total_tokens = usable_seqs * stride

        if total_tokens == 0:
            return

        data = mx.array(token_ids[:total_tokens], dtype=mx.int32)
        data = data.reshape(usable_seqs, stride)

        for batch_start in range(0, usable_seqs, batch_size):
            batch = data[batch_start : batch_start + batch_size]
            x = batch[:, :seq_len]
            y = batch[:, 1 : seq_len + 1]
            yield x, y

    def iter_masked_batches(
        sequences: list[list[int]],
        *,
        seq_len: int,
        batch_size: int,
        pad_token_id: int,
        strategy_token_id: int,
        weights: list[float] | None = None,
    ) -> Iterator[tuple[Any, Any, Any, Any]]:
        """Yield ``(x, y, loss_mask, example_weights)`` per-example, completion-masked batches.

        Each example is encoded independently (no cross-example packing, so the model
        never predicts across a document boundary), right-truncated to keep the
        completion when it exceeds ``seq_len + 1``, padded to ``seq_len``, and its loss
        mask zeroes both the prompt tokens (completion-only loss) and the padding.
        No batch is dropped: the final partial batch is yielded as-is.

        ``weights`` (reward-weighted regression) is an optional per-sequence list,
        aligned with ``sequences``. It is returned as a per-example weight vector
        ``example_weights`` (shape ``(batch,)``), NOT folded into the per-token mask,
        so ``compute_loss`` can weight each example's *mean* completion loss (a long
        completion can't drown out a short high-reward one). ``example_weights`` is
        ``None`` when ``weights`` is ``None`` or all ``1.0`` (byte-identical, unweighted).
        Dropped sequences (too short to form a pair) drop their weight too.
        """
        do_weight = weights is not None and any(w != 1.0 for w in weights)
        ws = weights if weights is not None else [1.0] * len(sequences)
        built: list[tuple[list[int], list[int], list[int], float]] = []
        for tokens, w in zip(sequences, ws, strict=True):
            example = build_masked_example(
                tokens,
                seq_len=seq_len,
                pad_token_id=pad_token_id,
                strategy_token_id=strategy_token_id,
            )
            if example is not None:
                inp, tgt, mask = example
                built.append((inp, tgt, mask, w))

        for start in range(0, len(built), batch_size):
            chunk = built[start : start + batch_size]
            xs = mx.array([c[0] for c in chunk], dtype=mx.int32)
            ys = mx.array([c[1] for c in chunk], dtype=mx.int32)
            ms = mx.array([c[2] for c in chunk], dtype=mx.float32)
            wv = mx.array([c[3] for c in chunk], dtype=mx.float32) if do_weight else None
            yield xs, ys, ms, wv

    # -----------------------------------------------------------------------
    # 5. Assessment oracle
    # -----------------------------------------------------------------------

    def assess_strategy_quality(
        *,
        model: Any,
        tokenizer: Any,
        scenario: Any,
        n_samples: int = 10,
        temperature: float = 0.0,
        top_k: int = 0,
        seed_base: int = 0,
        target_quality: int | None = None,
        collect_path: Path | None = None,
    ) -> dict[str, float]:
        """Assess model quality by generating strategies and scoring them.

        Uses scenario type detection:
        - Game scenarios (have ``execute_match``): score via match execution
        - Agent task scenarios (have ``evaluate_output``): score via evaluation

        Parameters
        ----------
        model:
            The trained GPTModel.
        tokenizer:
            Tokenizer with encode/decode methods.
        scenario:
            A scenario instance (game or agent task).
        n_samples:
            Number of strategies to generate and evaluate.
        temperature:
            Sampling temperature. ``<= 0`` (default) is greedy/deterministic, in
            which case every sample is identical; ``> 0`` draws diverse samples so
            the diversity of the model's output is actually measured.
        top_k:
            Optional top-k truncation applied when sampling.
        seed_base:
            Offset added to the per-sample seed (lets callers vary draws across
            rounds without colliding).

        Returns
        -------
        dict with ``avg_score`` and ``valid_rate``.
        """
        scores: list[float] = []
        valid_count = 0
        collected: list[dict[str, Any]] = []  # (strategy, score) for the self-improving loop

        is_game = hasattr(scenario, "execute_match")

        for i in range(n_samples):
            try:
                raw_output = _generate_strategy_text(
                    model=model,
                    tokenizer=tokenizer,
                    scenario=scenario,
                    seed=seed_base + i,
                    temperature=temperature,
                    top_k=top_k,
                    target_quality=target_quality,
                )
                strategy = _extract_strategy_json(raw_output)

                if strategy is not None:
                    valid_count += 1
                    if is_game:
                        result = scenario.execute_match(strategy, seed=i)
                    else:
                        result = scenario.evaluate_output(output=json.dumps(strategy))
                    scores.append(result.score)
                    if collect_path is not None:
                        collected.append({"strategy": strategy, "score": float(result.score)})
            except Exception:
                logger.debug("training.autoresearch.prepare: suppressed Exception", exc_info=True)

        if collect_path is not None:
            collect_path.parent.mkdir(parents=True, exist_ok=True)
            collect_path.write_text("\n".join(json.dumps(s) for s in collected) + "\n", encoding="utf-8")

        avg_score = sum(scores) / len(scores) if scores else 0.0
        valid_rate = valid_count / n_samples if n_samples > 0 else 0.0

        return {
            "avg_score": avg_score,
            "valid_rate": valid_rate,
        }

    def _generation_logit_mask(tokenizer: Any, vocab_size: int) -> Any:
        """MLX additive logit mask for training-time assessment generation.

        Wraps the shared :func:`generation_logit_mask_values` (blocks the phantom-id
        gap and the structural scenario/context/strategy tokens; ``<|score|>`` and
        ``<|end|>`` remain allowed as stops).
        """
        return mx.array(generation_logit_mask_values(tokenizer, vocab_size, block_structural_specials=True))

    def _generate_strategy_text(
        *,
        model: Any,
        tokenizer: Any,
        scenario: Any,
        seed: int,
        max_new_tokens: int = 128,
        temperature: float = 0.0,
        top_k: int = 0,
        target_quality: int | None = None,
    ) -> str:
        """Generate a candidate strategy from the model.

        ``temperature <= 0`` (default) uses greedy argmax and is deterministic.
        ``temperature > 0`` samples from the (optionally top-k truncated) softmax,
        seeded by ``seed`` so distinct seeds yield diverse completions. In both
        modes a vocab/special-token mask keeps generation within decodable ids and
        prevents the body from re-emitting structural header tokens.

        ``target_quality`` conditions generation on a quality bucket (for models
        trained score-conditioned), steering toward that quality.
        """

        if not hasattr(model, "cfg"):
            # Test doubles may not expose a sampling surface; fall back to the tokenizer stub.
            return cast(str, tokenizer.decode([seed] * 32))

        prompt = build_generation_prompt(scenario, target_quality=target_quality)
        token_ids = list(tokenizer.encode(prompt))
        seq_len = int(model.cfg.seq_len)
        vocab_size = int(getattr(model.cfg, "vocab_size", total_vocab_size(BASE_VOCAB_SIZE)))
        end_token_id = getattr(tokenizer, "end_token_id", None)
        mask = _generation_logit_mask(tokenizer, vocab_size)
        sampling = temperature is not None and temperature > 0.0
        if sampling:
            mx.random.seed(int(seed))

        for _ in range(max_new_tokens):
            window = token_ids[-seq_len:]
            x = mx.array([window], dtype=mx.int32)
            logits = model(x)[0, -1, :] + mask  # [vocab]
            if sampling:
                logits = logits / float(temperature)
                if top_k and top_k > 0:
                    k = min(int(top_k), int(logits.shape[-1]))
                    kth = mx.sort(logits)[-k]
                    logits = mx.where(logits < kth, mx.full(logits.shape, -1e9, dtype=logits.dtype), logits)
                next_token = int(mx.random.categorical(logits).item())
            else:
                next_token = int(mx.argmax(logits, axis=-1).item())
            token_ids.append(next_token)
            if end_token_id is not None and next_token == end_token_id:
                break

        return cast(str, tokenizer.decode(token_ids))
