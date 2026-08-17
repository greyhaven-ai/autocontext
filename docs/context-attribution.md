# Ablation-backed context attribution

Context attribution separates three evidence levels:

- `causal_ablation`: matched with/without-component trials isolate one immutable component.
- `paired_shadow`: matched outcomes compare bundles, but do not isolate the component from other edits.
- `component_correlated`: edit-size attribution identifies correlation only and is never reported as causal.

Every controlled trial joins to a component and bundle by digest and records the evaluator epoch, cohort, fixture, seed, score pair, token cost, test time, and known interaction components. A causal record stores its source trial IDs, so `reconstruct_causal_credit` / `reconstructCausalCredit` can recompute the mean effect from durable trials.

## Re-ablation

`plan_reablation` / `planReablation` runs on a configured cadence, a score plateau, or a bundle-composition change. It uses a hard budget and prioritizes components that are expensive in prompt tokens, low confidence, old, interaction-prone, or last tested in a different bundle.

Later trials append to `ContextAttributionLedger` and link to the prior attribution rather than replacing it. This allows a component once marked `retained` to become `uncertain`, a `demotion_candidate`, or `harmful` after interactions change.

## Prompt selection

`select_prompt_components` / `selectPromptComponents` can omit current-bundle components classified as harmful or neutral-and-expensive while retaining their full attribution history. Evidence from an older bundle composition is treated as uncertain and kept until re-ablation; evaluator-epoch mismatches are ignored.

Shared Python/TypeScript cases for isolated causal credit, interaction re-ablation, and insufficient budget live in [`context-attribution-parity-fixture.json`](context-attribution-parity-fixture.json).
