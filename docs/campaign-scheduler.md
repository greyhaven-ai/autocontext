# Optional campaign scheduler

Python's `CampaignScheduler` turns campaign branch/trial plans into bounded
work on user-controlled local workers, trusted hosts, or remote execution
adapters. It is an optional OSS coordinator, not a hosted placement or billing
service.

The shipped production entrypoint is `autoctx campaign run PLAN.json`. It
loads a schema-versioned plan, resolves the configured scenario and executor,
converts every branch/trial into a durable scheduler job, runs or resumes the
event log, persists raw result artifacts plus a scheduler utilization report,
and writes the canonical campaign-mode report under the scenario knowledge
root. `AUTOCONTEXT_EXECUTOR_MODE=local|monty|ssh|primeintellect` selects the
same execution data plane used by ordinary generation runs.

Minimal plan:

`evaluator_epoch` and `verifier_contract_ref` are not caller-selected labels.
Generate them with
`derive_campaign_evaluation_identity(settings, scenario_name)` and place those
exact values in every job. `campaign run` derives them again from the resolved
scenario rubric and judge route and rejects the complete plan before creating
scheduler state when either value differs.

```json
{
  "schema_version": 1,
  "campaign_id": "campaign-1",
  "run_id": "run-1",
  "scenario_name": "othello",
  "budget": {"jobs": 2, "wall_seconds": 120},
  "jobs": [
    {
      "job_id": "trial-1",
      "idempotency_key": "trial-1-v1",
      "branch_id": "branch-a",
      "objective": "test corner pressure",
      "strategy": {"mobility_weight": 0.5, "corner_weight": 1.0, "stability_weight": 0.5},
      "seed": 11,
      "lane_id": "confirmation",
      "fixture_digest": "fixture-11",
      "evaluator_epoch": "<derived evaluator epoch>",
      "verifier_contract_ref": "<derived runtime-scenario-v1 contract>",
      "reservation": {"jobs": 1}
    }
  ]
}
```

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
after the retry bound. An exact duplicate enqueue returns the original job by
idempotency key; reusing that key for a different request, or reusing a
`job_id` with a different key, fails closed.
Infrastructure retries persist and charge the usage of every completed attempt.

Cancellation is a durable request/acknowledgment lifecycle. A queued job
cancels immediately. A leased job enters `canceling`, and the scheduler invokes
the worker's optional `cancel(assignment)` hook. An acknowledged termination
releases its lease only after durably recording a provisional full-reservation
charge. The live service stops extending a canceling lease and
returns after its configurable cancellation grace even when worker code is
hung; the execution thread is daemonized and any later result remains
accountable. Cancel-lease expiry and shutdown-grace expiry use the same
provisional accounting; cancellation before dispatch remains free. Any result
that arrives after the request is recorded as
`job_canceled_late`, replaces that provisional charge exactly once, and is counted in
`late_completions` rather than scored. `RemoteCampaignWorker` forwards the hook
when its adapter implements `cancel_request`; unsupported providers therefore
remain explicit and fully accounted.

The event store is an append-only, checksummed JSONL log that fsyncs every
transition. Replay discards only an invalid, unterminated final record left by
a torn append; corruption in a complete record still fails closed.
Reconstructing a scheduler from the same store replays queued,
leased, retry, finished, canceled, worker, budget, affinity, and evidence-grant
state. Operators then call `reconcile()` with the current time to resolve
leases that expired while the coordinator was unavailable. Expiry charges the
lease reservation conservatively, then replaces that provisional charge once
if a late result provides actual usage.

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
`dispatch_once()` runs one bounded concurrent wave. `run_until_idle()` drains
until every job is terminal, including waiting for replayed orphan leases to
expire; it has an explicit deadline, cancellation event, poll interval, and
cancellation grace and fails loudly when queued work has no runnable worker.
The live and draining loops refill newly available slots while slower members
of an earlier wave continue, avoiding fleet-wide head-of-line blocking.
`serve(stop_event)` is the live,
restart-safe runner: it reconciles expired work, dispatches jobs that arrive
after startup, and heartbeats only actively leased work during execution. External
daemons may instead use
`claim()`, `heartbeat()`, and `complete()` directly.

When a worker advertises session reuse, compatible same-branch matched jobs are
grouped and `RemoteCampaignWorker.execute_many()` submits them through one
provider session with the exact reuse bound. Jobs never group across the
campaign/branch reuse key. Missing batch support is an infrastructure failure;
workers without a verified clean boundary receive an explicit cold-ephemeral
lease instead.

`report()` separates candidate failures from scheduler/infrastructure failures
and includes queue state, retry count, reserved and consumed campaign budgets,
and per-worker runtime, locality, concurrency, active leases, completion/failure
counts, resource consumption, cleanup success/failure counts, and heartbeat
time. Campaign and per-branch consumed totals include retries, stale/late
attempts, and cleanup outcomes and are replayed from the same event log. Results
from expired or superseded leases are durably charged once but never replace a
newer scored result. Because a dispatcher exception cannot prove which provider
calls ran, every affected assignment is charged its full token, compute,
shared-evidence, and job reservation. Its wall charge is the maximum of the
admitted wall reservation, measured elapsed time, and known partial usage;
known usage can only increase any dimension. When a
`CampaignAuditCheckpointRunner` is supplied, infrastructure completions invoke
`integrity_alert`, service/run termination invokes `final_completion`, and the
report includes durable audit records plus dispositions grouped by campaign.
Audit failures cannot rewrite scheduler status, scoring, retry, or budget
state, but their checkpoint and exception type are logged and durably surfaced
in `audit_failures_by_campaign`.

Library composition can use `build_campaign_scheduler_runtime(plan, *, store,
workers, clock=time.time, audit_checkpoints=None)`. Its
`CampaignSchedulerRuntimePlan` and `CampaignWorkerBinding` inputs configure or
validate durable state, register concrete workers, enqueue idempotently, and
return a ready `CampaignScheduler` service. Its complete plan identity (jobs,
budgets, concurrency, lease policy, and caller-supplied run/scenario identity)
is durably bound on first construction; a mutated resume fails before dispatch.

The shipped scenario worker executes the exact materialized fixture state whose
digest appears in the evaluation lane. Result artifact names include the lease
identity, so retries and late attempts cannot overwrite the scored attempt.
Because the current `ExecutionOutput` does not expose provider token/compute
usage, successful and candidate-failed jobs conservatively consume their
admitted token, compute, job, and shared-evidence reservations while recording
measured wall time. This prevents unobservable usage from bypassing a durable
budget cap.

Plan budgets, reservations, worker resources, timeouts, lease durations, and
concurrency values reject booleans, implicit numeric coercion, NaN, and
infinity. Aggregate drain-deadline overflow also fails before dispatch. Each
run persists the canonical plan as an immutable audit artifact and binds its
content digest. Campaign audits share `settings.runs_root/campaign-audits`, so
the configured provider-call limit applies across every run of one campaign
and the CLI reads the same store.

The scheduler does not implement hosted tenant placement, organization quotas,
provider billing, proprietary fleet routing, or a mandatory warm GPU pool.
TypeScript campaign reports remain interoperable artifacts; the executable
scheduler is intentionally Python-first because TaskRunner and the generic
remote-session adapter are currently Python control-plane surfaces.
