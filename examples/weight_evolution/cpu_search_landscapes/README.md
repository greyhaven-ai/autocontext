# Budget-limited CPU search landscapes (M19)

M18 calibrated generated confirmation and audit routing, but all policy contrasts
and exhaustive-practice gaps were zero. M19 adds separate tasks where the fixed
search budget leaves useful options unexplored. It checks whether the measured
workflow reproduces known advantages, traps and neutral outcomes.

The pinned population has three families, two structurally different instances
per family, two nested repeats and three unchanged policies: **36 searches and
twelve conditional exhaustive practice references**. Every mechanism has six
options, three trials and eight cases per role. The policies remain adaptive,
fixed-order and seeded-random; all receive the same task and capacity within a
paired task/repeat comparison.

## Constructed expectations

| Weight-search family | Intended adaptive versus fixed-order behavior |
| --- | --- |
| Informative improvement | Following early improvement reaches a better repair. |
| Misleading improvement | Following the initial improvement misses a better alternative. |
| Neutral | The options provide no improvement over baseline. |

The instances differ in corruption margins and repair effects. Opaque option
identities and generation seeds are fixed before execution. All task and seed
rows are retained; generation never filters them by measured outcomes.
Role inputs have distinct normalized token-count rays and stable teacher labels.
Declaration order is part of the constructed experiment, not an independent
sample. Repeats stay nested within instances.

Context prefixes use neutral features and decoding uses distinct settings with
top-k one. These are intentional neutral anchors, not a claim that arbitrary
context or decoding searches behave alike. Both remain at baseline, so the
unchanged C/W/D minimum-gain gate of 0.1 **vetoes all 36 searches and withholds
audit**. M18 remains the regression for generated positive audit routing.

The independent arithmetic oracle fixes complete option scores, ordered
per-case outputs, policy histories, selections, reference gaps and confirmation
outcomes before dispatch. It does not call the production decoder or proposal
policy. Asset loading regenerates exact bytes and rejects consistently rehashed
task or oracle drift. Expected metadata never enters policy inputs. Search uses
practice scores only; reference results cannot guide selection or reselection.

## Build and validate without workers

From the repository's `autocontext/` package directory:

```bash
uv sync --frozen --group dev
uv run python ../examples/weight_evolution/cpu_search_landscapes/build_bundle.py \
  --output /tmp/cpu-search-landscapes-bundle
uv run python ../examples/weight_evolution/cpu_search_landscapes/validate.py \
  --bundle /tmp/cpu-search-landscapes-bundle
```

Use a fresh bundle directory. Freeze source before building it: the measured
profile fingerprints the entire `weight_evolution` package. Replay historical
evidence using its original source and runtime. Earlier experiment modules and
contracts retain their existing identities.

## Measured execution and reporting

```bash
uv run python ../examples/weight_evolution/cpu_search_landscapes/run.py \
  --bundle /tmp/cpu-search-landscapes-bundle --output /tmp/cpu-search-landscapes-run
uv run python ../examples/weight_evolution/cpu_search_landscapes/report.py \
  --bundle /tmp/cpu-search-landscapes-bundle --output /tmp/cpu-search-landscapes-run
```

Every search must complete and publish before any reference is eligible. Failed
or uncertain searches block all twelve references with explicit zero-charge
decisions. Claimed work keeps its full reservation; no automatic retries,
refunds or redispatches are permitted. Durable outcomes can finish publication
without repeating an operation. Partial attempts remain evidence, not success.

The report separates integrity, completion and calibration. Best-so-far practice
curves come from retained trial histories without extra inference. It reports
per-policy exhaustive-practice gaps, paired confirmation differences and all five
resource meters, distinguishing reserved capacity from verified completed usage.
Worker duration is not total campaign cost. Reference costs remain separate.
Theoretical audit scores are explicitly unmeasured after veto.

## Linux/amd64 acceptance

On a native Linux/amd64 CPU host:

```bash
uv run python ../examples/weight_evolution/cpu_search_landscapes/acceptance.py \
  --output /tmp/cpu-search-landscapes-acceptance \
  --execution-context local-linux-amd64
```

The driver retains one fresh attempt, its source/runtime identities, all raw
journals, validation and acceptance receipts, and a guarded replay inventory.
The dedicated [CPU-only workflow](../../../.github/workflows/cpu-search-landscapes.yml)
uploads complete or partial evidence. It reserves time after the acceptance
deadline to preserve artifacts. Emulated amd64 must be explicitly labelled;
supplemental Linux/ARM64 validation is not native Linux/amd64 acceptance.

Acceptance requires all 36 searches and twelve references, **732 exact oracle
calibrations**, the declared informative/misleading/null contrasts and reference
gaps, all **984 measured inference processes observed and reaped**, all 36 vetoes
with no audit launches, and replay without dispatch or writes.
An aggregate adaptive win is not required. Expected adaptive losses and nulls
are part of the acceptance contract.

See the [validation record](MILESTONE19_VALIDATION.md) and
[machine-readable results](MILESTONE19_RESULTS.json) for the tested source
snapshot, platform limits and retained evidence.

These tasks calibrate finite constructed search behavior, not generalization,
natural-task transfer or Qwen reproduction. TypeScript runtime parity and
promotion remain deferred. **GPU execution remains deferred throughout.**
