"""Tests for derived progress digests (AC-512).

DDD: DigestBuilder derives compact operator-facing summaries from
session events, coordinator state, and heartbeat signals.
"""

from __future__ import annotations


class TestWorkerDigest:
    """Compact status of one active worker."""

    def test_create_from_worker(self) -> None:
        from autocontext.session.coordinator import Worker
        from autocontext.session.progress_digest import WorkerDigest

        worker = Worker.create(task="Research auth libraries", role="researcher")
        worker.start()
        digest = WorkerDigest.from_worker(worker)
        assert digest.worker_id == worker.worker_id
        assert digest.role == "researcher"
        assert digest.current_action == "Research auth libraries"
        assert digest.status == "running"

    def test_completed_worker_digest(self) -> None:
        from autocontext.session.coordinator import Worker
        from autocontext.session.progress_digest import WorkerDigest

        worker = Worker.create(task="t1", role="r1")
        worker.start()
        worker.complete(result="Found 3 options")
        digest = WorkerDigest.from_worker(worker)
        assert digest.status == "completed"
        assert "Found 3 options" in digest.last_result


class TestProgressDigest:
    """Aggregate summary: active workers, recent changes, next step."""

    def test_build_from_coordinator(self) -> None:
        from autocontext.session.coordinator import Coordinator
        from autocontext.session.progress_digest import ProgressDigest

        coord = Coordinator.create(session_id="s1", goal="Build API")
        w1 = coord.delegate(task="Research auth", role="researcher")
        w2 = coord.delegate(task="Research DB", role="researcher")
        w1.start()
        w2.start()
        w1.complete(result="OAuth2 recommended")

        digest = ProgressDigest.from_coordinator(coord)
        assert digest.goal == "Build API"
        assert digest.active_count == 1
        assert digest.completed_count == 1
        assert len(digest.worker_digests) == 2

    def test_digest_summary_is_short(self) -> None:
        from autocontext.session.coordinator import Coordinator
        from autocontext.session.progress_digest import ProgressDigest

        coord = Coordinator.create(session_id="s1", goal="test")
        w = coord.delegate(task="long task name " * 20, role="r1")
        w.start()

        digest = ProgressDigest.from_coordinator(coord)
        assert len(digest.summary) <= 300  # skimmable, not a wall of text

    def test_empty_coordinator_digest(self) -> None:
        from autocontext.session.coordinator import Coordinator
        from autocontext.session.progress_digest import ProgressDigest

        coord = Coordinator.create(session_id="s1", goal="test")
        digest = ProgressDigest.from_coordinator(coord)
        assert digest.active_count == 0
        assert "no workers" in digest.summary.lower() or "idle" in digest.summary.lower()

    def test_recent_changes_from_events(self) -> None:
        from autocontext.session.coordinator import Coordinator
        from autocontext.session.progress_digest import ProgressDigest

        coord = Coordinator.create(session_id="s1", goal="test")
        w = coord.delegate(task="t1", role="r1")
        w.start()
        coord.complete_worker(w.worker_id, result="done")

        digest = ProgressDigest.from_coordinator(coord, max_recent_events=5)
        assert len(digest.recent_changes) > 0

    def test_redirected_workers_still_appear_in_digest_summary(self) -> None:
        from autocontext.session.coordinator import Coordinator
        from autocontext.session.progress_digest import ProgressDigest

        coord = Coordinator.create(session_id="s1", goal="test")
        w = coord.delegate(task="t1", role="r1")
        w.start()
        coord.stop_worker(w.worker_id, reason="dead end")

        digest = ProgressDigest.from_coordinator(coord)
        assert digest.redirected_count == 1
        assert "redirected" in digest.summary.lower()
        assert len(digest.worker_digests) == 1
        assert digest.worker_digests[0].status == "redirected"


class TestDigestDegradation:
    """Digests degrade gracefully with insufficient signal."""

    def test_digest_from_session_without_coordinator(self) -> None:
        from autocontext.session.progress_digest import ProgressDigest
        from autocontext.session.types import Session

        session = Session.create(goal="Simple task")
        session.submit_turn(prompt="do something", role="competitor")

        digest = ProgressDigest.from_session(session)
        assert digest.goal == "Simple task"
        assert digest.active_count == 0  # no coordinator
        assert digest.turn_count == 1

    def test_digest_never_crashes(self) -> None:
        from autocontext.session.progress_digest import ProgressDigest

        # Empty inputs
        digest = ProgressDigest.empty()
        assert digest.summary
        assert digest.active_count == 0
