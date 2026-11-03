# Qwen weight-space reproduction: contract and offline checks

Milestone 1 pins the published Qwen3.5-0.8B p50 experiment and verifies its
assets as data. It provides offline scorer fixtures before an inference worker
or editing agent is introduced. Python only; TypeScript parity is deferred.

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
default byte budget. Full-model acquisition is not needed for this milestone.
`--lock PATH` before the subcommand selects another lock of the same fixed v1
profile; changing the experiment requires a new contract version.

Exit codes: `0` means the requested validation/verification succeeded, `1` means
selected local assets were missing/invalid, and `2` means the request, lock or
download failed. All commands emit JSON; request/download errors go to stderr.

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
No new model/scorer dependencies are installed by this example.

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

Asset verification does not reconstruct or validate loaded tensors. Milestone 2
must apply the patch in an isolated worker, check the original/edited tensor
hashes and untouched tensors, and build a pinned inference adapter. Dataset
snapshots, contamination checks, official scorer dependencies and exact runtime
versions remain explicit fidelity gaps. The original forward-pass service and
some grading imports were not released. No benchmark scores or autonomous
rediscovery claims are produced by milestone 1.
