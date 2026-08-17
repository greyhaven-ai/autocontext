from __future__ import annotations

import threading
import time
from pathlib import Path

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
