"""Session supervisor — registry for background, attachable work (AC-510).

Domain concepts:
- SupervisedEntry: entity tracking one background session/mission
- SupervisorState: lifecycle (launching → running → waiting → stopping → stopped/completed/failed)
- Supervisor: aggregate managing the registry of entries
- SupervisorStore: JSON persistence for restart recovery
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SupervisorState(StrEnum):
    """Lifecycle states for a supervised entry."""

    LAUNCHING = "launching"
    RUNNING = "running"
    WAITING = "waiting"  # blocked on approval, input, etc.
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


_ALIVE_STATES = frozenset({
    SupervisorState.LAUNCHING,
    SupervisorState.RUNNING,
    SupervisorState.WAITING,
    SupervisorState.STOPPING,
})


class SupervisedEntry(BaseModel):
    """Entity tracking one background session or mission.

    Create via SupervisedEntry.create(), not direct construction.
    """

    entry_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str
    goal: str
    workspace: str = ""
    state: SupervisorState = SupervisorState.LAUNCHING
    blocked_reason: str = ""
    error: str = ""
    created_at: str = Field(default_factory=_now)
    last_activity_at: str = Field(default_factory=_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        session_id: str,
        goal: str,
        workspace: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SupervisedEntry:
        return cls(
            session_id=session_id,
            goal=goal,
            workspace=workspace,
            metadata=metadata or {},
        )

    # -- Lifecycle transitions --

    def mark_running(self) -> None:
        self._require_state(
            {
                SupervisorState.LAUNCHING,
                SupervisorState.WAITING,
            },
            action="mark entry running",
        )
        self.state = SupervisorState.RUNNING
        self.blocked_reason = ""
        self._touch()

    def mark_waiting(self, reason: str = "") -> None:
        self._require_state(
            {
                SupervisorState.LAUNCHING,
                SupervisorState.RUNNING,
            },
            action="mark entry waiting",
        )
        self.state = SupervisorState.WAITING
        self.blocked_reason = reason
        self._touch()

    def mark_completed(self) -> None:
        self._require_state(
            {
                SupervisorState.LAUNCHING,
                SupervisorState.RUNNING,
                SupervisorState.WAITING,
                SupervisorState.STOPPING,
            },
            action="mark entry completed",
        )
        self.state = SupervisorState.COMPLETED
        self.blocked_reason = ""
        self._touch()

    def mark_failed(self, error: str = "") -> None:
        self._require_state(
            _ALIVE_STATES,
            action="mark entry failed",
        )
        self.state = SupervisorState.FAILED
        self.blocked_reason = ""
        self.error = error
        self._touch()

    def request_stop(self) -> None:
        self._require_state(
            {
                SupervisorState.LAUNCHING,
                SupervisorState.RUNNING,
                SupervisorState.WAITING,
            },
            action="request stop for entry",
        )
        self.state = SupervisorState.STOPPING
        self.blocked_reason = ""
        self._touch()

    def mark_stopped(self) -> None:
        self._require_state(
            {SupervisorState.STOPPING},
            action="mark entry stopped",
        )
        self.state = SupervisorState.STOPPED
        self.blocked_reason = ""
        self._touch()

    def heartbeat(self) -> None:
        self._touch()

    # -- Queries --

    @property
    def is_alive(self) -> bool:
        return self.state in _ALIVE_STATES

    # -- Internal --

    def _require_state(
        self,
        allowed: frozenset[SupervisorState] | set[SupervisorState],
        action: str,
    ) -> None:
        if self.state not in allowed:
            msg = f"Cannot {action} from state={self.state}"
            raise ValueError(msg)

    def _touch(self) -> None:
        self.last_activity_at = _now()


class Supervisor:
    """Manages the registry of supervised background sessions.

    In-memory registry with optional persistence via SupervisorStore.
    """

    def __init__(self) -> None:
        self._entries: dict[str, SupervisedEntry] = {}

    def launch(
        self,
        session_id: str,
        goal: str,
        workspace: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SupervisedEntry:
        """Register a new supervised session. Raises if already supervised."""
        if session_id in self._entries:
            msg = f"Session '{session_id}' is already supervised"
            raise ValueError(msg)

        entry = SupervisedEntry.create(
            session_id=session_id,
            goal=goal,
            workspace=workspace,
            metadata=metadata,
        )
        self._entries[session_id] = entry
        return entry

    def get(self, session_id: str) -> SupervisedEntry | None:
        return self._entries.get(session_id)

    def list_active(self) -> list[SupervisedEntry]:
        return [e for e in self._entries.values() if e.is_alive]

    def list_all(self) -> list[SupervisedEntry]:
        return list(self._entries.values())

    def stop(self, session_id: str) -> None:
        """Request graceful stop. Raises KeyError if not found."""
        entry = self._entries.get(session_id)
        if entry is None:
            msg = f"Session '{session_id}' not found in supervisor"
            raise KeyError(msg)
        entry.request_stop()

    def cleanup_stale(self, max_idle_seconds: float = 300) -> list[str]:
        """Mark entries with no heartbeat for too long as failed.

        Returns list of session_ids that were cleaned up.
        """
        now = datetime.now(UTC)
        cleaned: list[str] = []
        for entry in self._entries.values():
            if not entry.is_alive:
                continue
            try:
                last = datetime.fromisoformat(entry.last_activity_at)
                idle = (now - last).total_seconds()
            except (ValueError, TypeError):
                idle = max_idle_seconds + 1  # treat unparseable as stale

            if idle > max_idle_seconds:
                entry.mark_failed(error=f"stale: no activity for {idle:.0f}s")
                cleaned.append(entry.session_id)

        return cleaned

    def remove(self, session_id: str) -> bool:
        """Remove an entry from the registry. Returns True if found."""
        return self._entries.pop(session_id, None) is not None


class SupervisorStore:
    """JSON file persistence for supervisor state.

    Simple append-friendly format for restart recovery.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, supervisor: Supervisor) -> None:
        """Persist all entries to disk."""
        data = {
            sid: entry.model_dump()
            for sid, entry in supervisor._entries.items()
        }
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def restore(self, supervisor: Supervisor) -> None:
        """Load persisted entries into the supervisor."""
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        for sid, entry_data in raw.items():
            entry = SupervisedEntry.model_validate(entry_data)
            supervisor._entries[sid] = entry
