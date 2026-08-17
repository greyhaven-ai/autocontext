# Negative Result Ledger

autocontext provides a durable ledger for failed, pruned, rejected, or refused branches. It keeps negative evidence inspectable instead of collapsing it into `dead_ends.md` prose.

## Contract

Schema: [`negative-result-ledger.json`](negative-result-ledger.json)  
Legacy migration parity fixture: [`negative-result-ledger-parity-fixture.json`](negative-result-ledger-parity-fixture.json)
Applicability/retest parity fixture: [`negative-result-applicability-parity-fixture.json`](negative-result-applicability-parity-fixture.json)

A schema-v2 ledger contains:

- `entries`: negative branch examples with `failure_kind`, `disposition`, `score_delta`, evaluated seeds/probes, branch lineage, and evidence references.
- `context`: the scenario, exact context-bundle digest and family, evaluator epoch, verifier digest, trial cohort, component dependencies, and environment fingerprint under which the result occurred.
- `applicability_scope`: one of `exact_bundle`, `bundle_family`, `scenario_local`, `cross_scenario`, or `context_unknown`.
- Retest lineage: `retest_of_result_id`, `retest_outcome`, and `superseded_by_result_id` preserve later evidence without deleting the original result.
- `failure_mode_summary`: grouped counts by `failure_kind` and `disposition`, with the source `result_ids` preserved.

## Disposition semantics

- `caution`: evidence-backed warning. Prompt injection says it is **not a ban** and should only constrain retries with no differentiating evidence.
- `hard_ban`: reproducible contraindication within its matching scope. A cross-scenario hard ban requires `safety_policy_authority`; otherwise it is injected as a caution.
- `noise`: one-off or flaky result. It remains inspectable but is omitted from prompt lessons so exploration does not collapse.

## Applicability and retesting

Callers supply the current bundle, evaluator, verifier, dependencies, environment, and observation time when rendering prompt lessons. A bundle/family/scenario mismatch, evaluator or verifier change, changed dependency, changed environment, expired evidence, or newly available stronger evidence marks the result `retest_due` and downgrades it to a caution. Every injected lesson states why it applies or why it is qualified.

A `not_reproduced` retest links back to the original and marks that original as superseded. Both records remain in the durable ledger, but the superseded lesson is no longer injected.

Schema-v1 ledgers remain readable in Python and TypeScript. They migrate to v2 with `context_unknown`, so legacy evidence stays visible but cannot become an unscoped hard ban.

## OSS boundary

The contract is public and file-based. Hosted aggregation, tenant-level suppression, fleet-wide policy, and commercial scheduling remain deployment concerns.
