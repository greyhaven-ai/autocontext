# Live KernelBench H100 contract smoke

This is the live-GPU companion to the synthetic kernel-evolution MVP in the
parent directory. It evaluates a named strict-FP32 or relaxed-precision profile
on an NVIDIA H100 and applies correctness-slice, per-case, aggregate,
fresh-confirmation, and bounded sequential gates. It is not a KernelBench
leaderboard result.

## What is included

| File | Role |
| --- | --- |
| `control_smoke.py` | Runs a same-interpreter diagnostic and emits an explicitly non-authoritative control decision. |
| `campaign.py` | Validates production inputs and fails closed before evaluator, mailbox, or GPU work. |
| `production_runtime.py` | Composes the protected Docker/MIG evaluator path used for live validation while retaining the production release guard. |
| `adapter.py` | Trusted evaluator: owns private plans, references, fresh timing challenges, telemetry, and the v3 JSON report without importing generated source in protected mode. |
| `authority_transport.py` | Evaluator-owned, typed and bounded Unix-socket transport plus independent timing and partition-global memory observation. |
| `authority_worker.py` | Candidate-side executor; receives only its source, public support code, tensor frames, and one authority socket. |
| `confirmation_adapter.py` | Runs the independently committed confirmation plan through the same immutable adapter. |
| `profile_contract.py` | Defines both public precision profiles and validates private plan coverage, commitments, and freshness. |
| `reference.py` | Pinned KernelBench v0.1 Level 1 problem 1 PyTorch reference. |
| `tuned_candidate.py` | Grouped 128 x 128 x 64 Triton matmul tuned for SM90. |
| `recursive_champion.py` | Exact source bytes of the champion produced by the verified ten-proposal campaign. |
| `verified_h100_result.json` | Sanitized evidence from one successful H100 run; it is not a golden performance assertion. |
| `verified_recursive_h100_result.json` | Sanitized phase-one, recursive-lineage, fresh-confirmation, and export evidence from the live campaign. |
| `profile_reassessment.json` | Separately classifies that historical FP16-downcast champion under strict and relaxed semantics without claiming an unobserved live strict result. |
| `THIRD_PARTY_NOTICES.md` | Source attribution and retained license notices. |

The incumbent is not vendored. The driver reads `kernel.py` from a separately
checked-out, commit-pinned AutoKernel repository.

## Prerequisites

- Linux with one NVIDIA H100 (SM90), `nvidia-smi`, and a compatible driver.
- An idle or exclusive worker. Lock application clocks when the platform makes
  that available; the adapter records drift but cannot prevent noisy neighbors.
- Git and `uv`.
- An AutoContext source checkout with its Python environment synced.
- A separate Python 3.11+ GPU environment containing CUDA-enabled PyTorch and
  Triton. The verified run used Python 3.11.15, PyTorch 2.13.0+cu130, and
  Triton 3.7.1.

`control_smoke.py` explicitly selects the legacy `trusted_unsafe=True`
local-process runner. It is not a hostile-code sandbox and must evaluate only
source the operator is prepared to execute on a dedicated disposable host.
Its JSON uses the separate `autocontext.kernel-h100-control-smoke/v1` schema,
sets `authoritative: false`, and deliberately omits the forgeable raw report;
it must never be consumed as canonical promotion or profile evidence.

`campaign.py` has no local-process escape hatch. It validates the
declarative inputs for a digest-pinned image, explicit MIG UUID and capacity,
bounded resources, and private-plan schedule. It then fails closed before
creating a mailbox, resolving Docker, running host attestation, or spending GPU
time until the protected path passes its real H100/MIG release gate. The path
itself is now split: the trusted evaluator and candidate/incumbent authorities
run in distinct containers, and protected adapter mode never receives generated
source paths.

## Pin the incumbent

The measured incumbent is AutoKernel's basic Triton matmul at commit
`78435821cc3d5756ba6ee1785c397f6d8fa8c90d`:

```bash
git clone https://github.com/RightNow-AI/autokernel.git /absolute/path/to/autokernel
git -C /absolute/path/to/autokernel checkout --detach 78435821cc3d5756ba6ee1785c397f6d8fa8c90d
```

The adapter uses only that checkout's `kernel.py`; its PyTorch and Triton come
from the interpreter supplied with `--adapter-python`.

## Run the comparison

Run from this repository's Python package directory (`autocontext/`):

```bash
cd /absolute/path/to/autocontext/autocontext
uv run --frozen python ../examples/kernel_evolution/kernelbench_h100/control_smoke.py \
  --autocontext-src /absolute/path/to/autocontext/autocontext/src \
  --autokernel-root /absolute/path/to/autokernel \
  --adapter-python /absolute/path/to/gpu-venv/bin/python \
  --precision-profile strict-fp32-v1 \
  --private-plan /worker-private/strict-primary.json
```

Pass the literal virtual-environment interpreter path. Do not transform it with
`realpath`, `readlink -f`, or `Path.resolve()`: a venv's `bin/python` is often a
symlink, and invoking its final target bypasses `pyvenv.cfg` and loses the
venv's installed PyTorch and Triton packages. `control_smoke.py` deliberately
uses lexical `abspath` normalization only.

Omit `--candidate` to use `tuned_candidate.py`, or pass another trusted Python
source file exposing `kernel_fn(a, b)`. The incumbent must expose the same
entrypoint. A strict comparison of `recursive_champion.py` is expected to exit
`2`: it downcasts inputs and asserts the old fixed shape. The relaxed profile
retains the historical 1%-tolerance evidence under a separate protocol.

## Prepare worker-private plans

No correctness seeds, shapes, ranges, or case order are checked in. Before a
campaign, an operator creates one owner-readable primary plan and at least one
distinct owner-readable confirmation plan per requested proposal. The primary
file has `role: "primary"`; every confirmation file has `role: "confirmation"`.
All use schema `autocontext.kernel-private-plan/v1`, the selected
`profile_name`, a `cases` array, and a `timing_order` containing every case name
once. Each case contains exactly:

```text
name, split, seed, m, n, k, a_layout, b_layout,
value_class, magnitude_min, magnitude_max
```

`split` is `train` or `holdout`; layouts are `contiguous` or `transposed`. In
both splits, strict plans must cover non-tile-aligned square and rectangular
shapes, both layouts independently for operand A and operand B,
signed/small/large values, cancellation, and dynamic range. Magnitude ranges
are validated against the named class and are used by the adapter. The relaxed
profile is the legacy fixed, contiguous, positive-unit distribution. The
validator also requires unique seeds, disjoint primary and confirmation inputs,
and different relative timing orders.

The campaign hashes each complete plan before the first proposal. The protected
evaluator publishes only its canonical SHA-256 commitment. Its private plan,
reference, evaluator code, report directory, and opposite-role socket are never
mounted into a candidate container. The production entry point remains disabled
until the authority uses independently attested role grants, observes mutation
outside generated code's interpreter, measures candidate/incumbent/reference
under comparable trusted boundaries, creates containers under crash-safe
supervision, and completes the real H100/MIG gate.

## Production campaign status

The authenticated authority protocol prototype and fail-closed composition
guard are implemented, but the trusted evaluator/candidate boundary remains
incomplete. The recursive campaign stays unavailable until its four missing
boundary requirements are implemented and the exact path is validated on a
real H100/MIG partition. Invoking `campaign.py` validates
the private-plan schedule and declarative pinned-image/MIG/limit configuration,
then raises
`ProductionEvaluatorBoundaryUnavailable` before creating a mailbox or launching
a container. It does not attest the host GPU or construct a Docker evaluator.
There is no campaign override flag. The protected composition helper remains
inspectable, but its runner returns `resource_policy_unsupported` before host
attestation or container launch whenever any of the four authority requirements
is unavailable. The runner and runtime manifests publish the same authoritative
requirement map. In particular, crash-safe supervised container creation stays
explicitly unavailable as `crash_safe_container_creation` rather than relying
on post-create expiry labels to cover a coordinator crash during `docker create`.

The implemented prototype is an evaluator-owned accelerator RPC over bounded
JSON headers and canonical safetensors payloads—never pickle. Tensor payloads
retain exact shape, stride, and storage offset; unsupported overlapping or
aliased storage is rejected. Generated code receives only the tensors needed
for one invocation. The production path remains fail-closed because the current
same-interpreter authority cannot independently observe input mutation and the
shared accelerator grant cannot attest role isolation. Worker-issued mutation
claims are not accepted as evidence, reference timing is not comparable to the
RPC boundary, and container creation is not yet crash-safe. The evaluator uses fresh random timed
challenges, checks every response against its trusted reference, observes
candidate/incumbent end-to-end RPC latency and partition-global memory itself,
and binds request/output commitments and measurements to both artifact
identities, the private-plan commitment, evaluator/boundary builds, and host
accelerator attestation in a replay-verifiable receipt. Candidate timing/report
claims are not protocol fields. Exact statistics remain in evaluator-owned audit
evidence; recursive prompts receive only aggregate gate states and the
disposition, so a candidate cannot use deliberately modulated timing digits as
a holdout-return channel.

The production composition requires all of the following:

- a digest-pinned `--worker-image` and explicit `--container-python`;
- `--gpu-device MIG-...`, `--gpu-isolation-kind mig`, and the exact
  `--gpu-memory-bytes` reported by host `nvidia-smi`;
- an operator-pinned `--authority-hmac-key-id` and owner-only mode-0400/0600
  `--authority-hmac-secret-file`; the secret path and bytes are never exported;
- one `--primary-private-plan` and at least one distinct repeatable
  `--confirmation-private-plan` per requested proposal;
- an operator-only `--sealed-audit-root` disjoint from both `--mailbox` and
  `--output`; confirmation reports, case names, and detailed vetoes remain
  there until generation is terminal;
- explicit bounded worker limits, all of which are recorded in the runner
manifest.

The authoritative report must identify CUDA, `sm90`, and an NVIDIA H100. Its
GPU receipt includes the exact device or MIG UUID, isolation kind, enforced
capacity, and host `attestor_id`; profile export recomputes the branded digest
over that complete payload, reauthenticates the compact primary and confirmation
authority receipts against the pinned host key and build/boundary digests, and
exports those authenticated receipts without any private input transcript.
An A100/`sm80` report, a downgraded v2 link, or a missing/altered attestor field
cannot be labeled H100 profile evidence.

Each attempted confirmation consumes a different committed plan. Primary and
all confirmation plans must be pairwise disjoint in inputs and relative timing
order, preventing a later candidate from reusing a confirmation protocol.
Confirmation details are retained first in sealed operator audit storage and
excluded from the run directory, recursive feedback, scores, and lesson hints.
Only report/audit digests and aggregate gate states are public during
generation; terminal completion, failure, or interruption releases the audit
copy under the run directory. Promotion still reveals the unavoidable pass/fail
bit by deciding which champion is carried forward. Candidate
containers mount neither `run_dir` nor mailbox/report storage, have no network
or durable filesystem, and are destroyed between evaluations, so confirmation
details cannot persist into a later proposal.

`strict-fp32-v1` binds `input_downcast_allowed: false` and challenges downcasts
with strict numerical cases. This is behavioral enforcement, not a general
proof of a candidate's internal instruction precision; evidence should retain
that narrower claim unless a future evaluator adds IR/runtime attestation.

## Docker + MIG release gate

The real Docker security/crash-cleanup test is opt-in and is not exercised by
ordinary CI. Run it on the dedicated release host with one active MIG partition
and a digest-pinned CUDA image containing PyTorch:

```bash
cd /absolute/path/to/autocontext/autocontext
export AUTOCONTEXT_RUN_GPU_DOCKER_INTEGRATION=1
export AUTOCONTEXT_GPU_DEVICE_ID=MIG-...
export AUTOCONTEXT_GPU_DOCKER_IMAGE=registry.example/kernel@sha256:...
export AUTOCONTEXT_GPU_DOCKER_PYTHON=/absolute/python/path/inside/the/image
uv run --frozen pytest \
  tests/test_docker_kernel_worker.py::test_real_docker_mig_security_and_crash_cleanup_release_gate \
  -q
```

Set `AUTOCONTEXT_DOCKER_BINARY` or `AUTOCONTEXT_NVIDIA_SMI_BINARY` only when the
release host does not use the default command names. A passing gate verifies the
real MIG grant/capacity, denied egress and host paths, bounded output tmpfs, and
detached cleanup after coordinator `SIGKILL`. Record the command, image digest,
MIG UUID/capacity, and test log in the release checklist. This gate validates
the outer Docker worker; it does not by itself enable `campaign.py`.

The reserved AC-1003 test composes the protected factory twice with a hostile
candidate that would probe private mounts/environment and egress, patch
`torch.cuda.Event`, start a descendant, and write a workspace marker. Until the
role-isolated topology exists, both evaluations must fail with
`resource_policy_unsupported` before that source or any container is launched.
It is a pre-dispatch invariant, not a live H100 authority validation. The
normal unit suite covers the unavailable manifest; operators can additionally
exercise this reserved host setup with a valid strict primary plan outside the
candidate-visible AutoKernel root:

```bash
export AUTOCONTEXT_RUN_PROTECTED_GPU_INTEGRATION=1
export AUTOCONTEXT_GPU_MEMORY_BYTES=<exact-MIG-capacity-bytes>
export AUTOCONTEXT_AUTOKERNEL_ROOT=/absolute/path/to/autokernel
export AUTOCONTEXT_KERNEL_PRIVATE_PLAN=/worker-private/strict-primary.json
export AUTOCONTEXT_AUTHORITY_HMAC_KEY_ID=release-host-key-v1
export AUTOCONTEXT_AUTHORITY_HMAC_SECRET_FILE=/worker-private/authority-hmac.secret
uv run --frozen pytest \
  tests/test_kernel_h100_production_runtime.py::test_reserved_h100_protected_authority_gate_remains_unavailable \
  -q
```

Do not remove `require_protected_evaluator_boundary()` from `campaign.py` until
role-separated telemetry, trusted out-of-process mutation observation, and a
comparable reference timing boundary and crash-safe supervised creation are
implemented, then the reserved live gate passes on the digest-pinned image and
its authenticated receipts and teardown log have been reviewed.

## Named workload and protocol

New campaigns use problem ID
`kernelbench-v0.1-level1-1-matmul-profiled-h100-v1`. The protocol explicitly
names either `strict-fp32-v1` (`atol=rtol=0.0001`, no input downcast) or
`relaxed-precision-v1` (`atol=rtol=0.01`, FP16 input downcast allowed). It binds
input/accumulation/output precision, PyTorch FP32 reference settings with TF32
disabled, public input-distribution requirements, enforcement policy, exact
private-plan commitment, and the sequential budget into the protocol ID.

Every private case is a named correctness slice and a named performance case.
Train and holdout must each independently cover every shape, layout, and value
class named by the selected canonical profile. Every slice must pass
correctness; every case must meet the 0.98x incumbent no-regression floor before aggregate promotion. Eight paired
timing blocks aggregate all cases geometrically while retaining per-case
medians. The adapter uses three warmups and ten evaluator-randomized calls per
implementation per block. Every timed candidate/incumbent output is checked
against the trusted reference; the evaluator owns wall-clock measurement around
the complete authority exchange, so a candidate cannot substitute its clock or
replay an earlier output. Candidate and incumbent orders rotate across protected
blocks and share that RPC boundary. The local PyTorch CUDA-event reference is a
diagnostic with an explicitly non-comparable boundary and is excluded from
promotion comparisons. Protected evidence therefore remains ineligible until a
trusted boundary can make all required authority claims. The legacy trusted
control retains allocator peak measurements. Measurements are bound to exact
artifact identities. The static workload-family identity
covers the reference and problem contract; the workload fingerprint also binds
the selected profile, private-plan commitment, and sequential policy.

Provisional winners run through `confirmation_adapter.py` with a disjoint plan
and different relative order. The fresh plan commitment changes protocol and
workload-fingerprint identity, while compatible precision, reference, input
family, enforcement, and sequential semantics keep the compatibility ID stable.

For each provisional winner, the confirmation callback first self-evaluates
its incumbent under this fresh profile, then evaluates the candidate while
pinning that fresh scope, baseline, and protocol. The production runner vetoes
confirmation if the reference, static workload family, or execution environment
changes, if the protocol is not fresh, if its compatibility identity changes,
or if any correctness, significance, tail, drift, or resource gate fails.
Compatibility fixes precision, reference, distribution, tolerance,
no-regression floor, proposal budget, trial-count, warmup, and timing fields
while permitting only a new committed private input/order plan.

The control process first evaluates the incumbent against itself. Only after a
valid baseline does it evaluate the candidate while requiring the same hardware
scope, reference baseline, and protocol IDs.

An eligible candidate is promoted only when all of these hold:

- environment drift is at most 10%;
- relative improvement is at least 5%;
- every correctness slice and per-case no-regression floor passes;
- all eight pre-registered paired blocks clear the 5% margin, giving the sign
  e-process bound `2^-8 = 0.00390625 <= 0.05 / 10`;
- candidate p95 latency is no more than 5% above incumbent p95 latency.

The resulting ten-look Bonferroni familywise bound is at most `10 / 256 =
0.0390625`. This finite-sample claim assumes that, under the null and conditional
on prior blocks, each balanced paired block has probability at most one half of
clearing the full margin. It does not assume Gaussian timing noise or
independent timing magnitudes. The fixed block count, no early stopping,
balanced order, and schedule derivation from the committed private plan are
receipt-bound. Deterministic calibration exercises null, heavy-tail, paired
shared drift, across-block autocorrelated magnitudes, heteroskedasticity, and
all ten adaptive looks;
it is a stress diagnostic, not the source of the theorem claim.

## One-shot comparison output and exit status

`control_smoke.py` prints one JSON object with these top-level fields:

```text
schema_version   autocontext.kernel-h100-control-smoke/v1
evidence_status  non_authoritative_trusted_unsafe
authoritative    false
security_boundary, warning
problem_id
precision_profile, private_plan_commitment
artifact_identity_version
baseline   { eligible, artifact_digest, source_digest, median_ms,
             hardware_scope_id, baseline_id, protocol_id,
             protocol_compatibility_id }
candidate  { path, eligible, artifact_digest, source_digest, median_ms,
             incumbent_median_ms, reference_median_ms,
             speedup_vs_incumbent, speedup_vs_reference,
             derived_statistics_receipt, confidence_level, all_case_no_regression_passed,
             relative_improvement, environment_drift_ratio,
             rejection_reason, feedback }
control_decision  { promote, decision, reason }
```

The raw v4 report is deliberately omitted because it was produced by the same
interpreter as candidate code and is forgeable. The displayed measurements and
control decision are diagnostics only; they are not canonical promotion or
profile evidence.

The checked-in live evidence predates this v2 identity contract. Its observed
`artifact_digest` values are deliberately preserved as legacy source-only
SHA-256 values; they are tagged in the evidence files and must not be compared
directly with current ABI-bound digests. The v2 artifact identity hashes a
canonical mapping of the identity version, exact source digest, suffix, and
entrypoint, and keeps the source digest separately for integrity and historical
comparison.

For `control_smoke.py`, exit status `0` means its non-authoritative control
decision passed; it does not mean a production promotion occurred. Status `2`
means the comparison completed but the control decision rejected the candidate;
that remains a valid contract smoke result. A baseline or infrastructure failure
exits nonzero with diagnostics. `campaign.py` currently exits nonzero at its
mandatory protected-evaluator preflight; it cannot emit new production campaign
evidence until the live protected-boundary gate described above passes.

[`verified_h100_result.json`](verified_h100_result.json) records a sanitized
successful observation on an H100 80GB. It deliberately omits provider, node,
account, billing, and job identifiers, as well as the raw timing blocks. Treat
it as evidence that this path ran successfully, not as a portable speedup
promise or a value to assert in CI.

[`verified_recursive_h100_result.json`](verified_recursive_h100_result.json)
records a separate live ten-proposal campaign. Four proposals passed both the
primary gate and fresh-seed confirmation; six were rejected for compilation,
correctness, or insufficient improvement. The final
[`recursive_champion.py`](recursive_champion.py) measured 0.2888 ms in the
primary profile and 0.2895 ms in confirmation, or about 7.30x faster than the
phase-two starting candidate. Its large gain depends partly on converting the
fixed float32 inputs to float16 within the workload's 1% tolerance, so it is
not a bitwise-equivalent or general-purpose float32 matmul result.

[`profile_reassessment.json`](profile_reassessment.json) therefore retains that
observation only as relaxed-precision evidence and records why the exact source
is rejected by the strict contract. It explicitly marks the strict live H100
campaign as blocked on AC-1003 and then pending an external H100 run. That
historical statement remains accurate for the artifact; after the protected
gate passes and the campaign guard is removed, a completed new campaign writes
`profile_evidence.json` using the authenticated outer schema
`autocontext.kernel-profile-evidence-envelope/v1`. Its `profile` payload uses
schema `autocontext.kernel-h100-profile-evidence/v4`, and the outer content
digest plus operator-pinned HMAC key and tag authenticate every field, including
the exact
champion artifact and attempt identity, content-addressed primary
and confirmation report receipts, their plan commitments and protocol IDs,
the complete host-owned decision policy and its canonical policy ID, the
policy-bound finite-sample derivation receipts and deterministic calibration,
identities replayed from the complete all-v4 result/lineage/report chain before
export, CUDA/SM90/H100 identity, the
digest-verified host GPU attestation including its attestor ID, holdout correctness and per-case floors,
proposal spend, and whether an improvement survived strict FP32.

## Attribution

`reference.py` is adapted from
[KernelBench v0.1 Level 1 problem 1](https://github.com/ScalingIntelligence/KernelBench/blob/423217d9fda91e0c2d67e4a43bf62f96f6d104f1/KernelBench/level1/1_Square_matrix_multiplication_.py).
The tuned kernel follows the AutoKernel starter interface and the grouped
program ordering described by Triton's matrix-multiplication tutorial.
AutoKernel is used as an external checkout at the commit above. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for copyrights, source links,
and retained MIT license texts.
