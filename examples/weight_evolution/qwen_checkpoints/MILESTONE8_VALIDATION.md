# Milestone 8 validation — native tiny-checkpoint edits

Validated locally on 2026-11-03 on Apple Silicon/macOS with Python 3.13.1 and
the locked development environment. Worktree: `qwen-checkpoint-editing`; branch:
`codex/qwen-checkpoint-editing`. Base: merged `main` commit
`c5aeeb6593c9ed08103cfec333be5b3db459bf31`, including milestones 1–7 and
the reviewed dependency/provider integration.

## Scope implemented

The new data-only sparse edit core reuses M2's bounded safetensors reader,
deterministic writer, finite-value checks, and file/tensor identities. It pins
complete inventories, permits only specified tensors/indices, preserves exact
shape/dtype and unchanged payloads, rejects no-ops/nonfinite replacements, and
independently reconstructs every output tensor before accepting saved receipts.

Actual checkpoint materialization occurs inside M6's durable W edit dispatch,
before the edit is acknowledged. Every practice probe re-verifies checkpoint
bytes. C/D retain the base. The bridge verifies retained materializations on
resume even when M6 returns existing receipts without calling the adapter.
Typed native seals link the verified checkpoint to practice selection, restored
revision, journal-derived costs, and the legacy controller's compatibility
projection. Separate native confirmation verifies those seals again before
the existing one-look gate.

Existing M2–M7 implementation files and fixture pins remain unchanged. The new
checkpoint handles are compatibility strings only within those older schemas;
the authoritative export is `TensorCandidateArtifact`, explicitly represented
as `verified_safetensors_fixture`. New literal confirmation/audit catalogs bind
the new cells. No prior model observations are relabeled for edited checkpoints.

## Checks

Run from `autocontext/`:

```bash
uv sync --locked --group dev
.venv/bin/pytest tests/test_weight_*.py \
  tests/test_dict_type_consistency.py \
  tests/test_docker_watchdog_ownership.py tests/test_docker_kernel_worker.py \
  tests/test_protected_kernel_authority_runner.py tests/test_kernel_generation_campaign.py \
  tests/test_kernel_benchmark_contract.py tests/test_training_statistical_confirmation.py -m 'not live' -ra
.venv/bin/ruff check src tests ../examples/weight_evolution/qwen_reproduction/run.py \
  ../examples/weight_evolution/qwen_comparison ../examples/weight_evolution/qwen_sessions \
  ../examples/weight_evolution/qwen_adaptive ../examples/weight_evolution/qwen_checkpoints
.venv/bin/mypy src ../examples/weight_evolution/qwen_reproduction/run.py
.venv/bin/mypy ../examples/weight_evolution/qwen_checkpoints
.venv/bin/python ../scripts/check_markdown_links.py
git diff --check
```

Updating the M8 worktree to merged `main` exposed stale source pins from M2's
merged annotation changes and two new dictionary annotations that violated the
repository convention. The annotations now use `dict[str, Any]`; the M8 builder
regenerated only `workflow.json` and `fixtures.lock.json`. Base checkpoint bytes,
data, response catalogs, and all earlier milestone fixtures remain unchanged.

The selected regression suite passed **1,490 tests**, with **7 skipped**, in
174.61 seconds. Skips require explicit official scorer assets/environment (2),
real Docker/MIG (1), or native Windows launcher/Job Object behavior (4). They
provide no GPU acceptance or model-reproduction evidence.

The 141 new tests cover 60 tensor-edit cases, 27 candidate-bridge cases,
26 native-seal/confirmation cases, and 28 end-to-end workflow cases. They
exercise exact BF16/F16/F32/F64 values, allowed tensors, finite/shape/dtype/hash
constraints, independent recomputation, unchanged payloads, bounded storage,
interrupted publication, canonical handles, pre-probe materialization,
checkpoint deletion/corruption, selected and unselected checkpoint retention,
earlier-winner restoration, native/legacy linkage, type-substitution tampering,
one-look confirmation, vetoed audits, deterministic fixture rebuilding,
offline-only execution, and exact successful/vetoed resume without dispatch.

Independent review reproduced a concurrent registry race: a second cooperating
operation could inspect the first operation's staging directory before acquiring
the registry lock. Materialization and existing-store reads now share that lock.
Three threaded tests pause publication and verify that concurrent operations
wait, then authenticate the completed checkpoint. Read-only verification does
not create a missing store. The native verification and plain selection checks
also reject rehashed integer/boolean substitutions rather than normalizing them.

TypeScript execution parity remains deferred; no TypeScript runtime tests were
run. This validation uses selected regressions, not all repository tests.

Ruff passed. Mypy passed for 993 source/reproduction files and the two new example
files. Markdown validation passed for 423 local links across 162 files, and
`git diff --check` passed.

## CPU evidence and execution limits

The base is a **340-byte safetensors file** with four tensors. W's three programs
each change two entries from the pinned immutable base. The selected program
changes BF16 1 to 3 and F32 -1 to 2; protected F16/F64 payloads remain byte-for-byte
identical. The third W candidate regresses on literal practice responses, so the
controller restores round two's verified checkpoint before sealing. C's third
proposal and D's third probe fail and retain their charges.

Each full run contains 33 session operations, three edited checkpoint outputs,
three native seals, and a separate confirmation gate. The default hand-authored
confirmation passes C/W/D and reaches the 60-cell audit. A separate D-veto fixture
retains all search and checkpoint evidence but emits no audit. Neither outcome
is a measured consequence of the tensor values: probes and held-out responses
are prerecorded literal fixture catalogs.

Two independent complete runs and an exact resume produced **148 byte-identical
files**, including the real checkpoint outputs. Preflight succeeded without
creating materializations. The separate veto run retained the same native
candidate identities and correctly withheld its audit; its exact resume kept
all 145 output files byte-identical. Rebuilding the six-file fixture bundle
also reproduced every byte.

| Artifact | SHA-256 / digest |
| --- | --- |
| Input checkpoint | `5afa821526fe6ff31bb8c5ab7255e8889a5085a5cf2f3c6e89f6e6cf9482f6da` |
| Selected W program | `692dce89e7d8d8b9592b4ac39b4c00221ecb16ca9ae32af335e83058c4b2c48f` |
| Selected W output checkpoint | `a4ad53da6ad6135427f2fddd38f99aeeb02eb7a2b01673f2f4af4e82e3be756b` |
| Default workflow | `4036edb892e8259e06a0bf2c8bbcda7bce85263a652c53ecf754dc1b97ab3919` |
| Native W seal | `58836fbd858ae84c9df7113afc0fc3dd0087e9376e437f6bd0a766f6504c8068` |
| Fixture audit report | `9ae20c2295d36d8a3b23a96e52bea72eca23a5459c7334e72ba65f59fece79c5` |

No GPU, Docker worker, API model request, Qwen forward pass, downloaded model,
real benchmark evaluation, arbitrary code, or paid infrastructure ran. Tiny
tensor-edit correctness is now tested; model-quality improvements are not.
Search bytes/simulated seconds are not real token costs or hardware timing.
Checkpoint-size/candidate/replacement caps bound this trusted CPU workflow;
they do not provide a hard execution timeout or an untrusted-program sandbox.

Full Qwen checkpoint scale, arithmetic/optimizer edit languages, generated
tensor programs, inference-backed probes, benchmark practice/confirmation
selection and contamination review, independent repeated-search trials, GPU
acceptance, and published-score reproduction remain open. Existing GPU/runtime,
official scorer and full base-versus-patch gaps remain in
[M4 validation](../qwen_reproduction/MILESTONE4_VALIDATION.md). Native local hashes
are not remote attestation or a defense against replacing the entire experiment
and its trusted inputs. All scoring remains synthetic and ineligible for real
promotion or benchmark-performance claims.
