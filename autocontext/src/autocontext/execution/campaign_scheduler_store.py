"""Durable append-only event storage for the campaign scheduler."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from autocontext.context_bundles.models import stable_digest
from autocontext.execution.campaign_scheduler_models import SchedulerEvent


class CampaignSchedulerEventStore:
    """Checksummed append-only JSONL event store with fsync durability."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: SchedulerEvent) -> None:
        body = {
            "sequence": event.sequence,
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "event_type": event.event_type,
            "payload": event.payload,
        }
        line = json.dumps({**body, "checksum": stable_digest(body)}, sort_keys=True) + "\n"
        with self._lock:
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, line.encode())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def read(self) -> tuple[SchedulerEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[SchedulerEvent] = []
        for line_number, line in enumerate(self.path.read_text().splitlines(), start=1):
            try:
                data = json.loads(line)
                checksum = str(data.pop("checksum"))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(f"invalid scheduler event at line {line_number}") from exc
            if stable_digest(data) != checksum:
                raise ValueError(f"scheduler event checksum mismatch at line {line_number}")
            expected = len(events) + 1
            if data.get("sequence") != expected:
                raise ValueError(f"scheduler event sequence mismatch at line {line_number}")
            events.append(
                SchedulerEvent(
                    sequence=expected,
                    event_id=str(data["event_id"]),
                    timestamp=float(data["timestamp"]),
                    event_type=str(data["event_type"]),
                    payload=dict(data["payload"]),
                )
            )
        return tuple(events)


__all__ = ["CampaignSchedulerEventStore"]
