"""Domain-agnostic event stream emitter with thread safety."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

EventCallback = Callable[[str, dict[str, object]], None]


class EventStreamEmitter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._sequence = 0
        self._subscribers: list[EventCallback] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: EventCallback) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: EventCallback) -> None:
        with self._lock:
            self._subscribers.remove(callback)

    def emit(self, event: str, payload: dict[str, object], channel: str = "generation") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._sequence += 1
            seq = self._sequence
            subscribers = list(self._subscribers)
        line = {
            "ts": datetime.now(UTC).isoformat(),
            "v": 1,
            "seq": seq,
            "channel": channel,
            "event": event,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
        for cb in subscribers:
            try:
                cb(event, payload)
            except Exception:
                pass  # subscriber errors must never crash the loop
