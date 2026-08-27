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
            created = not self.path.exists()
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
            if created:
                self._fsync_parent_unlocked()

    def read(self) -> tuple[SchedulerEvent, ...]:
        with self._serialized():
            return self._read_unlocked()

    def _read_unlocked(self) -> tuple[SchedulerEvent, ...]:
        if not self.path.exists():
            return ()
        raw = self.path.read_bytes()
        lines = raw.splitlines(keepends=True)
        events: list[SchedulerEvent] = []
        offset = 0
        for line_number, encoded_line in enumerate(lines, start=1):
            terminated = encoded_line.endswith((b"\n", b"\r"))
            record = encoded_line.rstrip(b"\r\n")
            try:
                data = json.loads(record.decode("utf-8"))
                checksum = str(data.pop("checksum"))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                if line_number == len(lines) and not terminated:
                    # A process can die after a short append and before fsync. A
                    # torn, unterminated tail was never a committed transition;
                    # discard only that tail so the last checksummed event remains
                    # restartable. Corruption in any complete record still fails
                    # closed.
                    self._truncate_unlocked(offset)
                    return tuple(events)
                raise ValueError(f"invalid scheduler event at line {line_number}") from exc
            if stable_digest(data) != checksum:
                if line_number == len(lines) and not terminated:
                    self._truncate_unlocked(offset)
                    return tuple(events)
                raise ValueError(f"scheduler event checksum mismatch at line {line_number}")
            expected = len(events) + 1
            if data.get("sequence") != expected:
                if line_number == len(lines) and not terminated:
                    self._truncate_unlocked(offset)
                    return tuple(events)
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
            offset += len(encoded_line)
        if lines and events and not lines[-1].endswith((b"\n", b"\r")):
            # A fully written record whose trailing newline was lost is valid,
            # but normalize the separator before the next O_APPEND write.
            descriptor = os.open(self.path, os.O_APPEND | os.O_WRONLY)
            try:
                os.write(descriptor, b"\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return tuple(events)

    def _truncate_unlocked(self, offset: int) -> None:
        descriptor = os.open(self.path, os.O_WRONLY)
        try:
            os.ftruncate(descriptor, offset)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _fsync_parent_unlocked(self) -> None:
        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _serialized(self) -> Iterator[None]:
        """Serialize a complete read/compare/append operation across stores and processes."""

        with self._lock:
            with advisory_path_lock(self._lock_path):
                yield


__all__ = ["CampaignSchedulerEventStore", "StaleCampaignSchedulerError"]
