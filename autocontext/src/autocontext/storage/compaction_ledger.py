"""Append-only semantic compaction ledger storage."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from autocontext.knowledge.compaction import CompactionEntry

logger = logging.getLogger(__name__)

COMPACTION_LEDGER_TAIL_BYTES = 64 * 1024


class CompactionLedgerStore:
    """Persist Pi-shaped compaction entries and answer recent-entry lookups."""

    def __init__(
        self,
        *,
        runs_root: Path,
        mirror_bytes: Callable[[Path, bytes], None] | None = None,
        mirror_append_bytes: Callable[[Path, bytes], None] | None = None,
    ) -> None:
        self.runs_root = runs_root
        self._mirror_bytes = mirror_bytes
        self._mirror_append_bytes = mirror_append_bytes

    def ledger_path(self, run_id: str) -> Path:
        return self.runs_root / run_id / "compactions.jsonl"

    def latest_entry_path(self, run_id: str) -> Path:
        return self.runs_root / run_id / "compactions.latest"

    def append_entries(self, run_id: str, entries: list[CompactionEntry]) -> None:
        if not entries:
            return
        path = self.ledger_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        ledger_payload = "".join(
            json.dumps(entry.to_dict(), sort_keys=True) + "\n"
            for entry in entries
        ).encode()
        with path.open("ab") as handle:
            handle.write(ledger_payload)
        if self._mirror_append_bytes is not None:
            self._mirror_append_bytes(path, ledger_payload)

        latest_path = self.latest_entry_path(run_id)
        latest_payload = f"{entries[-1].entry_id}\n".encode()
        latest_path.write_bytes(latest_payload)
        if self._mirror_bytes is not None:
            self._mirror_bytes(latest_path, latest_payload)

    def read_entries(self, run_id: str, *, limit: int = 20) -> list[CompactionEntry]:
        path = self.ledger_path(run_id)
        if not path.exists():
            return []
        entries: list[CompactionEntry] = []
        text, truncated = self._read_text_for_recent_entries(path, limit)
        lines = text.splitlines()
        if truncated and lines:
            lines = lines[1:]
        for line in lines:
            if not line.strip():
                continue
            entry = self._parse_entry_line(line, path)
            if entry is not None:
                entries.append(entry)
        return entries[-limit:] if limit > 0 else entries

    def latest_entry_id(self, run_id: str) -> str:
        latest_path = self.latest_entry_path(run_id)
        if latest_path.exists():
            return latest_path.read_text(encoding="utf-8").strip()
        path = self.ledger_path(run_id)
        if not path.exists():
            return ""
        text, truncated = self._read_tail_text(path, COMPACTION_LEDGER_TAIL_BYTES)
        lines = text.splitlines()
        if truncated and lines:
            lines = lines[1:]
        for line in reversed(lines):
            entry = self._parse_entry_line(line, path)
            if entry is not None:
                return entry.entry_id
        return ""

    @staticmethod
    def _read_text_for_recent_entries(path: Path, limit: int) -> tuple[str, bool]:
        if limit <= 0:
            return path.read_text(encoding="utf-8"), False
        return CompactionLedgerStore._read_tail_text(path, COMPACTION_LEDGER_TAIL_BYTES)

    @staticmethod
    def _read_tail_text(path: Path, max_bytes: int) -> tuple[str, bool]:
        size = path.stat().st_size
        if size <= 0:
            return "", False
        bytes_to_read = min(size, max_bytes)
        start = size - bytes_to_read
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(bytes_to_read)
        return data.decode("utf-8", errors="replace"), start > 0

    @staticmethod
    def _parse_entry_line(line: str, path: Path) -> CompactionEntry | None:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("failed to parse compaction ledger line in %s", path, exc_info=True)
            return None
        if isinstance(raw, dict) and raw.get("type") == "compaction":
            return CompactionEntry.from_dict(raw)
        return None
