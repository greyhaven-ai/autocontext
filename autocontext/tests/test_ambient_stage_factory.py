from __future__ import annotations

from pathlib import Path

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.ingest import IngestStage
from autocontext.ambient.sources.jsonl_feed import JsonlFeedSource
from autocontext.ambient.sources.native import NativeRunsSource
from autocontext.ambient.stage import STAGE_NAMES, NoOpStage
from autocontext.ambient.stage_factory import build_stages
from autocontext.harness.core.events import EventStreamEmitter


def _charter(sources: list[CharterSource], tier: str = "oss") -> Charter:
    return Charter(
        tier=tier,  # type: ignore[arg-type]
        sources=sources,
        targets=[
            CharterTarget(
                name="t1",
                kind="role",
                selector="competitor@grid_ctf",
                base_model="m",
                min_dataset_records=1,
                eval_suite="e",
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=1.0, window_hours=24, disk_quota_gb=7.0),
    )


def test_build_stages_wires_enabled_sources(tmp_path: Path) -> None:
    charter = _charter(
        [
            CharterSource(name="native", kind="autocontext"),
            CharterSource(name="feed", kind="otel"),
            CharterSource(name="off", kind="otel", enabled=False),
        ]
    )
    stages = build_stages(
        charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        runs_db_path=tmp_path / "runs.sqlite3",
        otel_feed_dir=tmp_path / "feed",
    )
    assert set(stages.keys()) == set(STAGE_NAMES)
    ingest = stages["ingest"]
    assert isinstance(ingest, IngestStage)
    assert ingest.disk_quota_gb == 7.0
    kinds = [type(source) for source in ingest.sources]
    assert kinds == [NativeRunsSource, JsonlFeedSource]
    assert all(isinstance(stages[name], NoOpStage) for name in ("curate", "advise", "train", "evaluate"))


def test_unsupported_kinds_emit_event_and_are_skipped(tmp_path: Path) -> None:
    events_path = tmp_path / "events.ndjson"
    charter = _charter(
        [CharterSource(name="native", kind="autocontext"), CharterSource(name="box", kind="full-box")],
        tier="hosted-box",
    )
    stages = build_stages(
        charter,
        db_path=tmp_path / "ambient.sqlite3",
        emitter=EventStreamEmitter(events_path),
        runs_db_path=tmp_path / "runs.sqlite3",
        otel_feed_dir=tmp_path / "feed",
    )
    ingest = stages["ingest"]
    assert isinstance(ingest, IngestStage)
    assert len(ingest.sources) == 1
    assert "ingest_source_unsupported" in events_path.read_text(encoding="utf-8")
