"""Tests for the Linux TRL SFT backend (CI-safe, no TRL/torch/transformers).

The pure helpers -- record->pair conversion, config-kwargs shape, and the wall-clock
deadline logic -- are unit-testable without any heavy dependency. The heavy ``run``
path is exercised by patching the one lazy-import seam (``_load_sft_runtime``) with fakes,
so the whole runner runs on a machine without ``trl``; a second, env-gated test does a
real tiny CPU fine-tune and is skipped in CI.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

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


def test_deadline_trainer_callback_stops_training_past_deadline() -> None:
    """The real TrainerCallback wrapper flips control.should_training_stop once the deadline passes.

    This is the load-bearing enforcement line: a deadline in the past (a non-positive budget, so the
    real perf_counter clock is already at/after it) must set should_training_stop on the next step."""
    callback = sb._make_deadline_callback(_FakeTrainerCallback, deadline_seconds=-1.0)
    control = SimpleNamespace(should_training_stop=False)
    callback.on_step_end(args=None, state=None, control=control)
    assert control.should_training_stop is True


def test_deadline_trainer_callback_keeps_training_before_deadline() -> None:
    """A deadline far in the future leaves should_training_stop untouched: training continues."""
    callback = sb._make_deadline_callback(_FakeTrainerCallback, deadline_seconds=1e9)
    control = SimpleNamespace(should_training_stop=False)
    callback.on_step_end(args=None, state=None, control=control)
    assert control.should_training_stop is False


# ---------------------------------------------------------------------------
# Mock-the-run: exercise the whole runner with fakes (no real trl/torch/datasets)
# ---------------------------------------------------------------------------


class _FakeTrainerCallback:
    """Stand-in for ``transformers.TrainerCallback`` -- a real class so the deadline wrapper
    can subclass it; the deadline callback attached to the trainer is an instance of this."""


def _fake_runtime(captured: dict[str, Any]) -> tuple[Any, ...]:
    """Build the 7-tuple ``_load_sft_runtime`` returns, with fakes that record their inputs."""

    class _FakeSFTConfig:
        def __init__(self, **kwargs: Any) -> None:
            captured["config_kwargs"] = kwargs

    class _FakeLoraConfig:
        def __init__(self, **kwargs: Any) -> None:
            captured["lora_kwargs"] = kwargs

    class _FakeDataset:
        @classmethod
        def from_list(cls, pairs: list[dict[str, str]]) -> _FakeDataset:
            captured["dataset_pairs"] = pairs
            return cls()

    class _FakeModel:
        @classmethod
        def from_pretrained(cls, base_model: str) -> _FakeModel:
            captured["model_base"] = base_model
            return cls()

    class _FakeTokenizer:
        @classmethod
        def from_pretrained(cls, base_model: str) -> _FakeTokenizer:
            captured["tokenizer_base"] = base_model
            return cls()

    class _FakeSFTTrainer:
        def __init__(self, **kwargs: Any) -> None:
            captured["trainer_kwargs"] = kwargs
            self.model = None  # no torch params -> _trainable_params_m falls back to 0.0

        def train(self) -> None:
            captured["trained"] = True

        def save_model(self, path: str) -> None:
            captured["save_path"] = path

    return (
        _FakeSFTConfig,
        _FakeSFTTrainer,
        _FakeLoraConfig,
        _FakeDataset,
        _FakeModel,
        _FakeTokenizer,
        _FakeTrainerCallback,
    )


def _write_records(path: Path) -> list[dict[str, Any]]:
    """Write a 2-record jsonl (shared run_id so both survive the train/val split) and return them."""
    records = [
        {"run_id": "r0", "scenario": "grid_ctf", "prompt": "What is 2+2?", "strategy": "Answer: 4", "score": 1.0},
        {"run_id": "r0", "scenario": "grid_ctf", "prompt": "What is 3+5?", "strategy": "Answer: 8", "score": 0.5},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return records


def test_run_sft_training_wires_dataset_config_and_deadline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The runner (with the heavy imports faked) must: invoke preflight, build the dataset from
    sft_pairs_from_records, build the SFTConfig from build_sft_config_kwargs, attach the deadline
    TrainerCallback when a deadline is set, save to output_dir/'adapters', and return the metrics."""
    from autocontext.training.autoresearch.data_selection import prepare_training_records
    from autocontext.training.autoresearch.train import _all_records

    data_path = tmp_path / "records.jsonl"
    _write_records(data_path)
    output_dir = tmp_path / "out"
    captured: dict[str, Any] = {}

    monkeypatch.setattr(sb, "_preflight_sft_deps", lambda: captured.__setitem__("preflight", True))
    monkeypatch.setattr(sb, "_load_sft_runtime", lambda: _fake_runtime(captured))

    metrics = sb.run_sft_training(
        scenario_name="grid_ctf",
        data_path=data_path,
        output_dir=output_dir,
        base_model="fake/tiny-model",
        time_budget=600,
        memory_limit_mb=4096,
        deadline_seconds=30.0,
    )

    # Preflight ran before any heavy import.
    assert captured["preflight"] is True

    # The dataset was built from sft_pairs_from_records over the curated records.
    curated = prepare_training_records(_all_records(data_path), elite_fraction=1.0, dedupe=True)
    assert captured["dataset_pairs"] == sb.sft_pairs_from_records(curated)
    assert all(set(p) == {"prompt", "completion"} for p in captured["dataset_pairs"])

    # The SFTConfig kwargs came from build_sft_config_kwargs (its exact field set + values).
    expected_cfg = sb.build_sft_config_kwargs(
        output_dir=output_dir, max_steps=sb._sft_max_steps(600), batch_size=1, learning_rate=2e-4
    )
    assert captured["config_kwargs"] == expected_cfg
    assert captured["config_kwargs"]["output_dir"] == str(output_dir)

    # Model + tokenizer loaded from the requested base; LoRA config is the SFT default.
    assert captured["model_base"] == "fake/tiny-model" and captured["tokenizer_base"] == "fake/tiny-model"
    assert captured["lora_kwargs"] == {"r": 8, "lora_alpha": 16, "task_type": "CAUSAL_LM"}

    # A deadline attaches exactly one real TrainerCallback wrapping DeadlineCallback.
    callbacks = captured["trainer_kwargs"]["callbacks"]
    assert len(callbacks) == 1 and isinstance(callbacks[0], _FakeTrainerCallback)
    assert captured["trainer_kwargs"]["processing_class"] is not None

    # Trained, then saved to the seam's checkpoint path (output_dir/'adapters').
    assert captured["trained"] is True
    assert captured["save_path"] == str(output_dir / "adapters")

    # Metrics carry the seam-required keys.
    for key in ("training_seconds", "num_records", "valid_rate", "avg_score", "peak_memory_mb"):
        assert key in metrics
    assert metrics["num_records"] == 2.0
    assert metrics["valid_rate"] == 1.0
    assert metrics["avg_score"] == pytest.approx(0.75)  # mean of curated scores 1.0 and 0.5


def test_run_sft_training_attaches_no_callback_without_deadline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no deadline, the trainer gets an empty callbacks list (nothing to stop it early)."""
    data_path = tmp_path / "records.jsonl"
    _write_records(data_path)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(sb, "_preflight_sft_deps", lambda: None)
    monkeypatch.setattr(sb, "_load_sft_runtime", lambda: _fake_runtime(captured))

    sb.run_sft_training(
        scenario_name="grid_ctf",
        data_path=data_path,
        output_dir=tmp_path / "out",
        base_model="fake/tiny-model",
        time_budget=5,
        memory_limit_mb=4096,
        deadline_seconds=None,
    )
    assert captured["trainer_kwargs"]["callbacks"] == []


def test_run_sft_training_raises_when_no_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An empty jsonl yields no curated records; the runner raises rather than train on nothing."""
    data_path = tmp_path / "empty.jsonl"
    data_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(sb, "_preflight_sft_deps", lambda: None)
    monkeypatch.setattr(sb, "_load_sft_runtime", lambda: _fake_runtime({}))
    with pytest.raises(ValueError):
        sb.run_sft_training(
            scenario_name="grid_ctf",
            data_path=data_path,
            output_dir=tmp_path / "out",
            base_model="fake/tiny-model",
            time_budget=5,
            memory_limit_mb=4096,
        )


# ---------------------------------------------------------------------------
# Env-gated real fine-tune (downloads a base model + trains; skipped in CI)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("trl") is None or not os.environ.get("AUTOCONTEXT_SFT_E2E"),
    reason="downloads a base model + trains; set AUTOCONTEXT_SFT_E2E=1 with trl installed to run",
)
def test_run_sft_training_e2e_writes_adapter(tmp_path: Path) -> None:  # pragma: no cover - env-gated
    data_path = tmp_path / "records.jsonl"
    _write_records(data_path)
    output_dir = tmp_path / "out"

    metrics = sb.run_sft_training(
        scenario_name="grid_ctf",
        data_path=data_path,
        output_dir=output_dir,
        base_model="hf-internal-testing/tiny-random-LlamaForCausalLM",
        time_budget=120,
        memory_limit_mb=8192,
    )
    adapters = output_dir / "adapters"
    assert adapters.is_dir() and any(adapters.iterdir())
    assert metrics["num_records"] == 2.0
