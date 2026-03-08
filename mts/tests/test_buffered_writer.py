"""Tests for buffered artifact writer (MTS-24)."""
from __future__ import annotations

import json
from pathlib import Path

from mts.storage.buffered_writer import BufferedWriter


def test_write_text(tmp_path: Path) -> None:
    """Buffered text write is flushed to disk."""
    writer = BufferedWriter()
    writer.start()
    target = tmp_path / "out.md"
    writer.write_text(target, "hello world\n")
    writer.flush()
    writer.shutdown()
    assert target.read_text() == "hello world\n"


def test_write_json(tmp_path: Path) -> None:
    """Buffered JSON write is flushed to disk."""
    writer = BufferedWriter()
    writer.start()
    target = tmp_path / "data.json"
    writer.write_json(target, {"score": 0.5})
    writer.flush()
    writer.shutdown()
    data = json.loads(target.read_text())
    assert data["score"] == 0.5


def test_append_text(tmp_path: Path) -> None:
    """Buffered append adds to existing file."""
    writer = BufferedWriter()
    writer.start()
    target = tmp_path / "log.md"
    target.write_text("line 1\n")
    writer.append_text(target, "line 2\n")
    writer.flush()
    writer.shutdown()
    assert "line 1\nline 2\n" == target.read_text()


def test_creates_parent_dirs(tmp_path: Path) -> None:
    """Buffered write creates parent directories."""
    writer = BufferedWriter()
    writer.start()
    target = tmp_path / "deep" / "nested" / "file.txt"
    writer.write_text(target, "content\n")
    writer.flush()
    writer.shutdown()
    assert target.read_text() == "content\n"


def test_flush_blocks_until_empty(tmp_path: Path) -> None:
    """flush() blocks until all queued writes complete."""
    writer = BufferedWriter()
    writer.start()
    for i in range(20):
        writer.write_text(tmp_path / f"file_{i}.txt", f"content {i}\n")
    writer.flush()
    writer.shutdown()
    for i in range(20):
        assert (tmp_path / f"file_{i}.txt").read_text() == f"content {i}\n"


def test_shutdown_flushes_remaining(tmp_path: Path) -> None:
    """shutdown() flushes remaining items before stopping."""
    writer = BufferedWriter()
    writer.start()
    writer.write_text(tmp_path / "last.txt", "done\n")
    writer.shutdown()
    assert (tmp_path / "last.txt").read_text() == "done\n"


def test_no_start_writes_directly(tmp_path: Path) -> None:
    """Without start(), writes happen synchronously as fallback."""
    writer = BufferedWriter()
    target = tmp_path / "sync.txt"
    writer.write_text(target, "immediate\n")
    assert target.read_text() == "immediate\n"
