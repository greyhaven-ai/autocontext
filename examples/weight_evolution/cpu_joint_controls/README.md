# Generated joint controls and measured CPU campaign (M18)

This experiment calibrates generated positive and negative controls through the
existing CPU search, confirmation and audit workflow. It follows M17's successful
72-search pilot, where the W-specific control expectations matched but every
joint gate vetoed. M16 already tested hand-built pass/veto routing; M18 extends
that coverage to a separately pinned generated task population.

The new contract declares one instance of each of three families, two repeats
and three equal-budget policies: **18 searches and six conditional exhaustive
practice references**, with eight cases per role. The families are a joint
positive control, an already
correct baseline and a practice-only repair that fails confirmation because
its improvement is unnecessary on held-out cases. Search uses
the existing adaptive, fixed-order and seeded-random policies. Context (C),
weights (W) and decoding (D) must each improve on baseline (A) by at least 0.1
on confirmation before audit runs. Combined context/weights (CW) is reported
but is not an additional gate component.

## Contract and oracle

Generation and direct model/logit calculations fix the expected results before
the measured campaign runs. The contract pins task construction, seeds, option
identities, cases, source files, runtime, full five-meter capacity and the complete
schedule. No task or seed is selected by confirmation results. Roles use distinct
normalized token-count rays; structurally duplicate inputs are rejected without
examining inference scores.

The independent oracle does not launch workers or call the production search
policy to derive expected selections. Its expected selections and scores are
retained in the bundle before execution. Provenance and expected behavior remain
outside policy inputs. References receive practice cases only and cannot guide
search, selection or confirmation. Decoding expectations are conditional on the
two pinned inference seeds, 0 and 1; they do not imply distribution-wide improvement.

M13–M17 contracts and source modules retain their original identities. Adding new
modules changes the measured profile, which fingerprints the entire
`weight_evolution` package. **Freeze source before building a measured bundle.**
Replay old evidence using the source and environment that originally produced it.

## Build and validate without workers

From the repository's `autocontext/` package directory:

```bash
uv sync --frozen --group dev
uv run python ../examples/weight_evolution/cpu_joint_controls/build_bundle.py \
  --output /tmp/cpu-joint-controls-bundle
uv run python ../examples/weight_evolution/cpu_joint_controls/validate.py \
  --bundle /tmp/cpu-joint-controls-bundle
```

Use a fresh bundle directory. Validation regenerates the fixed task assets and
oracle expectations, checks exact inventories and rejects rehashed design drift.
It performs no model inference.

## Measured execution and read-only report

```bash
uv run python ../examples/weight_evolution/cpu_joint_controls/run.py \
  --bundle /tmp/cpu-joint-controls-bundle --output /tmp/cpu-joint-controls-run
uv run python ../examples/weight_evolution/cpu_joint_controls/report.py \
  --bundle /tmp/cpu-joint-controls-bundle --output /tmp/cpu-joint-controls-run
```

All 18 searches must complete and publish before any reference is eligible. A
failed or uncertain search blocks all six references, with explicit zero-charge
reference decisions. Claimed work retains its full reservation; there are no
automatic retries or refunds. The coordinator verifies retained child evidence
before later work and never redispatches a claimed child. A durable outcome can
finish missing publication without repeating its worker operation.

Reports separate evidence integrity, campaign completion and calibration.
Expected outcomes are shown beside measured component scores, selection and
gate results. Calibration compares ordered per-case predictions and correctness,
as well as aggregate scores. Positive rows require actual audit observations and receipts.
Veto rows must contain no audit inference claims or launches; any theoretical
oracle audit value remains explicitly unmeasured. Policy contrasts and
practice-reference gaps are descriptive, with repeats kept within tasks.
An adaptive win is not an acceptance requirement.

## Linux/amd64 acceptance

On a native Linux/amd64 CPU host:

```bash
uv run python ../examples/weight_evolution/cpu_joint_controls/acceptance.py \
  --output /tmp/cpu-joint-controls-acceptance \
  --execution-context local-linux-amd64
```

The acceptance driver requires a fresh output directory and retains one declared
attempt. It records source and environment identities, bundle validation, raw
journals, the report, guarded replay inventories and an acceptance receipt.
Caught failures retain an additional failure receipt; external termination may
leave only the earlier files. Partial evidence is never overwritten or treated
as a passing campaign.

Acceptance requires all 18 searches and six references to complete, all 342 pinned
calibration checks to match, six positive rows to execute audit, and twelve veto rows
to withhold it. Both report and terminal-run replay block worker launch,
materialization, publication and filesystem mutation, then compare every
retained file. The dedicated
[`cpu-joint-controls.yml`](../../../.github/workflows/cpu-joint-controls.yml)
workflow performs CPU-only Linux/amd64 acceptance and uploads evidence on
success or failure. An emulated amd64 host must use the explicit
`emulated-linux-amd64` label; it is not native acceptance.

## Scope and validation

See [validation](MILESTONE18_VALIDATION.md) and
[machine-readable results](MILESTONE18_RESULTS.json) for the tested source
snapshot and retained evidence. Native Linux/amd64 CI results must be reported
separately from macOS or supplemental Linux/ARM64 tests.

These are constructed calibration tasks, not evidence of adaptive superiority,
natural-task transfer or Qwen benchmark reproduction. Larger search landscapes,
performance changes, TypeScript runtime parity and promotion remain deferred.
GPU execution remains deferred throughout; no accelerator is requested.
