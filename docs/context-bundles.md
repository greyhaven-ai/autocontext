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
5. A static serveability check that rejects unsupported routing fields,
   construction-bound route changes, and DAG/tuning changes that require
   lifecycle reconstruction.
6. A final compare-and-swap confirming that the tested parent is still active.

The serving commit is one atomic replacement of `active.json`. Bundle manifests
are immutable and written before that pointer, so a reader sees either the
complete incumbent or the complete candidate. Rejected and inconclusive
results do not write the pointer. A confirmation policy cannot change while
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

## Python API

```python
from pathlib import Path

from autocontext.context_bundles import ContextBundleStore

store = ContextBundleStore(Path("knowledge"))
candidate = store.load_bundle("support", candidate_digest)

# Each item contains both scores for one identical fixture/seed.
result = store.record_matched_trials("support", candidate.digest, matched_trials)
if result.decision == "confirmed":
    promotion = store.promote(
        "support",
        candidate.digest,
        cohort="support-v3",
        rationale="confirmed on matched and held-out cases",
    )
```

`store.rollback(...)` restores the recorded rollback target with the same
atomic pointer mechanism. The Python generation loop creates candidates on
both its standard and tree-search paths. An evaluator or operator supplies the
matched evidence; the strategy tournament is not reused as evidence for a
context edit it never exercised.

## TypeScript parity

The `autoctx/context-bundles` subpath exports the same component kinds,
canonical JSON hashing, bundle validation, matched-trial keys, and adaptive
comparison decision. Python and TypeScript both validate
`fixtures/context-bundles/manifest-parity.json`, pinning byte-identical digests
across runtimes.
