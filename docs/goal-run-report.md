# Goal Run Report

The outer goal loop is a shared Python/TypeScript artifact for continue-until-verified execution. Inner loop limits are checkpoint/cadence controls; goal completion is decided by verification or an explicit terminal condition.

## Contract

Schema: [`goal-run-report.json`](goal-run-report.json)
Parity fixture: [`goal-run-report-parity-fixture.json`](goal-run-report-parity-fixture.json)

A report captures:

- goal and goal-run identity
- verifier reference and latest verifier state
- budget and usage counters, including no-progress tracking
- invoked inner-loop actions: `run`, `solve`, `improve`, `mission`, and `campaign`
- durable continuation or stop rationale
- resume token for process-restart recovery

## Statuses

Goal statuses: `continued`, `verified_complete`, `blocked`, `budget_exhausted`, `verifier_failed`, `no_progress`, and `canceled`.

`continued` means the supervisor should invoke the recorded `next_action_kind`. Terminal states record `stop_reason` and should not schedule another inner action without a new goal-run decision.

## OSS boundary

The shared contract, builder, parser, and file helpers are public. Hosted fleet routing, tenant scheduling, billing, warm pools, and proprietary dashboards remain out of scope.
