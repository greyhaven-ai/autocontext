from __future__ import annotations

import multiprocessing
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from autocontext.execution.campaign_scheduler import (
    CallableCampaignWorker,
    CampaignAssignment,
    CampaignEvidenceGrant,
    CampaignJobRequest,
    CampaignJobResult,
    CampaignScheduler,
    CampaignSchedulerEventStore,
    EvaluationLaneIdentity,
    RemoteCampaignWorker,
    SchedulerBudget,
    SchedulerResources,
    StaleCampaignSchedulerError,
    WorkerDescriptor,
)
from autocontext.execution.remote_execution import (
    RemoteCleanupOutcome,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteResourceUsage,
)

LANE = EvaluationLaneIdentity(
    lane_id="matched-main",
    fixture_digest="fixture-sha256",
    seeds=("7", "11"),
    evaluator_epoch="epoch-1",
    verifier_contract_ref="verifier-v1",
)


def _job(index: int, **overrides: object) -> CampaignJobRequest:
    values: dict[str, object] = {
        "job_id": f"job-{index}",
        "idempotency_key": f"idem-{index}",
        "campaign_id": "campaign-1",
        "branch_id": "branch-a",
        "job_kind": "trial",
        "lane": LANE,
        "reservation": SchedulerBudget(tokens=10, wall_seconds=5, compute_units=1, jobs=1),
    }
    values.update(overrides)
    return CampaignJobRequest(**values)  # type: ignore[arg-type]


def _worker(worker_id: str, **overrides: object) -> WorkerDescriptor:
    values: dict[str, object] = {
        "worker_id": worker_id,
        "runtime": "python-3.13",
        "resources": SchedulerResources(cpu_cores=2, memory_gb=8, disk_gb=20),
        "concurrency": 1,
        "environment_labels": {"image": "research-v1"},
    }
    values.update(overrides)
    return WorkerDescriptor(**values)  # type: ignore[arg-type]


def _success(assignment: CampaignAssignment) -> CampaignJobResult:
    return CampaignJobResult(
        outcome="candidate_success",
        consumed=SchedulerBudget(tokens=8, wall_seconds=1, compute_units=0.5, jobs=1),
        output_ref=f"artifact://{assignment.job.job_id}",
    )


def _race_scheduler_writer(
    path: str,
    index: int,
    ready: Any,
    start: Any,
    outcomes: Any,
) -> None:
    """Process target for exercising the event-store compare-and-append boundary."""

    scheduler = CampaignScheduler(CampaignSchedulerEventStore(path))
    ready.put(index)
    if not start.wait(timeout=10):
        outcomes.put(("timeout", index))
        return
    try:
        scheduler.configure_campaign(f"campaign-{index}", SchedulerBudget(jobs=1))
    except StaleCampaignSchedulerError:
        outcomes.put(("stale", index))
    else:
        outcomes.put(("appended", index))


def test_two_local_workers_dispatch_with_bounded_concurrency(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"), max_concurrency=2)
    active = 0
    peak = 0
    lock = threading.Lock()

    def execute(assignment: CampaignAssignment) -> CampaignJobResult:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _success(assignment)

    scheduler.register_worker(_worker("local-1"), CallableCampaignWorker(execute))
    scheduler.register_worker(_worker("local-2"), CallableCampaignWorker(execute))
    for index in range(4):
        scheduler.enqueue(_job(index))

    dispatched = scheduler.run_until_idle()
    report = scheduler.report()

    assert dispatched == 4
    assert peak == 2
    assert report.succeeded == 4
    assert report.running == 0
    assert sum(int(worker["completed_jobs"]) for worker in report.worker_utilization.values()) == 4


def test_expired_lease_reconciles_after_restart_without_double_counting(tmp_path: Path) -> None:
    now = [100.0]
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    first = CampaignScheduler(store, lease_seconds=10, clock=lambda: now[0])
    first.configure_campaign("campaign-1", SchedulerBudget(tokens=100, wall_seconds=100, compute_units=10, jobs=5))
    first.register_worker(_worker("lost-worker"))
    first.enqueue(_job(1, max_attempts=2))
    old_assignment = first.claim("lost-worker")[0]
    first.heartbeat("lost-worker", [old_assignment.lease.lease_id])

    now[0] = 121.0
    restarted = CampaignScheduler(store, lease_seconds=10, clock=lambda: now[0])
    assert restarted.reconcile() == ("job-1",)
    restarted.register_worker(_worker("replacement"), CallableCampaignWorker(_success))
    assert restarted.run_until_idle() == 1

    final = restarted.complete(old_assignment.lease.lease_id, CampaignJobResult(outcome="candidate_failure"))
    report = restarted.report()

    assert final.outcome == "candidate_success"
    assert restarted.job_status("job-1") == "succeeded"
    assert report.consumed_by_campaign["campaign-1"].jobs == 1
    assert report.retries == 1


def test_scheduler_respects_capabilities_resources_budgets_and_comparable_lanes(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.configure_campaign(
        "campaign-1",
        SchedulerBudget(tokens=20, wall_seconds=20, compute_units=5, jobs=2),
    )
    scheduler.register_worker(_worker("cpu"))
    scheduler.register_worker(
        _worker(
            "gpu",
            resources=SchedulerResources(
                cpu_cores=4,
                memory_gb=32,
                disk_gb=100,
                accelerator_kind="A100",
                accelerator_count=1,
            ),
            capabilities=frozenset({"cuda"}),
        )
    )
    gpu_job = _job(
        1,
        resources=SchedulerResources(
            cpu_cores=2,
            memory_gb=16,
            disk_gb=20,
            accelerator_kind="A100",
            accelerator_count=1,
        ),
        required_capabilities=frozenset({"cuda"}),
        cohort_id="cohort-1",
    )
    scheduler.enqueue(gpu_job)

    assert scheduler.claim("cpu") == ()
    assignment = scheduler.claim("gpu")[0]
    assert assignment.lease.environment_fingerprint == _worker(
        "gpu",
        resources=SchedulerResources(
            cpu_cores=4,
            memory_gb=32,
            disk_gb=100,
            accelerator_kind="A100",
            accelerator_count=1,
        ),
        capabilities=frozenset({"cuda"}),
    ).environment_fingerprint
    scheduler.complete(assignment.lease.lease_id, _success(assignment))

    over_budget = _job(2, reservation=SchedulerBudget(tokens=30, jobs=1))
    scheduler.enqueue(over_budget)
    assert scheduler.claim("cpu") == ()
    assert scheduler.job_status("job-2") == "budget_exhausted"

    changed_lane = EvaluationLaneIdentity(
        lane_id="matched-main",
        fixture_digest="different-fixture",
        seeds=("7", "11"),
        evaluator_epoch="epoch-1",
        verifier_contract_ref="verifier-v1",
    )
    with pytest.raises(ValueError, match="same evaluation lane"):
        scheduler.enqueue(_job(3, cohort_id="cohort-1", lane=changed_lane))


def test_warm_affinity_is_capability_driven_and_branch_scoped(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    assignments: list[CampaignAssignment] = []

    def execute(assignment: CampaignAssignment) -> CampaignJobResult:
        assignments.append(assignment)
        return _success(assignment)

    scheduler.register_worker(
        _worker("warm", sandbox_features=frozenset({"warm", "snapshot", "session_reuse"})),
        CallableCampaignWorker(execute),
    )
    scheduler.enqueue(_job(1, cohort_id="pair", branch_id="branch-a", prefer_warm_reuse=True))
    scheduler.enqueue(_job(2, cohort_id="pair", branch_id="branch-b", prefer_warm_reuse=True))

    scheduler.run_until_idle()

    assert [assignment.lease.lifecycle for assignment in assignments] == ["warm_snapshot", "warm_snapshot"]
    assert assignments[0].lease.reuse_key != assignments[1].lease.reuse_key
    assert ":branch-a:" in assignments[0].lease.reuse_key
    assert ":branch-b:" in assignments[1].lease.reuse_key


def test_cold_degradation_and_explicit_evidence_sharing_are_auditable(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store)
    scheduler.configure_campaign(
        "campaign-1",
        SchedulerBudget(tokens=100, jobs=10, shared_evidence_tokens=5),
    )
    scheduler.grant_evidence_share(
        CampaignEvidenceGrant(
            grant_id="share-1",
            campaign_id="campaign-1",
            from_branch_id="branch-a",
            to_branch_id="branch-b",
            evidence_ref="artifact://finding-1",
            token_cost=5,
        )
    )
    scheduler.register_worker(_worker("cold"))
    scheduler.enqueue(
        _job(
            1,
            branch_id="branch-b",
            prefer_warm_reuse=True,
            evidence_grant_ids=("share-1",),
        )
    )

    assignment = scheduler.claim("cold")[0]
    event_types = [event.event_type for event in store.read()]

    assert assignment.lease.lifecycle == "ephemeral_per_eval"
    assert assignment.lease.reuse_key.startswith("campaign-1:branch-b:")
    assert "warm_degraded" in event_types
    assert "evidence_granted" in event_types
    with pytest.raises(PermissionError, match="not scoped"):
        scheduler.enqueue(_job(2, branch_id="branch-c", evidence_grant_ids=("share-1",)))
    with pytest.raises(RuntimeError, match="budget exhausted"):
        scheduler.grant_evidence_share(
            CampaignEvidenceGrant("share-2", "campaign-1", "branch-a", "branch-b", "artifact://finding-2", 1)
        )


class _FakeRemoteAdapter:
    def execute_request(self, request: RemoteExecutionRequest) -> RemoteExecutionResult:
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider="fake-remote",
            status="success",
            usage=RemoteResourceUsage(wall_seconds=2.0, cpu_seconds=1.5),
            cleanup=RemoteCleanupOutcome(True, True, "remote-1"),
            session_id="remote-1",
        )


def test_optional_remote_adapter_integrates_without_provider_specific_scheduler_code(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    remote_worker = RemoteCampaignWorker(
        _FakeRemoteAdapter(),
        lambda assignment: RemoteExecutionRequest(
            task_id=assignment.job.job_id,
            image="research:latest",
            command="python task.py",
        ),
    )
    scheduler.register_worker(
        _worker("remote", locality="remote", capabilities=frozenset({"remote_execution"})),
        remote_worker,
    )
    scheduler.enqueue(_job(1, required_capabilities=frozenset({"remote_execution"})))

    scheduler.run_until_idle()
    result = scheduler.job_result("job-1")
    report = scheduler.report()

    assert result and result.outcome == "candidate_success"
    assert result.metadata["provider"] == "fake-remote"
    assert report.worker_utilization["remote"]["locality"] == "remote"
    assert report.consumed_by_campaign["campaign-1"].wall_seconds == 2.0


def test_duplicate_enqueue_and_event_checksum_are_fail_closed(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store)
    scheduler.enqueue(_job(1))

    duplicate = _job(2, idempotency_key="idem-1")
    assert scheduler.enqueue(duplicate) == "job-1"
    assert len(store.read()) == 1

    path = tmp_path / "events.jsonl"
    path.write_text(path.read_text().replace("job-1", "job-x", 1))
    with pytest.raises(ValueError, match="checksum mismatch"):
        CampaignSchedulerEventStore(path).read()


def test_duplicate_job_id_cannot_replace_a_leased_job_or_leak_its_reservation(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.configure_campaign("campaign-1", SchedulerBudget(tokens=100, jobs=10))
    scheduler.register_worker(_worker("local"))
    scheduler.enqueue(_job(1))
    assignment = scheduler.claim("local")[0]

    with pytest.raises(ValueError, match="job_id already exists"):
        scheduler.enqueue(_job(1, idempotency_key="different-key", branch_id="branch-b"))

    assert scheduler.job_status("job-1") == "leased"
    assert scheduler.report().reserved_by_campaign["campaign-1"] == SchedulerBudget(
        tokens=10,
        wall_seconds=5,
        compute_units=1,
        jobs=1,
    )
    scheduler.complete(assignment.lease.lease_id, _success(assignment))
    assert scheduler.report().reserved_by_campaign["campaign-1"] == SchedulerBudget()


def test_invalid_worker_outcome_is_rejected_before_it_can_poison_the_event_log(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store)
    scheduler.register_worker(_worker("local"))
    scheduler.enqueue(_job(1))
    scheduler.claim("local")
    event_count = scheduler.report().events

    with pytest.raises(ValueError, match="unsupported campaign job outcome"):
        CampaignJobResult(outcome="bogus")  # type: ignore[arg-type]

    assert scheduler.report().events == event_count
    assert CampaignScheduler(store).job_status("job-1") == "leased"


def test_retried_infrastructure_attempt_charges_actual_usage(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store)
    scheduler.configure_campaign("campaign-1", SchedulerBudget(tokens=100, jobs=10))
    scheduler.register_worker(_worker("local"))
    scheduler.enqueue(_job(1, max_attempts=2))
    first = scheduler.claim("local")[0]

    failed = CampaignJobResult(
        outcome="infrastructure_failure",
        consumed=SchedulerBudget(tokens=7, wall_seconds=2, compute_units=0.25, jobs=1),
        detail="provider timeout",
    )
    scheduler.complete(first.lease.lease_id, failed)

    report = scheduler.report()
    assert scheduler.job_status("job-1") == "queued"
    assert report.consumed_by_campaign["campaign-1"] == failed.consumed
    assert report.reserved_by_campaign["campaign-1"] == SchedulerBudget()
    assert report.worker_utilization["local"]["failed_jobs"] == 1
    assert report.worker_utilization["local"]["consumed"] == {
        "tokens": 7,
        "wall_seconds": 2,
        "compute_units": 0.25,
        "jobs": 1,
        "shared_evidence_tokens": 0,
    }

    second = scheduler.claim("local")[0]
    scheduler.complete(second.lease.lease_id, _success(second))
    assert scheduler.report().consumed_by_campaign["campaign-1"] == SchedulerBudget(
        tokens=15,
        wall_seconds=3,
        compute_units=0.75,
        jobs=2,
    )
    assert CampaignScheduler(store).report().consumed_by_campaign["campaign-1"] == SchedulerBudget(
        tokens=15,
        wall_seconds=3,
        compute_units=0.75,
        jobs=2,
    )


def test_direct_claim_honors_global_concurrency_limit(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(
        CampaignSchedulerEventStore(tmp_path / "events.jsonl"),
        max_concurrency=1,
    )
    scheduler.register_worker(_worker("worker-a"))
    scheduler.register_worker(_worker("worker-b"))
    scheduler.enqueue(_job(1))
    scheduler.enqueue(_job(2))

    first = scheduler.claim("worker-a")[0]
    assert scheduler.claim("worker-b") == ()

    scheduler.complete(first.lease.lease_id, _success(first))
    assert scheduler.claim("worker-b")[0].job.job_id == "job-2"


def test_temporary_reservation_pressure_keeps_job_queued(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.configure_campaign("campaign-1", SchedulerBudget(tokens=10, jobs=2))
    scheduler.register_worker(_worker("worker-a"))
    scheduler.register_worker(_worker("worker-b"))
    scheduler.enqueue(_job(1, reservation=SchedulerBudget(tokens=10, jobs=1)))
    scheduler.enqueue(_job(2, reservation=SchedulerBudget(tokens=10, jobs=1)))

    first = scheduler.claim("worker-a")[0]
    assert scheduler.claim("worker-b") == ()
    assert scheduler.job_status("job-2") == "queued"

    scheduler.complete(
        first.lease.lease_id,
        CampaignJobResult(outcome="candidate_success", consumed=SchedulerBudget(jobs=1)),
    )
    assert scheduler.claim("worker-b")[0].job.job_id == "job-2"


def test_cancel_during_dispatch_ignores_late_worker_completion(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    started = threading.Event()
    release = threading.Event()

    def execute(assignment: CampaignAssignment) -> CampaignJobResult:
        started.set()
        assert release.wait(timeout=2)
        return _success(assignment)

    scheduler.register_worker(_worker("local"), CallableCampaignWorker(execute))
    scheduler.enqueue(_job(1))
    dispatched: list[int] = []
    dispatch_thread = threading.Thread(target=lambda: dispatched.append(scheduler.dispatch_once()))
    dispatch_thread.start()
    assert started.wait(timeout=2)

    assert scheduler.cancel("job-1") is True
    release.set()
    dispatch_thread.join(timeout=2)

    assert not dispatch_thread.is_alive()
    assert dispatched == [1]
    assert scheduler.job_status("job-1") == "canceled"
    assert scheduler.report().running == 0


def test_concurrent_evidence_events_have_contiguous_replayable_sequences(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store)
    scheduler.configure_campaign("campaign-1", SchedulerBudget(shared_evidence_tokens=100))
    barrier = threading.Barrier(16)

    def grant(index: int) -> None:
        barrier.wait()
        scheduler.grant_evidence_share(
            CampaignEvidenceGrant(
                grant_id=f"share-{index}",
                campaign_id="campaign-1",
                from_branch_id="source",
                to_branch_id="target",
                evidence_ref=f"artifact://finding-{index}",
                token_cost=1,
            )
        )

    threads = [threading.Thread(target=grant, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert [event.sequence for event in store.read()] == list(range(1, 18))
    restarted = CampaignScheduler(store)
    assert restarted.report().consumed_by_campaign["campaign-1"].shared_evidence_tokens == 16


def test_independent_scheduler_instances_compare_and_append_without_corrupting_replay(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    schedulers = [
        CampaignScheduler(CampaignSchedulerEventStore(path)),
        CampaignScheduler(CampaignSchedulerEventStore(path)),
    ]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def configure(index: int) -> None:
        barrier.wait()
        try:
            schedulers[index].configure_campaign(f"campaign-{index}", SchedulerBudget(jobs=1))
        except StaleCampaignSchedulerError:
            outcome = "stale"
        else:
            outcome = "appended"
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=configure, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["appended", "stale"]
    events = CampaignSchedulerEventStore(path).read()
    assert [event.sequence for event in events] == [1]
    assert CampaignScheduler(CampaignSchedulerEventStore(path)).report().events == 1


def test_cross_process_scheduler_writers_compare_and_append_without_corrupting_replay(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    outcomes = context.Queue()
    processes = [
        context.Process(
            target=_race_scheduler_writer,
            args=(str(path), index, ready, start, outcomes),
        )
        for index in range(2)
    ]

    for process in processes:
        process.start()
    try:
        assert {ready.get(timeout=15) for _ in processes} == {0, 1}
        start.set()
        results = [outcomes.get(timeout=15) for _ in processes]
    finally:
        start.set()
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(result[0] for result in results) == ["appended", "stale"]
    events = CampaignSchedulerEventStore(path).read()
    assert [event.sequence for event in events] == [1]
    assert CampaignScheduler(CampaignSchedulerEventStore(path)).report().events == 1
