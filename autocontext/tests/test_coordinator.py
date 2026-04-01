"""Tests for coordinator-first execution mode (AC-515).

DDD: Coordinator owns the plan, delegates to Workers, collects results,
and steers follow-up. Workers have explicit lifecycle and lineage.
"""

from __future__ import annotations

import pytest


class TestWorker:
    """Worker entity tracks one delegated unit of work."""

    def test_create_worker(self) -> None:
        from autocontext.session.coordinator import Worker, WorkerStatus

        worker = Worker.create(task="Research auth libraries", role="researcher")
        assert worker.worker_id
        assert worker.task == "Research auth libraries"
        assert worker.role == "researcher"
        assert worker.status == WorkerStatus.PENDING

    def test_worker_lifecycle(self) -> None:
        from autocontext.session.coordinator import Worker, WorkerStatus

        w = Worker.create(task="t1", role="r1")
        w.start()
        assert w.status == WorkerStatus.RUNNING

        w.complete(result="Found 3 good libraries")
        assert w.status == WorkerStatus.COMPLETED
        assert w.result == "Found 3 good libraries"

    def test_worker_failure(self) -> None:
        from autocontext.session.coordinator import Worker, WorkerStatus

        w = Worker.create(task="t1", role="r1")
        w.start()
        w.fail(error="API timeout")
        assert w.status == WorkerStatus.FAILED
        assert w.error == "API timeout"

    def test_worker_redirect(self) -> None:
        from autocontext.session.coordinator import Worker, WorkerStatus

        w = Worker.create(task="wrong approach", role="r1")
        w.start()
        w.redirect(new_task="try different approach", reason="dead end")
        assert w.status == WorkerStatus.REDIRECTED
        assert w.redirect_reason == "dead end"

    def test_worker_lineage(self) -> None:
        from autocontext.session.coordinator import Worker

        w1 = Worker.create(task="t1", role="r1")
        w2 = Worker.create(task="t2", role="r1", parent_worker_id=w1.worker_id)
        assert w2.parent_worker_id == w1.worker_id


class TestCoordinator:
    """Coordinator aggregate owns plan, workers, and fan-out/fan-in."""

    def test_create_coordinator(self) -> None:
        from autocontext.session.coordinator import Coordinator

        coord = Coordinator.create(session_id="s1", goal="Build REST API")
        assert coord.session_id == "s1"
        assert coord.goal == "Build REST API"
        assert coord.workers == []

    def test_delegate_creates_worker(self) -> None:
        from autocontext.session.coordinator import Coordinator, WorkerStatus

        coord = Coordinator.create(session_id="s1", goal="test")
        worker = coord.delegate(task="Research auth", role="researcher")
        assert len(coord.workers) == 1
        assert worker.status == WorkerStatus.PENDING

    def test_fan_out(self) -> None:
        from autocontext.session.coordinator import Coordinator

        coord = Coordinator.create(session_id="s1", goal="test")
        workers = coord.fan_out([
            {"task": "Research auth", "role": "researcher"},
            {"task": "Research DB", "role": "researcher"},
            {"task": "Research cache", "role": "researcher"},
        ])
        assert len(workers) == 3
        assert len(coord.workers) == 3

    def test_fan_in_collects_completed_results(self) -> None:
        from autocontext.session.coordinator import Coordinator

        coord = Coordinator.create(session_id="s1", goal="test")
        workers = coord.fan_out([
            {"task": "t1", "role": "r1"},
            {"task": "t2", "role": "r1"},
        ])
        workers[0].start()
        workers[0].complete(result="result-1")
        workers[1].start()
        workers[1].complete(result="result-2")

        results = coord.fan_in()
        assert results == ["result-1", "result-2"]

    def test_fan_in_skips_incomplete(self) -> None:
        from autocontext.session.coordinator import Coordinator

        coord = Coordinator.create(session_id="s1", goal="test")
        workers = coord.fan_out([
            {"task": "t1", "role": "r1"},
            {"task": "t2", "role": "r1"},
        ])
        workers[0].start()
        workers[0].complete(result="done")
        # workers[1] still pending

        results = coord.fan_in()
        assert results == ["done"]

    def test_active_workers(self) -> None:
        from autocontext.session.coordinator import Coordinator

        coord = Coordinator.create(session_id="s1", goal="test")
        w1 = coord.delegate(task="t1", role="r1")
        w2 = coord.delegate(task="t2", role="r1")
        w1.start()
        w2.start()
        w2.complete(result="done")

        active = coord.active_workers
        assert len(active) == 1
        assert active[0].worker_id == w1.worker_id

    def test_stop_worker(self) -> None:
        from autocontext.session.coordinator import Coordinator, WorkerStatus

        coord = Coordinator.create(session_id="s1", goal="test")
        w = coord.delegate(task="t1", role="r1")
        w.start()

        coord.stop_worker(w.worker_id, reason="wrong direction")
        assert w.status == WorkerStatus.REDIRECTED

    def test_retry_creates_continuation(self) -> None:
        from autocontext.session.coordinator import Coordinator

        coord = Coordinator.create(session_id="s1", goal="test")
        w1 = coord.delegate(task="t1", role="r1")
        w1.start()
        w1.fail(error="timeout")

        w2 = coord.retry(w1.worker_id, new_task="t1 retry")
        assert w2.parent_worker_id == w1.worker_id
        assert w2.task == "t1 retry"
        assert len(coord.workers) == 2


class TestCoordinatorEvents:
    """Coordinator emits structured events for observability."""

    def test_delegate_emits_event(self) -> None:
        from autocontext.session.coordinator import Coordinator, CoordinatorEventType

        coord = Coordinator.create(session_id="s1", goal="test")
        coord.delegate(task="t1", role="r1")

        types = [e.event_type for e in coord.events]
        assert CoordinatorEventType.COORDINATOR_CREATED in types
        assert CoordinatorEventType.WORKER_DELEGATED in types

    def test_completion_emits_event(self) -> None:
        from autocontext.session.coordinator import Coordinator, CoordinatorEventType

        coord = Coordinator.create(session_id="s1", goal="test")
        w = coord.delegate(task="t1", role="r1")
        w.start()
        coord.complete_worker(w.worker_id, result="done")

        types = [e.event_type for e in coord.events]
        assert CoordinatorEventType.WORKER_COMPLETED in types
