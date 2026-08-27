# Campaign Mode Report

Campaign mode is a shared Python/TypeScript artifact for multi-branch runs. It
makes the hypothesis tree operator-visible independently of execution. Python
also offers an [optional OSS campaign scheduler](campaign-scheduler.md) for
user-controlled workers; hosted placement and proprietary orchestration remain
separate concerns.

## Contract

Schema: [`campaign-mode-report.json`](campaign-mode-report.json)
Parity fixture: [`campaign-mode-report-parity-fixture.json`](campaign-mode-report-parity-fixture.json)

A report captures:

- campaign and run identity
- branch budgets and usage
- comparable eval lanes with verifier contract references, seeds, and holdouts
- branch lineage and terminal states
- compact, evidence-backed cross-branch sharing with an item/summary budget
- links to progress, utilization, and negative-result artifacts
- final recommendation for the best evidence-backed branch

## Terminal states

Campaign states: `active`, `completed`, `failed`, `budget_exhausted`, `canceled`.

Branch states: `pending`, `running`, `continued`, `pruned`, `succeeded`, `failed`, `budget_exhausted`, `canceled`.

## Evidence sharing

`evidence_sharing.policy` caps how much cross-branch evidence may enter prompt context. Items outside the cap remain inspectable with `included: false` but should not be injected into prompts.

## OSS boundary

The artifact contract, builders, parsers, and file helpers are public. The
optional Python scheduler adds leases, resource matching, durable recovery, and
capability-driven reuse for user-controlled workers. Hosted tenant scheduling,
proprietary fleet routing, budget billing, managed warm pools, and proprietary
campaign dashboards remain out of scope.
