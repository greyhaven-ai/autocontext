"""Session runtime domain types (AC-507).

Bounded context: a Session is the aggregate root representing a
multi-turn, resumable, observable unit of work.

Key domain concepts:
- Session: aggregate root with explicit lifecycle
- Turn: a single request/response within a session
- SessionEvent: immutable event for replay and observation
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SessionStatus(StrEnum):
    """Lifecycle states for a session."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class TurnOutcome(StrEnum):
    """Outcome of a single turn within a session."""

    PENDING = "pending"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class SessionEventType(StrEnum):
    """Types of events emitted by sessions."""

    SESSION_CREATED = "session_created"
    SESSION_PAUSED = "session_paused"
    SESSION_RESUMED = "session_resumed"
    SESSION_COMPLETED = "session_completed"
    SESSION_FAILED = "session_failed"
    SESSION_CANCELED = "session_canceled"
    TURN_SUBMITTED = "turn_submitted"
    TURN_COMPLETED = "turn_completed"
    TURN_INTERRUPTED = "turn_interrupted"
    TURN_FAILED = "turn_failed"


class SessionEvent(BaseModel):
    """Immutable event in the session event stream."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    event_type: SessionEventType
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class Turn(BaseModel):
    """A single request/response cycle within a session."""

    turn_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    turn_index: int
    prompt: str
    role: str
    response: str = ""
    outcome: TurnOutcome = TurnOutcome.PENDING
    error: str = ""
    tokens_used: int = 0
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome == TurnOutcome.COMPLETED


class Session(BaseModel):
    """Aggregate root: a multi-turn, resumable unit of work.

    Create via Session.create(), not direct construction.
    """

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    goal: str
    status: SessionStatus = SessionStatus.ACTIVE
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    turns: list[Turn] = Field(default_factory=list)
    events: list[SessionEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = ""

    @classmethod
    def create(cls, goal: str, metadata: dict[str, Any] | None = None) -> Session:
        """Factory method — creates session and emits initial event."""
        session = cls(goal=goal, metadata=metadata or {})
        session._emit(SessionEventType.SESSION_CREATED, {"goal": goal})
        return session

    # -- Turn management --

    def submit_turn(self, prompt: str, role: str) -> Turn:
        """Submit a new turn. Session must be active."""
        if self.status != SessionStatus.ACTIVE:
            msg = f"Cannot submit turn: session is not active (status={self.status})"
            raise ValueError(msg)

        turn = Turn(turn_index=len(self.turns), prompt=prompt, role=role)
        self.turns.append(turn)
        self._emit(SessionEventType.TURN_SUBMITTED, {
            "turn_id": turn.turn_id,
            "role": role,
        })
        return turn

    def complete_turn(self, turn_id: str, response: str, tokens_used: int = 0) -> None:
        """Mark a turn as successfully completed."""
        turn = self._get_turn(turn_id)
        turn.outcome = TurnOutcome.COMPLETED
        turn.response = response
        turn.tokens_used = tokens_used
        turn.completed_at = datetime.now(UTC).isoformat()
        self._touch()
        self._emit(SessionEventType.TURN_COMPLETED, {
            "turn_id": turn_id,
            "tokens_used": tokens_used,
        })

    def interrupt_turn(self, turn_id: str, reason: str = "") -> None:
        """Mark a turn as interrupted (not a success)."""
        turn = self._get_turn(turn_id)
        turn.outcome = TurnOutcome.INTERRUPTED
        turn.error = reason
        turn.completed_at = datetime.now(UTC).isoformat()
        self._touch()
        self._emit(SessionEventType.TURN_INTERRUPTED, {
            "turn_id": turn_id,
            "reason": reason,
        })

    def fail_turn(self, turn_id: str, error: str = "") -> None:
        """Mark a turn as failed."""
        turn = self._get_turn(turn_id)
        turn.outcome = TurnOutcome.FAILED
        turn.error = error
        turn.completed_at = datetime.now(UTC).isoformat()
        self._touch()
        self._emit(SessionEventType.TURN_FAILED, {
            "turn_id": turn_id,
            "error": error,
        })

    # -- Lifecycle transitions --

    def pause(self) -> None:
        self._require_status(SessionStatus.ACTIVE, action="pause")
        self.status = SessionStatus.PAUSED
        self._touch()
        self._emit(SessionEventType.SESSION_PAUSED, {})

    def resume(self) -> None:
        self._require_status(SessionStatus.PAUSED, action="resume")
        self.status = SessionStatus.ACTIVE
        self._touch()
        self._emit(SessionEventType.SESSION_RESUMED, {})

    def complete(self, summary: str = "") -> None:
        self._require_not_terminal(action="complete")
        self.status = SessionStatus.COMPLETED
        self.summary = summary
        self._touch()
        self._emit(SessionEventType.SESSION_COMPLETED, {"summary": summary})

    def fail(self, error: str = "") -> None:
        self._require_not_terminal(action="fail")
        self.status = SessionStatus.FAILED
        self._touch()
        self._emit(SessionEventType.SESSION_FAILED, {"error": error})

    def cancel(self) -> None:
        self._require_not_terminal(action="cancel")
        self.status = SessionStatus.CANCELED
        self._touch()
        self._emit(SessionEventType.SESSION_CANCELED, {})

    # -- Queries --

    @property
    def total_tokens(self) -> int:
        return sum(t.tokens_used for t in self.turns)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    # -- Internal --

    def _get_turn(self, turn_id: str) -> Turn:
        for turn in self.turns:
            if turn.turn_id == turn_id:
                return turn
        msg = f"Turn {turn_id} not found in session {self.session_id}"
        raise KeyError(msg)

    def _require_status(self, expected: SessionStatus, action: str) -> None:
        if self.status != expected:
            msg = f"Cannot {action} session from status={self.status}"
            raise ValueError(msg)

    def _require_not_terminal(self, action: str) -> None:
        if self.status in {
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELED,
        }:
            msg = f"Cannot {action} session from terminal status={self.status}"
            raise ValueError(msg)

    def _touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()

    def _emit(self, event_type: SessionEventType, payload: dict[str, Any]) -> None:
        self.events.append(SessionEvent(
            event_type=event_type,
            payload={"session_id": self.session_id, **payload},
        ))
