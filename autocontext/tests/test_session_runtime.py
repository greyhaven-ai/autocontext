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
