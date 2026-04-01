"""Tests for session supervisor (AC-510).

DDD: Supervisor manages a registry of SupervisedEntry entities.
Each entry tracks a background session's lifecycle, heartbeat, and logs.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSupervisedEntry:
    """A supervised entry tracks one background session/mission."""

    def test_create_entry(self) -> None:
        from autocontext.session.supervisor import SupervisedEntry, SupervisorState

        entry = SupervisedEntry.create(
            session_id="sess-1",
            goal="Implement REST API",
            workspace="/tmp/project",
        )
        assert entry.entry_id
        assert entry.session_id == "sess-1"
        assert entry.state == SupervisorState.LAUNCHING
        assert entry.goal == "Implement REST API"
        assert entry.workspace == "/tmp/project"
        assert entry.blocked_reason == ""
        assert entry.created_at

    def test_entry_lifecycle_transitions(self) -> None:
        from autocontext.session.supervisor import SupervisedEntry, SupervisorState

        entry = SupervisedEntry.create(session_id="s1", goal="test")
        assert entry.state == SupervisorState.LAUNCHING

        entry.mark_running()
        assert entry.state == SupervisorState.RUNNING

        entry.mark_waiting(reason="approval needed")
        assert entry.state == SupervisorState.WAITING
        assert entry.blocked_reason == "approval needed"

        entry.mark_running()
        assert entry.state == SupervisorState.RUNNING
        assert entry.blocked_reason == ""

        entry.mark_completed()
        assert entry.state == SupervisorState.COMPLETED

    def test_entry_stop_lifecycle(self) -> None:
        from autocontext.session.supervisor import SupervisedEntry, SupervisorState

        entry = SupervisedEntry.create(session_id="s1", goal="test")
        entry.mark_running()
        entry.request_stop()
        assert entry.state == SupervisorState.STOPPING

        entry.mark_stopped()
        assert entry.state == SupervisorState.STOPPED

    def test_entry_failure(self) -> None:
        from autocontext.session.supervisor import SupervisedEntry, SupervisorState

        entry = SupervisedEntry.create(session_id="s1", goal="test")
        entry.mark_running()
        entry.mark_failed(error="OOM")
        assert entry.state == SupervisorState.FAILED
        assert entry.error == "OOM"

    def test_heartbeat_updates_last_activity(self) -> None:
        from autocontext.session.supervisor import SupervisedEntry

        entry = SupervisedEntry.create(session_id="s1", goal="test")
        entry.mark_running()
        old_activity = entry.last_activity_at
        entry.heartbeat()
        assert entry.last_activity_at >= old_activity

    def test_is_alive(self) -> None:
        from autocontext.session.supervisor import SupervisedEntry

        entry = SupervisedEntry.create(session_id="s1", goal="test")
        entry.mark_running()
        assert entry.is_alive

        entry.mark_completed()
        assert not entry.is_alive

    @pytest.mark.parametrize("terminal_action", ["mark_completed", "mark_failed"])
    def test_terminal_entries_cannot_reenter_active_states(
        self,
        terminal_action: str,
    ) -> None:
        from autocontext.session.supervisor import SupervisedEntry

        entry = SupervisedEntry.create(session_id="s1", goal="test")
        entry.mark_running()
        getattr(entry, terminal_action)()

        with pytest.raises(ValueError, match="mark entry running"):
            entry.mark_running()

        with pytest.raises(ValueError, match="request stop"):
            entry.request_stop()


class TestSupervisor:
    """Supervisor manages the registry of supervised entries."""

    def test_launch_registers_entry(self) -> None:
        from autocontext.session.supervisor import Supervisor

        sup = Supervisor()
        entry = sup.launch(session_id="s1", goal="test", workspace="/tmp")
        assert entry.session_id == "s1"
        assert sup.get("s1") is not None

    def test_list_active(self) -> None:
        from autocontext.session.supervisor import Supervisor

        sup = Supervisor()
        sup.launch(session_id="s1", goal="g1", workspace="/tmp")
        sup.launch(session_id="s2", goal="g2", workspace="/tmp")
        e3 = sup.launch(session_id="s3", goal="g3", workspace="/tmp")
        e3.mark_running()
        e3.mark_completed()

        active = sup.list_active()
        assert len(active) == 2  # s3 is completed, not active

    def test_stop_session(self) -> None:
        from autocontext.session.supervisor import Supervisor, SupervisorState

        sup = Supervisor()
        entry = sup.launch(session_id="s1", goal="test", workspace="/tmp")
        entry.mark_running()

        sup.stop("s1")
        assert entry.state == SupervisorState.STOPPING

    def test_stop_terminal_session_raises(self) -> None:
        from autocontext.session.supervisor import Supervisor

        sup = Supervisor()
        entry = sup.launch(session_id="s1", goal="test", workspace="/tmp")
        entry.mark_running()
        entry.mark_completed()

        with pytest.raises(ValueError, match="request stop"):
            sup.stop("s1")

    def test_stop_nonexistent_raises(self) -> None:
        from autocontext.session.supervisor import Supervisor

        sup = Supervisor()
        with pytest.raises(KeyError):
            sup.stop("nonexistent")

    def test_cleanup_stale_entries(self) -> None:
        from autocontext.session.supervisor import Supervisor

        sup = Supervisor()
        entry = sup.launch(session_id="s1", goal="test", workspace="/tmp")
        entry.mark_running()
        # Simulate stale: set last_activity_at to far past
        entry.last_activity_at = "2020-01-01T00:00:00+00:00"

        cleaned = sup.cleanup_stale(max_idle_seconds=60)
        assert len(cleaned) == 1
        assert cleaned[0] == "s1"
        assert entry.state.value == "failed"

    def test_duplicate_launch_raises(self) -> None:
        from autocontext.session.supervisor import Supervisor

        sup = Supervisor()
        sup.launch(session_id="s1", goal="test", workspace="/tmp")
        with pytest.raises(ValueError, match="already supervised"):
            sup.launch(session_id="s1", goal="test2", workspace="/tmp")


class TestSupervisorStore:
    """Supervisor state persists across restarts."""

    def test_save_and_restore(self, tmp_path: Path) -> None:
        from autocontext.session.supervisor import Supervisor, SupervisorStore

        store = SupervisorStore(tmp_path / "supervisor.json")
        sup = Supervisor()
        e1 = sup.launch(session_id="s1", goal="g1", workspace="/tmp")
        e1.mark_running()
        store.save(sup)

        sup2 = Supervisor()
        store.restore(sup2)
        assert sup2.get("s1") is not None
        assert sup2.get("s1").state.value == "running"
