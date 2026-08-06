from __future__ import annotations

from pathlib import Path

from autocontext.ambient.queue import AmbientQueue


def test_enqueue_claim_complete(tmp_path: Path) -> None:
    queue = AmbientQueue(tmp_path / "ambient.sqlite3")
    job_id = queue.enqueue("ingest", "poll_source", {"source": "native"})
    assert queue.depth("ingest") == 1
    job = queue.claim("ingest")
    assert job is not None
    assert job.job_id == job_id
    assert job.payload == {"source": "native"}
    assert queue.depth("ingest") == 0
    queue.complete(job.job_id)
    assert queue.claim("ingest") is None


def test_claim_is_stage_scoped(tmp_path: Path) -> None:
    queue = AmbientQueue(tmp_path / "ambient.sqlite3")
    queue.enqueue("train", "run_target", {"target": "t1"})
    assert queue.claim("ingest") is None
    assert queue.claim("train") is not None


def test_fail_returns_job_to_pending_with_attempts(tmp_path: Path) -> None:
    queue = AmbientQueue(tmp_path / "ambient.sqlite3")
    queue.enqueue("curate", "rebuild", {})
    job = queue.claim("curate")
    # AC-906: attempts is burned at claim so crash-loops reach the cap
    assert job is not None and job.attempts == 1
    queue.fail(job.job_id, "boom")
    retried = queue.claim("curate")
    assert retried is not None
    assert retried.attempts == 2


def test_queue_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "ambient.sqlite3"
    AmbientQueue(path).enqueue("ingest", "poll_source", {})
    assert AmbientQueue(path).depth("ingest") == 1


def test_claim_never_double_claims(tmp_path: Path) -> None:
    path = tmp_path / "ambient.sqlite3"
    queue_a = AmbientQueue(path)
    queue_b = AmbientQueue(path)
    queue_a.enqueue("ingest", "poll_source", {})
    first = queue_a.claim("ingest")
    second = queue_b.claim("ingest")
    assert first is not None
    assert second is None


def test_requeue_stale_running(tmp_path: Path) -> None:
    queue = AmbientQueue(tmp_path / "ambient.sqlite3")
    queue.enqueue("train", "run_target", {})
    assert queue.claim("train") is not None
    assert queue.depth("train") == 0
    assert queue.requeue_stale_running() == 1
    assert queue.depth("train") == 1
    assert queue.claim("train") is not None


def test_fail_requeues_below_max_attempts(tmp_path: Path) -> None:
    queue = AmbientQueue(tmp_path / "q.sqlite3")
    job_id = queue.enqueue("train", "run", {})
    claimed = queue.claim("train")
    assert claimed is not None
    queue.fail(job_id, "boom", max_attempts=3)
    # back to pending, claimable again
    assert queue.depth("train") == 1
    assert queue.dead_letter_count() == 0


def test_fail_dead_letters_at_max_attempts(tmp_path: Path) -> None:
    queue = AmbientQueue(tmp_path / "q.sqlite3")
    job_id = queue.enqueue("train", "run", {})
    for _ in range(3):
        queue.claim("train")
        queue.fail(job_id, "boom", max_attempts=3)
    # third failure reaches the cap: dead, not pending
    assert queue.depth("train") == 0
    assert queue.dead_letter_count() == 1
    assert queue.claim("train") is None


def test_crash_loop_reaches_dead_letter_without_fail(tmp_path: Path) -> None:
    """AC-906: a handler that kills the process never calls fail(); claims
    alone must burn the budget so requeue+reclaim cycles cannot loop forever."""
    queue = AmbientQueue(tmp_path / "q.sqlite3")
    queue.enqueue("train", "run", {})
    for _ in range(3):
        claimed = queue.claim("train", max_attempts=3)
        assert claimed is not None
        # simulate crash: no complete/fail, daemon restart requeues
        queue.requeue_stale_running()
    # budget exhausted purely via claims: next claim dead-letters it
    assert queue.claim("train", max_attempts=3) is None
    assert queue.dead_letter_count() == 1
