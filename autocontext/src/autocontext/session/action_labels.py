"""Compact action labels for timelines and event feeds (AC-513).

Domain concept: ActionLabel is a value object — a short, scannable
description of what just happened. Derived from events, not stored
as primary data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from autocontext.session.coordinator import CoordinatorEventType
from autocontext.session.types import SessionEventType

if TYPE_CHECKING:
    from autocontext.session.coordinator import Coordinator, CoordinatorEvent
    from autocontext.session.types import SessionEvent

_MAX_LABEL_LEN = 120

_FAILURE_EVENT_TYPES = frozenset({
    CoordinatorEventType.WORKER_FAILED.value,
    SessionEventType.TURN_FAILED.value,
    SessionEventType.TURN_INTERRUPTED.value,
    SessionEventType.SESSION_FAILED.value,
    SessionEventType.SESSION_CANCELED.value,
})

_EVENT_LABEL_MAP: dict[str, str] = {
    "coordinator_created": "Coordinator started",
    "worker_delegated": "Worker delegated",
    "worker_started": "Worker started",
    "worker_completed": "Worker completed",
    "worker_failed": "Worker failed",
    "worker_redirected": "Worker redirected",
    "fan_out": "Fan-out dispatched",
    "fan_in": "Fan-in collected",
    "session_created": "Session started",
    "session_paused": "Session paused",
    "session_resumed": "Session resumed",
    "session_completed": "Session completed",
    "session_failed": "Session failed",
    "session_canceled": "Session canceled",
    "turn_submitted": "Turn submitted",
    "turn_completed": "Turn completed",
    "turn_interrupted": "Turn interrupted",
    "turn_failed": "Turn failed",
}


class ActionLabel(BaseModel):
    """Short, scannable description for timeline/event display.

    Categories: action, tool, failure, noop, info
    """

    text: str
    category: str = "action"

    @classmethod
    def create(cls, text: str, category: str = "action") -> ActionLabel:
        truncated = _truncate(text)
        return cls(text=truncated, category=category)

    @classmethod
    def noop(cls, reason: str = "No changes") -> ActionLabel:
        return cls(text=_truncate(reason), category="noop")

    model_config = {"frozen": True}


def _truncate(text: str) -> str:
    """Truncate to _MAX_LABEL_LEN with ellipsis."""
    text = text.strip().replace("\n", " ")
    if len(text) <= _MAX_LABEL_LEN:
        return text
    return text[: _MAX_LABEL_LEN - 1] + "…"


def label_from_event(event: CoordinatorEvent | SessionEvent) -> ActionLabel:
    """Derive a compact label from a coordinator or session event."""
    event_type = event.event_type.value
    base = _EVENT_LABEL_MAP.get(event_type, event_type.replace("_", " ").title())

    # Enrich with payload details
    payload = event.payload
    detail_parts: list[str] = []
    for key in ("task", "role", "reason", "error", "worker_id", "turn_id"):
        val = payload.get(key)
        if val:
            detail_parts.append(f"{key}={str(val)[:40]}")

    if detail_parts:
        # Keep labels glanceable in narrow timeline views rather than dumping every payload field.
        detail = ", ".join(detail_parts[:3])
        text = f"{base}: {detail}"
    else:
        text = base

    category = "failure" if event_type in _FAILURE_EVENT_TYPES else "action"
    return ActionLabel.create(text, category=category)


def labels_from_coordinator(
    coordinator: Coordinator,
    max_labels: int = 20,
) -> list[ActionLabel]:
    """Generate labels from the coordinator's recent events."""
    events = coordinator.events[-max_labels:]
    return [label_from_event(e) for e in events]
