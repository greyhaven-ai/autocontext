"""Tests for the ReST-EM self-improving training loop (PR7).

The pure helpers (elite selection, sample -> record conversion) run in CI.
The full loop is an MLX end-to-end test, gated behind HAS_MLX like the other
backend smoke tests, and runs two rounds so the dataset-growth invariant is real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.training import HAS_MLX
from autocontext.training.autoresearch.self_improve import (
    representative_context,
    run_self_improving_loop,
    samples_to_records,
    select_elite_samples,
)


def test_select_elite_samples_keeps_top_fraction() -> None:
    samples = [{"strategy": {"a": i}, "score": float(i)} for i in range(8)]
    elite = select_elite_samples(samples, fraction=0.25)
    assert len(elite) == 2
    assert {s["score"] for s in elite} == {7.0, 6.0}


def test_select_elite_samples_empty() -> None:
    assert select_elite_samples([], fraction=0.5) == []


def test_samples_to_records_shapes_training_records() -> None:
    samples = [
        {"strategy": {"aggression": 0.5}, "score": 0.9},
        {"strategy": {"aggression": 0.1}, "score": 0.2},
    ]
    records = samples_to_records(samples, scenario_name="grid_ctf", run_id="gen_0")
    assert len(records) == 2
    for rec, src in zip(records, samples, strict=True):
        assert rec["run_id"] == "gen_0"
        assert rec["scenario"] == "grid_ctf"
        assert rec["strategy"] == src["strategy"]
        assert rec["score"] == src["score"]
        assert rec["context"] == {}


def test_samples_to_records_skips_entries_without_strategy() -> None:
    samples = [{"score": 0.5}, {"strategy": {"x": 1}, "score": 0.7}]
    records = samples_to_records(samples, scenario_name="grid_ctf", run_id="gen_1")
    assert len(records) == 1
    assert records[0]["strategy"] == {"x": 1}


def test_samples_to_records_carries_context() -> None:
    """Generated records inherit the supplied context (not an empty prefix)."""
    samples = [{"strategy": {"x": 1}, "score": 0.7}]
    ctx = {"playbook": "p", "hints": "h"}
    records = samples_to_records(samples, scenario_name="grid_ctf", run_id="gen_0", context=ctx)
    assert records[0]["context"] == ctx


def test_samples_to_records_preserves_per_sample_prompt() -> None:
    """A collected sample's prompt (the problem it was scored on, dataset-style agent tasks) must
    survive into the training record, so the next ReST-EM round trains against the right problem."""
    samples = [
        {"prompt": "Q: 2+2?", "strategy": "Answer: 4", "score": 1.0},
        {"strategy": {"a": 1}, "score": 0.5},  # single-task sample: no prompt
    ]
    records = samples_to_records(samples, scenario_name="gsm8k", run_id="gen_0")
    assert records[0]["prompt"] == "Q: 2+2?"
    assert "prompt" not in records[1]  # absent when the sample had none


def test_representative_context_picks_modal_seed_context() -> None:
    records = [
        {"context": {"playbook": "A"}},
        {"context": {"playbook": "A"}},
        {"context": {"playbook": "B"}},
    ]
    assert representative_context(records) == {"playbook": "A"}


def test_representative_context_empty_records() -> None:
    assert representative_context([]) == {}


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"rounds": 0}, "rounds must be a positive integer"),
        ({"samples_per_round": 0}, "samples_per_round must be a positive integer"),
        ({"train_steps": 0}, "train_steps must be a positive integer"),
    ],
)
def test_run_self_improving_loop_rejects_non_positive_counts(tmp_path: str, kwargs: dict, match: str) -> None:
    """Non-positive loop counts fail fast before any training (no degenerate no-op)."""
    data_path = Path(tmp_path) / "seed.jsonl"
    data_path.write_text(
        json.dumps({"run_id": "r0", "scenario": "grid_ctf", "context": {}, "strategy": {"a": 1}, "score": 0.5}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=match):
        run_self_improving_loop(
            scenario_name="grid_ctf",
            data_path=data_path,
            output_dir=Path(tmp_path) / "out",
            **kwargs,
        )


def test_run_training_collect_samples_path_non_sft_raises(tmp_path: str) -> None:
    """Collect-samples is for the SFT backends (mlx/mlxlm); the RL/distill backends reject it."""
    from autocontext.training.autoresearch.train import run_training

    records = [
        {"run_id": f"r{i % 2}", "scenario": "grid_ctf", "context": {}, "strategy": {"a": 0.5}, "score": 0.5 + 0.01 * i}
        for i in range(6)
    ]
    data_path = Path(tmp_path) / "data.jsonl"
    data_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    with pytest.raises(NotImplementedError, match="mlx and mlxlm"):
        run_training(
            scenario_name="grid_ctf",
            data_path=data_path,
            output_dir=Path(tmp_path) / "out",
            time_budget=30,
            memory_limit_mb=4096,
            train_steps=1,
            batch_size=1,
            seq_len=16,
            backend="cuda",
            collect_samples_path=Path(tmp_path) / "samples.jsonl",
        )


def test_run_self_improving_loop_rejects_non_sft_backend(tmp_path: str) -> None:
    """ReST-EM is iterative SFT; the online-RL / distillation backends have no SFT sample stream."""
    data_path = Path(tmp_path) / "seed.jsonl"
    data_path.write_text(
        json.dumps({"run_id": "r0", "scenario": "grid_ctf", "context": {}, "strategy": {"a": 1}, "score": 0.5}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="backend 'mlx' or 'mlxlm'"):
        run_self_improving_loop(scenario_name="grid_ctf", data_path=data_path, output_dir=Path(tmp_path) / "out", backend="grpo")


def test_run_self_improving_loop_threads_backend_and_adapter_params(tmp_path: str, monkeypatch) -> None:
    """backend + adapter params must reach run_training, so `--backend mlxlm` actually fine-tunes
    the adapter rather than silently falling back to the from-scratch GPT. Mocks training (no mlx)."""
    import autocontext.training.autoresearch.train as train_mod

    data_path = Path(tmp_path) / "seed.jsonl"
    data_path.write_text(
        json.dumps({"run_id": "r0", "scenario": "grid_ctf", "context": {}, "strategy": {"a": 1}, "score": 0.5}) + "\n",
        encoding="utf-8",
    )
    seen: list[dict] = []

    def fake_run_training(**kwargs):
        seen.append(kwargs)
        return {"avg_score": 0.7, "valid_rate": 1.0}

    # run_self_improving_loop imports run_training from this module at call time.
    monkeypatch.setattr(train_mod, "run_training", fake_run_training)
    run_self_improving_loop(
        scenario_name="grid_ctf",
        data_path=data_path,
        output_dir=Path(tmp_path) / "out",
        rounds=1,
        final_train=False,
        backend="mlxlm",
        base_model="mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        fine_tune_type="dora",
        num_layers=4,
    )
    assert seen, "training was never invoked"
    call = seen[0]
    assert call["backend"] == "mlxlm"
    assert call["base_model"] == "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
    assert call["fine_tune_type"] == "dora"
    assert call["num_layers"] == 4


def test_run_self_improving_loop_selects_best_round_not_last(tmp_path: str, monkeypatch) -> None:
    """The loop can peak early and decay (the GSM8K STaR finding: round 1 best, then down). The
    shipped model (best_model_dir/best_round) must be the highest-scoring pass, not blindly the
    last/final one. Mocks training (no mlx)."""
    import autocontext.training.autoresearch.train as train_mod

    data_path = Path(tmp_path) / "seed.jsonl"
    data_path.write_text(
        json.dumps({"run_id": "r0", "scenario": "grid_ctf", "context": {}, "strategy": {"a": 1}, "score": 0.5}) + "\n",
        encoding="utf-8",
    )
    # round_0=0.50, round_1=0.80 (peak), round_2=0.60, final=0.55 -> best is round_1
    scores = iter([0.50, 0.80, 0.60, 0.55])
    monkeypatch.setattr(train_mod, "run_training", lambda **kw: {"avg_score": next(scores), "valid_rate": 1.0})

    out = Path(tmp_path) / "out"
    result = run_self_improving_loop(scenario_name="grid_ctf", data_path=data_path, output_dir=out, rounds=3, final_train=True)

    assert result["best_round"] == "round_1"
    assert result["best_avg_score"] == 0.80
    assert result["best_model_dir"] == str(out / "round_1")
    # the final all-data pass scored lower (0.55) and must NOT be the shipped model
    assert result["final_model_dir"] == str(out / "final")
    assert result["best_model_dir"] != result["final_model_dir"]


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_self_improving_loop_two_rounds_grows_dataset(tmp_path: str) -> None:
    """Two ReST-EM rounds: the loop trains, collects+filters samples, and the
    dataset strictly grows when elite samples are kept (or stays equal if a round
    produced no valid samples). History length and accounting are pinned."""

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
    data_path = Path(tmp_path) / "seed.jsonl"
    data_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    result = run_self_improving_loop(
        scenario_name="grid_ctf",
        data_path=data_path,
        output_dir=Path(tmp_path) / "loop",
        rounds=2,
        samples_per_round=2,
        elite_fraction=0.5,
        train_steps=1,
        batch_size=1,
        seq_len=16,
        time_budget=30,
        memory_limit_mb=4096,
    )

    assert result["rounds"] == 2
    assert len(result["history"]) == 2
    # Dataset is monotone non-decreasing across rounds and never below the seed size.
    sizes = [h["dataset_size"] for h in result["history"]]
    assert sizes[0] >= len(records)
    assert sizes[1] >= sizes[0]
    assert result["final_dataset_size"] == sizes[-1]
    assert Path(result["final_dataset"]).exists()
    for h in result["history"]:
        assert h["num_elite"] <= h["num_samples"]

    # The final retrain bakes every collected sample (incl. the last round's elite)
    # into a shipped model: the model artifact exists and a final score is reported.
    assert result["final_avg_score"] is not None
    assert result["final_model_dir"] is not None
    assert (Path(result["final_model_dir"]) / "model.safetensors").exists()

    # Generated elite records carry the seed dataset's context, not an empty prefix.
    final_records = [json.loads(line) for line in Path(result["final_dataset"]).read_text().splitlines() if line.strip()]
    generated = [r for r in final_records if str(r["run_id"]).startswith("gen_")]
    for rec in generated:
        assert rec["context"] == {"playbook": "p"}


@pytest.mark.skipif(not HAS_MLX, reason="MLX not installed")
def test_self_improving_loop_final_train_off_skips_final_model(tmp_path: str) -> None:
    """With final_train=False the loop only grows the dataset (no final model)."""
    records = [
        {"run_id": f"r{i % 2}", "scenario": "grid_ctf", "context": {}, "strategy": {"a": 0.5}, "score": 0.5 + 0.01 * i}
        for i in range(6)
    ]
    data_path = Path(tmp_path) / "seed.jsonl"
    data_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    result = run_self_improving_loop(
        scenario_name="grid_ctf",
        data_path=data_path,
        output_dir=Path(tmp_path) / "loop",
        rounds=1,
        samples_per_round=2,
        elite_fraction=0.5,
        train_steps=1,
        batch_size=1,
        seq_len=16,
        time_budget=30,
        memory_limit_mb=4096,
        final_train=False,
    )
    assert result["final_avg_score"] is None
    assert result["final_model_dir"] is None
    assert Path(result["final_dataset"]).exists()
