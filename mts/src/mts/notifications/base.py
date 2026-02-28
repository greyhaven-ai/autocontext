"""Base notifier interface and event types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class EventType(StrEnum):
    THRESHOLD_MET = "threshold_met"
    REGRESSION = "regression"
    COMPLETION = "completion"
    FAILURE = "failure"


@dataclass(slots=True)
class NotificationEvent:
    """Event emitted by the task runner or improvement loop."""

    type: EventType
    task_name: str
    task_id: str | None = None
    score: float | None = None
    previous_best: float | None = None
    round_count: int = 0
    cost_usd: float | None = None
    output_preview: str = ""
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if self.type == EventType.THRESHOLD_MET:
            return f"✅ {self.task_name}: score {self.score:.2f} met threshold (round {self.round_count})"
        if self.type == EventType.REGRESSION:
            return f"⚠️ {self.task_name}: score dropped {self.previous_best:.2f} → {self.score:.2f}"
        if self.type == EventType.COMPLETION:
            score_str = f"{self.score:.2f}" if self.score is not None else "N/A"
            return f"📋 {self.task_name}: completed {self.round_count} rounds, best score {score_str}"
        if self.type == EventType.FAILURE:
            preview = (self.error or "unknown")[:100]
            return f"❌ {self.task_name}: failed — {preview}"
        return f"{self.task_name}: {self.type}"


class Notifier(ABC):
    """Abstract base for notification delivery."""

    @abstractmethod
    def notify(self, event: NotificationEvent) -> None:
        """Send a notification. Must not raise — failures are logged and swallowed."""
        ...
