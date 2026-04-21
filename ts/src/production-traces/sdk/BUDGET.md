# autoctx/production-traces — bundle budget

## Ceiling

**100 kB gzipped** at the `autoctx/production-traces` subpath entry.

Enforced in CI by
`scripts/check-production-traces-sdk-bundle-size.mjs`. Fail-loud on any
PR that pushes the bundle over `BUDGET_BYTES = 102_400`.

## Measurement

* Bundler: esbuild with `platform=neutral`, `target=es2022`, `format=esm`.
* Tree-shaking + minification enabled.
* Node built-ins (`node:crypto`, `node:fs`, `node:path`, `node:url`)
  marked `external` — customers' runtime provides these; counting
  polyfills would over-state the SDK's own code footprint.
* The SDK's npm-runtime deps (`ajv`, `ajv-formats`, `ulid`) are bundled
  in — that's the real install cost.
* Output gzipped with zlib default compression (level 6).

## Baseline projection (spec §6.3)

| Component | Approx gzipped |
|---|---|
| ajv strict mode + ajv-formats | 33 kB |
| ulid | 3 kB |
| Compiled JSON Schemas + types | 3 kB |
| Canonical-JSON (reused from control-plane) | 1 kB |
| SDK source (buildTrace + writeJsonl + TraceBatch + hashing + validate) | 10–15 kB |
| **A2-II-a ship target** | **~55 kB** |
| Headroom | ~45 kB |

## Current baseline (post-A2-II-a)

| Metric | Value |
|---|---|
| Raw bundle | ~170 kB |
| Gzipped | **~48 kB** |
| Budget | 100 kB |
| Headroom | ~52 kB |

Comfortably inside the ~55 kB ship target AND comfortably inside the
100 kB budget. Headroom reserved for A2-II-b (OpenAI integration) and
subsequent detector plugins.

## Budget bumps

Budget bumps are PR decisions — same discipline as the
type-assertion budget (which grew 520 → 740 across Foundations B
and A). If a feature genuinely needs more than 100 kB:

1. Update `BUDGET_BYTES` in
   `scripts/check-production-traces-sdk-bundle-size.mjs`.
2. Add a justification paragraph to the PR description explaining
   what the new bytes buy.
3. Note the bump in the CHANGELOG `[SDK]` section.

Do **not** bump the budget to work around an accidental regression —
run the script with `--report` to see top module contributors and
audit which dep grew.

## Top contributors inspection

```
npm run check:production-traces-sdk-bundle-size -- --report
```

Writes `bundle-report.txt` with the 20 largest source modules by raw
byte contribution. Use this when the budget creeps up.
