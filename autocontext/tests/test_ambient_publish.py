"""tests for ambient candidate publish."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.ambient.charter import CharterTarget
from autocontext.ambient.publish import publish_candidate
from autocontext.ambient.training_backend import TrainOutcome
from autocontext.training.model_registry import ModelRegistry


def _target(**overrides: Any) -> CharterTarget:
    base: dict[str, Any] = {
        "name": "competitor-local",
        "kind": "role",
        "selector": "competitor",
        "base_model": "tiny",
        "min_dataset_records": 1,
        "eval_suite": "anchor-v1",
    }
    base.update(overrides)
    return CharterTarget(**base)


def test_publish_registers_a_non_active_candidate(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    outcome = TrainOutcome(
        checkpoint_path=tmp_path / "out" / "adapters",
        backend="trl",
        metrics={"avg_score": 0.8, "valid_rate": 1.0, "num_records": 10.0},
        gpu_hours=1.0,
    )

    artifact_id = publish_candidate(
        outcome=outcome,
        target=_target(),
        scenario="grid_ctf",
        registry=registry,
        artifacts_root=tmp_path / "artifacts",
        run_id="ambient-run-1",
        record_count=10,
    )

    record = registry.load(artifact_id)
    assert record is not None
    assert record.activation_state == "candidate"
    assert record.metadata.get("produced_by") == "finetune:competitor-local"
    # the adapter-serving path refuses a record without base_model, so it must be stamped
    assert record.metadata.get("base_model") == "tiny"
    assert record.metadata.get("record_count") == 10


def test_publish_is_idempotent_on_same_inputs(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    outcome = TrainOutcome(tmp_path / "out" / "adapters", "trl", {"avg_score": 0.5}, 1.0)
    kwargs = dict(
        outcome=outcome,
        target=_target(),
        scenario="grid_ctf",
        registry=registry,
        artifacts_root=tmp_path / "artifacts",
        run_id="ambient-run-1",
        record_count=10,
    )
    first = publish_candidate(**kwargs)
    second = publish_candidate(**kwargs)
    assert first == second
    assert len(registry.list_all()) == 1
