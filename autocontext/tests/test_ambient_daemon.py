from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.daemon import AmbientDaemon
from autocontext.ambient.proposals import ProposalStore
from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.stage import STAGE_NAMES, StageContext, StageResult
from autocontext.harness.core.events import EventStreamEmitter


def _charter() -> Charter:
    return Charter(
        tier="oss",
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=[
            CharterTarget(
                name="t1",
                kind="role",
                selector="competitor@grid_ctf",
                base_model="Qwen/Qwen2.5-3B-Instruct",
                min_dataset_records=10,
                eval_suite="grid_ctf_holdout",
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=1.0, window_hours=24, disk_quota_gb=10.0),
    )


def _daemon(tmp_path: Path, stages: dict[str, object] | None = None, threshold: int = 3) -> AmbientDaemon:
    return AmbientDaemon(
        charter=_charter(),
        queue=AmbientQueue(tmp_path / "ambient.sqlite3"),
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        stages=stages,  # type: ignore[arg-type]
        breaker_threshold=threshold,
    )


@dataclass
class ExplodingStage:
    name: str

    def run_once(self, ctx: StageContext) -> StageResult:
        raise RuntimeError("stage exploded")


@dataclass
class CountingStage:
    name: str
    runs: int = 0

    def run_once(self, ctx: StageContext) -> StageResult:
        self.runs += 1
        return StageResult(processed=1)


def test_default_daemon_has_five_noop_stages(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    assert set(daemon.status().keys()) == set(STAGE_NAMES)


def test_run_cycle_isolates_stage_exceptions(tmp_path: Path) -> None:
    counting = CountingStage(name="curate")
    stages = {"ingest": ExplodingStage(name="ingest"), "curate": counting}
    daemon = _daemon(tmp_path, stages=stages)
    results = daemon.run_cycle()
    assert counting.runs == 1
    assert results["curate"] == StageResult(processed=1)
    assert "ingest" not in results or results["ingest"].errors >= 1


def test_breaker_pauses_stage_and_others_continue(tmp_path: Path) -> None:
    counting = CountingStage(name="curate")
    daemon = _daemon(tmp_path, stages={"ingest": ExplodingStage(name="ingest"), "curate": counting}, threshold=2)
    for _ in range(3):
        daemon.run_cycle()
    assert daemon.status()["ingest"]["paused"] is True
    assert counting.runs == 3


def test_run_stage_once_manual_lever_resets_breaker(tmp_path: Path) -> None:
    counting = CountingStage(name="ingest")
    daemon = _daemon(tmp_path, stages={"ingest": ExplodingStage(name="ingest")}, threshold=1)
    daemon.run_cycle()
    assert daemon.status()["ingest"]["paused"] is True
    daemon._stages["ingest"] = counting  # simulate operator fixing the source
    result = daemon.run_stage_once("ingest")
    assert result == StageResult(processed=1)
    assert daemon.status()["ingest"]["paused"] is False


def test_run_stage_once_unknown_stage_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        _daemon(tmp_path).run_stage_once("nonsense")


def test_run_forever_requeues_stale_running_jobs_before_first_cycle(tmp_path: Path) -> None:
    queue = AmbientQueue(tmp_path / "ambient.sqlite3")
    queue.enqueue("ingest", "poll_source", {})
    assert queue.claim("ingest") is not None  # simulate a crash mid-job
    daemon = AmbientDaemon(
        charter=_charter(),
        queue=queue,
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
    )
    # construction is side-effect free: the in-flight job stays running
    assert daemon.status()["ingest"]["queue_depth"] == 0
    daemon.run_forever(poll_seconds=0.0, max_cycles=0)
    assert daemon.status()["ingest"]["queue_depth"] == 1


def test_run_forever_respects_max_cycles(tmp_path: Path) -> None:
    counting = CountingStage(name="curate")
    daemon = _daemon(tmp_path, stages={"curate": counting})
    daemon.run_forever(poll_seconds=0.0, max_cycles=3)
    assert counting.runs == 3


@dataclass
class ErrorReportingStage:
    name: str

    def run_once(self, ctx: StageContext) -> StageResult:
        return StageResult(processed=0, errors=1)


def test_returned_errors_trip_breaker_and_emit_stage_paused(tmp_path: Path) -> None:
    events_path = tmp_path / "events.ndjson"
    daemon = AmbientDaemon(
        charter=_charter(),
        queue=AmbientQueue(tmp_path / "ambient.sqlite3"),
        emitter=EventStreamEmitter(events_path),
        stages={"ingest": ErrorReportingStage(name="ingest")},
        breaker_threshold=2,
    )
    daemon.run_cycle()
    daemon.run_cycle()
    assert daemon.status()["ingest"]["paused"] is True
    contents = events_path.read_text(encoding="utf-8")
    assert contents.count("stage_paused") == 1


def test_run_forever_rejects_negative_poll_seconds(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    with pytest.raises(ValueError, match="zero or positive"):
        daemon.run_forever(poll_seconds=-1.0)


def test_second_resident_daemon_refuses_to_start(tmp_path: Path) -> None:
    import fcntl

    queue = AmbientQueue(tmp_path / "ambient.sqlite3")
    daemon = AmbientDaemon(
        charter=_charter(),
        queue=queue,
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
    )
    lock_path = queue.db_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = lock_path.open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)  # first daemon holds the lock
    try:
        with pytest.raises(RuntimeError, match="another ambient daemon"):
            daemon.run_forever(poll_seconds=0.0, max_cycles=0)
    finally:
        holder.close()


def test_lock_releases_after_run_forever(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    daemon.run_forever(poll_seconds=0.0, max_cycles=1)
    daemon.run_forever(poll_seconds=0.0, max_cycles=1)  # second sequential run acquires cleanly


def test_daemon_threads_proposal_store_into_stage_context(tmp_path: Path) -> None:
    seen: list[object] = []

    @dataclass(slots=True)
    class Capture:
        name: str = "advise"

        def run_once(self, ctx: StageContext) -> StageResult:
            seen.append(ctx.proposal_store)
            return StageResult()

    store = ProposalStore(tmp_path / "proposals.jsonl")
    daemon = AmbientDaemon(
        charter=_charter(),
        queue=AmbientQueue(tmp_path / "ambient.sqlite3"),
        emitter=EventStreamEmitter(tmp_path / "events.ndjson"),
        stages={"advise": Capture()},
        proposal_store=store,
    )
    daemon.run_stage_once("advise")
    assert seen == [store]
