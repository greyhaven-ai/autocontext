"""Tests for remote mission bridge (AC-514).

DDD: RemoteBridge manages observation and approval relay.
RemoteSession tracks one connected observer/controller.
ApprovalRequest models delegated approval flow.
"""

from __future__ import annotations

import pytest


class TestRemoteSession:
    """A connected remote observer or controller."""

    def test_create_viewer(self) -> None:
        from autocontext.session.remote_bridge import RemoteSession, SessionRole

        session = RemoteSession.create(
            session_id="s1", operator="alice", role=SessionRole.VIEWER
        )
        assert session.operator == "alice"
        assert session.role == SessionRole.VIEWER
        assert not session.can_approve

    def test_create_controller(self) -> None:
        from autocontext.session.remote_bridge import RemoteSession, SessionRole

        session = RemoteSession.create(
            session_id="s1", operator="bob", role=SessionRole.CONTROLLER
        )
        assert session.can_approve

    def test_viewer_cannot_approve(self) -> None:
        from autocontext.session.remote_bridge import RemoteSession, SessionRole

        session = RemoteSession.create(
            session_id="s1", operator="alice", role=SessionRole.VIEWER
        )
        assert not session.can_approve
        assert not session.can_control


class TestApprovalRequest:
    """Delegated approval with timeout and audit."""

    def test_create_request(self) -> None:
        from autocontext.session.remote_bridge import ApprovalRequest

        req = ApprovalRequest.create(
            action="deploy to production",
            context="All tests pass, ready to deploy",
        )
        assert req.request_id
        assert req.action == "deploy to production"
        assert req.status == "pending"

    def test_approve(self) -> None:
        from autocontext.session.remote_bridge import ApprovalRequest

        req = ApprovalRequest.create(action="deploy")
        req.approve(by="bob")
        assert req.status == "approved"
        assert req.decided_by == "bob"

    def test_deny(self) -> None:
        from autocontext.session.remote_bridge import ApprovalRequest

        req = ApprovalRequest.create(action="deploy")
        req.deny(by="alice", reason="Not ready")
        assert req.status == "denied"
        assert req.denial_reason == "Not ready"

    def test_timeout(self) -> None:
        from autocontext.session.remote_bridge import ApprovalRequest

        req = ApprovalRequest.create(action="deploy")
        req.timeout()
        assert req.status == "timed_out"

    def test_decision_is_terminal(self) -> None:
        from autocontext.session.remote_bridge import ApprovalRequest

        req = ApprovalRequest.create(action="deploy")
        req.approve(by="bob")

        with pytest.raises(ValueError, match="status=approved"):
            req.deny(by="bob", reason="changed my mind")


class TestRemoteBridge:
    """Bridge manages remote sessions and approval relay."""

    def test_connect_observer(self) -> None:
        from autocontext.session.remote_bridge import RemoteBridge, SessionRole

        bridge = RemoteBridge(mission_id="m1")
        session = bridge.connect(operator="alice", role=SessionRole.VIEWER)
        assert len(bridge.connected_sessions) == 1
        assert not session.can_approve

    def test_request_approval_routed_to_controllers(self) -> None:
        from autocontext.session.remote_bridge import RemoteBridge, SessionRole

        bridge = RemoteBridge(mission_id="m1")
        bridge.connect(operator="alice", role=SessionRole.VIEWER)
        bridge.connect(operator="bob", role=SessionRole.CONTROLLER)

        req = bridge.request_approval(action="deploy", context="ready")
        assert req.status == "pending"
        assert len(bridge.pending_approvals) == 1

    def test_respond_to_approval(self) -> None:
        from autocontext.session.remote_bridge import RemoteBridge, SessionRole

        bridge = RemoteBridge(mission_id="m1")
        bridge.connect(operator="bob", role=SessionRole.CONTROLLER)
        req = bridge.request_approval(action="deploy", context="ready")

        bridge.respond(req.request_id, approved=True, by="bob")
        assert req.status == "approved"
        assert len(bridge.pending_approvals) == 0

    def test_viewer_cannot_respond(self) -> None:
        from autocontext.session.remote_bridge import RemoteBridge, SessionRole

        bridge = RemoteBridge(mission_id="m1")
        bridge.connect(operator="alice", role=SessionRole.VIEWER)
        req = bridge.request_approval(action="deploy", context="ready")

        with pytest.raises(PermissionError, match="viewer"):
            bridge.respond(req.request_id, approved=True, by="alice")

    def test_unconnected_operator_cannot_respond(self) -> None:
        from autocontext.session.remote_bridge import RemoteBridge, SessionRole

        bridge = RemoteBridge(mission_id="m1")
        bridge.connect(operator="alice", role=SessionRole.VIEWER)
        req = bridge.request_approval(action="deploy", context="ready")

        with pytest.raises(PermissionError, match="not connected"):
            bridge.respond(req.request_id, approved=True, by="mallory")

    def test_disconnect(self) -> None:
        from autocontext.session.remote_bridge import RemoteBridge, SessionRole

        bridge = RemoteBridge(mission_id="m1")
        session = bridge.connect(operator="alice", role=SessionRole.VIEWER)
        bridge.disconnect(session.remote_session_id)
        assert len(bridge.connected_sessions) == 0
