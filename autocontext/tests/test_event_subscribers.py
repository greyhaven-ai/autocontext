from __future__ import annotations

from pathlib import Path

from autocontext.loop.events import EventStreamEmitter


def test_subscriber_receives_events(tmp_path: Path) -> None:
    emitter = EventStreamEmitter(tmp_path / "events.ndjson")
    received: list[tuple[str, dict[str, object]]] = []
    emitter.subscribe(lambda e, p: received.append((e, p)))

    emitter.emit("test_event", {"key": "value"})
    assert len(received) == 1
    assert received[0] == ("test_event", {"key": "value"})


def test_unsubscribe_stops_delivery(tmp_path: Path) -> None:
    emitter = EventStreamEmitter(tmp_path / "events.ndjson")
    received: list[tuple[str, dict[str, object]]] = []

    def cb(e: str, p: dict[str, object]) -> None:
        received.append((e, p))

    emitter.subscribe(cb)
    emitter.emit("first", {})
    assert len(received) == 1

    emitter.unsubscribe(cb)
    emitter.emit("second", {})
    assert len(received) == 1  # no new events


def test_subscriber_error_does_not_crash_emit(tmp_path: Path) -> None:
    emitter = EventStreamEmitter(tmp_path / "events.ndjson")
    good_received: list[str] = []

    def bad_cb(_e: str, _p: dict[str, object]) -> None:
        raise RuntimeError("boom")

    def good_cb(e: str, _p: dict[str, object]) -> None:
        good_received.append(e)

    emitter.subscribe(bad_cb)
    emitter.subscribe(good_cb)

    emitter.emit("test", {"x": 1})
    # Good subscriber still receives despite bad one throwing
    assert good_received == ["test"]
    # File was still written
    assert (tmp_path / "events.ndjson").exists()


def test_multiple_subscribers(tmp_path: Path) -> None:
    emitter = EventStreamEmitter(tmp_path / "events.ndjson")
    a: list[str] = []
    b: list[str] = []
    emitter.subscribe(lambda e, _p: a.append(e))
    emitter.subscribe(lambda e, _p: b.append(e))

    emitter.emit("ev1", {})
    emitter.emit("ev2", {})
    assert a == ["ev1", "ev2"]
    assert b == ["ev1", "ev2"]
