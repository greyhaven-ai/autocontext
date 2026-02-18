"""Tests for mts.harness.core.events — EventStreamEmitter with thread safety."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from mts.harness.core.events import EventStreamEmitter


def test_emitter_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "events.ndjson"
    emitter = EventStreamEmitter(nested)
    emitter.emit("test_event", {"key": "value"})
    assert nested.exists()


def test_emitter_writes_ndjson(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(path)
    emitter.emit("gen_start", {"gen": 1})
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["event"] == "gen_start"
    assert data["payload"] == {"gen": 1}
    assert "ts" in data
    assert data["v"] == 1
    assert data["seq"] == 1
    assert data["channel"] == "generation"


def test_emitter_increments_sequence(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(path)
    emitter.emit("a", {})
    emitter.emit("b", {})
    emitter.emit("c", {})
    lines = path.read_text().strip().split("\n")
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [1, 2, 3]


def test_emitter_default_channel(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(path)
    emitter.emit("evt", {})
    data = json.loads(path.read_text().strip())
    assert data["channel"] == "generation"


def test_emitter_custom_channel(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(path)
    emitter.emit("evt", {}, channel="ecosystem")
    data = json.loads(path.read_text().strip())
    assert data["channel"] == "ecosystem"


def test_subscriber_receives_events(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(path)
    received: list[tuple[str, dict[str, object]]] = []
    emitter.subscribe(lambda e, p: received.append((e, p)))
    emitter.emit("test", {"x": 1})
    assert len(received) == 1
    assert received[0] == ("test", {"x": 1})


def test_subscriber_error_does_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(path)

    def bad_callback(event: str, payload: dict[str, object]) -> None:
        raise RuntimeError("boom")

    emitter.subscribe(bad_callback)
    # Should not raise
    emitter.emit("test", {})
    assert path.exists()


def test_unsubscribe_removes_callback(tmp_path: Path) -> None:
    path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(path)
    received: list[str] = []
    cb = lambda e, p: received.append(e)  # noqa: E731
    emitter.subscribe(cb)
    emitter.emit("a", {})
    emitter.unsubscribe(cb)
    emitter.emit("b", {})
    assert received == ["a"]


def test_emitter_thread_safety(tmp_path: Path) -> None:
    """Concurrent emits from multiple threads produce correct sequence numbers."""
    path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(path)
    n_threads = 10
    n_per_thread = 50
    barrier = threading.Barrier(n_threads)

    def _worker() -> None:
        barrier.wait()
        for i in range(n_per_thread):
            emitter.emit("thread_event", {"i": i})

    threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = path.read_text().strip().split("\n")
    assert len(lines) == n_threads * n_per_thread
    seqs = sorted(json.loads(line)["seq"] for line in lines)
    assert seqs == list(range(1, n_threads * n_per_thread + 1))
