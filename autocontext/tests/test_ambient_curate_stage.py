"""tests for the curate stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.curate import CurateStage
from autocontext.ambient.datasets import DatasetStore
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.stage import StageContext
from autocontext.ambient.trace_store import TraceStore
from autocontext.harness.core.events import EventStreamEmitter


def _charter(targets: list[CharterTarget]) -> Charter:
    return Charter(
        tier="oss",
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=targets,
        budgets=CharterBudgets(gpu_hours_per_window=1.0, window_hours=24, disk_quota_gb=1.0),
    )


def _target(name: str = "competitor-local", **overrides: Any) -> CharterTarget:
    base: dict[str, Any] = {
        "name": name,
        "kind": "role",
        "selector": "competitor",
        "base_model": "qwen2.5-3b",
        "min_dataset_records": 10,
        "eval_suite": "anchor-v1",
    }
    base.update(overrides)
    return CharterTarget(**base)


def _ctx(tmp_path: Path, charter: Charter) -> StageContext:
    return StageContext(
        charter=charter,
        queue=AmbientQueue(tmp_path / "queue.sqlite3"),
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
    )


def _output_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run_a",
        "scenario": "grid_ctf",
        "generation_index": 0,
        "role": "competitor",
        "content": "strategy text",
        "status": "completed",
        "mean_score": 0.5,
        "best_score": 0.9,
        "gate_decision": "advance",
        "created_at": "t",
    }
    payload.update(overrides)
    return payload


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_eligible_traces_land_in_the_target_dataset(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    traces.append("autocontext-outputs:native", "agent_output", _output_payload(), "frontier", 0)
    traces.append("autocontext-outputs:native", "agent_output", _output_payload(role="analyst"), "frontier", 0)
    datasets = DatasetStore(tmp_path / "datasets")
    stage = CurateStage(name="curate", trace_store=traces, dataset_store=datasets)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()])))

    assert result.processed == 1
    assert result.errors == 0
    lines = datasets.dataset_path("competitor-local").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["strategy"] == "strategy text"
    manifest = datasets.load_manifest("competitor-local")
    assert manifest.record_count == 1
    assert manifest.skipped_total == 1
    assert manifest.last_record_id == 2  # the cursor passed the ineligible trace too


def test_quarantined_provenance_counts_but_never_lands(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    traces.append("autocontext-outputs:native", "agent_output", _output_payload(), "finetune:lineage-1", 0)
    datasets = DatasetStore(tmp_path / "datasets")
    stage = CurateStage(name="curate", trace_store=traces, dataset_store=datasets)

    result = stage.run_once(_ctx(tmp_path, _charter([_target()])))

    assert result.processed == 0
    assert not datasets.dataset_path("competitor-local").exists()
    manifest = datasets.load_manifest("competitor-local")
    assert manifest.quarantined_total == 1
    assert manifest.last_record_id == 1


def test_second_run_consumes_nothing_new(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    traces.append("autocontext-outputs:native", "agent_output", _output_payload(), "frontier", 0)
    datasets = DatasetStore(tmp_path / "datasets")
    stage = CurateStage(name="curate", trace_store=traces, dataset_store=datasets)
    charter = _charter([_target()])

    stage.run_once(_ctx(tmp_path, charter))
    second = stage.run_once(_ctx(tmp_path, charter))

    assert second.processed == 0
    assert len(datasets.dataset_path("competitor-local").read_text(encoding="utf-8").splitlines()) == 1


def test_two_targets_hold_independent_cursors(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    traces.append("autocontext-outputs:native", "agent_output", _output_payload(), "frontier", 0)
    traces.append("autocontext-outputs:native", "agent_output", _output_payload(scenario="othello"), "frontier", 0)
    datasets = DatasetStore(tmp_path / "datasets")
    stage = CurateStage(name="curate", trace_store=traces, dataset_store=datasets)
    charter = _charter(
        [
            _target(),
            _target(name="grid-only", kind="task_family", selector="grid_ctf"),
        ]
    )

    result = stage.run_once(_ctx(tmp_path, charter))

    # role target takes both outputs; task-family target takes only grid_ctf
    assert result.processed == 3
    assert datasets.load_manifest("competitor-local").record_count == 2
    assert datasets.load_manifest("grid-only").record_count == 1
    assert datasets.load_manifest("grid-only").skipped_total == 1


def test_evaluative_target_is_skipped_with_event(tmp_path: Path) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    traces.append("autocontext-outputs:native", "agent_output", _output_payload(role="curator"), "frontier", 0)
    datasets = DatasetStore(tmp_path / "datasets")
    stage = CurateStage(name="curate", trace_store=traces, dataset_store=datasets)

    result = stage.run_once(_ctx(tmp_path, _charter([_target(name="curator-local", selector="curator")])))

    assert result.processed == 0
    assert not datasets.dataset_path("curator-local").exists()
    events = _events(tmp_path)
    assert any(e["event"] == "curate_target_skipped" and e["payload"]["reason"] == "evaluative_role" for e in events)


def test_target_failure_is_isolated(tmp_path: Path, monkeypatch: Any) -> None:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    traces.append("autocontext-outputs:native", "agent_output", _output_payload(), "frontier", 0)
    datasets = DatasetStore(tmp_path / "datasets")
    stage = CurateStage(name="curate", trace_store=traces, dataset_store=datasets)
    charter = _charter([_target(name="boom"), _target(name="fine")])

    original = datasets.load_manifest

    def exploding(target: str) -> Any:
        if target == "boom":
            raise RuntimeError("manifest io failure")
        return original(target)

    monkeypatch.setattr(datasets, "load_manifest", exploding)

    result = stage.run_once(_ctx(tmp_path, charter))

    assert result.errors == 1
    assert result.processed == 1
    events = _events(tmp_path)
    assert any(e["event"] == "curate_target_failed" and e["payload"]["target"] == "boom" for e in events)
