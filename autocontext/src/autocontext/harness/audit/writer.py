"""Append-only audit log writer with thread safety and ndjson persistence."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from autocontext.harness.audit.types import AuditCategory, AuditEntry


class AppendOnlyAuditWriter:
    """Thread-safe, append-only audit log backed by ndjson file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._sequence = 0
        self._lock = threading.Lock()

    def append(self, entry: AuditEntry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._sequence += 1
            seq = self._sequence
        line = {"seq": seq, **entry.to_dict()}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")

    def read_all(self) -> list[AuditEntry]:
        return self._read_lines()

    def read(
        self,
        *,
        category: AuditCategory | None = None,
        actor: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[AuditEntry]:
        entries = self._read_lines()
        if category is not None:
            entries = [e for e in entries if e.category == category]
        if actor is not None:
            entries = [e for e in entries if e.actor == actor]
        if after is not None:
            entries = [e for e in entries if e.timestamp > after]
        if before is not None:
            entries = [e for e in entries if e.timestamp < before]
        return entries

    def count(self) -> int:
        if not self._path.exists():
            return 0
        with self._path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def _read_lines(self) -> list[AuditEntry]:
        if not self._path.exists():
            return []
        entries: list[AuditEntry] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                data = json.loads(stripped)
                data.pop("seq", None)
                entries.append(AuditEntry.from_dict(data))
        return entries
