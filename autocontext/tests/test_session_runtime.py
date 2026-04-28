"""Tests for session runtime foundation (AC-507).

TDD + DDD: defines the domain model contracts first.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSessionDomainModel:
    """Session aggregate root with explicit lifecycle."""

    def test_create_session(self) -> None:
        from autocontext.session.types import Session, SessionStatus

        session = Session.create(goal="Implement a REST API", metadata={"project": "acme"})
        assert session.session_id  # auto-generated
        assert session.status == SessionStatus.ACTIVE
        assert session.goal == "Implement a REST API"
        assert session.metadata["project"] == "acme"
        assert session.turns == []
        assert session.created_at

    def test_session_submit_turn(self) -> None:
        from autocontext.session.types import Session, TurnOutcome

        session = Session.create(goal="test")
        turn = session.submit_turn(prompt="Write hello world", role="competitor")
        assert turn.turn_index == 0
        assert turn.prompt == "Write hello world"
        assert turn.role == "competitor"
        assert turn.outcome == TurnOutcome.PENDING

    def test_session_complete_turn(self) -> None:
        from autocontext.session.types import Session, TurnOutcome

        session = Session.create(goal="test")
        turn = session.submit_turn(prompt="Write hello world", role="competitor")
        session.complete_turn(turn.turn_id, response="print('hello world')", tokens_used=50)
        assert turn.outcome == TurnOutcome.COMPLETED
        assert turn.response == "print('hello world')"
        assert turn.tokens_used == 50

    def test_session_interrupt_turn(self) -> None:
        from autocontext.session.types import Session, TurnOutcome

        session = Session.create(goal="test")
        turn = session.submit_turn(prompt="long task", role="competitor")
        session.interrupt_turn(turn.turn_id, reason="timeout")
        assert turn.outcome == TurnOutcome.INTERRUPTED
        assert turn.error == "timeout"

    def test_interrupted_turn_not_mistaken_for_success(self) -> None:
        from autocontext.session.types import Session

        session = Session.create(goal="test")
        turn = session.submit_turn(prompt="long task", role="competitor")
        session.interrupt_turn(turn.turn_id, reason="timeout")
        assert not turn.succeeded

    def test_session_lifecycle_transitions(self) -> None:
        from autocontext.session.types import Session, SessionStatus

        session = Session.create(goal="test")
        assert session.status == SessionStatus.ACTIVE

        session.pause()
        assert session.status == SessionStatus.PAUSED

        session.resume()
        assert session.status == SessionStatus.ACTIVE

        session.complete(summary="done")
        assert session.status == SessionStatus.COMPLETED
        assert session.summary == "done"

    @pytest.mark.parametrize("terminal_action", ["complete", "fail", "cancel"])
    def test_terminal_sessions_cannot_resume_or_accept_new_turns(
        self,
        terminal_action: str,
    ) -> None:
        from autocontext.session.types import Session

        session = Session.create(goal="test")
        getattr(session, terminal_action)()

        with pytest.raises(ValueError, match="resume"):
            session.resume()

        with pytest.raises(ValueError, match="not active"):
            session.submit_turn(prompt="should fail", role="competitor")

    def test_cannot_submit_turn_when_paused(self) -> None:
        from autocontext.session.types import Session

        session = Session.create(goal="test")
        session.pause()
        with pytest.raises(ValueError, match="not active"):
            session.submit_turn(prompt="should fail", role="competitor")

    def test_session_tracks_usage(self) -> None:
        from autocontext.session.types import Session

        session = Session.create(goal="test")
        t1 = session.submit_turn(prompt="p1", role="competitor")
        session.complete_turn(t1.turn_id, response="r1", tokens_used=100)
        t2 = session.submit_turn(prompt="p2", role="analyst")
        session.complete_turn(t2.turn_id, response="r2", tokens_used=200)
        assert session.total_tokens == 300
        assert session.turn_count == 2


class TestSessionBranchLineage:
    """Branchable session lineage for Pi-shaped harness workflows."""

    def test_session_starts_on_main_branch(self) -> None:
        from autocontext.session.types import Session

        session = Session.create(goal="explore")
        turn = session.submit_turn(prompt="p1", role="competitor")

        assert session.active_branch_id == "main"
        assert turn.branch_id == "main"
        assert turn.parent_turn_id == ""

    def test_fork_from_turn_creates_branch_with_parent_lineage(self) -> None:
        from autocontext.session.types import Session, SessionEventType

        session = Session.create(goal="explore")
        root = session.submit_turn(prompt="root", role="competitor")
        session.complete_turn(root.turn_id, response="r1")

        branch = session.fork_from_turn(root.turn_id, branch_id="experimental", label="try alternate")
        next_turn = session.submit_turn(prompt="branch prompt", role="competitor")

        assert branch.branch_id == "experimental"
        assert branch.parent_turn_id == root.turn_id
        assert branch.label == "try alternate"
        assert session.active_branch_id == "experimental"
        assert next_turn.branch_id == "experimental"
        assert next_turn.parent_turn_id == root.turn_id
        assert session.active_turn_id == next_turn.turn_id

        event_types = [event.event_type for event in session.events]
        assert SessionEventType.BRANCH_CREATED in event_types
        assert SessionEventType.BRANCH_SWITCHED in event_types

    def test_switch_branch_sets_next_turn_parent_to_branch_leaf(self) -> None:
        from autocontext.session.types import Session

        session = Session.create(goal="explore")
        main = session.submit_turn(prompt="main", role="competitor")
        session.complete_turn(main.turn_id, response="main response")
        session.fork_from_turn(main.turn_id, branch_id="alt")
        alt = session.submit_turn(prompt="alt", role="competitor")
        session.complete_turn(alt.turn_id, response="alt response")

        session.switch_branch("main")
        followup = session.submit_turn(prompt="main followup", role="analyst")

        assert followup.branch_id == "main"
        assert followup.parent_turn_id == main.turn_id

    def test_branch_path_returns_only_turns_on_active_lineage(self) -> None:
        from autocontext.session.types import Session

        session = Session.create(goal="explore")
        root = session.submit_turn(prompt="root", role="competitor")
        session.complete_turn(root.turn_id, response="root response")
        session.fork_from_turn(root.turn_id, branch_id="alt")
        alt = session.submit_turn(prompt="alt", role="competitor")
        session.complete_turn(alt.turn_id, response="alt response")

        path = session.branch_path("alt")

        assert [turn.turn_id for turn in path] == [root.turn_id, alt.turn_id]


class TestSessionEvents:
    """Session emits structured events for replay and observation."""

    def test_session_emits_events(self) -> None:
        from autocontext.session.types import Session, SessionEventType

        session = Session.create(goal="test")
        assert len(session.events) >= 1  # session_created event
        assert session.events[0].event_type == SessionEventType.SESSION_CREATED

    def test_turn_events_recorded(self) -> None:
        from autocontext.session.types import Session, SessionEventType

        session = Session.create(goal="test")
        turn = session.submit_turn(prompt="p1", role="competitor")
        session.complete_turn(turn.turn_id, response="r1", tokens_used=50)

        event_types = [e.event_type for e in session.events]
        assert SessionEventType.TURN_SUBMITTED in event_types
        assert SessionEventType.TURN_COMPLETED in event_types


class TestSessionStore:
    """Sessions persist and restore with full fidelity."""

    def test_save_and_load(self, tmp_path: Path) -> None:
        from autocontext.session.store import SessionStore
        from autocontext.session.types import Session, SessionStatus

        store = SessionStore(tmp_path / "sessions.sqlite3")
        session = Session.create(goal="persist test")
        turn = session.submit_turn(prompt="p1", role="competitor")
        session.complete_turn(turn.turn_id, response="r1", tokens_used=100)

        store.save(session)
        loaded = store.load(session.session_id)

        assert loaded is not None
        assert loaded.session_id == session.session_id
        assert loaded.goal == "persist test"
        assert loaded.status == SessionStatus.ACTIVE
        assert len(loaded.turns) == 1
        assert loaded.turns[0].response == "r1"
        assert loaded.total_tokens == 100

    def test_list_sessions(self, tmp_path: Path) -> None:
        from autocontext.session.store import SessionStore
        from autocontext.session.types import Session

        store = SessionStore(tmp_path / "sessions.sqlite3")
        s1 = Session.create(goal="goal 1")
        s2 = Session.create(goal="goal 2")
        store.save(s1)
        store.save(s2)

        sessions = store.list()
        assert len(sessions) == 2
