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
    assert job is not None and job.attempts == 0
    queue.fail(job.job_id, "boom")
    retried = queue.claim("curate")
    assert retried is not None
    assert retried.attempts == 1


def test_queue_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "ambient.sqlite3"
    AmbientQueue(path).enqueue("ingest", "poll_source", {})
    assert AmbientQueue(path).depth("ingest") == 1
