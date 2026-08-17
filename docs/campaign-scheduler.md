# Optional campaign scheduler

Python's `CampaignScheduler` turns campaign branch/trial plans into bounded
work on user-controlled local workers, trusted hosts, or remote execution
adapters. It is an optional OSS coordinator, not a hosted placement or billing
service.

```python
from pathlib import Path

from autocontext.execution.campaign_scheduler import (
    CallableCampaignWorker,
    CampaignJobRequest,
    CampaignJobResult,
    CampaignScheduler,
    CampaignSchedulerEventStore,
    EvaluationLaneIdentity,
    SchedulerBudget,
    SchedulerResources,
    WorkerDescriptor,
)

store = CampaignSchedulerEventStore(Path(".autoctx/campaign-events.jsonl"))
scheduler = CampaignScheduler(store, lease_seconds=30, max_concurrency=2)
scheduler.configure_campaign(
    "campaign-1",
    SchedulerBudget(tokens=100_000, wall_seconds=3_600, jobs=20),
)
scheduler.register_worker(
    WorkerDescriptor(
        worker_id="local-1",
        runtime="python-3.13",
        resources=SchedulerResources(cpu_cores=4, memory_gb=16, disk_gb=50),
    ),
    CallableCampaignWorker(
        lambda assignment: CampaignJobResult(outcome="candidate_success")
    ),
)
```

## Scheduling contract

Workers advertise runtime, CPU, memory, disk, optional accelerator capacity,
sandbox features, locality, capability names, and concurrency. Jobs declare
the corresponding requirements, an idempotency key, maximum attempts,
campaign/branch reservations, and a complete evaluation-lane identity
(fixtures, seeds, evaluator epoch, and verifier contract).

`claim()` creates an expiring lease only after resource, capability, global
concurrency, branch-budget, and campaign-budget checks succeed. `heartbeat()`
extends leases owned by that worker. `reconcile()` releases an expired
reservation and either requeues the job or records an infrastructure failure
after the retry bound. Duplicate enqueue returns the original job by
idempotency key, and a reused `job_id` with a different key fails closed.
Infrastructure retries persist and charge the usage of every completed attempt.

Cancellation and lease expiry currently make scheduler state safe but do not
cancel work already running at the provider. The worker protocol must gain a
cancel-request/acknowledgment lifecycle before those leases can safely release
their concurrency slots and before late provider usage can be accounted. Until
then, operators must not treat scheduler cancellation as provider cancellation.

The event store is an append-only, checksummed JSONL log that fsyncs every
transition. Reconstructing a scheduler from the same store replays queued,
leased, retry, finished, canceled, worker, budget, affinity, and evidence-grant
state. Operators then call `reconcile()` with the current time to resolve
leases that expired while the coordinator was unavailable.

## Comparable lanes and warm reuse

The first lease in a matched cohort pins its environment fingerprint. Other
cohort jobs can run only on an equivalent environment. Warm/snapshot or
session reuse is selected only when both the job requests it and the worker
advertises the required feature; otherwise the assignment explicitly degrades
to cold ephemeral execution and emits `warm_degraded`.

Every reuse key contains the campaign and branch identity. Candidate state can
therefore never be reused across branches, even when two branches share a
cohort label. Cross-branch evidence uses a separate `CampaignEvidenceGrant`
with source branch, target branch, evidence reference, and token cost. Grants
are checked on enqueue and charged against the campaign's evidence budget.

## Workers and reporting

`CallableCampaignWorker` adapts local/TaskRunner-style work, and
`RemoteCampaignWorker` adapts any provider-neutral `RemoteExecutionAdapter`.
`dispatch_once()` runs one bounded concurrent wave; `run_until_idle()` repeats
waves until no schedulable job remains. External daemons may instead use
`claim()`, `heartbeat()`, and `complete()` directly.

`report()` separates candidate failures from scheduler/infrastructure failures
and includes queue state, retry count, reserved and consumed campaign budgets,
and per-worker runtime, locality, concurrency, active leases, completion/failure
counts, resource consumption, and heartbeat time.

The scheduler does not implement hosted tenant placement, organization quotas,
provider billing, proprietary fleet routing, or a mandatory warm GPU pool.
TypeScript campaign reports remain interoperable artifacts; the executable
scheduler is intentionally Python-first because TaskRunner and the generic
remote-session adapter are currently Python control-plane surfaces.
