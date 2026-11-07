# CPU search robustness to ordering and budget (M20)

M19 demonstrated expected wins, losses and ties on six constructed weight-search
tasks. M20 tests how those results depend on declaration order and search budget.
It preserves the six tasks, candidate identities, tensor bytes, cases, labels,
policies and scoring rules. Only the declaration order of existing weight options
and the number of search rounds vary. Rebuilding weights with different ordinal
positions would change the task and is rejected.

## Pinned experiment

| Panel | Coverage | Evidence |
| --- | --- | --- |
| Exhaustive offline oracle | Six base tasks × 90 legal orders × budgets 1, 3 and 6 × four policy/seed combinations = 6,480 weight trajectories | Independent finite arithmetic, no worker dispatch |
| Measured order comparison | Six base tasks × canonical/reversed lane orders × three policies, three rounds | 36 CPU searches |
| Measured full budget | First instance of each family × canonical order × three policies, six rounds | Nine CPU searches |
| Practice reference | One exhaustive reference per measured task/order/budget variant | 15 references, eligible only after all 45 searches publish |

The 90 orders preserve step-one-before-step-two within each of three lanes.
Canonical order is A1, B1, C1, A2, B2, C2; reversed lanes are C1, B1, A1, C2, B2,
A2. The offline oracle uses adaptive seed 0, fixed-order seed 0 and seeded-random
seeds 0 and 1. The measured panel uses one frozen repeat at seed 0. Random ranking
hashes stable option identities: invariance applies to declaration order, not to
arbitrary identity renaming.

The six base tasks remain the sampling units. Orders, budgets and nested seeds
are not independent replications. All cells are declared before execution;
measured outcomes cannot select tasks, seeds, orders or budgets. All three
policies receive equal capacity within each task/order/budget cell. Full-budget
cells necessarily have greater capacity than three-round cells.

The oracle fixes option scores, outputs, histories, selections, costs and reference
gaps without calling production proposal helpers, the decoder or measured workers.
Loading regenerates exact assets and rejects consistently rehashed drift.
Offline expectations remain explicitly separate from measured evidence.

Context and decoding stay neutral. The unchanged C/W/D minimum-gain gate of 0.1
vetoes all 45 searches and withholds audit. M18 remains the positive-audit
regression. No aggregate adaptive win is required: order-dependent wins and
losses are descriptive results, not a pass criterion.

## Offline build and validation

Run from the repository's `autocontext/` package directory:

```bash
uv sync --frozen --group dev
uv run python ../examples/weight_evolution/cpu_search_robustness/build_bundle.py \
  --output /tmp/cpu-search-robustness-bundle
uv run python ../examples/weight_evolution/cpu_search_robustness/validate.py \
  --bundle /tmp/cpu-search-robustness-bundle
```

Use a fresh directory without symlinked parents (on macOS, use `/private/tmp`
in place of `/tmp`) and freeze source before building. The measured profile
fingerprints the entire `weight_evolution` package. Replay older evidence with
its original source and runtime. Existing experiment modules and contracts retain
their identities.

## Measured CPU campaign

```bash
uv run python ../examples/weight_evolution/cpu_search_robustness/run.py \
  --bundle /tmp/cpu-search-robustness-bundle --output /tmp/cpu-search-robustness-run
uv run python ../examples/weight_evolution/cpu_search_robustness/report.py \
  --bundle /tmp/cpu-search-robustness-bundle --output /tmp/cpu-search-robustness-run
```

All 45 searches must complete and publish before any reference can run. A failed
or uncertain search blocks all 15 references with explicit zero-charge decisions.
Claimed work retains its full reservation. There are no automatic retries,
refunds or redispatches; durable outcomes can finish publication without repeating
an operation. Partial attempts remain evidence and cannot pass acceptance.

Reports separate integrity, completion, oracle calibration and robustness. They
include 135 practice prefix curves derived from retained histories, order effects,
short/full budget comparisons and all five resource meters. Reference costs stay
separate; worker duration is not total campaign cost. Theoretical audit scores
remain unmeasured after veto. References never guide selection or reselection.

## Linux/amd64 acceptance

On a native Linux/amd64 CPU host:

```bash
uv run python ../examples/weight_evolution/cpu_search_robustness/acceptance.py \
  --output /tmp/cpu-search-robustness-acceptance \
  --execution-context local-linux-amd64
```

The driver retains a fresh attempt, source/runtime identities, raw journals,
validation receipts and guarded replay inventory. The dedicated
[CPU-only workflow](../../../.github/workflows/cpu-search-robustness.yml) retains
complete or partial evidence with time reserved for artifact upload. Supplemental
Linux/ARM64 execution is labelled separately and cannot satisfy native
Linux/amd64 acceptance.

Acceptance requires all 45 searches and 15 references, **915 exact oracle
calibrations**, **90 measured robustness checks**, **135 complete practice
curves**, and **1,311 inference processes observed and reaped**. Robustness checks
cover reference-score order invariance, seeded-random order invariance,
full-budget zero regret, shorter-budget history prefixes, neutral weight tasks
and neutral context/decoding anchors. Replay blocks dispatch and writes and must
preserve every retained byte.

See the [validation record](MILESTONE20_VALIDATION.md) and
[machine-readable results](MILESTONE20_RESULTS.json) for evidence and limitations.
These are finite constructed search controls, not natural-task generalization or
Qwen reproduction. Non-neutral context/decoding robustness, TypeScript parity
and promotion remain deferred. **GPU execution remains deferred throughout.**
