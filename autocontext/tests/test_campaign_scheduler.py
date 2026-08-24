from __future__ import annotations

import multiprocessing
import threading
import time
from collections.abc import Sequence
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


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (SchedulerResources, {"cpu_cores": float("nan")}),
        (SchedulerResources, {"memory_gb": float("inf")}),
        (SchedulerResources, {"disk_gb": True}),
        (SchedulerResources, {"accelerator_count": 1.5}),
        (SchedulerResources, {"accelerator_kind": 7}),
        (SchedulerBudget, {"tokens": True}),
        (SchedulerBudget, {"wall_seconds": float("nan")}),
        (SchedulerBudget, {"compute_units": float("inf")}),
        (SchedulerBudget, {"jobs": 1.5}),
        (SchedulerBudget, {"shared_evidence_tokens": False}),
    ],
)
def test_scheduler_resources_and_budgets_reject_nonfinite_or_coerced_values(
    factory: Any,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory(**kwargs)


@pytest.mark.parametrize(
    ("lease_seconds", "max_concurrency"),
    [(float("nan"), 1), (float("inf"), 1), (1.0, True), (1.0, 1.5)],
)
def test_scheduler_rejects_nonfinite_lease_and_coerced_concurrency(
    tmp_path: Path,
    lease_seconds: object,
    max_concurrency: object,
) -> None:
    with pytest.raises(ValueError):
        CampaignScheduler(
            CampaignSchedulerEventStore(tmp_path / "events.jsonl"),
            lease_seconds=lease_seconds,  # type: ignore[arg-type]
            max_concurrency=max_concurrency,  # type: ignore[arg-type]
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
    assert report.consumed_by_campaign["campaign-1"].jobs == 2
    assert report.late_completions == 1
    restarted.complete(old_assignment.lease.lease_id, CampaignJobResult(outcome="candidate_failure"))
    assert restarted.report().consumed_by_campaign["campaign-1"].jobs == 2
    assert report.retries == 1


def test_run_until_idle_waits_for_replayed_orphan_lease_then_retries(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    first = CampaignScheduler(store, lease_seconds=0.05)
    first.register_worker(_worker("restarted"))
    first.enqueue(_job(1, max_attempts=2))
    orphan = first.claim("restarted")[0]

    restarted = CampaignScheduler(store, lease_seconds=0.05)
    restarted.register_worker(_worker("restarted"), CallableCampaignWorker(_success))
    started = time.monotonic()

    assert restarted.run_until_idle(poll_interval=0.005, timeout_seconds=1.0) == 1
    assert time.monotonic() - started >= 0.03
    assert restarted.job_status(orphan.job.job_id) == "succeeded"
    assert restarted.report().running == 0
    assert restarted.report().retries == 1


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
    assert (
        assignment.lease.environment_fingerprint
        == _worker(
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
    )
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


class _ReusingRemoteAdapter(_FakeRemoteAdapter):
    def __init__(self) -> None:
        self.batches: list[tuple[RemoteExecutionRequest, ...]] = []

    def execute_requests(self, requests: tuple[RemoteExecutionRequest, ...]) -> tuple[RemoteExecutionResult, ...]:
        self.batches.append(requests)
        return tuple(self.execute_request(request) for request in requests)


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


def test_matched_remote_trials_use_one_advertised_reuse_batch(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    adapter = _ReusingRemoteAdapter()
    scheduler.register_worker(
        _worker(
            "remote-reuse",
            locality="remote",
            concurrency=2,
            capabilities=frozenset({"remote_execution"}),
            sandbox_features=frozenset({"session_reuse"}),
        ),
        RemoteCampaignWorker(
            adapter,
            lambda assignment: RemoteExecutionRequest(
                task_id=assignment.job.job_id,
                image="research:latest",
                command="python task.py",
            ),
        ),
    )
    for index in range(2):
        scheduler.enqueue(
            _job(
                index,
                cohort_id="matched-pair",
                prefer_warm_reuse=True,
                required_capabilities=frozenset({"remote_execution"}),
            )
        )

    assert scheduler.run_until_idle() == 2

    assert len(adapter.batches) == 1
    assert len(adapter.batches[0]) == 2
    assert {request.lifecycle for request in adapter.batches[0]} == {"reuse_matched_trials"}
    assert {request.max_reuse_tasks for request in adapter.batches[0]} == {2}


def test_live_service_claims_late_jobs_and_heartbeats_active_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store, lease_seconds=0.03, clock=lambda: now[0])
    worker_started = threading.Event()
    release_worker = threading.Event()
    first_claim_seen = threading.Event()
    heartbeat_seen = threading.Event()

    def execute(assignment: CampaignAssignment) -> CampaignJobResult:
        worker_started.set()
        if not release_worker.wait(timeout=2):
            raise TimeoutError("test did not release campaign worker")
        return _success(assignment)

    original_claim = scheduler.claim

    def observed_claim(worker_id: str, *, limit: int | None = None) -> tuple[CampaignAssignment, ...]:
        assignments = original_claim(worker_id, limit=limit)
        if assignments and not first_claim_seen.is_set():
            first_claim_seen.set()
            now[0] = 100.02
        return assignments

    original_heartbeat = scheduler.heartbeat

    def observed_heartbeat(worker_id: str, lease_ids: Sequence[str] = ()) -> None:
        original_heartbeat(worker_id, lease_ids)
        if lease_ids:
            heartbeat_seen.set()

    monkeypatch.setattr(scheduler, "claim", observed_claim)
    monkeypatch.setattr(scheduler, "heartbeat", observed_heartbeat)
    scheduler.register_worker(_worker("live"), CallableCampaignWorker(execute))
    stop = threading.Event()
    dispatched: list[int] = []
    service_errors: list[BaseException] = []

    def run_service() -> None:
        try:
            dispatched.append(scheduler.serve(stop, poll_interval=0.01))
        except BaseException as exc:
            service_errors.append(exc)

    service = threading.Thread(target=run_service)
    service.start()
    try:
        scheduler.enqueue(_job(1))
        assert worker_started.wait(timeout=2)
        assert heartbeat_seen.wait(timeout=2)
        now[0] = 100.04
        assert scheduler.reconcile() == ()
        assert scheduler.job_status("job-1") == "leased"
        release_worker.set()
        deadline = time.monotonic() + 2
        while scheduler.job_status("job-1") != "succeeded" and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        release_worker.set()
        stop.set()
        service.join(timeout=2)

    assert not service.is_alive()
    assert service_errors == []
    assert dispatched == [1]
    assert scheduler.job_status("job-1") == "succeeded"
    assert "worker_heartbeat" in [event.event_type for event in store.read()]


def test_duplicate_enqueue_and_event_checksum_are_fail_closed(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store)
    scheduler.enqueue(_job(1))

    duplicate = _job(2, idempotency_key="idem-1")
    with pytest.raises(ValueError, match="idempotency key conflicts"):
        scheduler.enqueue(duplicate)
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


def test_live_scheduler_runs_integrity_and_final_audit_checkpoints(tmp_path: Path) -> None:
    class DurableRecord:
        def to_dict(self) -> dict[str, object]:
            return {"audit": {"audit_id": "audit-1"}, "dispositions": [{"outcome": "mitigated"}]}

    class DurableStore:
        def records(self, campaign_id: str) -> tuple[DurableRecord, ...]:
            assert campaign_id == "campaign-1"
            return (DurableRecord(),)

    class Auditor:
        store = DurableStore()

    class AuditRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []
            self.auditor = Auditor()

        def review_checkpoint(self, checkpoint, evidence, *, cancellation_event=None):
            del cancellation_event
            self.calls.append((checkpoint, dict(evidence)))
            return None

    audits = AuditRunner()
    scheduler = CampaignScheduler(
        CampaignSchedulerEventStore(tmp_path / "events.jsonl"),
        audit_checkpoints=audits,
    )
    scheduler.register_worker(
        _worker("broken"),
        CallableCampaignWorker(
            lambda assignment: CampaignJobResult(
                outcome="infrastructure_failure",
                detail=f"sandbox failed for {assignment.job.job_id}",
                cleanup_succeeded=False,
            )
        ),
    )
    scheduler.enqueue(_job(1, max_attempts=1))

    assert scheduler.run_until_idle() == 1

    assert [checkpoint for checkpoint, _ in audits.calls] == [
        "integrity_alert",
        "final_completion",
    ]
    final = audits.calls[-1][1]
    assert final["jobs"][0]["status"] == "infrastructure_failed"
    assert final["jobs"][0]["scored_result"]["outcome"] == "infrastructure_failure"
    assert scheduler.report().audit_records_by_campaign["campaign-1"][0]["dispositions"] == [{"outcome": "mitigated"}]


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
    assert scheduler.job_result("job-1") is None
    assert scheduler.report().running == 0
    assert scheduler.report().late_completions == 1
    assert scheduler.report().consumed_by_campaign["campaign-1"].jobs == 1

    restarted = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    assert restarted.job_status("job-1") == "canceled"
    assert restarted.job_result("job-1") is None
    assert restarted.report().late_completions == 1
    assert restarted.report().consumed_by_campaign["campaign-1"].jobs == 1


def test_cancel_invokes_worker_termination_hook_and_accounts_final_usage(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    started = threading.Event()
    terminated = threading.Event()

    def execute(assignment: CampaignAssignment) -> CampaignJobResult:
        started.set()
        assert terminated.wait(timeout=2)
        return CampaignJobResult(
            outcome="infrastructure_failure",
            consumed=SchedulerBudget(wall_seconds=0.1, jobs=1),
            detail="canceled by scheduler",
        )

    def cancel(assignment: CampaignAssignment) -> bool:
        assert assignment.job.job_id == "job-1"
        terminated.set()
        return True

    scheduler.register_worker(_worker("local"), CallableCampaignWorker(execute, cancel=cancel))
    scheduler.enqueue(_job(1))
    dispatch_thread = threading.Thread(target=scheduler.dispatch_once)
    dispatch_thread.start()
    assert started.wait(timeout=2)

    assert scheduler.cancel("job-1") is True
    dispatch_thread.join(timeout=2)

    assert terminated.is_set()
    assert not dispatch_thread.is_alive()
    assert scheduler.job_status("job-1") == "canceled"
    assert scheduler.job_result("job-1") is None
    report = scheduler.report()
    assert report.late_completions == 1
    assert report.consumed_by_campaign["campaign-1"].wall_seconds == pytest.approx(0.1)


def test_acknowledged_dispatched_cancel_is_provisionally_charged_then_late_actual_replaces_once(
    tmp_path: Path,
) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store)
    started = threading.Event()
    release = threading.Event()
    actual = CampaignJobResult(
        outcome="infrastructure_failure",
        consumed=SchedulerBudget(tokens=3, wall_seconds=0.25, compute_units=0.25, jobs=1),
        detail="provider acknowledged cancellation",
        cleanup_succeeded=True,
    )

    def execute(_assignment: CampaignAssignment) -> CampaignJobResult:
        started.set()
        assert release.wait(timeout=2)
        return actual

    scheduler.register_worker(_worker("local"), CallableCampaignWorker(execute, cancel=lambda _: True))
    scheduler.enqueue(_job(1))
    dispatch_thread = threading.Thread(target=scheduler.dispatch_once)
    dispatch_thread.start()
    assert started.wait(timeout=2)

    assert scheduler.cancel("job-1") is True
    assert scheduler.job_status("job-1") == "canceled"
    provisional = scheduler.report().consumed_by_campaign["campaign-1"]
    assert provisional == SchedulerBudget(tokens=10, wall_seconds=5, compute_units=1, jobs=1)
    restarted = CampaignScheduler(store)
    assert restarted.report().consumed_by_campaign["campaign-1"] == provisional

    release.set()
    dispatch_thread.join(timeout=2)
    lease_payload = next(event.payload["lease"] for event in store.read() if event.event_type == "job_leased")
    assert isinstance(lease_payload, dict)
    scheduler.complete(str(lease_payload["lease_id"]), actual)

    report = scheduler.report()
    assert report.consumed_by_campaign["campaign-1"] == actual.consumed
    assert report.late_completions == 1
    final = CampaignScheduler(store).report()
    assert final.consumed_by_campaign["campaign-1"] == actual.consumed
    assert final.late_completions == 1


def test_canceling_lease_expiry_persists_full_provisional_charge(tmp_path: Path) -> None:
    now = [10.0]
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store, lease_seconds=2.0, clock=lambda: now[0])
    scheduler.register_worker(_worker("lost"), CallableCampaignWorker(_success))
    scheduler.enqueue(_job(1))
    assignment = scheduler.claim("lost")[0]

    assert scheduler.cancel("job-1") is True
    now[0] = 13.0
    assert scheduler.reconcile() == ("job-1",)

    assert scheduler.job_status("job-1") == "canceled"
    assert scheduler.report().consumed_by_campaign["campaign-1"] == SchedulerBudget(
        tokens=10,
        wall_seconds=5,
        compute_units=1,
        jobs=1,
    )
    scheduler.complete(assignment.lease.lease_id, _success(assignment))
    scheduler.complete(assignment.lease.lease_id, _success(assignment))
    assert scheduler.report().late_completions == 1


def test_queued_cancellation_remains_free(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.enqueue(_job(1))

    assert scheduler.cancel("job-1") is True

    assert scheduler.job_status("job-1") == "canceled"
    assert scheduler.report().consumed_by_campaign.get("campaign-1", SchedulerBudget()) == SchedulerBudget()


def test_live_service_cancel_grace_does_not_wait_for_hung_worker_or_extend_canceling_lease(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store, lease_seconds=0.03)
    started = threading.Event()
    release = threading.Event()

    def execute(assignment: CampaignAssignment) -> CampaignJobResult:
        started.set()
        release.wait()
        return _success(assignment)

    scheduler.register_worker(_worker("hung"), CallableCampaignWorker(execute))
    scheduler.enqueue(_job(1))
    stop = threading.Event()
    service = threading.Thread(target=lambda: scheduler.serve(stop, poll_interval=0.005, cancel_grace_seconds=0.03))
    service.start()
    assert started.wait(timeout=2)
    stop.set()
    service.join(timeout=0.5)

    assert not service.is_alive()
    assert scheduler.job_status("job-1") == "canceled"
    assert scheduler.report().consumed_by_campaign["campaign-1"] == SchedulerBudget(
        tokens=10,
        wall_seconds=5,
        compute_units=1,
        jobs=1,
    )
    events = list(store.read())
    cancel_index = next(index for index, event in enumerate(events) if event.event_type == "job_cancel_requested")
    assert all(event.event_type != "worker_heartbeat" for event in events[cancel_index + 1 :])

    release.set()
    deadline = time.monotonic() + 2
    while scheduler.report().late_completions == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert scheduler.report().late_completions == 1
    assert scheduler.report().consumed_by_campaign["campaign-1"].jobs == 1


def test_dispatcher_exception_charges_full_wall_reservation_and_cleanup_failure(tmp_path: Path) -> None:
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store)
    scheduler.configure_campaign("campaign-1", SchedulerBudget(jobs=1, wall_seconds=1_000))

    def fail(_assignment: CampaignAssignment) -> CampaignJobResult:
        time.sleep(0.01)
        raise RuntimeError("dispatcher failure")

    scheduler.register_worker(_worker("broken"), CallableCampaignWorker(fail))
    scheduler.enqueue(
        _job(
            1,
            max_attempts=1,
            reservation=SchedulerBudget(tokens=10, wall_seconds=600, compute_units=1, jobs=1),
        )
    )
    scheduler.enqueue(_job(2, max_attempts=1))

    assert scheduler.run_until_idle() == 1
    report = scheduler.report()
    assert report.consumed_by_campaign["campaign-1"].jobs == 1
    assert report.consumed_by_campaign["campaign-1"].wall_seconds == 600
    assert report.cleanup_by_campaign["campaign-1"] == {"succeeded": 0, "failed": 1}
    assert report.worker_utilization["broken"]["cleanup_failed"] == 1
    assert CampaignScheduler(store).report().cleanup_by_campaign == report.cleanup_by_campaign


def test_invalid_batch_cardinality_preserves_known_usage_in_failure_accounting(tmp_path: Path) -> None:
    class PartialBatchWorker:
        def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
            raise AssertionError(f"unexpected scalar dispatch for {assignment.job.job_id}")

        def execute_many(self, assignments: tuple[CampaignAssignment, ...]) -> tuple[CampaignJobResult, ...]:
            assert len(assignments) == 2
            return (
                CampaignJobResult(
                    outcome="candidate_success",
                    consumed=SchedulerBudget(
                        tokens=13,
                        wall_seconds=7,
                        compute_units=2,
                        jobs=1,
                        shared_evidence_tokens=3,
                    ),
                ),
            )

    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.register_worker(
        _worker("partial", concurrency=2, sandbox_features=frozenset({"session_reuse"})),
        PartialBatchWorker(),
    )
    scheduler.enqueue(_job(1, cohort_id="pair", prefer_warm_reuse=True, max_attempts=1))
    scheduler.enqueue(_job(2, cohort_id="pair", prefer_warm_reuse=True, max_attempts=1))

    assert scheduler.run_until_idle() == 2

    report = scheduler.report()
    assert report.infrastructure_failed == 2
    consumed = report.consumed_by_campaign["campaign-1"]
    assert consumed.tokens == 23
    assert consumed.wall_seconds >= 7
    assert consumed.compute_units == 3
    assert consumed.jobs == 2
    assert consumed.shared_evidence_tokens == 3
    assert report.cleanup_by_campaign["campaign-1"] == {"succeeded": 0, "failed": 2}


def test_ambiguous_dispatch_failure_charges_every_assignment_full_reservation(tmp_path: Path) -> None:
    class AmbiguousBatchWorker:
        def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
            raise AssertionError(f"unexpected scalar dispatch for {assignment.job.job_id}")

        def execute_many(self, assignments: tuple[CampaignAssignment, ...]) -> tuple[CampaignJobResult, ...]:
            assert len(assignments) == 2
            time.sleep(0.01)
            raise ConnectionError("response stream lost after provider dispatch")

    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.register_worker(
        _worker("ambiguous", concurrency=2, sandbox_features=frozenset({"session_reuse"})),
        AmbiguousBatchWorker(),
    )
    reservation = SchedulerBudget(tokens=11, wall_seconds=5, compute_units=2, jobs=1, shared_evidence_tokens=4)
    scheduler.enqueue(
        _job(1, reservation=reservation, cohort_id="pair", prefer_warm_reuse=True, max_attempts=1)
    )
    scheduler.enqueue(
        _job(2, reservation=reservation, cohort_id="pair", prefer_warm_reuse=True, max_attempts=1)
    )

    assert scheduler.run_until_idle() == 2

    consumed = scheduler.report().consumed_by_campaign["campaign-1"]
    assert consumed.tokens == 22
    assert consumed.compute_units == 4
    assert consumed.jobs == 2
    assert consumed.shared_evidence_tokens == 8
    assert consumed.wall_seconds >= 0.02


def test_runtime_factory_returns_configured_restartable_service(tmp_path: Path) -> None:
    from autocontext.execution.campaign_scheduler_runtime import (
        CampaignSchedulerRuntimePlan,
        CampaignWorkerBinding,
        build_campaign_scheduler_runtime,
    )

    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    plan = CampaignSchedulerRuntimePlan(
        campaign_id="campaign-1",
        budget=SchedulerBudget(jobs=1),
        jobs=(_job(1),),
        max_concurrency=1,
    )
    scheduler = build_campaign_scheduler_runtime(
        plan,
        store=store,
        workers=(CampaignWorkerBinding(_worker("factory"), CallableCampaignWorker(_success)),),
    )

    assert scheduler.run_until_idle(timeout_seconds=1.0) == 1
    resumed = build_campaign_scheduler_runtime(
        plan,
        store=store,
        workers=(CampaignWorkerBinding(_worker("factory"), CallableCampaignWorker(_success)),),
    )
    assert resumed.run_until_idle(timeout_seconds=1.0) == 0
    assert resumed.report().succeeded == 1


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


def test_event_store_discards_only_an_uncommitted_torn_tail(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = CampaignSchedulerEventStore(path)
    scheduler = CampaignScheduler(store)
    scheduler.configure_campaign("campaign-1", SchedulerBudget(jobs=1))
    with path.open("ab") as stream:
        stream.write(b'{"sequence":2,"event_id":"crashed-mid-append"')

    restarted = CampaignScheduler(CampaignSchedulerEventStore(path))

    assert restarted.report().events == 1
    assert len(CampaignSchedulerEventStore(path).read()) == 1
    assert path.read_bytes().endswith(b"\n")


def test_evidence_grant_admission_includes_active_lease_reservations(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(CampaignSchedulerEventStore(tmp_path / "events.jsonl"))
    scheduler.configure_campaign("campaign-1", SchedulerBudget(jobs=2, shared_evidence_tokens=10))
    scheduler.register_worker(_worker("worker"))
    scheduler.enqueue(
        _job(
            1,
            reservation=SchedulerBudget(jobs=1, shared_evidence_tokens=10),
        )
    )
    scheduler.claim("worker")

    with pytest.raises(RuntimeError, match="shared-evidence budget exhausted"):
        scheduler.grant_evidence_share(
            CampaignEvidenceGrant("share-1", "campaign-1", "branch-a", "branch-b", "artifact://share", 10)
        )

    report = scheduler.report()
    assert report.reserved_by_campaign["campaign-1"].shared_evidence_tokens == 10
    assert report.consumed_by_campaign.get("campaign-1", SchedulerBudget()).shared_evidence_tokens == 0


def test_drain_refills_freed_slots_before_slowest_wave_member_finishes(tmp_path: Path) -> None:
    scheduler = CampaignScheduler(
        CampaignSchedulerEventStore(tmp_path / "events.jsonl"),
        max_concurrency=4,
    )
    slow_release = threading.Event()
    slow_finished = threading.Event()
    refilled_before_slow_finished: list[bool] = []

    def execute(assignment: CampaignAssignment) -> CampaignJobResult:
        if assignment.job.job_id == "job-0":
            try:
                slow_release.wait(timeout=5)
            finally:
                slow_finished.set()
        elif assignment.job.job_id == "job-4":
            refilled_before_slow_finished.append(not slow_finished.is_set())
            slow_release.set()
        return _success(assignment)

    scheduler.register_worker(
        _worker(
            "wide",
            concurrency=4,
            resources=SchedulerResources(cpu_cores=4, memory_gb=8, disk_gb=20),
        ),
        CallableCampaignWorker(execute),
    )
    for index in range(5):
        scheduler.enqueue(_job(index))

    try:
        dispatched = scheduler.run_until_idle(poll_interval=0.005, timeout_seconds=10.0)
    finally:
        slow_release.set()

    assert dispatched == 5
    assert refilled_before_slow_finished == [True]
    assert scheduler.report().succeeded == 5


def test_expired_lease_charge_is_replaced_exactly_once_by_late_actual_usage(tmp_path: Path) -> None:
    now = [10.0]
    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store, lease_seconds=2.0, clock=lambda: now[0])
    scheduler.register_worker(_worker("lost"))
    scheduler.enqueue(_job(1, max_attempts=1))
    assignment = scheduler.claim("lost")[0]
    now[0] = 13.0

    assert scheduler.reconcile() == ("job-1",)
    provisional = scheduler.report()
    assert provisional.consumed_by_campaign["campaign-1"].tokens == 10
    assert provisional.cleanup_by_campaign["campaign-1"] == {"succeeded": 0, "failed": 1}

    actual = CampaignJobResult(
        outcome="candidate_success",
        consumed=SchedulerBudget(tokens=3, wall_seconds=1.0, jobs=1),
        cleanup_succeeded=True,
    )
    scheduler.complete(assignment.lease.lease_id, actual)
    scheduler.complete(assignment.lease.lease_id, actual)

    report = scheduler.report()
    assert report.consumed_by_campaign["campaign-1"] == actual.consumed
    assert report.consumed_by_branch["campaign-1"]["branch-a"] == actual.consumed
    assert report.cleanup_by_campaign["campaign-1"] == {"succeeded": 1, "failed": 0}
    assert report.late_completions == 1
    assert CampaignScheduler(store).report().consumed_by_campaign["campaign-1"] == actual.consumed


def test_audit_checkpoint_failures_are_logged_and_durably_reported(tmp_path: Path, caplog: Any) -> None:
    class BrokenAuditRunner:
        def review_checkpoint(self, checkpoint, evidence, *, cancellation_event=None):
            del checkpoint, evidence, cancellation_event
            raise RuntimeError("auditor unavailable")

    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    scheduler = CampaignScheduler(store, audit_checkpoints=BrokenAuditRunner())
    scheduler.register_worker(
        _worker("broken"),
        CallableCampaignWorker(lambda _: CampaignJobResult(outcome="infrastructure_failure")),
    )
    scheduler.enqueue(_job(1, max_attempts=1))

    scheduler.run_until_idle()

    failures = scheduler.report().audit_failures_by_campaign["campaign-1"]
    assert [item["checkpoint"] for item in failures] == ["integrity_alert", "final_completion"]
    assert all(item["failure"] == "RuntimeError" for item in failures)
    assert "campaign audit checkpoint" in caplog.text
    assert CampaignScheduler(store).report().audit_failures_by_campaign["campaign-1"] == failures


def test_service_final_audit_is_not_pre_canceled_by_shutdown_event(tmp_path: Path) -> None:
    class AuditRunner:
        def __init__(self) -> None:
            self.cancellation_events: list[threading.Event | None] = []

        def review_checkpoint(self, checkpoint, evidence, *, cancellation_event=None):
            del checkpoint, evidence
            self.cancellation_events.append(cancellation_event)

    audits = AuditRunner()
    scheduler = CampaignScheduler(
        CampaignSchedulerEventStore(tmp_path / "events.jsonl"),
        audit_checkpoints=audits,
    )
    scheduler.register_worker(_worker("worker"))
    scheduler.enqueue(_job(1))
    assignment = scheduler.claim("worker")[0]
    scheduler.complete(assignment.lease.lease_id, _success(assignment))
    stop = threading.Event()
    stop.set()

    assert scheduler.serve(stop) == 0
    assert audits.cancellation_events == [None]


def test_runtime_plan_identity_rejects_mutated_resume_payload(tmp_path: Path) -> None:
    from dataclasses import replace

    from autocontext.execution.campaign_scheduler_runtime import (
        CampaignSchedulerRuntimePlan,
        CampaignWorkerBinding,
        build_campaign_scheduler_runtime,
    )

    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    original_job = _job(1, payload={"strategy": "old"})
    original = CampaignSchedulerRuntimePlan(
        campaign_id="campaign-1",
        budget=SchedulerBudget(jobs=1),
        jobs=(original_job,),
        identity={"run_id": "run-1"},
    )
    binding = CampaignWorkerBinding(_worker("worker"), CallableCampaignWorker(_success))
    build_campaign_scheduler_runtime(original, store=store, workers=(binding,))

    mutated = replace(original, jobs=(replace(original_job, payload={"strategy": "new"}),))
    with pytest.raises(ValueError, match="durable plan identity"):
        build_campaign_scheduler_runtime(mutated, store=store, workers=(binding,))


def test_runtime_factory_upgrades_a_matching_partial_legacy_plan(tmp_path: Path) -> None:
    from autocontext.execution.campaign_scheduler_runtime import (
        CampaignSchedulerRuntimePlan,
        CampaignWorkerBinding,
        build_campaign_scheduler_runtime,
    )

    store = CampaignSchedulerEventStore(tmp_path / "events.jsonl")
    legacy = CampaignScheduler(store)
    legacy.configure_campaign("campaign-1", SchedulerBudget(jobs=2))
    first, second = _job(1), _job(2)
    legacy.enqueue(first)
    plan = CampaignSchedulerRuntimePlan(
        campaign_id="campaign-1",
        budget=SchedulerBudget(jobs=2),
        jobs=(first, second),
    )

    scheduler = build_campaign_scheduler_runtime(
        plan,
        store=store,
        workers=(CampaignWorkerBinding(_worker("worker"), CallableCampaignWorker(_success)),),
    )

    assert scheduler.run_until_idle() == 2
    assert scheduler.report().succeeded == 2
