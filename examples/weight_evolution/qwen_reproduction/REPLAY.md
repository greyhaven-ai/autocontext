# Frozen base-versus-patch replay

Milestone 3 pins datasets and CPU scorers, partitions paired inference into
bounded shards, and reconstructs a report from complete saved evidence. It
reuses milestone 2's checkpoint verification and isolated inference runner.
Actual CUDA inference remains unvalidated on this development host; the commands
below provide the execution path, not measured reproduction scores.

Run commands from `autocontext/`. First follow the [checkpoint guide](README.md)
to acquire model assets and create `runs/qwen-replay/base/checkpoint` and
`runs/qwen-replay/published_patch/checkpoint` on a compatible host. The original
reproduction and inference runtime locks stay unchanged.

## Acquire and validate evaluator inputs

Dataset acquisition stores benchmark data outside Git. Scorer acquisition stores
verified official source and tokenizer resources; fetching does not execute them.

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py fetch-datasets \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py verify-datasets \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py fetch-scorers \
  --scorer-root ../examples/weight_evolution/qwen_reproduction/.scorer-assets
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py verify-scorers \
  --scorer-root ../examples/weight_evolution/qwen_reproduction/.scorer-assets
```

The [dataset guide](DATASETS.md) documents exact snapshot and normalization pins.
The [scorer guide](SCORERS.md) gives the separate Python 3.13 installation with
artifact hashes. After installing that environment, preflight every gold answer
and official instruction before scheduling model work:

```bash
PYTHONPATH=src ../examples/weight_evolution/qwen_reproduction/.scorer/.venv/bin/python \
  ../examples/weight_evolution/qwen_reproduction/run.py preflight-scorers \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets \
  --scorer-root ../examples/weight_evolution/qwen_reproduction/.scorer-assets
```

Preflight executes only the authenticated official scorer code. It runs no model
and downloads nothing. Gold answers and IFEval instructions stay with the
evaluator; inference requests contain problem IDs and prompts only.

## Freeze the cohort and budget

Plans bind the reproduction, dataset, scorer, published-reference and runtime
locks, both independently verified checkpoints, ordered problem membership,
sample counts, mode and all shard requests. Changing any of these changes the
plan identity. Each shard covers the same problems for both variants; the
controller alternates which variant runs first across shard pairs.

This bounded diagnostic plan selects the first 16 pinned MATH-500 rows with a
64-token cap. It is labeled smoke evidence, uses benchmark items for diagnostics,
and is not an independent held-out study:

```bash
mkdir -p ./runs/qwen-replay
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py plan-replay \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets \
  --base-checkpoint ./runs/qwen-replay/base/checkpoint \
  --patch-checkpoint ./runs/qwen-replay/published_patch/checkpoint \
  --dataset math500 --smoke-max-new-tokens 64 --smoke-problems 16 \
  --max-total-generated-tokens 10000 --max-total-wall-seconds 7200 \
  --output ./runs/qwen-replay/smoke-plan.json
```

Planning verifies real checkpoint/data identities but executes no model. Output
must be a new plan file. Omit both `--dataset` and `--smoke-max-new-tokens` for a
full three-dataset plan at the source's 32,768-token cap. Full reproduction mode
requires every row in each selected locked dataset, never a silently truncated
cohort. Supply deliberate total token and worker-time ceilings; planning rejects
insufficient ceilings.

With the default eight-problem shards and the current full datasets, the full
paired plan contains **592 worker shards**, at most **374,865,920 generated
tokens**, and **1,065,600 reserved worker-seconds**. These are conservative
ceilings, not measured use or a monetary estimate. No such GPU study was run
during implementation. Its theoretical report-disk ceiling is about 39.7 GB.

Limits can be set with `--max-shard-problems`, `--max-shard-generated-tokens`,
`--max-report-bytes`, `--shard-timeout-seconds`, `--memory-mb` and `--cpu-count`.
The report-size estimator reserves token/prompt overhead; it is not a proof of
the byte size of arbitrary decoded Unicode. The worker independently enforces
the hard byte cap. Oversized evidence fails the shard; no answers are truncated
or dropped to make an aggregate complete. Revise and freeze a new plan if bounds
need to change; existing runs cannot be resumed under changed limits.

## Execute or reconcile the frozen plan

Inspect the saved plan without starting containers:

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py run-replay \
  --plan ./runs/qwen-replay/smoke-plan.json \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets \
  --base-checkpoint ./runs/qwen-replay/base/checkpoint \
  --patch-checkpoint ./runs/qwen-replay/published_patch/checkpoint \
  --gpu-device 0 --output ./runs/qwen-replay/smoke-run
```

Add `--execute` on the GPU host to dispatch serially. This uses the locally
installed immutable image and one explicit GPU device, with the shared isolation
and deadline watchdog. It does not provision paid hardware. The plan is printed
before dispatch, and the replay directory records its exact inputs:

```text
plan.json / harness.json       frozen study and worker-source identities
claims/<shard>.json             durable pre-dispatch token/time reservation
requests/<shard>.json           projected inference request, without answers
runs/<shard>/                  M2 raw report and execution receipt
completed/<shard>.json          independently checked evidence hashes
failures/<shard>.json           unreconciled infrastructure failure
replay.receipt.json             completion of the entire planned run
```

Repeating the same command resumes only complete, verified shards without
redispatching them. A claim with no complete valid result is ambiguous and stops
the run; there is no automatic retry or budget reset. A crash after verified
worker publication can be reconciled without running inference again. Modified
plans, checkpoints, datasets, source code or saved completion evidence are
rejected. Restore the original checkout to resume an existing run. Operator
resolution of ambiguous in-flight work remains explicit; the example does not
provide a command that silently deletes claims or retries them.

## Rebuild the reproduction report

Use the pinned CPU scorer environment to evaluate saved outputs. This command
does not need CUDA or contact a model:

```bash
PYTHONPATH=src ../examples/weight_evolution/qwen_reproduction/.scorer/.venv/bin/python \
  ../examples/weight_evolution/qwen_reproduction/run.py report-replay \
  --plan ./runs/qwen-replay/smoke-plan.json \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets \
  --scorer-root ../examples/weight_evolution/qwen_reproduction/.scorer-assets \
  --runs-root ./runs/qwen-replay/smoke-run --output ./runs/qwen-replay/smoke-report
```

The evaluator requires exact shard/problem/sample coverage for both variants.
It verifies raw-report hashes, resource receipts, checkpoint/request/runtime
identities and consistent harness, prompt tokenization and hardware observations.
It validates all gold/instruction inputs before scoring and streams raw evidence
one shard at a time. Altered evidence between validation and scoring fails.
Missing or failed observations prevent publication; wrong/missing model answers
remain in the denominator.

The new report directory contains `report.json`, `report.md` and a final
`complete.json` marker. The JSON retains scored sample identities and hashes
linking back to raw evidence, grouped per-problem scores, provenance and measured
costs. Markdown shows base/patch absolute accuracy, descriptive paired intervals,
token-limit rates, mean/p95 generated tokens and worker elapsed time. Accuracy
is a mean over samples within each problem, then over problems; it is not pass@k.
Paired-t intervals use problems as the observation unit. Small/zero-variance
cohorts and multiple benchmarks do not establish noninferiority, familywise
significance or promotion.

The [published-results lock](published-results.lock.json) authenticates the final
upstream aggregate JSON files included as attributed reference data. Full-cohort
reports show exact measured-minus-published discrepancies. Smoke/subset/fixture
results show published values only as context, with discrepancies suppressed.
`--allow-fixture` permits explicit synthetic worker evidence for integration
tests and labels the entire report **fixture**; it cannot certify reproduction.

The unchanged source checkpoints, chosen runtime, data snapshots and official
checkers are pinned independently. Original private dataset identity, some
upstream scorer-wrapper behavior and original hardware remain uncertain.
Contiguous batching differs from the original strided batches and can affect
sampled outputs. Practice/confirmation cohorts, contamination audits, fresh
editing, comparison conditions and promotion remain later work. Reports make
no automatic claim that behavioral reproduction succeeded.
