from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.trace_store import TraceStore


def test_append_and_recent(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    first = store.append("native", "generation", {"score": 1.0}, "frontier", 0)
    second = store.append("otel", "llm_call", {"prompt": "hi"}, "frontier", 2)
    assert second > first
    assert store.count() == 2
    assert store.count("native") == 1
    newest = store.recent(limit=1)[0]
    assert newest.record_id == second
    assert newest.payload == {"prompt": "hi"}
    assert newest.redaction_findings == 2
    assert newest.created_at


def test_cursor_roundtrip_and_upsert(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    assert store.get_cursor("native") is None
    store.set_cursor("native", "41")
    store.set_cursor("native", "42")
    assert store.get_cursor("native") == "42"


def test_shares_db_file_with_queue(tmp_path: Path) -> None:
    db = tmp_path / "ambient.sqlite3"
    queue = AmbientQueue(db)
    store = TraceStore(db)
    queue.enqueue("ingest", "poll_source", {})
    store.append("native", "generation", {}, "frontier", 0)
    assert queue.depth("ingest") == 1
    assert store.count() == 1


def test_append_batch_commits_records_and_cursor_together(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    inserted = store.append_batch(
        "autocontext:native",
        [("generation", {"n": 1}, "frontier", 0), ("llm_call", {"n": 2}, "frontier", 1)],
        "7",
    )
    assert inserted == 2
    assert store.count() == 2
    assert store.get_cursor("autocontext:native") == "7"


def test_append_batch_rolls_back_on_serialization_failure(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    store.set_cursor("autocontext:native", "3")
    with pytest.raises(TypeError):
        store.append_batch(
            "autocontext:native",
            [
                ("generation", {"n": 1}, "frontier", 0),
                ("generation", {"bad": object()}, "frontier", 0),
            ],
            "9",
        )
    assert store.count() == 0
    assert store.get_cursor("autocontext:native") == "3"


def test_prune_oldest_removes_oldest_fraction(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "ambient.sqlite3")
    ids = [store.append("native", "generation", {"i": i}, "frontier", 0) for i in range(10)]
    deleted = store.prune_oldest(0.3)
    assert deleted == 3
    remaining = {record.record_id for record in store.recent(limit=100)}
    assert set(ids[3:]) == remaining
    assert store.db_size_bytes() > 0
