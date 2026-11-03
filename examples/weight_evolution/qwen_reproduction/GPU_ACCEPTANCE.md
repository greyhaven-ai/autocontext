# GPU acceptance and full reproduction

Milestone 4 implements the host checks, live acceptance gate and complete-study
coordinator. **Real GPU acceptance and the full benchmark run remain pending.**
CPU tests exercise synthetic execution envelopes; they do not establish that the
pinned CUDA environment works or that the published patch improves scores.

No GPU is needed to develop/test this workflow, validate downloaded assets,
plan budgets, or score saved outputs. A Linux/amd64 NVIDIA host is required to
validate the pinned vLLM inference environment and produce real model outputs.
These commands never rent hardware or pull an image. Run them directly on the
host owning the GPU, Docker daemon and local files; remote Docker endpoints and
MIG devices are outside this initial acceptance workflow.

## Prepare the host and inputs

Use the frozen checkout throughout acceptance and full replay. Changes to worker
source invalidate acceptance and require the original checkout for resume.
Follow the [asset guide](README.md), [dataset guide](DATASETS.md) and
[scorer installation guide](SCORERS.md). Install the pinned image using the exact
digest in [runtime.lock.json](runtime.lock.json) as described in the asset guide.
The host needs a compatible driver and NVIDIA Container Toolkit. Readiness
inspection alone does not establish driver/CUDA/model compatibility.

All commands below run from `autocontext/`. The `.scorer/.venv` is the separate
hash-locked Python 3.13 CPU scorer environment; inference still runs in Docker's
pinned Python environment. In this example, the physical GPU selector is `0`.

Inspect prerequisites without starting any container:

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py check-gpu \
  --gpu-device 0 --output-parent ./runs/qwen-m4 \
  --required-free-bytes 42479910912
```

The JSON lists blockers and exact observations for host/daemon platform,
installed image, physical GPU UUID, driver, CPU/RAM and free output disk. Exit
status 1 means prerequisites are unmet, and 2 means invalid inputs or another
command error. `ready_for_acceptance` only permits attempting the live smoke.
The default full-study storage reservation is about 42.5 GB of additional free
space after acceptance, excluding existing acceptance exports, the Docker image
and input assets. Free capacity is an observation, not an exclusive reserve.

## Reconstruct and accept both variants

Inspect the fixed acceptance budget first:

```bash
PYTHONPATH=src ../examples/weight_evolution/qwen_reproduction/.scorer/.venv/bin/python \
  ../examples/weight_evolution/qwen_reproduction/run.py accept-gpu \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets \
  --scorer-root ../examples/weight_evolution/qwen_reproduction/.scorer-assets \
  --gpu-device 0 --output ./runs/qwen-m4/acceptance \
  --max-total-generated-tokens 1024 --max-total-wall-seconds 14400
```

Add `--execute` to the same command on the GPU host. Before dispatch, it checks
readiness and preflights the pinned scorer against every gold/instruction row.
Then it runs two Docker reconstructions and six inference shards: one locked
problem per dataset for each variant. With the default 64-token cap, the sixteen
samples allow at most 1,024 generated tokens. Eight workers with 1,800-second
timeouts reserve at most 14,400 worker-seconds; these are ceilings, not expected
duration or a price estimate. No score threshold is used for loader acceptance.

The gate requires independently verified checkpoint tensors, nonempty generated
text/tokens, paired prompt/tokenizer agreement, live CUDA evidence, and matching
image, runtime, harness and actual GPU UUID throughout. The CLI resolves a GPU
index to its physical UUID before dispatch. Driver and daemon identities bind
acceptance to the host stack used for the full run.

Outputs include `manifest.json`, durable claims/completions, reconstruction
evidence under `reconstruction/{base,published_patch}`, the six-shard `smoke/`
replay and final `acceptance.receipt.json`. Verified completed work resumes
without redispatch. An ambiguous claimed worker remains an explicit error.

This smoke initializes the full context window but uses one prompt and short
outputs. It does not prove full-batch/long-output capacity, correct benchmark
answers or behavioral reproduction. Whole-cohort tokenizer length validation
still happens shard by shard; any oversized input stops execution without
silently dropping that benchmark row.

## Freeze and run the full paired study

After acceptance, freeze a full plan from its independently verified exports:

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py plan-replay \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets \
  --base-checkpoint ./runs/qwen-m4/acceptance/reconstruction/base/checkpoint \
  --patch-checkpoint ./runs/qwen-m4/acceptance/reconstruction/published_patch/checkpoint \
  --max-total-generated-tokens 374865920 --max-total-wall-seconds 1065600 \
  --output ./runs/qwen-m4/full-plan.json

PYTHONPATH=src ../examples/weight_evolution/qwen_reproduction/.scorer/.venv/bin/python \
  ../examples/weight_evolution/qwen_reproduction/run.py run-reproduction \
  --plan ./runs/qwen-m4/full-plan.json --acceptance ./runs/qwen-m4/acceptance \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --dataset-root ../examples/weight_evolution/qwen_reproduction/.datasets \
  --scorer-root ../examples/weight_evolution/qwen_reproduction/.scorer-assets \
  --gpu-device 0 --output ./runs/qwen-m4/full
```

The second command is a dry inspection until `--execute` is added. Its
`acceptance_verified: false` explicitly distinguishes planning from acceptance.
The full coordinator requires all 500 MATH-500, 1,319 GSM8K and 541 IFEval rows;
smoke/subset plans cannot enter this path. Defaults yield 592 worker shards and
at most 374,865,920 generated tokens. The 1,065,600 worker-second reservation is
296 hours of summed timeout ceilings, not expected GPU usage. Choose an actual
host/runtime budget before dispatch; insufficient frozen bounds fail planning.

Execution rechecks all acceptance evidence, official scorer prerequisites and
host identity before inference. Every completed shard must match the accepted
CUDA UUID/runtime/harness before another shard can dispatch. Infrastructure
errors stop the run and retain claims; they never become wrong-answer scores.

The coordinator writes `manifest.json`, `readiness.initial.json`, the existing
`replay/` evidence tree, and independently scored `report/report.json` and
`report/report.md`. `reproduction.receipt.json` is published only after complete
coverage and report publication. Resume revalidates completed evidence and
existing reports without silently overwriting incomplete or altered artifacts.
The lower-level `run-replay` remains available for M3 diagnostics; use
`run-reproduction` for the M4 acceptance gate.

Reports preserve the [M3 metric and fidelity contract](REPLAY.md): per-problem
mean sample accuracy, paired descriptive intervals, generation costs and exact
differences from pinned published aggregates. A complete report is a measured
comparison, not automatic proof of reproduction success or a promotion decision.
Unreleased private datasets/runtime/adapters, batching differences, contamination
audits, fresh editing and comparison conditions remain separate gaps.
