# Negative Result Ledger

AC-823 adds a durable ledger for failed, pruned, rejected, or refused branches. It keeps negative evidence inspectable instead of collapsing it into `dead_ends.md` prose.

## Contract

Schema: [`negative-result-ledger.json`](negative-result-ledger.json)  
Parity fixture: [`negative-result-ledger-parity-fixture.json`](negative-result-ledger-parity-fixture.json)

A ledger contains:

- `entries`: negative branch examples with `failure_kind`, `disposition`, `score_delta`, evaluated seeds/probes, branch lineage, and evidence references.
- `failure_mode_summary`: grouped counts by `failure_kind` and `disposition`, with the source `result_ids` preserved.

## Disposition semantics

- `caution`: evidence-backed warning. Prompt injection says it is **not a ban** and should only constrain retries with no differentiating evidence.
- `hard_ban`: reproducible contraindication. Prompt injection says do not repeat without new evidence.
- `noise`: one-off or flaky result. It remains inspectable but is omitted from prompt lessons so exploration does not collapse.

## OSS boundary

The contract is public and file-based. Hosted aggregation, tenant-level suppression, fleet-wide policy, and commercial scheduling remain deployment concerns.
