# Milestone 2 validation

Validated on 2026-11-02, on branch `codex/qwen-reconstruction-runner`, based on
milestone 1 commit `0bd152db9df7736000610f63e1162404daa46b8a`.

## Real checkpoint replay

All 16 pinned assets passed size/SHA-256 verification, including the optional
base shard and tokenizer. Both checkpoints were reconstructed directly on CPU
(Python 3.13.1, NumPy 2.4.6) and independently verified against those assets.
No upstream program was executed. The original reproduction lock is unchanged:
`c90af4037a6cd9e8d368da4e9a65a0cde7d1102dc883602948edbfe4009a7cf0`.

| Evidence | Base | Published patch |
| --- | --- | --- |
| Retained text tensors | 320 | 320 |
| Excluded visual/MTP tensors, individually hashed | 168 | 168 |
| Changed BF16 entries | 0 | 130,188 |
| Checkpoint SHA-256 | `a9ba1786becfad2aaa69d12bd2ef225eef5e01cbf8f1954d0bbc114635de296e` | `51fa940a5d9ac48df33c2923793c600976be27f7eec9760a846149135d2080cb` |
| Tensor-content digest | `c464c59917f632718ce6b4a928f71a829ef00d9b98dc17ce23a6cb11245adc99` | `499bbe639716480e19eb0831717dedc835a9d1b3515e846f4b914646d8332ca6` |

Both edited tensors match the published hashes in the reproduction lock.
The independent official safetensors 0.6.2 reader also opened both exports,
enumerated 320 tensors, and confirmed BF16 `[1024, 3584]` patch tensor metadata.
That interoperability check was run in a temporary environment; it does not add
a package dependency to autocontext.

## Offline and regression checks

From the repository's Python package directory:

```bash
.venv/bin/pytest \
  tests/test_weight_reproduction_contract.py tests/test_weight_reproduction_scorers.py \
  tests/test_weight_checkpoint_reconstruction.py tests/test_weight_runtime.py \
  tests/test_weight_inference.py tests/test_weight_docker_runner.py \
  tests/test_weight_worker_entrypoint.py tests/test_docker_watchdog_ownership.py \
  tests/test_docker_kernel_worker.py tests/test_protected_kernel_authority_runner.py \
  tests/test_kernel_generation_campaign.py tests/test_kernel_benchmark_contract.py
.venv/bin/ruff check src tests ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/mypy src ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/python ../scripts/check_markdown_links.py
```

The selected test run passed **510 tests**, with **5 skipped**. Repository-wide
Ruff and mypy passed (964 source files), as did Markdown links and
`git diff --check`. The example's inference dry run bound the synthetic request
to four samples and a maximum of 256 generated tokens, without executing a model.
The full repository pytest suite was not run.

## Remaining acceptance work

The immutable Linux/amd64 vLLM image was verified through registry metadata.
Neither the real Docker worker path nor CUDA inference was executed: this host
has no NVIDIA GPU. Container lifecycle and inference backend tests use explicit
fixtures. The chosen image must pass reconstruction and base/edited inference
smoke tests on a compatible GPU host before being described as a working inference
environment. Its exact observed Python/package versions and hardware will be
recorded in execution evidence.

Dataset provenance, official graders, bounded full-cohort sharding and behavioral
base-versus-patch reproduction remain milestone 3 work. No benchmark scores,
editing-agent discovery, hard GPU-memory isolation, or promotion evidence are
claimed here.
