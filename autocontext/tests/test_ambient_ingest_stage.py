from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.ingest import IngestStage
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.sources.contract import RawTrace, SourcePoll
from autocontext.ambient.stage import StageContext
from autocontext.ambient.trace_store import TraceStore
from autocontext.harness.core.events import EventStreamEmitter

_FAKE_KEY = "sk-ant-api03-" + "a" * 32


def _charter() -> Charter:
    return Charter(
        tier="oss",
        sources=[CharterSource(name="native", kind="autocontext")],
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
        budgets=CharterBudgets(gpu_hours_per_window=1.0, window_hours=24, disk_quota_gb=10.0),
    )


def _ctx(tmp_path: Path) -> StageContext:
    return StageContext(
        charter=_charter(),
        queue=AmbientQueue(tmp_path / "ambient.sqlite3"),
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
    )


@dataclass
class ScriptedSource:
    name: str
    polls: list[SourcePoll]
    kind: str = "autocontext"
    seen_cursors: list[str | None] = field(default_factory=list)

    def poll(self, cursor: str | None) -> SourcePoll:
        self.seen_cursors.append(cursor)
        return self.polls.pop(0) if self.polls else SourcePoll()


@dataclass
class ExplodingSource:
    name: str
    kind: str = "autocontext"

    def poll(self, cursor: str | None) -> SourcePoll:
        raise RuntimeError("feed offline")


def test_ingest_appends_redacts_and_advances_cursor(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    source = ScriptedSource(
        name="native",
        polls=[SourcePoll(records=[RawTrace(kind="generation", payload={"note": f"key {_FAKE_KEY}"})], next_cursor="5")],
    )
    stage = IngestStage(name="ingest", trace_store=store, sources=[source], disk_quota_gb=10.0)
    result = stage.run_once(_ctx(tmp_path))
    assert result.processed == 1 and result.errors == 0
    assert source.seen_cursors == [None]
    assert store.get_cursor("autocontext:native") == "5"
    record = store.recent(limit=1)[0]
    assert _FAKE_KEY not in str(record.payload)
    assert record.redaction_findings >= 1
    again = stage.run_once(_ctx(tmp_path))
    assert again.processed == 0
    assert source.seen_cursors[-1] == "5"


def test_batch_failure_leaves_no_records_and_no_cursor(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = TraceStore(tmp_path / "ambient.sqlite3")
    source = ScriptedSource(
        name="native",
        polls=[SourcePoll(records=[RawTrace(kind="generation", payload={"n": 1})], next_cursor="5")],
    )

    def _boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("store down")

    monkeypatch.setattr(TraceStore, "append_batch", _boom)
    stage = IngestStage(name="ingest", trace_store=store, sources=[source], disk_quota_gb=10.0)
    result = stage.run_once(_ctx(tmp_path))
    assert result.errors == 1
    assert result.processed == 0
    assert store.count() == 0
    assert store.get_cursor("autocontext:native") is None


def test_failing_source_isolated_and_counted(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    good = ScriptedSource(name="ok", polls=[SourcePoll(records=[RawTrace(kind="x", payload={})], next_cursor="1")])
    stage = IngestStage(
        name="ingest",
        trace_store=store,
        sources=[ExplodingSource(name="bad"), good],
        disk_quota_gb=10.0,
    )
    result = stage.run_once(_ctx(tmp_path))
    assert result.errors == 1
    assert result.processed == 1
    events = (tmp_path / "events.ndjson").read_text(encoding="utf-8")
    assert "ingest_source_failed" in events
    assert "ingest_completed" in events


def test_disk_quota_triggers_prune(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    for i in range(50):
        store.append("native", "generation", {"pad": "x" * 200, "i": i}, "frontier", 0)
    stage = IngestStage(name="ingest", trace_store=store, sources=[], disk_quota_gb=1e-9)
    before = store.count()
    stage.run_once(_ctx(tmp_path))
    assert store.count() < before
    events = (tmp_path / "events.ndjson").read_text(encoding="utf-8")
    assert "trace_retention_pruned" in events


def test_unsupported_kinds_announced_once_at_run_time(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    stage = IngestStage(
        name="ingest",
        trace_store=store,
        sources=[],
        disk_quota_gb=10.0,
        unsupported=[("box", "full-box")],
    )
    ctx = _ctx(tmp_path)
    stage.run_once(ctx)
    stage.run_once(ctx)
    events = (tmp_path / "events.ndjson").read_text(encoding="utf-8")
    assert events.count("ingest_source_unsupported") == 1
