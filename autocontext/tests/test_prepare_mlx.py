"""Tests for autoresearch prepare.py data loading and assessment oracle (MTS-177).

Tests involving MLX are skipped when MLX is not installed (CI-safe).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.training import HAS_MLX

# ---------------------------------------------------------------------------
# Tests that run WITHOUT MLX (data loading, JSONL parsing)
# ---------------------------------------------------------------------------


def test_jsonl_loading_and_split(tmp_path: Path) -> None:
    """load_jsonl() loads records and splits by run_id into train/val."""
    from autocontext.training.autoresearch.prepare import load_jsonl

    # Create sample JSONL
    records = []
    for i in range(20):
        records.append({
            "run_id": f"run_{i % 5}",
            "scenario": "grid_ctf",
            "strategy": {"aggression": 0.5, "defense": 0.3},
            "score": 0.5 + i * 0.01,
            "context": "some playbook text",
        })
    jsonl_path = tmp_path / "data.jsonl"
    jsonl_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    train_records, val_records = load_jsonl(jsonl_path, val_fraction=0.2)

    # All records should be accounted for
    total = len(train_records) + len(val_records)
    assert total == 20

    # Split should be by run_id, not random row
    train_run_ids = {r["run_id"] for r in train_records}
    val_run_ids = {r["run_id"] for r in val_records}
    assert train_run_ids.isdisjoint(val_run_ids), "Train/val should not share run_ids"


def test_format_training_example() -> None:
    """format_example() produces the expected token format."""
    from autocontext.training.autoresearch.prepare import format_example

    result = format_example(
        scenario="grid_ctf",
        context="Use high aggression.",
        strategy_json='{"aggression": 0.8}',
        score=1245.3,
    )
    assert "<|scenario|>" in result
    assert "grid_ctf" in result
    assert "<|context|>" in result
    assert "<|strategy|>" in result
    assert "<|score|>" in result
    assert "1245.3" in result
    assert "<|end|>" in result


def test_total_vocab_size_includes_special_tokens() -> None:
    """The model vocab reserves slots for the autoresearch special tokens."""
    from autocontext.training.autoresearch.prepare import BASE_VOCAB_SIZE, SPECIAL_TOKEN_STRINGS, total_vocab_size

    assert total_vocab_size(BASE_VOCAB_SIZE) == BASE_VOCAB_SIZE + len(SPECIAL_TOKEN_STRINGS)


def test_best_known_opponent_extraction(tmp_path: Path) -> None:
    """extract_best_opponent() returns the highest-scoring strategy."""
    from autocontext.training.autoresearch.prepare import extract_best_opponent

    records = [
        {"run_id": "r1", "scenario": "grid_ctf", "strategy": {"aggression": 0.3}, "score": 0.5, "context": ""},
        {"run_id": "r1", "scenario": "grid_ctf", "strategy": {"aggression": 0.9}, "score": 0.9, "context": ""},
        {"run_id": "r2", "scenario": "grid_ctf", "strategy": {"aggression": 0.6}, "score": 0.7, "context": ""},
    ]
    best = extract_best_opponent(records)
    assert best["aggression"] == 0.9


def test_extract_strategy_json_without_trailing_special_token() -> None:
    """_extract_strategy_json accepts outputs that end immediately after the strategy JSON."""
    from autocontext.training.autoresearch.prepare import _extract_strategy_json

    parsed = _extract_strategy_json('<|strategy|>{"aggression": 0.6}')
    assert parsed == {"aggression": 0.6}


# ---------------------------------------------------------------------------
# Tests that REQUIRE MLX
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_bpe_training(tmp_path: Path) -> None:
    """train_tokenizer() produces a tokenizer that can encode/decode."""
    from autocontext.training.autoresearch.prepare import train_tokenizer

    # Create sample text corpus
    corpus = [
        "<|scenario|>grid_ctf<|context|>playbook text<|strategy|>{}<|score|>1.0<|end|>"
        for _ in range(50)
    ]
    corpus_path = tmp_path / "corpus.txt"
    corpus_path.write_text("\n".join(corpus), encoding="utf-8")

    tokenizer = train_tokenizer(corpus_path, vocab_size=256)
    encoded = tokenizer.encode("<|scenario|>grid_ctf<|end|>")
    assert isinstance(encoded, list)
    assert len(encoded) > 0
    decoded = tokenizer.decode(encoded)
    assert "grid_ctf" in decoded


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_dataloader_shape(tmp_path: Path) -> None:
    """create_dataloader() yields batches with correct shapes."""

    from autocontext.training.autoresearch.prepare import create_dataloader

    # Create fake token IDs
    token_ids = list(range(512))
    seq_len = 32
    batch_size = 4

    batches = list(create_dataloader(token_ids, seq_len=seq_len, batch_size=batch_size))
    assert len(batches) > 0
    x, y = batches[0]
    assert x.shape == (batch_size, seq_len)
    assert y.shape == (batch_size, seq_len)


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_assess_strategy_quality_game_scenario() -> None:
    """assess_strategy_quality() works with game scenarios (execute_match)."""
    from unittest.mock import MagicMock

    from autocontext.training.autoresearch.prepare import assess_strategy_quality

    # Create a mock scenario with execute_match (game scenario)
    mock_scenario = MagicMock()
    mock_scenario.execute_match.return_value = MagicMock(score=0.75)

    # Create a mock model + tokenizer that produce valid JSON strategies
    mock_model = MagicMock()
    mock_tokenizer = MagicMock()
    mock_tokenizer.decode.return_value = '<|strategy|>{"aggression": 0.5, "defense": 0.3}<|end|>'

    result = assess_strategy_quality(
        model=mock_model,
        tokenizer=mock_tokenizer,
        scenario=mock_scenario,
        n_samples=3,
    )
    assert "avg_score" in result
    assert "valid_rate" in result
    assert isinstance(result["avg_score"], float)
    assert isinstance(result["valid_rate"], float)


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_assess_strategy_quality_agent_task() -> None:
    """assess_strategy_quality() detects agent task scenarios correctly."""
    from unittest.mock import MagicMock

    from autocontext.training.autoresearch.prepare import assess_strategy_quality

    # Agent task scenario: has evaluate_output but NOT execute_match
    mock_scenario = MagicMock(spec=["evaluate_output", "get_task_prompt"])
    mock_scenario.evaluate_output.return_value = MagicMock(score=0.8)

    mock_model = MagicMock()
    mock_tokenizer = MagicMock()
    mock_tokenizer.decode.return_value = '<|strategy|>{"plan": "do stuff"}<|end|>'

    result = assess_strategy_quality(
        model=mock_model,
        tokenizer=mock_tokenizer,
        scenario=mock_scenario,
        n_samples=2,
    )
    assert "avg_score" in result
    assert isinstance(result["avg_score"], float)
