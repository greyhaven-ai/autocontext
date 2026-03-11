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

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mts.training import HAS_MLX

if HAS_MLX:
    import mlx.core as mx  # type: ignore[import-not-found]


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


def format_example(
    *,
    scenario: str,
    context: str,
    strategy_json: str,
    score: float,
) -> str:
    """Format a single training example in the standard input format.

    Format:
        <|scenario|>{scenario}<|context|>{context}<|strategy|>{strategy_json}<|score|>{score}<|end|>
    """
    return f"<|scenario|>{scenario}<|context|>{context}<|strategy|>{strategy_json}<|score|>{score}<|end|>"


def extract_best_opponent(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the highest-scoring strategy from a list of records.

    Returns the strategy dict of the record with the highest score.
    """
    best = max(records, key=lambda r: r["score"])
    return dict(best["strategy"])


# ---------------------------------------------------------------------------
# 3. Tokenizer training (requires MLX extra for rustbpe/tiktoken)
# ---------------------------------------------------------------------------

if HAS_MLX:

    def train_tokenizer(corpus_path: Path, vocab_size: int = 8192) -> Any:
        """Train a BPE tokenizer on the given corpus.

        Uses rustbpe for fast BPE training and wraps with tiktoken for
        encode/decode.

        Parameters
        ----------
        corpus_path:
            Path to a text file containing the training corpus.
        vocab_size:
            Target vocabulary size.

        Returns
        -------
        A tokenizer object with ``encode(text) -> list[int]`` and
        ``decode(tokens) -> str`` methods.
        """
        import rustbpe  # type: ignore[import-not-found]
        import tiktoken  # type: ignore[import-not-found]

        text = corpus_path.read_text(encoding="utf-8")
        # Train BPE merges
        trainer = rustbpe.BPETrainer(vocab_size=vocab_size)
        merges = trainer.train(text)

        # Build tiktoken encoding from the merges
        # Special tokens for our format
        special_tokens = {
            "<|scenario|>": vocab_size,
            "<|context|>": vocab_size + 1,
            "<|strategy|>": vocab_size + 2,
            "<|score|>": vocab_size + 3,
            "<|end|>": vocab_size + 4,
        }

        _BPE_PAT = (
            r"(?i:'s|'t|'re|'ve|'m|'ll|'d)"
            r"|[^\r\n\p{L}\p{N}]?\p{L}+"
            r"|\p{N}{1,3}"
            r"| ?[^\s\p{L}\p{N}]+[\r\n]*"
            r"|\s*[\r\n]+"
            r"|\s+"
        )
        enc = tiktoken.Encoding(
            name="mts_autoresearch",
            pat_str=_BPE_PAT,
            mergeable_ranks=merges,
            special_tokens=special_tokens,
        )
        return enc

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

    # -----------------------------------------------------------------------
    # 5. Assessment oracle
    # -----------------------------------------------------------------------

    def assess_strategy_quality(
        *,
        model: Any,
        tokenizer: Any,
        scenario: Any,
        n_samples: int = 10,
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

        Returns
        -------
        dict with ``avg_score`` and ``valid_rate``.
        """
        scores: list[float] = []
        valid_count = 0

        is_game = hasattr(scenario, "execute_match")

        for i in range(n_samples):
            try:
                # Generate strategy text from model
                raw_output = tokenizer.decode([0] * 32)  # placeholder generation
                strategy = _extract_strategy_json(raw_output)

                if strategy is not None:
                    valid_count += 1
                    if is_game:
                        result = scenario.execute_match(strategy, seed=i)
                        scores.append(result.score)
                    else:
                        # Agent task scenario
                        result = scenario.evaluate_output(
                            output=json.dumps(strategy),
                        )
                        scores.append(result.score)
            except Exception:
                pass

        avg_score = sum(scores) / len(scores) if scores else 0.0
        valid_rate = valid_count / n_samples if n_samples > 0 else 0.0

        return {
            "avg_score": avg_score,
            "valid_rate": valid_rate,
        }

    def _extract_strategy_json(text: str) -> dict[str, Any] | None:
        """Extract JSON strategy from model output text."""
        match = re.search(r"<\|strategy\|>(.*?)<\|", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                return None
        # Try parsing the whole text as JSON
        try:
            return json.loads(text)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            return None
