"""Remote mission bridge with delegated approval relay (AC-514).

Domain concepts:
- RemoteSession: one connected observer or controller
- SessionRole: viewer (read-only) or controller (can approve/control)
- ApprovalRequest: delegated approval with status tracking
- RemoteBridge: aggregate managing connections and approval relay
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionRole(StrEnum):
    VIEWER = "viewer"
    CONTROLLER = "controller"


class RemoteSession(BaseModel):
    """One connected remote observer or controller."""

    remote_session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str
    operator: str
    role: SessionRole
    connected_at: str = Field(default_factory=_now)

    @classmethod
    def create(cls, session_id: str, operator: str, role: SessionRole) -> RemoteSession:
        return cls(session_id=session_id, operator=operator, role=role)

    @property
    def can_approve(self) -> bool:
        return self.role == SessionRole.CONTROLLER

    @property
    def can_control(self) -> bool:
        return self.role == SessionRole.CONTROLLER


class ApprovalRequest(BaseModel):
    """A delegated approval with status tracking and audit."""

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    action: str
    context: str = ""
    status: str = "pending"  # pending, approved, denied, timed_out, canceled
    decided_by: str = ""
    denial_reason: str = ""
    created_at: str = Field(default_factory=_now)
    decided_at: str = ""

    @classmethod
    def create(cls, action: str, context: str = "") -> ApprovalRequest:
        return cls(action=action, context=context)

    def approve(self, by: str) -> None:
        self._require_pending(action="approve request")
        self.status = "approved"
        self.decided_by = by
        self.decided_at = _now()

    def deny(self, by: str, reason: str = "") -> None:
        self._require_pending(action="deny request")
        self.status = "denied"
        self.decided_by = by
        self.denial_reason = reason
        self.decided_at = _now()

    def timeout(self) -> None:
        self._require_pending(action="time out request")
        self.status = "timed_out"
        self.decided_at = _now()

    def cancel(self) -> None:
        self._require_pending(action="cancel request")
        self.status = "canceled"
        self.decided_at = _now()

    def _require_pending(self, action: str) -> None:
        if self.status != "pending":
            msg = f"Cannot {action} once status={self.status}"
            raise ValueError(msg)


class RemoteBridge:
    """Manages remote sessions and approval relay for a mission.

    Enforces role-based access: viewers observe, controllers approve.
    """

    def __init__(self, mission_id: str) -> None:
        self.mission_id = mission_id
        self._sessions: dict[str, RemoteSession] = {}
        self._approvals: dict[str, ApprovalRequest] = {}

    def connect(self, operator: str, role: SessionRole) -> RemoteSession:
        session = RemoteSession.create(
            session_id=self.mission_id, operator=operator, role=role,
        )
        self._sessions[session.remote_session_id] = session
        return session

    def disconnect(self, remote_session_id: str) -> None:
        self._sessions.pop(remote_session_id, None)

    @property
    def connected_sessions(self) -> list[RemoteSession]:
        return list(self._sessions.values())

    def request_approval(self, action: str, context: str = "") -> ApprovalRequest:
        req = ApprovalRequest.create(action=action, context=context)
        self._approvals[req.request_id] = req
        return req

    @property
    def pending_approvals(self) -> list[ApprovalRequest]:
        return [a for a in self._approvals.values() if a.status == "pending"]

    def respond(self, request_id: str, approved: bool, by: str, reason: str = "") -> None:
        """Respond to an approval request. Only controllers may respond."""
        # Check operator role
        operator_session = next(
            (s for s in self._sessions.values() if s.operator == by), None
        )
        if operator_session is None:
            msg = f"Operator '{by}' is not connected and cannot respond to approvals"
            raise PermissionError(msg)
        if operator_session.role != SessionRole.CONTROLLER:
            msg = f"Operator '{by}' is a viewer and cannot respond to approvals"
            raise PermissionError(msg)

        req = self._approvals.get(request_id)
        if req is None:
            msg = f"Approval request '{request_id}' not found"
            raise KeyError(msg)

        if approved:
            req.approve(by=by)
        else:
            req.deny(by=by, reason=reason)
