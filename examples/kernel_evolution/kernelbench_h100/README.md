# Live KernelBench H100 contract smoke

This is the live-GPU companion to the synthetic kernel-evolution MVP in the
parent directory. It evaluates a named strict-FP32 or relaxed-precision profile
on an NVIDIA H100 and applies correctness-slice, per-case, aggregate,
fresh-confirmation, and bounded sequential gates. It is not a KernelBench
leaderboard result.

## What is included

| File | Role |
| --- | --- |
| `control_smoke.py` | Runs a baseline self-evaluation, pins its identities, evaluates the candidate, and makes the promotion decision. |
| `campaign.py` | Runs the production recursive evolution/confirmation path with a hard ten-proposal cap and a human-operated mailbox. |
| `adapter.py` | Owns compilation, private-plan correctness, per-case and aggregate CUDA-event timing, hardware identity, and the v2 JSON report. |
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

These historical smoke scripts explicitly select the legacy
`trusted_unsafe=True` local-process runner. They are not a hostile-code sandbox
and must evaluate only source the operator is prepared to execute on a
dedicated disposable host. Production campaigns should package this adapter in
a pinned image and use `DockerKernelBenchmarkRunner` with a verified MIG or
hardware-partition grant, denied network, scrubbed credentials, and read-only
harness mounts as described in `autocontext/docs/kernel-evolution.md`.

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
campaign, an operator creates two owner-readable JSON files on the worker, one
with `role: "primary"` and one with `role: "confirmation"`. Both use schema
`autocontext.kernel-private-plan/v1`, the selected `profile_name`, a `cases`
array, and a `timing_order` containing every case name once. Each case contains
exactly:

```text
name, split, seed, m, n, k, a_layout, b_layout,
value_class, magnitude_min, magnitude_max
```

`split` is `train` or `holdout`; layouts are `contiguous` or `transposed`.
Strict plans must jointly cover non-tile-aligned square and rectangular shapes,
both layouts, signed/small/large values, cancellation, and dynamic range. The
relaxed profile is the legacy fixed, contiguous, positive-unit distribution.
The validator requires train and holdout cases, unique seeds, disjoint primary
and confirmation inputs, and different relative timing orders.

The campaign hashes each complete plan before the first proposal, publishes
only its canonical SHA-256 commitment in `campaign_config.json`, and passes the
plan as an immutable worker input. Candidate files and prompts do not receive
the private material. Treat the worker process as the trust boundary; this
subprocess-only example is not a defense against actively malicious code that
inspects its host process.

## Run a bounded recursive campaign

`campaign.py` delegates baseline evaluation, recursive prompt construction,
promotion policy, fresh confirmation, append-only lineage, and champion updates
to the production `KernelEvolutionRunner`. Its generator does not contain a
model client or credentials. For generation `N` (zero based), it:

1. atomically writes the exact AutoContext prompt to `prompt_N.md`;
2. prints the same full prompt to stdout for detached-job logs;
3. waits for a stable, regular, non-symlink `candidate_N.py`;
4. records the accepted candidate's exact digest before returning it to the
   production runner.

The CLI accepts from one through ten proposals and rejects larger budgets. Each
candidate wait is bounded to at most 24 hours and defaults to one hour.

### Phase 1: confirm the bundled tuned candidate

Start a new one-proposal campaign with AutoKernel's `kernel.py` as the explicit
baseline. Run this in a detached worker session or one terminal:

```bash
mkdir /absolute/path/to/mailbox-phase1
cd /absolute/path/to/autocontext/autocontext
uv run --frozen python ../examples/kernel_evolution/kernelbench_h100/campaign.py \
  --autokernel-root /absolute/path/to/autokernel \
  --baseline /absolute/path/to/autokernel/kernel.py \
  --adapter-python /absolute/path/to/gpu-venv/bin/python \
  --precision-profile relaxed-precision-v1 \
  --primary-private-plan /worker-private/relaxed-primary.json \
  --confirmation-private-plan /worker-private/relaxed-confirmation.json \
  --mailbox /absolute/path/to/mailbox-phase1 \
  --proposals 1 \
  --candidate-wait-timeout 3600 \
  --output /absolute/path/to/kernel-runs \
  --run-id h100-confirm-tuned
```

After `prompt_0.md` appears, read it and atomically publish the checked-in tuned
candidate from another terminal:

```bash
less /absolute/path/to/mailbox-phase1/prompt_0.md
cp /absolute/path/to/autocontext/examples/kernel_evolution/kernelbench_h100/tuned_candidate.py \
  /absolute/path/to/mailbox-phase1/candidate_0.py.tmp
mv /absolute/path/to/mailbox-phase1/candidate_0.py.tmp \
  /absolute/path/to/mailbox-phase1/candidate_0.py
```

Do not start phase 2 merely because the process exited successfully: a complete
campaign may retain its baseline after rejecting a candidate. Require both
primary promotion and fresh confirmation:

```bash
jq -e '
  .attempts[-1].decision == "promoted" and
  .attempts[-1].confirmation_decision.promote == true
' /absolute/path/to/kernel-runs/relaxed-precision-v1/h100-confirm-tuned/summary.json
```

The accepted phase-1 candidate digest should match the checked-in evidence:
`sha256:500a28c8bfd5374884eca49824de76855e314154c1e770d0ca7c8ad79a9e46e4`.

### Phase 2: recurse from the confirmed candidate

Only after the phase-1 gate passes, start a new run and a new empty mailbox with
`tuned_candidate.py` as the explicit baseline:

```bash
mkdir /absolute/path/to/mailbox-phase2
cd /absolute/path/to/autocontext/autocontext
uv run --frozen python ../examples/kernel_evolution/kernelbench_h100/campaign.py \
  --autokernel-root /absolute/path/to/autokernel \
  --baseline /absolute/path/to/autocontext/examples/kernel_evolution/kernelbench_h100/tuned_candidate.py \
  --adapter-python /absolute/path/to/gpu-venv/bin/python \
  --precision-profile relaxed-precision-v1 \
  --primary-private-plan /worker-private/relaxed-primary.json \
  --confirmation-private-plan /worker-private/relaxed-confirmation.json \
  --mailbox /absolute/path/to/mailbox-phase2 \
  --proposals 10 \
  --candidate-wait-timeout 3600 \
  --output /absolute/path/to/kernel-runs \
  --run-id h100-recursive-10
```

For each `N` from `0` through `9`, read `prompt_N.md`, author a complete source
module using the current champion and accumulated benchmark feedback, and
publish it via `candidate_N.py.tmp` followed by an atomic rename to
`candidate_N.py`. A pre-existing prompt, candidate, or receipt is rejected, so
use a new mailbox and run ID for every campaign. AutoContext may compact a long
champion inside its recursive prompt; the exact champion remains available in
the run directory as `champion.py` and as a content-addressed artifact.

The profile-namespaced run directory contains the production manifest, `campaign_config.json`,
candidate/report artifacts, per-attempt JSON, append-only `lineage.jsonl`, the
current champion, a final `summary.json`, and observed `profile_evidence.json`
on successful completion. The
mailbox retains every prompt, submitted candidate, digest receipt, and
`campaign_status.json`. SIGINT/SIGTERM is converted into a production
`interrupted` manifest before exit; completed attempts and lineage remain
durable. A candidate timeout records a failed manifest and preserves all prior
attempts.

The two phases above reproduce the historical relaxed search under private
plans. Run a separate `strict-fp32-v1` campaign with strict primary and
confirmation plans to make a strict claim. Do not seed it with
`recursive_champion.py`: the control smoke and
`profile_reassessment.json` demonstrate that exact FP16-downcast, fixed-shape
artifact is ineligible. Strict and relaxed runs always live under different
profile directories, even when their operator-supplied run IDs match.

## Named workload and protocol

New campaigns use problem ID
`kernelbench-v0.1-level1-1-matmul-profiled-h100-v1`. The protocol explicitly
names either `strict-fp32-v1` (`atol=rtol=0.0001`, no input downcast) or
`relaxed-precision-v1` (`atol=rtol=0.01`, FP16 input downcast allowed). It binds
input/accumulation/output precision, PyTorch FP32 reference settings with TF32
disabled, public input-distribution requirements, enforcement policy, exact
private-plan commitment, and the sequential budget into the protocol ID.

Every private case is a named correctness slice and a named performance case.
Every train and holdout slice must pass correctness; every case must meet the
0.98x incumbent no-regression floor before aggregate promotion. Eight paired
timing blocks aggregate all cases geometrically while retaining per-case
medians. The adapter uses three warmups and ten synchronized CUDA-event calls
per implementation per block. Candidate, incumbent, and PyTorch reference
orders rotate across blocks. Candidate and incumbent CUDA peak allocation and
reservation are measured in separate reset windows across every case and are
bound to their exact artifact identities. The static workload-family identity
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
- the Bonferroni-adjusted lower speedup bound uses `0.05 / 10` and reaches at
  least `1 / (1 - 0.05)` (the nominal 95% bound is reported separately);
- candidate p95 latency is no more than 5% above incumbent p95 latency.

## One-shot comparison output and exit status

The command prints one JSON object with these top-level fields:

```text
problem_id
artifact_identity_version
baseline   { eligible, artifact_digest, source_digest, median_ms,
             hardware_scope_id, baseline_id, protocol_id,
             protocol_compatibility_id }
candidate  { path, eligible, artifact_digest, source_digest, median_ms,
             incumbent_median_ms, reference_median_ms,
             speedup_vs_incumbent, speedup_vs_reference, speedup_lcb95,
             speedup_lcb, confidence_level, all_case_no_regression_passed,
             relative_improvement, environment_drift_ratio,
             rejection_reason, feedback }
promotion  { promote, decision, reason }
report     autocontext.kernelbench-eval/v2
```

The nested v2 report contains separate exact-source digests and ABI-bound
artifact identities, hardware and workload identity, the complete protocol,
compile/correctness reports, all eight raw
timing blocks, and resource metadata. AutoContext ignores claimed scores from
stdout and recomputes medians, speedups, the confidence bound, and drift from
those blocks.

The checked-in live evidence predates this v2 identity contract. Its observed
`artifact_digest` values are deliberately preserved as legacy source-only
SHA-256 values; they are tagged in the evidence files and must not be compared
directly with current ABI-bound digests. Report v2 hashes a canonical mapping
of the identity version, exact source digest, suffix, and entrypoint, and keeps
the source digest separately for integrity and historical comparison.

For `control_smoke.py`, exit status `0` means promoted. Status `2` means the
comparison completed but the candidate was rejected; that remains a valid
contract smoke result. A baseline or infrastructure failure exits nonzero with
diagnostics. `campaign.py` instead exits `0` when the requested campaign
completed, even if every proposal was rejected; inspect `summary.json` and use
the phase-one gate above before starting phase two.

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
campaign as pending. A completed new campaign writes `profile_evidence.json`
with profile/protocol IDs, plan commitments, holdout correctness and per-case
floors, proposal spend, and whether an improvement survived strict FP32.

## Attribution

`reference.py` is adapted from
[KernelBench v0.1 Level 1 problem 1](https://github.com/ScalingIntelligence/KernelBench/blob/423217d9fda91e0c2d67e4a43bf62f96f6d104f1/KernelBench/level1/1_Square_matrix_multiplication_.py).
The tuned kernel follows the AutoKernel starter interface and the grouped
program ordering described by Triton's matrix-multiplication tutorial.
AutoKernel is used as an external checkout at the commit above. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for copyrights, source links,
and retained MIT license texts.
