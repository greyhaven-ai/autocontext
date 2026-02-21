"""Heartbeat types — agent liveness monitoring and stall detection."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class AgentStatus(enum.StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    STALLED = "stalled"
    RECOVERED = "recovered"
    TERMINATED = "terminated"


class EscalationLevel(enum.StrEnum):
    WARN = "warn"
    PAUSE = "pause"
    RESTART = "restart"
    TERMINATE = "terminate"


@dataclass(frozen=True, slots=True)
class HeartbeatRecord:
    """Single immutable heartbeat from an agent."""

    agent_id: str
    role: str
    timestamp: str
    generation: int | None
    status: AgentStatus
    detail: str = ""

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "timestamp": self.timestamp,
            "generation": self.generation,
            "status": self.status.value,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeartbeatRecord:
        return cls(
            agent_id=data["agent_id"],
            role=data["role"],
            timestamp=data["timestamp"],
            generation=data.get("generation"),
            status=AgentStatus(data["status"]),
            detail=data.get("detail", ""),
        )


@dataclass(frozen=True, slots=True)
class StallPolicy:
    """Configuration for stall detection and escalation."""

    stall_timeout_seconds: float = 300.0
    escalation_levels: tuple[EscalationLevel, ...] = (
        EscalationLevel.WARN,
        EscalationLevel.PAUSE,
        EscalationLevel.RESTART,
        EscalationLevel.TERMINATE,
    )
    escalation_interval_seconds: float = 60.0
    max_restart_attempts: int = 2


@dataclass(frozen=True, slots=True)
class StallEvent:
    """Record of a detected stall and the action taken."""

    agent_id: str
    role: str
    timestamp: str
    seconds_since_heartbeat: float
    escalation_level: EscalationLevel
    action_taken: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "timestamp": self.timestamp,
            "seconds_since_heartbeat": self.seconds_since_heartbeat,
            "escalation_level": self.escalation_level.value,
            "action_taken": self.action_taken,
        }
