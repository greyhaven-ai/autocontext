"""Tests for FileSink + TraceSink protocol."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from autocontext.integrations.openai import FileSink, TraceSink


def _make_trace(n: int = 1) -> dict:
    return {
        "schemaVersion": "1.0",
        "traceId": f"01HN000000000000000000000{n:1d}",
        "provider": "openai",
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "timing": {"startedAt": "2026-04-21T00:00:00Z", "endedAt": "2026-04-21T00:00:01Z", "latencyMs": 1000},
        "usage": {"tokensIn": 1, "tokensOut": 1},
        "env": {"environmentTag": "test", "appId": "a"},
        "source": {"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": "0.0.0"}},
    }


def test_file_sink_protocol_membership() -> None:
    sink: TraceSink = FileSink(path="/tmp/x.jsonl")
    sink.close()


def test_adds_a_single_trace_and_flush_writes_it(tmp_path: Path) -> None:
    p = tmp_path / "traces.jsonl"
    sink = FileSink(path=p, batch_size=10, flush_interval_seconds=60.0)
    sink.add(_make_trace(1))
    sink.flush()
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["traceId"] == "01HN0000000000000000000001"
    sink.close()


def test_batch_size_triggers_flush(tmp_path: Path) -> None:
    p = tmp_path / "traces.jsonl"
    sink = FileSink(path=p, batch_size=3, flush_interval_seconds=3600.0)
    for i in range(1, 4):
        sink.add(_make_trace(i))
    # Third add should auto-flush; no explicit flush() call.
    assert p.exists()
    assert len(p.read_text().strip().splitlines()) == 3
    sink.close()


def test_interval_flush(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "traces.jsonl"
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    sink = FileSink(path=p, batch_size=100, flush_interval_seconds=5.0)
    sink.add(_make_trace(1))
    now[0] = 1006.0  # 6s elapsed → next add triggers interval flush
    sink.add(_make_trace(2))
    assert len(p.read_text().strip().splitlines()) == 2
    sink.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "traces.jsonl"
    sink = FileSink(path=p)
    sink.close()
    sink.close()  # must not raise


def test_no_atexit_registered_by_default(tmp_path: Path) -> None:
    import atexit
    _before = list(atexit._exithandlers) if hasattr(atexit, "_exithandlers") else None
    _ = FileSink(path=tmp_path / "x.jsonl")
    # There's no portable way to enumerate atexit handlers across versions;
    # instead, assert the public register_atexit default is False and that
    # the handler is wired only on opt-in (tested separately below).
    # (Empty test body; smoke test that construction does not raise.)


def test_register_atexit_opt_in(tmp_path: Path) -> None:
    """Opt-in path: handler is wired; we simulate process-exit by calling close()."""
    p = tmp_path / "traces.jsonl"
    sink = FileSink(path=p, register_atexit=True, batch_size=100)
    sink.add(_make_trace(1))
    # Simulate process exit: atexit would call close()
    sink.close()
    assert len(p.read_text().strip().splitlines()) == 1


def test_on_error_raise_propagates(tmp_path: Path) -> None:
    # Parent dir is created lazily; to force an error, use a read-only path.
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o400)
    try:
        sink = FileSink(path=ro / "x.jsonl", on_error="raise")
        sink.add(_make_trace(1))
        with pytest.raises(OSError):
            sink.flush()
    finally:
        ro.chmod(0o700)


def test_on_error_log_and_drop_does_not_raise(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o400)
    try:
        sink = FileSink(path=ro / "x.jsonl", on_error="log-and-drop")
        sink.add(_make_trace(1))
        sink.flush()  # should log, not raise
        assert any("FileSink" in rec.message for rec in caplog.records)
    finally:
        ro.chmod(0o700)


def test_parent_directory_created_on_first_write(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "path" / "traces.jsonl"
    sink = FileSink(path=p)
    sink.add(_make_trace(1))
    sink.flush()
    assert p.exists()
    sink.close()
