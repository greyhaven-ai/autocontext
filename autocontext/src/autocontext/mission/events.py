"""AC-697 mission event emitter (slice 2).

Mirrors ``ts/src/mission/events.ts``. Callback-based pub/sub so the
mission manager can broadcast lifecycle events without coupling to
any specific transport. The TS implementation extends Node's
``EventEmitter``; the Python equivalent uses a plain listener
registry per event kind because Python's built-in ``Observer``
patterns are heavyweight and the surface is small.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictStr

__all__ = [
    "MissionCreatedEvent",
    "MissionEventEmitter",
    "MissionStatusChangedEvent",
    "MissionStepEvent",
    "MissionVerifiedEvent",
]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MissionCreatedEvent(_Event):
    mission_id: StrictStr
    name: StrictStr
    goal: StrictStr
    timestamp: StrictStr


class MissionStepEvent(_Event):
    mission_id: StrictStr
    description: StrictStr
    step_number: int
    timestamp: StrictStr


class MissionStatusChangedEvent(_Event):
    mission_id: StrictStr
    from_status: StrictStr
    to_status: StrictStr
    timestamp: StrictStr


class MissionVerifiedEvent(_Event):
    mission_id: StrictStr
    passed: bool
    reason: StrictStr
    timestamp: StrictStr


_EVENT_KINDS = (
    "mission_created",
    "mission_step",
    "mission_status_changed",
    "mission_verified",
)


class MissionEventEmitter:
    """Tiny callback registry for mission lifecycle events.

    Listeners are subscribed via ``on(event_name, callback)`` and
    called synchronously when an emit-method fires. Exceptions
    raised by a listener bubble up (the caller decides whether to
    swallow them); the manager calls listeners after the SQL
    commit, so a throwing listener does not corrupt persisted
    state.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[[Any], None]]] = {kind: [] for kind in _EVENT_KINDS}

    def on(self, event: str, callback: Callable[[Any], None]) -> None:
        if event not in self._listeners:
            raise ValueError(f"unknown mission event {event!r}; expected one of {list(self._listeners)}")
        self._listeners[event].append(callback)

    def _emit(self, event: str, payload: Any) -> None:
        for callback in list(self._listeners[event]):
            callback(payload)

    def emit_created(self, mission_id: str, name: str, goal: str) -> None:
        self._emit(
            "mission_created",
            MissionCreatedEvent(mission_id=mission_id, name=name, goal=goal, timestamp=_utc_now_iso()),
        )

    def emit_step(self, mission_id: str, description: str, step_number: int) -> None:
        self._emit(
            "mission_step",
            MissionStepEvent(
                mission_id=mission_id,
                description=description,
                step_number=step_number,
                timestamp=_utc_now_iso(),
            ),
        )

    def emit_status_change(self, mission_id: str, from_status: str, to_status: str) -> None:
        self._emit(
            "mission_status_changed",
            MissionStatusChangedEvent(
                mission_id=mission_id,
                from_status=from_status,
                to_status=to_status,
                timestamp=_utc_now_iso(),
            ),
        )

    def emit_verified(self, mission_id: str, passed: bool, reason: str) -> None:
        self._emit(
            "mission_verified",
            MissionVerifiedEvent(
                mission_id=mission_id,
                passed=passed,
                reason=reason,
                timestamp=_utc_now_iso(),
            ),
        )
