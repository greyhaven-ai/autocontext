# Context bundles and outcome-gated promotion

autocontext treats the complete prompt and harness state as one immutable
`ContextBundle`. A bundle can contain the playbook, hints, prompt fragments,
context policies, completion checks, tool guidance and specifications, harness
validators, and routing configuration. Every component has a SHA-256 digest;
the sorted manifest has its own digest plus the parent bundle and evaluator
epoch.

## Lifecycle

```text
proposed -> screened -> confirmed -> active -> superseded
     |          |
     +----------+-> rejected
```

Architect and coach output is written beneath
`knowledge/<scenario>/context_bundles/candidates/<digest>/`. It does not enter
the next live prompt merely because the current strategy advanced. Promotion
requires all of the following:

1. A cheap screen using candidate/incumbent pairs from identical fixtures,
   seeds, cohorts, and evaluator epoch.
2. Adaptive matched confirmation whose confidence interval clears the minimum
   effect.
3. A held-out matched lane with no regression.
4. A replay of the persisted raw trials under the exact persisted
   `ConfirmationPolicy`; the replayed result must field-for-field match the
   recorded comparison.
5. When campaign-level error control is configured, a durable alpha
   reservation created before the candidate is observed. Repeated seeds for
   one fixture collapse into one dependence block, evaluation lanes must be
   block-disjoint, and the campaign-adjusted interval must pass before serving.
   Fixture digests derive from canonical initial state plus challenger
   observation—not seed, lane, or display labels—and every planned fixture is
   durably reserved before evaluation. A later adaptive candidate cannot reuse
   any actual fixture previously exposed in the campaign.
6. An optional read-only `pre_promotion` audit. `review_required` or
   `safe_pause_recommended` holds the candidate without changing its score.
   Once enabled, audit transport or evidence-integrity failure also holds the
   persisted confirmation for a resumable retry; it never fails open.
7. A static serveability check that rejects unsupported routing fields,
   construction-bound route changes, and DAG/tuning changes that require
   lifecycle reconstruction.
8. A final compare-and-swap confirming that the tested parent is still active.

The serving commit is one atomic replacement of `active.json`. Bundle manifests
are immutable and written before that pointer, so a reader sees either the
complete incumbent or the complete candidate. Rejected and inconclusive
results do not write the pointer. They persist an immutable
`negative_result.json` ledger beside the candidate, bound to the exact bundle,
evaluator epoch, cohort, matched seeds/probes, and evidence artifact. An
inconclusive result also invokes the optional `inconclusive_gate` auditor, and
evaluator exceptions invoke `integrity_alert`; neither audit may rewrite the
deterministic comparison. A confirmation policy cannot change while
evidence is being collected. `matched_trials.json` schema 2 stores raw pairs,
the policy, and its digest in one atomically replaced envelope. The candidate
record is updated second; if that write is interrupted, replaying the same
request recovers the record from the already-bound evidence without allowing a
policy change. Legacy array artifacts and their lane/display-name pair keys are
accepted explicitly, then rewritten to schema 2 after a verified replay.
Legacy rows that collapse to the same current fixture-digest/seed identity fail
closed. Terminal legacy candidates can only migrate already-identical evidence;
new or changed terminal trials are rejected. Each promotion records the exact candidate,
incumbent, evaluator epoch, cohort, policy and policy digest, rationale,
replayed evidence summary, and rollback target under
`context_bundles/promotions/`.

`ContextBundlePromotionCoordinator` is the live evaluation boundary used by
both standard and tree-search generation pipelines. Set
`AUTOCONTEXT_CONTEXT_BUNDLE_PROMOTION_ENABLED=true` to make the shipped CLI,
server, MCP, A/B, and ecosystem `GenerationRunner` entrypoints construct the
coordinator automatically; constructor injection remains available for custom
embeddings and tests. It alternates candidate/incumbent call order, stops early
on rejection or exhausted uncertainty, runs the audit and false-promotion
gates, then refreshes the active digest and serving routing immediately after
the atomic pointer changes. A crash or gate failure leaves the incumbent
pointer untouched. Partial evidence, an audit hold, or an audit transport
failure defers the generation instead of advancing to a new cohort. Retrying
the same run and generation discovers its single pending candidate, verifies
the persisted plan and policy, skips completed pair keys, and resumes before
any new coach or architect work. A terminal campaign false-promotion block is
recorded as a rejected candidate and is therefore never mistaken for a
resumable audit hold.

Every proposal also persists the exact `manifest_diff.json`. Promotion
recomputes it from the immutable manifests. If a promoted experiment is an
exact component addition, held-out matched trials are converted into
`causal_attribution.json` only after proving that the comparison omits that
one `(kind, key, digest)` and every other component is identical. Replacements
and multi-component edits are never labeled causal.

## Python API

```python
from pathlib import Path

from autocontext.context_bundles import (
    CampaignFalsePromotionController,
    ContextBundlePromotionCoordinator,
    ContextBundleStore,
)

store = ContextBundleStore(Path("knowledge"))
candidate = store.load_bundle("support", candidate_digest)

# Production generation receives a coordinator with an evaluator and a
# predeclared matched-unit plan. The durable controller prevents a new model or
# candidate generator from resetting the campaign's error budget.
coordinator = ContextBundlePromotionCoordinator(
    store,
    evaluator,
    evaluation_units,
    cohort="support-v3",
    false_promotion_controller=CampaignFalsePromotionController(
        Path("knowledge/support/promotion-risk")
    ),
    campaign_id="support-campaign-42",
)
result = coordinator.evaluate_candidate("support", candidate.digest)
```

The live runner keeps one `campaign_id` for the complete run, but rebuilds the
coordinator with a deterministic generation-specific cohort and seed namespace.
That preserves the alpha ledger across adaptive candidates while preventing
confirmation and held-out plans from being recycled between generations. A
schema-v1 risk ledger with prior candidates has no trustworthy fixture history,
so it remains readable for audit but fails closed for further evaluation; start
a new campaign identity to continue.

`store.rollback(...)` restores the recorded rollback target with the same
atomic pointer mechanism. The Python generation loop creates candidates on
both its standard and tree-search paths. The shipped runtime evaluator builds
each arm from its explicitly supplied immutable bundle and executes the real
scenario evaluator in a clean model-session boundary; fixture and held-out seed
identities remain outside model-visible prompts. The strategy tournament is
not reused as evidence for a context edit it never exercised.

Matched evaluation materializes the state and challenger observation once,
hashes that exact pair, and supplies all three values to the execution
supervisor. The local, Monty, Prime Intellect, and SSH executors implement this
prepared-fixture contract. Prime packages and SSH responses carry the bound
fixture digest back as an execution attestation; ordinary unprepared requests
carry none of the prepared-fixture metadata. A custom executor that cannot
consume the exact prepared state fails before scoring instead of regenerating
the fixture from its seed or falling back to unattested execution.

## TypeScript parity

The `autoctx/context-bundles` subpath exports the same component kinds,
canonical JSON hashing, bundle validation, matched-trial keys, and adaptive
comparison decision. It also exports the campaign alpha allocation,
dependence-aware evidence gate, exact candidate fixture reservations, and
persisted-state constructors. Evidence authorization in both runtimes requires
candidate, epoch, cohort, lane, fixture digest, seed, and plan digest to match
the durable reservation. Python and TypeScript both validate
`fixtures/context-bundles/manifest-parity.json`, pinning byte-identical digests
across runtimes. `false-promotion-parity.json` pins alpha, rounded confidence
thresholds, state digests, and every evidence-gate outcome.

The default dependence-aware method applies Student-t intervals to independent
fixture-block means after spending alpha across every possible within-candidate
look. This assumes block means are exchangeable with finite variance. For
bounded, skewed, or heavy-tailed effects, select `bounded_hoeffding` and declare
the score-difference bounds before evaluation. See
[false-promotion calibration](false-promotion-calibration.md) for measured
error, power, and cost.
