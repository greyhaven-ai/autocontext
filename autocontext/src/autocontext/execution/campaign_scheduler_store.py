"""Durable append-only event storage for the campaign scheduler."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from autocontext.context_bundles.models import stable_digest
from autocontext.execution.campaign_scheduler_models import SchedulerEvent
from autocontext.util.file_lock import advisory_path_lock


class StaleCampaignSchedulerError(RuntimeError):
    """Raised when another scheduler advanced an event log first."""


class CampaignSchedulerEventStore:
    """Checksummed append-only JSONL event store with fsync durability."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path = self.path.resolve()
        self._lock_path = resolved_path.with_name(f".{resolved_path.name}.lock")
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
        with self._serialized():
            current = self._read_unlocked()
            current_sequence = current[-1].sequence if current else 0
            expected_sequence = current_sequence + 1
            if event.sequence != expected_sequence:
                raise StaleCampaignSchedulerError(
                    "scheduler event log advanced concurrently: "
                    f"attempted sequence {event.sequence}, expected {expected_sequence}; "
                    "construct a fresh CampaignScheduler before retrying"
                )
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                payload = line.encode()
                offset = 0
                while offset < len(payload):
                    written = os.write(descriptor, payload[offset:])
                    if written == 0:
                        raise OSError("scheduler event append made no progress")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def read(self) -> tuple[SchedulerEvent, ...]:
        with self._serialized():
            return self._read_unlocked()

    def _read_unlocked(self) -> tuple[SchedulerEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[SchedulerEvent] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
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

    @contextmanager
    def _serialized(self) -> Iterator[None]:
        """Serialize a complete read/compare/append operation across stores and processes."""

        with self._lock:
            with advisory_path_lock(self._lock_path):
                yield


__all__ = ["CampaignSchedulerEventStore", "StaleCampaignSchedulerError"]
