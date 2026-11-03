# Qwen weight-space reproduction: verified checkpoints and pinned inference

Milestone 1 pins the published Qwen3.5-0.8B p50 experiment and its offline scorer
boundaries. Milestone 2 reconstructs and independently verifies the base and
published-patch text checkpoints, then provides a pinned Docker inference runner
that records raw generation evidence. Python only; TypeScript parity is deferred.

Milestone 3 adds [dataset pins](DATASETS.md), [official CPU scorer pins](SCORERS.md),
and [bounded paired replay with saved-evidence reporting](REPLAY.md). Follow the
replay guide after reconstructing the two checkpoints below.
See [milestone 3 validation](MILESTONE3_VALIDATION.md) for real evaluator checks,
frozen plan bounds and the remaining GPU reproduction work.

Milestone 4 adds [GPU readiness, acceptance and the gated full-study workflow](GPU_ACCEPTANCE.md).
`check-gpu` is read-only; `accept-gpu` and `run-reproduction` require `--execute`
to start workers. CPU implementation does not require a GPU. See
[milestone 4 validation](MILESTONE4_VALIDATION.md) for checks and live-run status.

Both real checkpoints were reconstructed and verified on CPU during development.
The Docker execution path and GPU inference have not been exercised on NVIDIA
hardware. No reproduction benchmark scores are claimed.
See [VALIDATION.md](VALIDATION.md) for checkpoint identities, checks run and
remaining acceptance work.

The source is [Weight-Space Programming](https://rikiyat.github.io/blog/weight-space-programming/)
and its [released experiment](https://github.com/RikiyaT/lm_understand-logs/tree/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f/exp8_math).
See [NOTICE.md](NOTICE.md) for attribution and licenses.

## Validate the contract offline

From the repository's Python package directory, after `uv sync --group dev`:

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py validate-lock
uv run --frozen pytest tests/test_weight_reproduction_contract.py tests/test_weight_reproduction_scorers.py
```

`validate-lock` performs no network calls. It checks the versioned protocol,
required immutable asset identities, paths, hashes, sampling and tensor metadata.
Its JSON result includes a canonical lock digest and unresolved fidelity gaps.
Success means the contract is valid; it does not mean assets have been downloaded
or that a model is ready to run.

The [lock file](reproduction.lock.json) fixes:

- Source commit `4d09ba6d6a7e891dd8063974dfe81b3d78e3494f` and model revision
  `2fc06364715b967f1860aea9cf38778875588b17`.
- The BF16 text export, config, tokenizer configuration, chat template, original
  sampling parameters, exact math prompt suffix, and sample counts.
- Published patch/delta file hashes plus original/edited hashes for the two
  affected tensors. The patch selects 131,072 entries; 130,188 BF16 values change.
- Fourteen required source/patch/configuration files totaling 472,000 bytes.
- Optional base-model and tokenizer file hashes from pinned Hugging Face LFS
  metadata, without bundling those files in this repository.

## Explicit asset acquisition and offline verification

Fetching is a separate action. By default it downloads only the fourteen small
required assets, verifies size and SHA-256, and publishes each file atomically.
It never executes the downloaded Python, opens the patch archive, or loads a
model. Existing verified files are reused; existing corrupt files are preserved
and cause failure.

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py fetch-assets \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets

uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py verify-assets \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets
```

The `.assets/` directory is ignored by Git. The default total download ceiling is
64 MiB. A file is accepted only at the exact locked size/hash. Symlinks, special
files, path traversal, unpinned URLs, duplicate assets, and removal of required
pins are rejected. HTTPS redirects for pinned host downloads must remain HTTPS;
the fixed digest also authenticates CDN-delivered bytes.

For a selected asset, append `--asset patch_delta` (repeat the flag for multiple
IDs). A selected-assets result covers only the listed files. Optional large
assets require explicit selection, and the model shard additionally exceeds the
default byte budget. Reconstruction requires both optional files, in addition
to the fourteen required assets fetched above:

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py fetch-assets \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --asset base_weights --asset tokenizer_vocabulary --max-download-bytes 1800000000
```

The two files total 1,759,750,582 bytes. Allow additional disk space for both
reconstructed checkpoints and staging directories. Verification re-reads the
source shard and output tensors; retaining the source assets is required for
subsequent checkpoint verification and inference.

`--lock PATH` before the subcommand selects another lock of the same fixed v1
profile; changing the experiment requires a new contract version.

Exit codes: `0` means the requested operation succeeded, `1` means selected local
assets were missing/invalid, and `2` means the request, lock or operation failed.
All commands emit JSON; errors and execution resource plans go to stderr.

## Reconstruct and independently verify checkpoints

[runtime.lock.json](runtime.lock.json) fixes the Linux amd64 vLLM 0.32.0 image by
its immutable digest. Fetch the image explicitly before executing either worker:

```bash
docker pull --platform linux/amd64 \
  vllm/vllm-openai:v0.32.0@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9
```

The worker uses the local image and does not pull images automatically.
Reconstruction needs CPU and memory; it does not request a GPU. The image metadata
specifies CUDA 13.0.2. Inference additionally requires an NVIDIA GPU, a compatible
driver, and the NVIDIA Container Toolkit. This is a declared reproduction runtime;
the original experiment did not release an environment lock. Its package versions
and hardware must not be inferred from a Transformers version in model metadata.
The digest pins the environment, while runtime receipts record installed package
versions and hardware actually observed during execution.

From `autocontext/`, create an ignored output parent and inspect the resource plan:

```bash
mkdir -p ./runs/qwen-replay
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py reconstruct \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --variant base --output ./runs/qwen-replay/base
```

Without `--execute`, this prints a plan and starts no container. A plan validates
request metadata and paths; it is not a checkpoint or runtime-readiness test.
Add `--execute` to reconstruct each variant:

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py reconstruct \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --variant base --output ./runs/qwen-replay/base --execute

uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py reconstruct \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --variant published_patch --output ./runs/qwen-replay/published_patch --execute

uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py verify-checkpoint \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --checkpoint ./runs/qwen-replay/published_patch/checkpoint
```

Each `--output` must be a new directory whose parent already exists. The runner
publishes the checkpoint under its `checkpoint/` child, with a worker report and
execution receipt. The completion receipt is published last; an interrupted
publication leaves an incomplete directory, never a successful run. Existing
output directories are refused. `verify-checkpoint`
runs locally, needs NumPy, and independently compares every output tensor and
loader asset to the authenticated source inputs; it does not trust the worker's
receipt. `--runtime PATH`, when needed, is a global option before the subcommand.

The reconstruction worker uses data-only NPZ loading with pickle disabled,
checks declared array sizes before allocation, and applies the released sparse
uint16 BF16 replacements without floating-point rounding. It preserves every
retained tensor's name, shape, dtype, and bytes except the two pinned changes.
The text export retains 320 language-model tensors, including FP32 tensors, and
excludes 153 visual and 15 multi-token-prediction tensors. Receipts record the
names, shapes, dtypes and hashes of retained and excluded tensors, checkpoint
content identity, and the pinned configuration/tokenizer files. Unexpected
checkpoint files or tensor names fail verification before model loading.

The CPU development run verified both published edited tensor hashes:

| Tensor | Reconstructed BF16 SHA-256 | Changed entries |
| --- | --- | ---: |
| `model.language_model.layers.15.mlp.down_proj.weight` | `82e2d733ea64feaeb4af93a1e065641d3df98b494a8b7576ef2f52e90fe2ee53` | 65,260 |
| `model.language_model.layers.19.mlp.down_proj.weight` | `8ef3c61bc36af113ad58e89dbb0a4a14dcba5a45cb750c15ab515c82ddd12eb2` | 64,928 |

Exactly 130,188 BF16 values change. The 131,072 selected-entry count describes
the source's selected rectangle capacity; the released delta contains only
changed positions. Reconstruction cannot independently identify every selected
position that rounded back to its original value. This distinction is recorded
in each receipt.

These checks used the real downloaded assets and the reconstruction functions
directly on CPU. They establish tensor reconstruction, not successful execution
of the pinned Docker image or behavioral reproduction on a GPU.

## Run a bounded inference smoke request

[smoke.request.json](smoke.request.json) is an authored arithmetic example routed
through the MATH-500 sampling protocol, using the published-patch variant and a
64-token cap. Its dataset hash identifies the synthetic input cohort. In general,
`dataset_sha256` is caller-supplied metadata; it does not certify official dataset
membership, snapshots, splits or contamination checks. The complete request
digest also binds the actual problem text, order, variant and smoke settings.

```bash
uv run --frozen python ../examples/weight_evolution/qwen_reproduction/run.py infer \
  --asset-root ../examples/weight_evolution/qwen_reproduction/.assets \
  --checkpoint ./runs/qwen-replay/published_patch/checkpoint \
  --request ../examples/weight_evolution/qwen_reproduction/smoke.request.json \
  --variant published_patch --gpu-device 0 --output ./runs/qwen-replay/smoke
```

Inspect the plan, then repeat with `--execute` on the GPU host. Select one explicit
GPU device; the runner does not accept `all`. Defaults limit the worker to four
CPUs, 16 GiB host memory and 1,800 seconds; flags can set resource limits. The
existing Docker isolation/watchdog infrastructure supplies network denial,
read-only inputs, bounded output capture, deadline enforcement and cleanup.
This development runner does not attest exclusive GPU-memory ownership or produce
promotion evidence.

Before inference, the worker verifies the checkpoint again, renders the pinned
thinking-enabled chat template, and enforces the source decoding parameters.
It saves raw response text and token IDs, rendered prompts and prompt token IDs,
hashes, sample indices, seed, termination reasons, timing and observed runtime
identity. Output is generation evidence, with no benchmark score attached.
Missing dependencies and worker failures are infrastructure failures, not zero
scores. Prompt overflow fails explicitly rather than truncating the problem.

Smoke requests are labeled separately and do not reproduce the original token
budget. Reproduction-mode requests use 32,768 output tokens and a 34,816-token
model limit, with four samples for MATH-500 and two for GSM8K/IFEval. Official
grading and benchmark cohort certification remain later work. A full benchmark
can exceed the runner's 64 MiB report limit; shard orchestration and report
aggregation must be added before a full-cohort study.

## Scorer boundaries and fixtures

`autocontext.weight_evolution.scorers` implements extraction and aggregation
without importing a model stack:

- `score_math` extracts the source protocol's last boxed answer, discarding
  completed reasoning before the last `</think>`. A valid box in an unfinished
  or length-limited response can still score correctly, matching the source.
- `score_ifeval` requires a completed thinking section in thinking mode and
  computes strict prompt-level accuracy: all requested instructions must pass.
- Missing/wrong answers stay in the denominator. Worker/checker failures raise
  a distinct infrastructure error instead of silently becoming model failures.
- `grouped_sample_accuracy` averages samples within each problem and then across
  problems. It rejects duplicate/missing sample counts and does not compute
  pass@k. A future evaluator must also verify the complete expected problem set.

Math equivalence requires an explicitly injected verifier or an installed,
pinned `math-verify` runtime. IFEval requires an explicitly supplied official
instruction registry. Missing dependencies fail explicitly. The test fixtures
are authored synthetic cases and use injected test checkers; they validate
extraction, error handling and aggregation, **not full official benchmark parity**.
Offline scorer fixtures need no model runtime. Inference dependencies are
provided separately by the pinned Docker image.

```python
from pathlib import Path

from autocontext.weight_evolution.assets import verify_assets
from autocontext.weight_evolution.models import load_reproduction_lock

lock = load_reproduction_lock(
    Path("../examples/weight_evolution/qwen_reproduction/reproduction.lock.json")
)
report = verify_assets(lock, Path("../examples/weight_evolution/qwen_reproduction/.assets"))
print(report.to_dict())
```

## Remaining reproduction work

Run the [GPU acceptance workflow](GPU_ACCEPTANCE.md) on compatible NVIDIA hardware
before claiming inference readiness. Dataset/scorer pins, bounded cohort
shards and saved-evidence reporting are implemented in milestone 3; a real
base-versus-patch GPU evaluation is still required. Original private dataset
identity, scorer-wrapper behavior and runtime/hardware remain fidelity gaps.
Practice/discovery snapshots and contamination audits remain later work. The
original editor driver and forward-pass service were not released. Fresh
discovery and the planned comparison conditions remain subsequent milestones.

The original `reproduction.lock.json` remains unchanged so its identity and
historical milestone-1 fidelity notes stay reproducible. Those notes are not a
dynamic readiness status: checkpoint receipts record the completed tensor
verification, and the separate runtime lock describes the chosen environment.
