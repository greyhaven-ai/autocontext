"""tests for the train stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import autocontext.ambient.train as train_mod
from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.datasets import DatasetStore
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.stage import StageContext
from autocontext.ambient.train import TrainStage
from autocontext.ambient.training_backend import TrainOutcome
from autocontext.ambient.usage import UsageLedger
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.training.model_registry import ModelRegistry

_NOW = "2026-07-06T12:00:00+00:00"


def _target(name: str = "competitor-local", **overrides: Any) -> CharterTarget:
    base: dict[str, Any] = {
        "name": name,
        "kind": "role",
        "selector": "competitor",
        "base_model": "tiny",
        "min_dataset_records": 5,
        "eval_suite": "anchor-v1",
    }
    base.update(overrides)
    return CharterTarget(**base)


def _charter(targets: list[CharterTarget], autonomy: str = "train", gpu_hours: float = 10.0) -> Charter:
    return Charter(
        tier="oss",
        autonomy=autonomy,
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=targets,
        budgets=CharterBudgets(gpu_hours_per_window=gpu_hours, window_hours=24, disk_quota_gb=1.0),
    )


def _ctx(tmp_path: Path, charter: Charter) -> StageContext:
    return StageContext(
        charter=charter,
        queue=AmbientQueue(tmp_path / "q.sqlite3"),
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
    )


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _stage(tmp_path: Path) -> TrainStage:
    return TrainStage(
        name="train",
        dataset_store=DatasetStore(tmp_path / "datasets"),
        usage_ledger=UsageLedger(tmp_path / "usage.sqlite3"),
        registry=ModelRegistry(tmp_path / "registry"),
        artifacts_root=tmp_path / "artifacts",
        checkpoints_root=tmp_path / "checkpoints",
        now_fn=lambda: _NOW,
    )


def _seed_dataset(tmp_path: Path, target: str, count: int) -> None:
    store = DatasetStore(tmp_path / "datasets")
    records = [{"run_id": "r", "scenario": "grid_ctf", "strategy": f"s{i}", "score": 0.9, "context": {}} for i in range(count)]
    store.append_records(target, records)
    manifest = store.load_manifest(target)
    store.save_manifest(store.absorb(manifest, [0.9] * count, 0, 0, count))


def _patch_backend(monkeypatch: Any, *, available: str | None, gpu_hours: float = 1.0) -> None:
    monkeypatch.setattr(train_mod, "select_backend", lambda method: available)

    def fake_run(backend_name: str, request: Any) -> TrainOutcome:
        return TrainOutcome(
            checkpoint_path=request.output_dir / "adapters",
            backend=backend_name,
            metrics={"avg_score": 0.8, "valid_rate": 1.0, "num_records": 5.0, "training_seconds": gpu_hours * 3600},
            gpu_hours=gpu_hours,
        )

    monkeypatch.setattr(train_mod, "run_training", fake_run)


def test_trains_and_publishes_a_candidate(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    _patch_backend(monkeypatch, available="trl", gpu_hours=2.0)
    stage = _stage(tmp_path)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()])))

    assert result.processed == 1
    assert result.errors == 0
    assert stage.usage_ledger.used_in_window("competitor-local", 24, _NOW) == 2.0
    assert len(stage.registry.list_all()) == 1
    events = _events(tmp_path)
    assert any(e["event"] == "train_candidate_published" for e in events)


def test_below_min_records_is_a_silent_skip(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 2)  # min is 5
    _patch_backend(monkeypatch, available="trl")
    stage = _stage(tmp_path)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()])))

    assert result.processed == 0
    assert stage.registry.list_all() == []


def test_propose_autonomy_requires_approval(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    _patch_backend(monkeypatch, available="trl")
    stage = _stage(tmp_path)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()], autonomy="propose")))

    assert result.processed == 0
    events = _events(tmp_path)
    assert any(e["event"] == "train_requires_approval" for e in events)


def test_budget_exhausted_skips(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    trained = {"ran": False}
    monkeypatch.setattr(train_mod, "select_backend", lambda method: "trl")

    def fake_run(backend_name: str, request: Any) -> TrainOutcome:
        trained["ran"] = True  # the pre-flight gate must return before we get here
        return TrainOutcome(request.output_dir / "adapters", backend_name, {"num_records": 5.0, "training_seconds": 3600.0}, 1.0)

    monkeypatch.setattr(train_mod, "run_training", fake_run)
    stage = _stage(tmp_path)
    # window budget is 10.0; the default reserved envelope is time_budget (1800s) + assess
    # overhead (1800s) = 1.0h. seed 9.8 used so the pre-flight estimate (9.8 + 1.0 = 10.8 > 10.0)
    # breaches before any training happens.
    stage.usage_ledger.record("competitor-local", 9.8, _NOW)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()], gpu_hours=10.0)))

    assert result.processed == 0
    assert not trained["ran"]  # pre-flight prevented the spend: run_training was never reached
    assert stage.registry.list_all() == []  # and nothing was published
    events = _events(tmp_path)
    assert any(e["event"] == "train_budget_exhausted" for e in events)


def test_envelope_over_budget_blocks_even_when_train_budget_alone_fits(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    trained = {"ran": False}
    monkeypatch.setattr(train_mod, "select_backend", lambda method: "trl")

    def fake_run(backend_name: str, request: Any) -> TrainOutcome:
        trained["ran"] = True
        return TrainOutcome(request.output_dir / "adapters", backend_name, {"num_records": 5.0, "training_seconds": 3600.0}, 1.0)

    monkeypatch.setattr(train_mod, "run_training", fake_run)
    stage = _stage(tmp_path)
    # window budget is 10.0. train_budget alone is 1800s = 0.5h, which would fit (9.4 + 0.5 = 9.9).
    # but the reserved envelope adds the 1800s assess overhead -> 1.0h, so 9.4 + 1.0 = 10.4 > 10.0
    # and the job is blocked pre-flight. this is the under-reservation the fix closes.
    stage.usage_ledger.record("competitor-local", 9.4, _NOW)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()], gpu_hours=10.0)))

    assert result.processed == 0
    assert not trained["ran"]
    assert stage.registry.list_all() == []
    events = _events(tmp_path)
    assert any(e["event"] == "train_budget_exhausted" for e in events)


def test_sft_backend_reserves_true_ceiling_not_envelope(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    _patch_backend(monkeypatch, available="sft", gpu_hours=0.5)
    stage = _stage(tmp_path)
    # the sft backend enforces a real in-run wall-clock deadline, so its reservation is the TRUE
    # ceiling = time_budget 1800s = 0.5h (no assess overhead). window budget is 10.0; seed 9.4 used:
    # 9.4 + 0.5(ceiling) = 9.9 < 10.0 so the job is ALLOWED. the conservative envelope (0.5 + 0.5
    # assess = 1.0h) would have blocked it (9.4 + 1.0 = 10.4 > 10.0); proving it is not applied.
    stage.usage_ledger.record("competitor-local", 9.4, _NOW)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()], gpu_hours=10.0)))

    assert result.processed == 1
    events = _events(tmp_path)
    assert any(e["event"] == "train_candidate_published" for e in events)
    assert not any(e["event"] == "train_budget_exhausted" for e in events)


def test_mlxlm_backend_keeps_conservative_envelope(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    trained = {"ran": False}
    monkeypatch.setattr(train_mod, "select_backend", lambda method: "mlxlm")

    def fake_run(backend_name: str, request: Any) -> TrainOutcome:
        trained["ran"] = True  # the envelope must block before we get here
        return TrainOutcome(request.output_dir / "adapters", backend_name, {"num_records": 5.0, "training_seconds": 3600.0}, 1.0)

    monkeypatch.setattr(train_mod, "run_training", fake_run)
    stage = _stage(tmp_path)
    # mlxlm is NOT deadline-capable: its assessment runs outside any in-run deadline, so the
    # conservative envelope (0.5 + 0.5 assess = 1.0h) still applies. the true ceiling alone (0.5h)
    # would fit (9.4 + 0.5 = 9.9), but the envelope blocks it (9.4 + 1.0 = 10.4 > 10.0).
    stage.usage_ledger.record("competitor-local", 9.4, _NOW)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()], gpu_hours=10.0)))

    assert result.processed == 0
    assert not trained["ran"]
    assert stage.registry.list_all() == []
    events = _events(tmp_path)
    assert any(e["event"] == "train_budget_exhausted" for e in events)


def test_budget_pool_is_charter_wide(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    trained = {"ran": False}
    monkeypatch.setattr(train_mod, "select_backend", lambda method: "trl")

    def fake_run(backend_name: str, request: Any) -> TrainOutcome:
        trained["ran"] = True  # the shared pool must block before we get here
        return TrainOutcome(request.output_dir / "adapters", backend_name, {"num_records": 5.0, "training_seconds": 3600.0}, 1.0)

    monkeypatch.setattr(train_mod, "run_training", fake_run)
    stage = _stage(tmp_path)
    # window budget is 10.0h, shared charter-wide. another target already spent 9.6h, so the pool
    # is nearly exhausted. competitor-local itself has used 0, but its pre-flight (envelope 1.0h)
    # reads the CHARTER-WIDE total: 9.6 + 1.0 = 10.6 > 10.0, so it is blocked. under the old
    # per-target read it would have seen 0 and trained, spending a second full window.
    stage.usage_ledger.record("competitor-other", 9.6, _NOW)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()], gpu_hours=10.0)))

    assert result.processed == 0
    assert not trained["ran"]  # the shared pool prevented the spend
    assert stage.registry.list_all() == []
    events = _events(tmp_path)
    exhausted = [e for e in events if e["event"] == "train_budget_exhausted"]
    assert any(e["payload"]["target"] == "competitor-local" for e in exhausted)
    assert any(e["payload"]["used_gpu_hours"] == 9.6 for e in exhausted)


def test_unchanged_dataset_is_not_retrained(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    calls = {"count": 0}
    monkeypatch.setattr(train_mod, "select_backend", lambda method: "trl")

    def fake_run(backend_name: str, request: Any) -> TrainOutcome:
        calls["count"] += 1
        return TrainOutcome(
            request.output_dir / "adapters",
            backend_name,
            {"num_records": 6.0, "training_seconds": 3600.0},
            1.0,
        )

    monkeypatch.setattr(train_mod, "run_training", fake_run)
    stage = _stage(tmp_path)
    charter = _charter([_target()])

    first = stage.run_once(_ctx(tmp_path, charter))
    second = stage.run_once(_ctx(tmp_path, charter))

    assert first.processed == 1
    assert second.processed == 0  # the unchanged manifest is skipped the second cycle
    assert calls["count"] == 1  # run_training ran exactly once
    assert stage.usage_ledger.used_in_window("competitor-local", 24, _NOW) == 1.0  # charged once
    assert len(stage.registry.list_all()) == 1
    events = _events(tmp_path)
    up_to_date = [e for e in events if e["event"] == "train_up_to_date"]
    assert len(up_to_date) == 1
    assert up_to_date[0]["payload"]["record_count"] == 6


def test_growing_dataset_trains_a_distinct_checkpoint(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 5)
    monkeypatch.setattr(train_mod, "select_backend", lambda method: "trl")

    def fake_run(backend_name: str, request: Any) -> TrainOutcome:
        return TrainOutcome(
            request.output_dir / "adapters",
            backend_name,
            {"num_records": 6.0, "training_seconds": 3600.0},
            1.0,
        )

    monkeypatch.setattr(train_mod, "run_training", fake_run)
    stage = _stage(tmp_path)
    charter = _charter([_target()])

    stage.run_once(_ctx(tmp_path, charter))
    _seed_dataset(tmp_path, "competitor-local", 1)  # grow 5 -> 6
    stage.run_once(_ctx(tmp_path, charter))

    records = stage.registry.list_all()
    assert len(records) == 2
    paths = sorted(r.checkpoint_path for r in records)
    assert any("/5/" in p for p in paths)
    assert any("/6/" in p for p in paths)


def test_no_available_backend_is_not_an_error(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "competitor-local", 6)
    _patch_backend(monkeypatch, available=None)
    stage = _stage(tmp_path)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()])))

    assert result.processed == 0
    assert result.errors == 0
    events = _events(tmp_path)
    assert any(e["event"] == "train_no_backend" for e in events)


def test_target_failure_is_isolated(tmp_path: Path, monkeypatch: Any) -> None:
    _seed_dataset(tmp_path, "boom", 6)
    _seed_dataset(tmp_path, "fine", 6)
    monkeypatch.setattr(train_mod, "select_backend", lambda method: "trl")

    def fake_run(backend_name: str, request: Any) -> TrainOutcome:
        if "boom" in str(request.output_dir):
            raise RuntimeError("backend blew up")
        return TrainOutcome(request.output_dir / "adapters", backend_name, {"num_records": 5.0, "training_seconds": 3600.0}, 1.0)

    monkeypatch.setattr(train_mod, "run_training", fake_run)
    stage = _stage(tmp_path)
    charter = _charter([_target(name="boom"), _target(name="fine")])

    result = stage.run_once(_ctx(tmp_path, charter))

    assert result.errors == 1
    assert result.processed == 1
    events = _events(tmp_path)
    assert any(e["event"] == "train_target_failed" and e["payload"]["target"] == "boom" for e in events)
