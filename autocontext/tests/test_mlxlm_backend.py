"""Tests for the mlx-lm pretrained-finetune backend.

The pure data-conversion and dependency-preflight tests are CI-safe (no mlx-lm).
The end-to-end LoRA fine-tune is gated behind both mlx-lm being installed AND an
explicit env flag, since it downloads a base model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from autocontext.training.autoresearch import mlxlm_backend as mb


def test_build_completion_record_without_quality() -> None:
    rec = mb.build_completion_record(task_prompt="Build a cap set.", strategy_json='{"points": [1, 2]}')
    assert rec == {"prompt": "Build a cap set.", "completion": '{"points": [1, 2]}'}


def test_build_completion_record_with_quality_prefix() -> None:
    rec = mb.build_completion_record(task_prompt="Build a cap set.", strategy_json='{"points": [1]}', quality=4, num_buckets=5)
    assert rec["completion"] == '{"points": [1]}'
    assert rec["prompt"].endswith("Build a cap set.")
    assert "Target quality: 4 out of 4" in rec["prompt"]


def test_records_to_completions_buckets_by_score() -> None:
    records = [{"strategy": {"a": 1}, "score": 1.0}, {"strategy": {"a": 2}, "score": 0.0}]
    comps = mb.records_to_completions(records, task_prompt="T", score_conditioned=True, num_buckets=5)
    assert "Target quality: 4" in comps[0]["prompt"]  # top score -> top bucket
    assert "Target quality: 0" in comps[1]["prompt"]  # zero score -> bottom bucket
    # without conditioning, no quality directive
    plain = mb.records_to_completions(records, task_prompt="T", score_conditioned=False)
    assert all("Target quality" not in c["prompt"] for c in plain)


def test_write_completion_dataset_writes_train_and_valid(tmp_path: Path) -> None:
    records = [{"strategy": {"a": i}, "score": i / 10} for i in range(10)]
    data_dir = tmp_path / "data"
    n_train, n_val = mb.write_completion_dataset(records, data_dir, task_prompt="T")
    assert (data_dir / "train.jsonl").exists()
    assert (data_dir / "valid.jsonl").exists()
    assert n_train + n_val == 10
    assert n_val >= 1  # mlx-lm requires a non-empty validation set
    # each line is a valid completions record
    for line in (data_dir / "train.jsonl").read_text(encoding="utf-8").splitlines():
        obj = json.loads(line)
        assert "prompt" in obj and "completion" in obj


def test_write_completion_dataset_single_record_reuses_for_valid(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    n_train, n_val = mb.write_completion_dataset([{"strategy": {"a": 1}, "score": 1.0}], data_dir, task_prompt="T")
    assert n_train == 1 and n_val == 1  # valid reuses the single example


def test_mlxlm_registered_in_backend_registry() -> None:
    # [P1] regression: the public TrainingRunner resolves the backend via this registry.
    from autocontext.training.backends import default_backend_registry

    backend = default_backend_registry().get("mlxlm")
    assert backend is not None
    assert backend.name == "mlxlm"
    assert "mlxlm" in str(backend.default_checkpoint_dir("grid_ctf"))


def test_training_runner_accepts_mlxlm_backend(tmp_path: Path) -> None:
    # [P1] regression: constructing the runner with backend="mlxlm" must not raise
    # "Unknown training backend" (the documented autoctx train --backend mlxlm path).
    from autocontext.training.runner import TrainingConfig, TrainingRunner

    cfg = TrainingConfig(scenario="grid_ctf", data_path=tmp_path / "data.jsonl", backend="mlxlm")
    runner = TrainingRunner(cfg, work_dir=tmp_path / "ws")
    assert runner is not None


def test_write_completion_dataset_holds_out_tail_not_elite(tmp_path: Path) -> None:
    # [P2] regression: records arrive elite-first; the top record must be TRAINED on,
    # not held out for validation.
    records = [{"strategy": {"rank": s}, "score": s / 10} for s in (9, 8, 7, 6, 5)]
    data_dir = tmp_path / "data"
    mb.write_completion_dataset(records, data_dir, task_prompt="T")

    def _ranks(path: Path) -> set[int]:
        return {
            json.loads(json.loads(line)["completion"])["rank"] for line in path.read_text(encoding="utf-8").splitlines() if line
        }

    train_ranks = _ranks(data_dir / "train.jsonl")
    valid_ranks = _ranks(data_dir / "valid.jsonl")
    assert 9 in train_ranks  # best example is trained on
    assert 9 not in valid_ranks
    assert valid_ranks == {5}  # the lowest-scoring elite is held out


def test_scenario_task_prompt_prefers_get_task_prompt() -> None:
    class _Scn:
        def initial_state(self, seed: int | None = None) -> dict:
            return {"n": 4}

        def get_task_prompt(self, state: dict) -> str:
            return f"Construct for n={state['n']}."

    assert mb.scenario_task_prompt(_Scn()) == "Construct for n=4."


def test_scenario_task_prompt_falls_back_to_description() -> None:
    class _Scn:
        description = "fallback description"

    assert mb.scenario_task_prompt(_Scn()) == "fallback description"


def test_preflight_rejects_missing_mlx_lm(monkeypatch) -> None:
    import importlib.util as _u

    from autocontext.training.autoresearch import train as train_mod

    real = _u.find_spec

    def fake(name, *a, **k):
        return None if name == "mlx_lm" else real(name, *a, **k)

    monkeypatch.setattr(train_mod.importlib.util, "find_spec", fake)
    with pytest.raises(RuntimeError, match="mlx-lm"):
        train_mod._preflight_backend_deps("mlxlm")


# ---------------------------------------------------------------------------
# End-to-end LoRA fine-tune (downloads a base model; opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not mb.HAS_MLXLM or not os.environ.get("AUTOCONTEXT_MLXLM_E2E"),
    reason="requires mlx-lm + AUTOCONTEXT_MLXLM_E2E=1 (downloads a base model)",
)
def test_mlxlm_end_to_end_smoke(tmp_path: Path) -> None:
    from autocontext.training.autoresearch.train import run_training

    records = [
        {"run_id": "r0", "scenario": "grid_ctf", "context": {}, "strategy": {"aggression": 0.5}, "score": 0.5} for _ in range(6)
    ]
    data_path = tmp_path / "data.jsonl"
    data_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    metrics = run_training(
        scenario_name="grid_ctf",
        data_path=data_path,
        output_dir=tmp_path / "out",
        time_budget=600,
        memory_limit_mb=8192,
        train_steps=2,
        batch_size=1,
        num_layers=2,
        assess_samples=1,
        backend="mlxlm",
    )
    assert "avg_score" in metrics and "num_records" in metrics
