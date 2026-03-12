"""Tests for task queue priority ordering and concurrent access (MTS-14)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from autocontext.storage.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    s = SQLiteStore(tmp_path / "test.db")
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    s.migrate(migrations_dir)
    return s


class TestPriorityOrdering:
    def test_higher_priority_dequeued_first(self, store):
        """Tasks with higher priority should be dequeued before lower."""
        store.enqueue_task("low", "spec_a", priority=1)
        store.enqueue_task("high", "spec_b", priority=10)
        store.enqueue_task("med", "spec_c", priority=5)

        first = store.dequeue_task()
        second = store.dequeue_task()
        third = store.dequeue_task()

        assert first["id"] == "high"
        assert second["id"] == "med"
        assert third["id"] == "low"

    def test_same_priority_fifo(self, store):
        """Tasks with same priority should dequeue in FIFO order."""
        store.enqueue_task("first", "spec_a", priority=5)
        # Small sleep to ensure created_at differs
        time.sleep(0.01)
        store.enqueue_task("second", "spec_b", priority=5)
        time.sleep(0.01)
        store.enqueue_task("third", "spec_c", priority=5)

        assert store.dequeue_task()["id"] == "first"
        assert store.dequeue_task()["id"] == "second"
        assert store.dequeue_task()["id"] == "third"

    def test_empty_queue_returns_none(self, store):
        assert store.dequeue_task() is None

    def test_already_running_not_dequeued(self, store):
        """Running tasks should not be dequeued again."""
        store.enqueue_task("t1", "spec_a", priority=5)
        first = store.dequeue_task()
        assert first["id"] == "t1"
        assert store.dequeue_task() is None  # Already running

    def test_completed_tasks_not_dequeued(self, store):
        """Completed tasks should not be re-dequeued."""
        store.enqueue_task("t1", "spec_a", priority=5)
        store.dequeue_task()
        store.complete_task("t1", best_score=0.9, best_output="done", total_rounds=1, met_threshold=True)
        assert store.dequeue_task() is None

    def test_mixed_status_only_pending(self, store):
        """Only pending tasks should be dequeued."""
        store.enqueue_task("pending1", "spec_a", priority=1)
        store.enqueue_task("will_run", "spec_b", priority=10)
        store.enqueue_task("pending2", "spec_c", priority=5)

        # Claim the highest priority one
        claimed = store.dequeue_task()
        assert claimed["id"] == "will_run"

        # Next should be pending2 (priority 5), not will_run again
        next_task = store.dequeue_task()
        assert next_task["id"] == "pending2"

    def test_default_priority_zero(self, store):
        """Default priority should be 0."""
        store.enqueue_task("default_prio", "spec_a")
        store.enqueue_task("high_prio", "spec_b", priority=1)

        assert store.dequeue_task()["id"] == "high_prio"
        assert store.dequeue_task()["id"] == "default_prio"


class TestConcurrentAccess:
    def test_no_double_processing(self, store):
        """Two threads dequeuing simultaneously should not get the same task."""
        for i in range(10):
            store.enqueue_task(f"task_{i}", "spec_a", priority=i)

        claimed: list[str] = []
        errors: list[Exception] = []

        def worker():
            try:
                while True:
                    task = store.dequeue_task()
                    if task is None:
                        break
                    claimed.append(task["id"])
                    time.sleep(0.001)  # Simulate work
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Worker errors: {errors}"
        assert len(claimed) == 10, f"Expected 10, got {len(claimed)}: {claimed}"
        assert len(set(claimed)) == 10, f"Duplicates found: {claimed}"

    def test_concurrent_priority_order(self, store):
        """Even under contention, tasks should come out roughly in priority order."""
        for i in range(20):
            store.enqueue_task(f"task_{i:02d}", "spec_a", priority=i)

        claimed: list[str] = []
        lock = threading.Lock()

        def worker():
            while True:
                task = store.dequeue_task()
                if task is None:
                    break
                with lock:
                    claimed.append(task["id"])

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(claimed) == 20
        assert len(set(claimed)) == 20
        # The highest priority tasks should appear in the first half
        first_half = set(claimed[:10])
        high_prio = {f"task_{i:02d}" for i in range(10, 20)}
        overlap = first_half & high_prio
        assert len(overlap) >= 7, f"Expected most high-prio in first half, got {len(overlap)}"

    def test_scheduled_task_not_dequeued_early(self, store):
        """Tasks with future scheduled_at should not be dequeued."""
        store.enqueue_task("future", "spec_a", priority=10, scheduled_at="2099-01-01T00:00:00")
        store.enqueue_task("now", "spec_b", priority=1)

        task = store.dequeue_task()
        assert task["id"] == "now"
        # Future task should still be pending
        assert store.dequeue_task() is None
