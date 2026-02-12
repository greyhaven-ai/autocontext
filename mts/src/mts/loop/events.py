from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class EventStreamEmitter:
    def __init__(self, path: Path):
        self.path = path
        self._sequence = 0

    def emit(self, event: str, payload: dict[str, object], channel: str = "generation") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sequence += 1
        line = {
            "ts": datetime.now(UTC).isoformat(),
            "v": 1,
            "seq": self._sequence,
            "channel": channel,
            "event": event,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
