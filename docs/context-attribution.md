# Ablation-backed context attribution

Context attribution separates three evidence levels:

- `causal_ablation`: matched with/without-component trials from a trusted
  producer claim to isolate one immutable component.
- `paired_shadow`: matched outcomes compare bundles, but do not isolate the component from other edits.
- `component_correlated`: edit-size attribution identifies correlation only and is never reported as causal.

Every controlled trial joins to a component and distinct tested/comparison bundles by digest and records the evaluator epoch, cohort, fixture, seed, score pair, token cost, test time, and known interaction components. Aggregation rejects duplicate fixture/seed pairs and mixed comparison/cohort groups. A controlled record stores deterministic matched-pair keys and exact source-trial digests, so `reconstruct_causal_credit` / `reconstructCausalCredit` can verify its provenance before recomputing the mean effect from durable trials.

`attribute_manifest_verified_trials` / `attributeManifestVerifiedTrials`
resolve both immutable manifests, recompute their digests and canonical
manifest diff, require the same scenario/evaluator epoch, and bind the diff to
the source trial. `causal_ablation` is accepted only when the tested manifest
contains the declared `(kind, key, digest)`, the comparison omits it, and that
is the sole component change. Replacements, unrelated bundles,
multi-component edits, missing/tampered manifests, and post-hoc relabeling fail
closed. `reconstruct_manifest_verified_causal_credit` redoes the same checks
before replaying credit.

The live context promotion path persists immutable `manifest_diff.json` on
proposal and verifies it again before cutover. An exact component-addition
experiment also writes held-out source trials and verified records to
`causal_attribution.json` before promotion. Comparisons that are only
replacement or multi-component evidence remain noncausal; they cannot reach
that artifact by asserting an evidence label.

## Re-ablation

`plan_reablation` / `planReablation` runs on a configured cadence, a score plateau, or a bundle-composition change. It uses a hard budget and prioritizes components that are expensive in prompt tokens, low confidence, old, interaction-prone, or last tested in a different bundle.

Later trials append to `ContextAttributionLedger` and link to the prior attribution rather than replacing it. This allows a component once marked `retained` to become `uncertain`, a `demotion_candidate`, or `harmful` after interactions change.

The ledger schema is version 2. Python model loading and the TypeScript
`parseContextAttributionLedger` helper migrate schema-1 history. Because those
records did not persist the new comparison, classification-policy, matched-pair,
or source-trial bindings, migration marks them `legacy_unverified`. They remain
available as history and may be superseded by fresh evidence, but cannot pass
controlled replay or drive prompt demotion until re-ablation creates a fully
bound record. Schema-1 records that already contain every current binding are
preserved as verified. Digest-bound string arrays use ECMAScript UTF-16 ordering
in both runtimes.

## Prompt selection

`select_prompt_components` / `selectPromptComponents` can omit current-bundle components classified as harmful or neutral-and-expensive while retaining their full attribution history. Evidence from an older bundle composition is treated as uncertain and kept until re-ablation; evaluator-epoch mismatches are ignored.

Shared Python/TypeScript cases for isolated causal credit, interaction re-ablation, and insufficient budget live in [`context-attribution-parity-fixture.json`](context-attribution-parity-fixture.json). Exact valid removal and invalid replacement behavior is pinned by `fixtures/context-bundles/causal-attribution-manifest-parity.json`.
