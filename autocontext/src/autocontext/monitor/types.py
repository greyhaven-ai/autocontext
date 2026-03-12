"""Monitor condition and alert types (AC-209)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConditionType(StrEnum):
    """Supported monitor condition types."""

    METRIC_THRESHOLD = "metric_threshold"
    STALL_WINDOW = "stall_window"
    ARTIFACT_CREATED = "artifact_created"
    PROCESS_EXIT = "process_exit"
    HEARTBEAT_LOST = "heartbeat_lost"


@dataclass(slots=True)
class MonitorCondition:
    """A user-defined monitor condition."""

    id: str
    name: str
    condition_type: ConditionType
    params: dict[str, Any]
    scope: str
    active: bool = True
    created_at: str = ""


@dataclass(slots=True)
class MonitorAlert:
    """An alert fired when a monitor condition is met."""

    id: str
    condition_id: str
    condition_name: str
    condition_type: ConditionType
    scope: str
    detail: str
    fired_at: str
    payload: dict[str, Any] = field(default_factory=dict)


def make_id() -> str:
    """Generate a unique hex ID."""
    return uuid.uuid4().hex
