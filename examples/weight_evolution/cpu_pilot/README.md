# Full measured CPU pilot

Milestone 17 declares **72 measured searches** using the existing twelve control
tasks, two inference/order-seed repeats and three equal-budget policies. The
tasks, options, seeds, eight cases per role, scorer, worker limits and joint
confirmation gate are unchanged. It reuses M16's measured search and reference
adapters and per-operation journals. GPU execution remains deferred.

See the [M17 validation](MILESTONE17_VALIDATION.md) and
[machine-readable results](MILESTONE17_RESULTS.json) for test evidence and the
remaining native Linux/amd64 full-pilot requirement.

The complete schedule also reserves **24 conditional exhaustive practice
references**, one per task/repeat. They become eligible only after all 72 source
searches complete and their results are published. Any failed or uncertain
search blocks all reference dispatch; each blocked decision has zero actual
charge. Search and maximum reference capacities are pinned separately before
execution. Claimed work retains its full reservation without refunds or retries.

Run from the repository's `autocontext/` Python package directory:

```bash
uv run python ../examples/weight_evolution/cpu_pilot/build_bundle.py \
  --output ../runs/cpu-measured-pilot-bundle
uv run python ../examples/weight_evolution/cpu_pilot/validate.py \
  --bundle ../runs/cpu-measured-pilot-bundle
uv run python ../examples/weight_evolution/cpu_pilot/run.py \
  --bundle ../runs/cpu-measured-pilot-bundle --output ../runs/cpu-measured-pilot
uv run python ../examples/weight_evolution/cpu_pilot/report.py \
  --bundle ../runs/cpu-measured-pilot-bundle --output ../runs/cpu-measured-pilot
```

Building and validation perform no model inference. Freeze the source snapshot
before building: the measured profile fingerprints the weight-evolution package,
so even adding a module changes that profile. The pilot contract separately pins
its own orchestration, M16 adapter implementation, native worker runtime, measured
profile, failure codec, exact roster and full five-meter capacity. Earlier
contracts and retained runs keep their original identities.

`run.py` verifies every retained prefix before later unclaimed work. It may
finish publication from a valid retained outcome, but never redispatches a
claimed search or reference. Missing outcomes remain uncertain and charged.
`report.py` verifies complete or partial evidence without writes or inference.
Neither command reselects a candidate or sends held-out cases to references.

## Linux/amd64 acceptance

The acceptance driver requires a fresh directory and a Linux/amd64 Python
process. It records the source and environment before dispatch, attempts the
declared pilot once, saves the report, and verifies report/terminal replay with
worker launch, checkpoint materialization, publication and filesystem writes
blocked. It retains raw partial evidence and exits unsuccessfully when the pilot
is incomplete, calibration fails, sources drift or replay fails.

```bash
uv run python ../examples/weight_evolution/cpu_pilot/acceptance.py \
  --output ../runs/cpu-measured-pilot-acceptance \
  --campaign-id cpu-measured-pilot-acceptance-v1 \
  --execution-context local-linux-amd64
```

Use `--execution-context emulated-linux-amd64` when an ARM host emulates x86_64.
The context is operator-declared metadata, not hardware attestation. Emulated
acceptance does not close the native Linux/amd64 CI gap. The
[CPU measured pilot workflow](../../../.github/workflows/cpu-measured-pilot.yml)
runs one Ubuntu 24.04 X64 job for relevant pull requests and can also be
dispatched manually. Acceptance has a 165-minute step deadline within a
180-minute job limit, leaving time to preserve partial evidence. The first
90-minute attempt retained all 72 searches and 14 of 24 references before
cancellation; the revised allowance provides additional time for full-prefix checks
and final guarded replays. Progress logs contain advisory publication counts,
which are not verified results and do not determine acceptance.
The workflow records the exact checked-out commit and
GitHub run/attempt, installs locked dependencies, and uploads complete or partial
evidence even after failure. There is no matrix, automatic retry, GPU setup or
external model provider. A newer revision does not cancel an in-progress pilot.
An explicitly rerun job has a new declared attempt identity and never overwrites
the earlier evidence.
The `git_commit` field identifies the checked-out PR head; GitHub's separately
retained `GITHUB_SHA` can identify the pull-request merge-event revision.

The retained directory contains `attempt.json`, the pinned `bundle/`, raw
`campaign/` journals and checkpoint stores, `validation.json`, `report.json`,
`replay.json` with full file hashes, and `acceptance.json`. A caught exception
also creates `attempt-failure.json`. An external termination may leave only the
earlier records; their absence is not evidence that execution did not occur.

## Interpretation

Evidence integrity, campaign completion and calibration acceptance are separate
results. A valid failed-prefix report remains a partial pilot, not a passing
execution. Every scheduled row stays visible, including failed, uncertain,
blocked and unstarted work. Unknown usage is not zero.

Calibration and descriptive policy/reference comparisons remain within the
three existing families. Their weight-positive controls do not guarantee the
joint C/W/D gate passes; expected vetoes withhold audit. Separate regression
fixtures cover actual pass/veto audit routing. This pilot does not add a new
positive joint-gate family, recover M13's missing historical observations, or
establish adaptive-search superiority, natural-task transfer or Qwen benchmark
reproduction. TypeScript runtime parity and promotion remain deferred.

M16's Linux/amd64 integration CI passed; that gap is closed. M17's full-pilot
Linux/amd64 evidence is a separate acceptance requirement and must be reported
from its own retained attempt. GPU/Qwen inference and the published
base-versus-patch reproduction remain unexecuted.
