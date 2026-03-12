"""Tests for autocontext.harness.audit.writer — AppendOnlyAuditWriter."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from autocontext.harness.audit.types import AuditCategory, AuditEntry
from autocontext.harness.audit.writer import AppendOnlyAuditWriter


def _make_entry(
    *,
    category: AuditCategory = AuditCategory.SYSTEM,
    actor: str = "harness",
    action: str = "test",
    detail: str = "",
    metadata: dict | None = None,
    timestamp: str | None = None,
) -> AuditEntry:
    return AuditEntry(
        timestamp=timestamp or AuditEntry.now(),
        category=category,
        actor=actor,
        action=action,
        detail=detail,
        metadata=metadata or {},
    )


def test_writer_creates_file_on_first_append(tmp_path: Path) -> None:
    audit_file = tmp_path / "sub" / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    assert not audit_file.exists()
    writer.append(_make_entry())
    assert audit_file.exists()


def test_writer_appends_ndjson_lines(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    writer.append(_make_entry(action="first"))
    writer.append(_make_entry(action="second"))
    writer.append(_make_entry(action="third"))
    lines = audit_file.read_text().strip().split("\n")
    assert len(lines) == 3


def test_writer_thread_safe(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    barrier = threading.Barrier(50)

    def _write(idx: int) -> None:
        barrier.wait()
        writer.append(_make_entry(action=f"thread-{idx}"))

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = audit_file.read_text().strip().split("\n")
    assert len(lines) == 50


def test_writer_entries_are_valid_json(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    for i in range(5):
        writer.append(_make_entry(action=f"action-{i}", metadata={"i": i}))
    for line in audit_file.read_text().strip().split("\n"):
        parsed = json.loads(line)
        assert isinstance(parsed, dict)


def test_writer_preserves_all_fields(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    original = AuditEntry(
        timestamp="2025-06-15T12:00:00+00:00",
        category=AuditCategory.LLM_CALL,
        actor="competitor",
        action="generate",
        detail="produced strategy",
        metadata={"model": "claude-3", "tokens": 1500},
    )
    writer.append(original)
    recovered = writer.read_all()
    assert len(recovered) == 1
    entry = recovered[0]
    assert entry.timestamp == original.timestamp
    assert entry.category == original.category
    assert entry.actor == original.actor
    assert entry.action == original.action
    assert entry.detail == original.detail
    assert entry.metadata == original.metadata


def test_writer_sequence_numbers_monotonic(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    for i in range(10):
        writer.append(_make_entry(action=f"step-{i}"))
    lines = audit_file.read_text().strip().split("\n")
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == list(range(1, 11))


def test_writer_read_all(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    actions = ["alpha", "beta", "gamma"]
    for action in actions:
        writer.append(_make_entry(action=action))
    entries = writer.read_all()
    assert len(entries) == 3
    assert [e.action for e in entries] == actions


def test_writer_read_by_category(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    writer.append(_make_entry(category=AuditCategory.LLM_CALL, action="call1"))
    writer.append(_make_entry(category=AuditCategory.ERROR, action="err1"))
    writer.append(_make_entry(category=AuditCategory.LLM_CALL, action="call2"))
    writer.append(_make_entry(category=AuditCategory.GATE_DECISION, action="gate1"))
    results = writer.read(category=AuditCategory.LLM_CALL)
    assert len(results) == 2
    assert all(e.category == AuditCategory.LLM_CALL for e in results)
    assert [e.action for e in results] == ["call1", "call2"]


def test_writer_read_by_actor(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    writer.append(_make_entry(actor="competitor", action="a1"))
    writer.append(_make_entry(actor="analyst", action="a2"))
    writer.append(_make_entry(actor="competitor", action="a3"))
    results = writer.read(actor="competitor")
    assert len(results) == 2
    assert [e.action for e in results] == ["a1", "a3"]


def test_writer_read_by_time_range(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    writer.append(_make_entry(timestamp="2025-01-01T00:00:00+00:00", action="early"))
    writer.append(_make_entry(timestamp="2025-06-15T12:00:00+00:00", action="mid"))
    writer.append(_make_entry(timestamp="2025-12-31T23:59:59+00:00", action="late"))
    # After filter only
    results_after = writer.read(after="2025-06-01T00:00:00+00:00")
    assert len(results_after) == 2
    assert [e.action for e in results_after] == ["mid", "late"]
    # Before filter only
    results_before = writer.read(before="2025-07-01T00:00:00+00:00")
    assert len(results_before) == 2
    assert [e.action for e in results_before] == ["early", "mid"]
    # Combined range
    results_range = writer.read(
        after="2025-03-01T00:00:00+00:00",
        before="2025-09-01T00:00:00+00:00",
    )
    assert len(results_range) == 1
    assert results_range[0].action == "mid"


def test_writer_count(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_file)
    assert writer.count() == 0
    for i in range(7):
        writer.append(_make_entry(action=f"entry-{i}"))
    assert writer.count() == 7
