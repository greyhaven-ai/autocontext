"""Audit log types — immutable, append-only entry records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class AuditCategory(StrEnum):
    LLM_CALL = "llm_call"
    GATE_DECISION = "gate_decision"
    COST_EVENT = "cost_event"
    CONFIG_CHANGE = "config_change"
    ERROR = "error"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Single immutable audit log entry."""

    timestamp: str
    category: AuditCategory
    actor: str
    action: str
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "category": self.category.value,
            "actor": self.actor,
            "action": self.action,
            "detail": self.detail,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEntry:
        return cls(
            timestamp=data["timestamp"],
            category=AuditCategory(data["category"]),
            actor=data["actor"],
            action=data["action"],
            detail=data.get("detail", ""),
            metadata=data.get("metadata", {}),
        )
