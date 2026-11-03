# Milestone 4 validation

Implemented on branch `codex/qwen-gpu-acceptance`, based on milestone 3 commit
`df828930240da0afbe0a388dd0b8d89141fb45f0`, in a separate worktree.

**Status: CPU implementation and validation complete; real GPU acceptance and
the full base-versus-patch benchmark run remain pending.** No GPU was provisioned,
no model inference was performed, and no measured reproduction report exists.

## Implemented workflow

- Read-only, bounded host checks inspect the coordinator/daemon architecture,
  local Docker routing, pinned installed image, physical NVIDIA device and
  CPU/RAM/disk prerequisites. Success only permits attempting live acceptance.
- Acceptance reconstructs both checkpoints through the existing isolated worker
  and independently rechecks tensors/receipts. It then requires six live smoke
  shards across all three datasets, paired prompt/tokenization agreement,
  nonempty generations and consistent image/runtime/hardware evidence.
- CUDA-selected GPU UUID, host driver version and Docker daemon identity bind
  acceptance to the host used for the full study. Worker source is frozen too.
- The full coordinator requires all three complete locked datasets, verified
  live acceptance and official scorer preflight. It reuses M3's bounded replay,
  claims/resume and offline reporting; per-shard provenance drift stops further
  dispatch. Report/receipt completion is published only after complete grading.
- All model execution commands remain explicit with `--execute`. The default
  path prints budgets and does not imply acceptance has passed.

The [GPU guide](GPU_ACCEPTANCE.md) contains the executable host workflow.
Reproduction, runtime, dataset, scorer and published-reference locks are
unchanged. Core dependencies and CI are unchanged; Python-first parity remains
explicit and there is no new production CLI/MCP surface.

## Actual local checks

The host is Darwin/arm64. Read-only inspection reached its OrbStack Docker daemon,
which reports Linux/aarch64. `check-gpu` correctly returned status 1 and these
blockers:

- coordinator platform is not Linux/amd64;
- daemon platform is not amd64;
- the pinned vLLM image is not installed locally;
- `nvidia-smi` is unavailable.

Running `accept-gpu --execute` with the verified datasets returned the same
readiness failure before creating its output directory, dispatch claims or
containers. There is no acceptance receipt to mistake for successful inference.

The acceptance dry run used real dataset identities and bounded two
reconstructions plus six inference workers: sixteen samples, at most 1,024
generated tokens and 14,400 worker-seconds with default timeouts. These are
ceilings, not measured use. The separate official scorer environment preflighted
all 500 MATH-500, 1,319 GSM8K and 541 IFEval rows successfully.

Planning independently reverified the existing real CPU-reconstructed base and
patch checkpoints. The new full plan has digest
`5603c712c147cfdd8c9d8490e80c4e871dc7ae2c68efca5b7611d5afa996618c`,
592 worker shards, a 374,865,920 generated-token ceiling and 1,065,600 reserved
worker-seconds. Its limits use those exact ceilings; the difference from the M3
plan digest reflects these explicit budget values. This plan is preparation,
not proof of live GPU acceptance. No model/container was started by planning.
The full coordinator's initial output-storage reservation is 42,479,910,912
bytes in addition to existing acceptance exports and input/image caches. Stored
reports consume part of that reservation on resume and are independently
revalidated before replay reuse.

## Tests and static checks

From `autocontext/`:

```bash
.venv/bin/pytest tests/test_weight_*.py \
  tests/test_docker_watchdog_ownership.py tests/test_docker_kernel_worker.py \
  tests/test_protected_kernel_authority_runner.py tests/test_kernel_generation_campaign.py \
  tests/test_kernel_benchmark_contract.py tests/test_training_statistical_confirmation.py
.venv/bin/ruff check src tests ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/mypy src ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/python ../scripts/check_markdown_links.py
```

The selected regression run passed **860 tests**, with **7 skipped**. These
include synthetic CUDA/Docker envelopes and mocked coordinator tests, not real
GPU execution. The skips are existing opt-in integrations, including the two
official-scorer report tests; those integrations were not rerun in this
milestone. Actual pinned-scorer preflight was run separately as described above.
Repository-wide Ruff and mypy passed (976 source files). Local Markdown links
(378 links across 154 Markdown files) and `git diff --check` passed. The complete repository pytest suite and
TypeScript runtime tests were not run.

New checks cover unsupported/malformed host observations, bounded probe output
and deadlines, nested output paths without filesystem mutation, immutable
reconstruction claims, live-only smoke evidence, actual CUDA UUID handling,
host driver/daemon changes, scorer failures before dispatch, exact full-cohort
gating, immediate stop on shard drift, resume without redispatch, profile
downgrade rejection and bounded verification of saved Markdown/JSON reports.

## Outstanding execution and fidelity work

A compatible Linux/amd64 NVIDIA host and explicit resource budget are still
needed to run the real acceptance and full cohort. The pinned image's actual
native loader, CUDA kernels, driver/toolkit compatibility and long-generation
capacity remain unvalidated. A one-problem/64-token smoke would establish only
the basic execution path; the full 8-problem shards and 32,768-token cap can
still encounter resource failures. Prompt input-length checks remain per shard.

Original private dataset/runtime/adapter parity, contiguous versus upstream
strided batching, contamination audits, practice/confirmation cohorts and later
editing/comparison conditions remain the fidelity gaps described in
[milestone 3](MILESTONE3_VALIDATION.md). Local hashes and receipts are consistency
checks, not remote attestation or exclusive GPU-memory ownership. Completion of
a measured report will not automatically assert reproduction success or
authorize promotion.
