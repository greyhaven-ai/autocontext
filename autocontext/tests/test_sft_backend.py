"""Tests for the Linux TRL SFT backend pure helpers (CI-safe, no TRL/torch/transformers).

Task 3 adds the heavy run function and the real TrainerCallback wrapper; these three
helpers -- record->pair conversion, config-kwargs shape, and the wall-clock deadline
logic -- are pure and unit-testable without any of those dependencies.
"""

from __future__ import annotations

from pathlib import Path

from autocontext.training.autoresearch import mlxlm_backend as mb
from autocontext.training.autoresearch import sft_backend as sb


def test_sft_pairs_match_build_completion_record() -> None:
    """Each pair must equal build_completion_record's output for the same record, proving the
    adapter reuses the mlxlm mapping rather than reimplementing it."""
    records = [
        {"run_id": "r0", "scenario": "grid_ctf", "prompt": "What is 2+2?", "strategy": "Answer: 4", "score": 1.0},
        {"run_id": "r1", "scenario": "grid_ctf", "prompt": "What is 3+5?", "strategy": "Answer: 8", "score": 0.5},
    ]
    pairs = sb.sft_pairs_from_records(records)
    assert pairs == [
        mb.build_completion_record(task_prompt="What is 2+2?", strategy_json="Answer: 4"),
        mb.build_completion_record(task_prompt="What is 3+5?", strategy_json="Answer: 8"),
    ]
    assert all(set(p) == {"prompt", "completion"} for p in pairs)


def test_sft_pairs_serialize_dict_strategy_like_mlxlm() -> None:
    """A game-scenario record carries a JSON-object strategy; the completion must be the JSON
    serialization the mlxlm mapping produces, not a repr."""
    records = [{"scenario": "grid_ctf", "prompt": "T", "strategy": {"a": 1}, "score": 1.0}]
    pairs = sb.sft_pairs_from_records(records)
    assert pairs == [mb.build_completion_record(task_prompt="T", strategy_json='{"a": 1}')]


def test_sft_pairs_carry_reasoning_prefix() -> None:
    """Reason-then-construct records prepend the teacher rationale to the completion; the adapter
    must thread the reasoning field through the mlxlm mapping."""
    records = [{"scenario": "grid_ctf", "prompt": "T", "strategy": "Answer: 4", "reasoning": "2+2=4", "score": 1.0}]
    pairs = sb.sft_pairs_from_records(records)
    assert pairs == [mb.build_completion_record(task_prompt="T", strategy_json="Answer: 4", reasoning="2+2=4")]


def test_build_sft_config_kwargs_shape() -> None:
    kwargs = sb.build_sft_config_kwargs(
        output_dir=Path("/tmp/out"),
        max_steps=50,
        batch_size=4,
        learning_rate=1e-4,
    )
    assert kwargs["output_dir"] == "/tmp/out"
    assert isinstance(kwargs["output_dir"], str)
    assert kwargs["max_steps"] == 50
    assert kwargs["per_device_train_batch_size"] == 4
    assert kwargs["learning_rate"] == 1e-4
    # The dict is splatted as SFTConfig(**kwargs); it must carry only SFTConfig fields.
    assert set(kwargs) == {"output_dir", "max_steps", "per_device_train_batch_size", "learning_rate"}
    assert "deadline_seconds" not in kwargs


def test_deadline_callback_should_stop_at_boundary() -> None:
    cb = sb.DeadlineCallback(deadline_seconds=10.0, clock=lambda: 0.0)
    assert cb.should_stop_at(9.999) is False
    assert cb.should_stop_at(10.0) is True  # at the deadline stops
    assert cb.should_stop_at(10.001) is True


def test_deadline_callback_should_stop_uses_clock() -> None:
    ticks = iter([100.0, 105.0, 130.0])  # start, before deadline, after deadline

    def clock() -> float:
        return next(ticks)

    cb = sb.DeadlineCallback(deadline_seconds=20.0, clock=clock)  # consumes 100.0 as start
    assert cb.should_stop() is False  # 105.0 - 100.0 = 5.0 < 20.0
    assert cb.should_stop() is True  # 130.0 - 100.0 = 30.0 >= 20.0
