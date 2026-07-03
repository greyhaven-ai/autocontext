"""jsonl feed source: consumes production-traces sdk / otel-bridge output directories."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from autocontext.ambient.sources.contract import RawTrace, SourcePoll


@dataclass(slots=True)
class JsonlFeedSource:
    name: str
    feed_dir: Path
    batch_size: int = 500

    def _files(self) -> list[Path]:
        if not self.feed_dir.exists():
            return []
        return sorted(self.feed_dir.rglob("*.jsonl"))

    def poll(self, cursor: str | None) -> SourcePoll:
        cursor_file, cursor_line = None, 0
        if cursor:
            rel, _, line = cursor.rpartition(":")
            cursor_file, cursor_line = rel, int(line)
        records: list[RawTrace] = []
        last_file, last_line = cursor_file, cursor_line
        for path in self._files():
            rel = str(path.relative_to(self.feed_dir))
            if cursor_file is not None and rel < cursor_file:
                continue
            start = cursor_line if rel == cursor_file else 0
            lines = path.read_text(encoding="utf-8").splitlines()
            for index in range(start, len(lines)):
                if len(records) >= self.batch_size:
                    return SourcePoll(records=records, next_cursor=f"{last_file}:{last_line}")
                line = lines[index].strip()
                last_file, last_line = rel, index + 1
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                records.append(
                    RawTrace(
                        kind=str(obj.get("kind", "trace")),
                        payload=obj,
                        produced_by=str(obj.get("produced_by", "frontier")),
                    )
                )
        if not records and (last_file == cursor_file and last_line == cursor_line):
            return SourcePoll()
        if not records:
            return SourcePoll(records=[], next_cursor=None)
        return SourcePoll(records=records, next_cursor=f"{last_file}:{last_line}")
