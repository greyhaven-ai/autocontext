# CPU failure evidence

Milestone 14 preserves useful evidence when a bounded CPU search fails. Its
separately pinned supervisor calls the unchanged CPU search and model worker,
captures the live exception chain, and binds the observation to the claimed
operation and retained child evidence. GPU execution remains deferred.

M13's pilot completed 71 of 72 searches. One eight-case `confirmation-CW` batch
failed with `CpuRunnerError`; the existing journal retained its exception class
but lost the underlying message. This milestone cannot recover that missing
observation or establish what caused the historical failure. That campaign stays
visible, with its failed row, reserved capacity and blocked references.

The [M14 validation record](MILESTONE14_VALIDATION.md) documents the declared
follow-up, measured results, compatibility checks and remaining gaps.

## Evidence and replay

Failure observations are separate from the original child journal. They record
what the supervisor observed during the first dispatch, including bounded
exception text and operation bindings. A diagnostic message is evidence, not a
verified root-cause classification. Unavailable process and resource details
remain unknown.

Successful searches keep their existing receipts. Failed or interrupted searches
retain their complete reservation. Resume verifies existing evidence and does
not retry a claimed child or manufacture a missing live observation.
A completed child can recover its parent result from verified retained evidence;
a missing live observation stays explicitly unavailable. Reports perform no
inference and do not write evidence.

The M11–M13 sources, contracts and retained records remain unchanged. The new
parent contract identifies its actual supervisor implementation; it does not
attribute new orchestration behavior to an earlier coordinator.

## Declare and run

Run these commands from the repository's `autocontext/` directory on Linux or
macOS. Build the bundle on the execution host: `runtime.json` pins that runtime.
Use fresh, separate bundle, campaign, plan and diagnostic directories.

```bash
uv sync --locked --group dev

uv run python ../examples/weight_evolution/cpu_failures/build_bundle.py \
  --preset fixture --campaign-id cpu-failure-fixture-v1 \
  --output ../runs/cpu-failures/bundle

uv run python ../examples/weight_evolution/cpu_failures/run.py \
  --bundle ../runs/cpu-failures/bundle validate

uv run python ../examples/weight_evolution/cpu_failures/run.py \
  --bundle ../runs/cpu-failures/bundle run --output ../runs/cpu-failures/campaign

uv run python ../examples/weight_evolution/cpu_failures/run.py \
  --bundle ../runs/cpu-failures/bundle report --output ../runs/cpu-failures/campaign
```

The fixture declares six task instances, one repeat and three policies: eighteen
searches and, if all searches complete, six shared practice references. The
`pilot` preset declares twelve instances and two repeats: seventy-two searches
and twenty-four conditional references. Both preserve the M13 task families,
eight cases per role, four options per mechanism and three search trials.

`validate` and `report` perform no model execution or output writes. The campaign
report contains the existing family-specific metrics under `campaign`, with
separate `failures` and `failure_coverage` under the new supervisor identity.

## Diagnose a complete campaign

Only create a reference plan after every source search completes. A completed
joint veto is eligible; a failed or uncertain source is not. Plan creation is
read-only with respect to the source campaign and writes only its new plan.

```bash
uv run python ../examples/weight_evolution/cpu_failures/run.py \
  --bundle ../runs/cpu-failures/bundle diagnostic-plan \
  --campaign-output ../runs/cpu-failures/campaign --plan ../runs/cpu-failures/plan

uv run python ../examples/weight_evolution/cpu_failures/run.py \
  --bundle ../runs/cpu-failures/bundle diagnostic-validate \
  --campaign-output ../runs/cpu-failures/campaign --plan ../runs/cpu-failures/plan

uv run python ../examples/weight_evolution/cpu_failures/run.py \
  --bundle ../runs/cpu-failures/bundle diagnostic-run \
  --campaign-output ../runs/cpu-failures/campaign --plan ../runs/cpu-failures/plan \
  --output ../runs/cpu-failures/diagnostic

uv run python ../examples/weight_evolution/cpu_failures/run.py \
  --bundle ../runs/cpu-failures/bundle diagnostic-report \
  --campaign-output ../runs/cpu-failures/campaign --plan ../runs/cpu-failures/plan \
  --output ../runs/cpu-failures/diagnostic
```

Each reference evaluates the baseline and all twelve declared C/W/D options on
practice only, sharing its results across policies. It reuses existing
checkpoints and keeps its capacity separate from source search budgets. The new
plan binds the actual supervisor report and ledger as well as the original
search evidence. Diagnostic reports retain per-reference evidence and
family-specific transfer summaries without an unselected held-out evaluation.

## Follow-up scope

A follow-up campaign must be declared separately before execution. Its policies,
task construction, search limits, stopping rule and joint C/W/D confirmation gate
remain fixed. Every scheduled outcome is retained, including failures. A W
component passing alone cannot unlock audit.

Practice references have a separate bounded budget and require every source
search to complete. A failed or uncertain source blocks the reference plan.
Family-specific reports preserve missing denominators and distinguish observed
confirmation from analytical control expectations. The workflow does not
automatically retry or replace a campaign. It does not select a pooled winner or
claim an adaptive advantage.

These constructed tiny CPU controls exercise failure reporting and transfer
calibration. They do not establish language-model performance or diagnose M13's
historical failure. Qwen/GPU acceptance, the published base-versus-patch
reproduction, live proposal models, larger CPU models, arbitrary programs,
TypeScript parity and promotion remain deferred.
