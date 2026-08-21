# Kernel evolution

AutoContext can drive an AutoKernel-style search without compiling or running
generated kernels inside the control process. The Python MVP composes the
existing multi-generation prompt/playbook loop with a kernel-specific,
correctness-first promotion gate and a structured external benchmark.

The split is intentional:

| Control plane (AutoContext) | Data plane (trusted GPU worker) |
| --- | --- |
| Generate complete candidate source | Pin one problem, reference, inputs, seeds, and tolerances |
| Carry the incumbent and benchmark feedback forward | Compile candidate and current incumbent |
| Recompute benchmark statistics with a bounded sequential policy | Run host-owned correctness and worker-private holdout trials |
| Decide promotion independently of scalar prompt score | Record paired/interleaved timing blocks |
| Require an optional fresh-protocol confirmation before promotion | Re-run provisional winners with disjoint host-owned inputs and measurement order |
| Persist source, raw reports, decisions, and lineage | Report hardware, runtime, driver, toolchain, and workload identity |

Generated Python, CUDA, or Triton must use `DockerKernelBenchmarkRunner` (or an
equivalent VM/cgroup backend) in production. It provides an OS boundary with no
network, no ambient credentials, read-only input and harness mounts, an
ephemeral tmpfs candidate workspace, and an explicit GPU partition grant. Host
isolation is necessary but not sufficient for trustworthy evidence: generated
code must also run outside the interpreter or process that owns private plans,
correctness, timing, telemetry, and the authoritative report. The legacy local
subprocess runner is lifecycle containment only and now requires the
conspicuous `trusted_unsafe=True` opt-in.

## Production host-containment worker

This composition establishes the host and GPU-partition boundary only. The
adapter named in `command` must additionally isolate generated execution from
its authoritative evaluator; importing candidate Python into that adapter
would still make correctness, timing, telemetry, and report values untrusted.

```python
from pathlib import Path
from autocontext.kernel_evolution import (
    DockerGPUDeviceGrant,
    DockerKernelBenchmarkRunner,
    DockerKernelWorkerLimits,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    NvidiaSMIGPUDeviceAttestor,
)

gpu_limit = 20 * 1024**3
isolated = DockerKernelBenchmarkRunner(
    [
        "python", "{immutable_0}",
        "--candidate", "{candidate}",
        "--incumbent", "{incumbent}",
        "--reference", "{immutable_1}",
        "--report", "{report}",
    ],
    image="registry.example/kernelbench@sha256:<64-hex-digest>",
    immutable_paths=[Path("adapter.py"), Path("reference.py")],
    gpu_grant=DockerGPUDeviceGrant(
        device_id="MIG-GPU-.../1/0",
        isolation_kind="mig",
        enforced_memory_bytes=gpu_limit,
    ),
    gpu_attestor=NvidiaSMIGPUDeviceAttestor(),
    limits=DockerKernelWorkerLimits(max_gpu_memory_bytes=gpu_limit),
)
evaluator = KernelBenchmarkEvaluator(
    isolated,
    KernelBenchmarkEvaluatorConfig(
        problem_id="kernelbench-level1-problem1",
        require_resource_telemetry=True,
        max_gpu_memory_bytes=gpu_limit,
    ),
)
```

`gpu_grant` is mandatory and `all` is forbidden. A plain visibility grant such
as `--gpus device=0` does not enforce a byte ceiling, so it is rejected with
`resource_policy_unsupported` when the worker has a GPU-memory limit. Use a MIG
or other hardware partition whose capacity was independently verified and is
no larger than the configured ceiling. Allocation/reservation telemetry is a
second fail-closed check, not a substitute for that enforcement boundary.

The worker applies wall-clock, CPU-time/CPU-share, RAM/swap, PID, bounded
stdout/stderr/report, report-structure, and tmpfs byte/inode limits. Only the
candidate/incumbent input directory and benchmark/reference paths are mounted
read-only. The candidate workspace and `/output` report area are ephemeral,
bounded tmpfs mounts. While the authenticated supervisor and container remain
alive, the host copies and verifies the bounded regular `/output/report.json`,
then acknowledges completion and stops the container; only that verified report
persists in a private host temporary directory. The root filesystem is read-only,
Linux capabilities are dropped, `no-new-privileges` is enabled, the environment
is rebuilt from an empty set, and network mode is `none`. Every outcome removes
and verifies the labeled container. Before a new run, expired labeled workers
left by a crashed coordinator are reconciled.

## Run the vertical slice

The example fixes one synthetic KernelBench-shaped problem and needs no GPU:

```bash
cd autocontext
uv run --frozen python ../examples/kernel_evolution/run.py
```

It evaluates a baseline followed by three proposals: a wrong-but-fast kernel,
a correct change below the promotion margin, and a clear winner. The printed
run directory contains:

```text
manifest.json
lineage.jsonl
artifacts/<abi-bound-artifact-sha256>.py
reports/<report-sha256>.json
attempts/<attempt-id>.json
champion.py
champion.json
summary.json
```

`proposals=N` always means N primary attempts after the mandatory baseline, so
the primary benchmark runs N+1 times. An optional confirmation callback runs
only for provisional winners. Rejected attempts remain append-only evidence.
Each new proposal's parent is the current champion, not the most recent
rejected attempt, and the final summary reports the champion rather than the
last trial.

## Python API

```python
from pathlib import Path
from autocontext.kernel_evolution import (
    ExternalKernelBenchmarkRunner,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
)

external = ExternalKernelBenchmarkRunner(
    [
        "/opt/kernel-worker/.venv/bin/python",
        "/opt/kernel-worker/kernelbench_adapter.py",
        "--candidate", "{candidate}",
        "--incumbent", "{incumbent}",
        "--artifact-identity-version", "{artifact_identity_version}",
        "--candidate-artifact-digest", "{candidate_artifact_digest}",
        "--incumbent-artifact-digest", "{incumbent_artifact_digest}",
        "--candidate-source-digest", "{candidate_source_digest}",
        "--incumbent-source-digest", "{incumbent_source_digest}",
        "--candidate-source-suffix", "{candidate_source_suffix}",
        "--incumbent-source-suffix", "{incumbent_source_suffix}",
        "--candidate-entrypoint", "{candidate_entrypoint}",
        "--incumbent-entrypoint", "{incumbent_entrypoint}",
        "--report", "{report}",
    ],
    cwd=Path("/opt/kernel-worker"),
    immutable_paths=[
        Path("/opt/kernel-worker/kernelbench_adapter.py"),
        Path("/opt/kernel-worker/problems/level1/1"),
    ],
    trusted_unsafe=True,
)
evaluator = KernelBenchmarkEvaluator(
    external,
    KernelBenchmarkEvaluatorConfig(
        problem_id="kernelbench-level1-problem1",
        # proposal_cap=20 spends alpha=.0025 per look; retain 100 tail draws.
        bootstrap_samples=40_000,
    ),
)

# Use a separately pinned adapter/profile with different host-owned seeds and
# measurement order. Its own baseline pins that confirmation protocol.
confirmation_external = ExternalKernelBenchmarkRunner(
    [
        "/opt/kernel-worker/.venv/bin/python",
        "/opt/kernel-worker/kernelbench_confirmation_adapter.py",
        "--candidate", "{candidate}",
        "--incumbent", "{incumbent}",
        "--artifact-identity-version", "{artifact_identity_version}",
        "--candidate-artifact-digest", "{candidate_artifact_digest}",
        "--incumbent-artifact-digest", "{incumbent_artifact_digest}",
        "--candidate-source-digest", "{candidate_source_digest}",
        "--incumbent-source-digest", "{incumbent_source_digest}",
        "--candidate-source-suffix", "{candidate_source_suffix}",
        "--incumbent-source-suffix", "{incumbent_source_suffix}",
        "--candidate-entrypoint", "{candidate_entrypoint}",
        "--incumbent-entrypoint", "{incumbent_entrypoint}",
        "--report", "{report}",
    ],
    cwd=Path("/opt/kernel-worker"),
    immutable_paths=[
        Path("/opt/kernel-worker/kernelbench_confirmation_adapter.py"),
        Path("/opt/kernel-worker/problems/level1/1-confirmation"),
    ],
    trusted_unsafe=True,
)
confirmation_evaluator = KernelBenchmarkEvaluator(
    confirmation_external,
    KernelBenchmarkEvaluatorConfig(
        problem_id="kernelbench-level1-problem1",
        bootstrap_samples=40_000,
    ),
)

def confirm(candidate, incumbent):
    fresh_baseline = confirmation_evaluator.evaluate(incumbent, incumbent)
    if not fresh_baseline.eligible:
        return None
    return confirmation_evaluator.evaluate(
        candidate,
        incumbent,
        expected_scope_id=fresh_baseline.hardware_scope_id,
        expected_baseline_id=fresh_baseline.baseline_id,
        expected_protocol_id=fresh_baseline.protocol_id,
    )

runner = KernelEvolutionRunner(
    KernelEvolutionConfig(
        problem_id="kernelbench-level1-problem1",
        task_prompt="Optimize ModelNew; preserve its ABI and exact semantics.",
        baseline_source=Path("kernel.py").read_text(),
        min_relative_improvement=0.01,
        precision_profile="strict-fp32-v1",
        proposal_cap=20,
        familywise_alpha=0.05,
    ),
    generate_fn=my_kernel_generator,
    evaluator=evaluator,
    lineage_root=Path("runs/kernel-evolution"),
    confirmation_fn=confirm,
)
result = runner.run(proposals=20)
```

The command is an argv sequence and never uses a shell. It receives initially
read-only candidate/incumbent files and must atomically write JSON to
`{report}`. Stdout and stderr are bounded diagnostics and are never interpreted
as correctness or performance; exceeding either byte cap terminates the worker
process group. A dedicated report directory is watched against aggregate byte,
entry-count, and tree-depth caps, including atomic temporary files. The runner
pins the executable invocation and resolved target path, fingerprints every
configured immutable harness tree before launch and again after execution,
rejects symlinks and special files in those trees, re-hashes the candidate and
incumbent, and tears down the worker process group after every invocation,
including a nominal exit. Include the resolved executable in `immutable_paths`
when its bytes must also be content-pinned.

Lifecycle containment is implemented once for every terminal outcome: success,
command failure, timeout, stdout/stderr overflow, and report overflow. On POSIX,
the adapter starts in a new session and the complete process group is killed.
On Windows, AutoContext first assigns a trusted gate process to a kill-on-close
Job Object; only then does it send the gate's one-byte start signal. The gate's
interpreter uses `-I -S`, excluding user configuration, environment Python
paths, `site`, `.pth` files, and `sitecustomize` before Job assignment. The
adapter and its descendants therefore inherit the Job without a
start-before-assignment window. Teardown checks Job termination, waits for its
active-process count to reach zero, and reports termination, wait, query, or
handle-close failures as contract errors. The adapter's `HOME`, `USERPROFILE`,
`TEMP`, `TMP`, and `TMPDIR` all point to its temporary workspace; Windows
`SystemRoot` and `WINDIR` are preserved from the control process. Custom
environment values cannot override those reserved paths.

These local-process controls limit ordinary adapter mistakes, but they are not
a hostile-code sandbox. Use them only with `trusted_unsafe=True` on a dedicated
trusted worker. `DockerKernelBenchmarkRunner` is the shipped host-containment
boundary; equivalent custom backends must enforce and attest the same controls.
Neither makes a report authoritative if candidate code shares a process with
the evaluator. Production adapters need a second, evaluator-owned execution
boundary so the candidate cannot observe private plans or replace correctness,
timing, telemetry, and report controls.

`confirmation_fn` is optional and backward compatible. When present, it
receives the exact provisional candidate and incumbent and must return a
consumer-validated `KernelBenchmarkObservation`. AutoContext rejects missing,
ineligible, identity-mismatched, same-protocol, protocol-incompatible, or
different-environment confirmation. Successful confirmation may have a
different workload scope because fresh seeds are part of the workload
fingerprint, but its static workload-family ID must match. That family binds
the named precision profile, numerical and reference semantics, tolerances,
public input-distribution requirements, per-case floor, and enforcement policy
while excluding only the committed private input/order material. The backend,
device, runtime, driver, toolchain, hardware
metadata (for example device UUID or MIG/topology identity), problem, and
reference baseline must also match. Its protocol ID must differ while its
compatibility ID must match, so only the seed/order commitment can change;
tolerances, correctness/hidden trials, warmups, timing blocks, calls per block,
and the bounded sequential-testing policy remain fixed.
The confirmation observation, report digest, and decision are persisted with
the attempt.

## Benchmark report contract

The adapter writes `autocontext.kernelbench-eval/v3`. Pydantic validates it
with unknown fields forbidden and NaN, infinity, zero, or negative timings
rejected. New v3 lineage and result artifacts also bind the complete
statistics/decision policy, including whether candidate promotion requires a
fresh confirmation, and replay every parent/champion transition and accepted
resource gate from the embedded raw reports. Result, attempt, and report schema
versions cannot be mixed. The reader accepts an all-v2 chain as explicitly
legacy and non-authoritative only when it contains none of the v3 decision
fields. Important fields are:

```json
{
  "schema_version": "autocontext.kernelbench-eval/v3",
  "evaluation_status": "complete",
  "failure_kind": null,
  "problem_id": "kernelbench-level1-problem1",
  "artifact_identity_version": "autocontext.kernel-artifact/v2",
  "candidate_artifact_digest": "sha256:...",
  "incumbent_artifact_digest": "sha256:...",
  "candidate_source_digest": "sha256:...",
  "incumbent_source_digest": "sha256:...",
  "candidate_source_suffix": ".py",
  "incumbent_source_suffix": ".py",
  "candidate_entrypoint": "ModelNew",
  "incumbent_entrypoint": "ModelNew",
  "baseline_id": "sha256:...",
  "hardware": {
    "backend": "cuda",
    "architecture": "sm90",
    "device_name": "NVIDIA H100",
    "runtime": "cuda-12.8",
    "driver": "580.65",
    "toolchain": "torch-2.8/triton-3.4",
    "workload_family_id": "sha256:...",
    "workload_fingerprint": "sha256:...",
    "metadata": {}
  },
  "hardware_scope_id": "sha256:...",
  "protocol": {
    "correctness_trials": 2,
    "hidden_trials": 1,
    "warmup_runs": 3,
    "timing_blocks": 30,
    "calls_per_block": 20,
    "atol": 0.01,
    "rtol": 0.01,
    "seed_commitment": "sha256:...",
    "compatibility_version": "autocontext.kernel-protocol-compatibility/v1",
    "semantics": {
      "profile_name": "strict-fp32-v1",
      "numerical": {"input_dtype": "float32", "minimum_input_precision": "float32", "accumulation_dtype": "float32", "output_dtype": "float32", "input_downcast_allowed": false},
      "reference": {"implementation": "torch.matmul", "precision": "float32", "tf32_allowed": false, "deterministic_algorithms": true},
      "inputs": {"family": "matmul-generalization-v1", "required_shape_classes": ["non-tile-square", "rectangular"], "required_layouts": ["contiguous", "transposed"], "required_value_classes": ["signed", "small", "large", "cancellation", "dynamic-range"], "required_slices": ["train", "holdout"]},
      "enforcement": {"require_every_correctness_slice": true, "require_every_case_no_regression": true, "require_paired_aggregate_performance": true, "candidate_controls_protected": true, "minimum_case_speedup_vs_incumbent": 0.98}
    },
    "sequential_testing": {"method": "bonferroni", "proposal_cap": 20, "familywise_alpha": 0.05}
  },
  "compile": {
    "candidate_passed": true,
    "incumbent_passed": true,
    "candidate_compile_ms": 4271.0,
    "diagnostics": ""
  },
  "correctness": {
    "passed": true,
    "tests_run": 2,
    "tests_passed": 2,
    "hidden_tests_run": 1,
    "hidden_tests_passed": 1,
    "parameter_state_match": true,
    "input_mutation_detected": false,
    "failures": [],
    "slices": [
      {"name": "worker-case-a", "split": "train", "cases_run": 1, "cases_passed": 1, "passed": true},
      {"name": "worker-case-b", "split": "holdout", "cases_run": 1, "cases_passed": 1, "passed": true}
    ]
  },
  "performance": {
    "blocks": [
      {"block": 0, "candidate_ms": 0.0183, "incumbent_ms": 0.0194, "reference_ms": 0.0271}
    ],
    "cases": [
      {"name": "worker-case-a", "split": "train", "candidate_median_ms": 0.0183, "incumbent_median_ms": 0.0194, "reference_median_ms": 0.0271, "minimum_speedup_vs_incumbent": 0.98, "passed_no_regression": true},
      {"name": "worker-case-b", "split": "holdout", "candidate_median_ms": 0.0200, "incumbent_median_ms": 0.0201, "reference_median_ms": 0.0290, "minimum_speedup_vs_incumbent": 0.98, "passed_no_regression": true}
    ]
  },
  "resources": {
    "candidate_artifact_digest": "sha256:...",
    "incumbent_artifact_digest": "sha256:...",
    "candidate_peak_allocated_bytes": 69206016,
    "candidate_peak_reserved_bytes": 73400320,
    "incumbent_peak_allocated_bytes": 67108864,
    "incumbent_peak_reserved_bytes": 71303168,
    "candidate_peak_memory_bytes": 73400320,
    "incumbent_peak_memory_bytes": 71303168,
    "device_total_memory_bytes": 85899345920
  },
  "metadata": {}
}
```

The abbreviated block list above must contain exactly `timing_blocks` entries
in a real report. Failed evaluations use `candidate_error` or
`infrastructure_error`, set a `failure_kind`, omit performance, and may omit
correctness when compilation never completed.

Production evaluators set `require_resource_telemetry=True`. Candidate and
incumbent peak allocation and reservation are measured in separate reset
windows and bound to their ABI-qualified artifact digests. Missing metrics
reject as `missing_resource_telemetry`; CUDA/container OOM,
`resource_exceeded`, unsupported hard GPU-memory enforcement, and verified
teardown failure remain distinct outcomes.

AutoContext does not trust supplied summary numbers. It recomputes geometric
paired speedups, medians, p95 latency, reference drift, the nominal 95% bound,
and a deterministic paired-bootstrap lower bound adjusted for the complete
proposal budget. It also recomputes
source digests and the canonical hardware scope ID. A different GPU,
driver/runtime/toolchain, workload fingerprint, or reference baseline is a hard
scope mismatch. It also hashes and pins the complete protocol from the baseline,
so later proposals cannot weaken precision semantics, tolerances, holdout
commitments, slice floors, search budget, warmups, timing blocks, or calls per
block.

The paired-bootstrap bound is a deterministic empirical percentile, not an
exact distribution-free confidence interval. AutoContext requires at least 100
expected resamples in the requested lower tail (`bootstrap_samples * alpha >=
100`) so production evidence is not based on an unstable rank-one or rank-two
order statistic. Runner construction validates that constraint against the
host-owned proposal cap, and report consumption checks it again. For example,
`alpha=0.005` requires at least 20,000 resamples; 2,000 resamples for a
10,000-proposal Bonferroni budget fail closed.

The v2 artifact digest is a domain-separated canonical-JSON hash of four
framed fields: identity version, SHA-256 of the exact source bytes, source
suffix, and entrypoint. Canonical JSON supplies unambiguous field boundaries;
the separate source digest remains the file-integrity and historical-evidence
identity. This is an intentional migration from report v1, whose
`artifact_digest` was only the source-byte SHA-256 and therefore did not bind
the executable ABI. Existing v1 evidence remains valid under that legacy
meaning but must not be compared directly with v2 artifact digests.

## Promotion gate

Promotion is separate from the normalized score used to enrich the next
generation's prompt. Gates run in this order:

1. The command, JSON contract, artifact hashes, problem, baseline, and hardware scope are valid.
2. Candidate and incumbent compile, and every named train and holdout correctness slice passes.
3. Every named case meets the protocol-owned no-regression floor, including each holdout case.
4. The report contains at least the configured number of paired aggregate blocks.
5. Relative latency improvement meets the margin:

   ```text
   paired_speedup = geometric_mean(incumbent_ms / candidate_ms)
   relative_improvement = 1 - (1 / paired_speedup)
   ```

   The boundary is inclusive: a configured 5% margin accepts exactly 5%.
6. The paired-bootstrap lower bound uses `familywise_alpha / proposal_cap`
   (Bonferroni alpha spending) and supports the configured margin. The nominal
   95% bound remains separately reported for compatibility; it is not the
   bounded campaign's promotion statistic.
7. Candidate p95, reference drift, and peak-memory use remain within configured limits.
8. When confirmation is configured, a distinct but compatibility-matched
   protocol must pass the same correctness, improvement, confidence, tail,
   drift, and resource gates. Each attempted confirmation burns its protocol
   identity; later adaptive proposals must use a new independently committed
   confirmation plan. Detailed confirmation feedback and metrics are persisted
   for audit but excluded from the recursive playbook.

Compile failure, malformed JSON, nonzero command exit, timeout, correctness
failure, insufficient gain, or measurement instability rejects only that
proposal and preserves the incumbent. An invalid baseline is terminal because
there is no trustworthy comparison scope, but its failed attempt remains
auditable on disk.

Every promotion decision also persists ordered gate results. Each gate is
explicitly `passed`, `failed`, or `not-evaluated`, so an early fail-closed exit
does not imply that later confidence, tail, or resource gates ran.

## Adapting AutoKernel / KernelBench

Use AutoKernel and KernelBench as the frozen data plane, not as the promotion
authority. A production adapter for one pinned problem should:

- ignore candidate-provided input factories, tolerances, seeds, and timing counts;
- initialize candidate and reference from identical parameter state (and verify a state hash);
- run fresh hidden inputs owned by the harness;
- compile and warm up before measurement;
- interleave candidate, incumbent, and reference within paired timing blocks;
- write raw samples and identity fields to the report file; and
- leave the benchmark/reference/problem mounts unchanged.

Do not scrape AutoKernel's greppable stdout or accept an agent's claimed
speedup. Its stock benchmark is useful machinery, but the adapter must expose
the structured contract and keep all evaluator-controlled inputs outside
candidate control. The synthetic adapter in the example is a reference for the
I/O boundary, not for CUDA measurement methodology.

This MVP is intentionally Python-first and library-only: it adds no public CLI,
island/concurrent GPU search, cross-hardware champion transfer, or TypeScript
surface. Its lineage root is a standalone artifact directory and is not yet
indexed by `autoctx status`, mirrored through `ArtifactStore`, or discoverable
through the run API.

Bounded searches set `proposal_cap` and `familywise_alpha`. AutoContext rejects
requests above the cap before benchmarking, uses the actual Bonferroni-adjusted
bound for every proposal, and persists proposal index, cap, per-proposal alpha,
cumulative spend, and confidence level with each attempt. Configure
`confirmation_fn` as well so provisional winners face a fresh process,
disjoint committed inputs, and a different measurement order. Supply enough
fresh plans for every possible confirmation attempt; a fixed confirmation plan
cannot be reused after its result has influenced champion selection. Legacy v2
observations without a sequential policy are read by mapping their persisted
95% bound into the generic lower-bound field. Missing adjusted metrics on a
sequential observation still fail closed, and legacy protocols should not
support new recursive performance claims.

## Why recursive kernel improvement matters

“Recursive” here means each accepted kernel becomes both the next prompt's
starting point and the next proposal's paired benchmark incumbent. That turns a
coding agent into a measurable local-search process over implementations tuned
to a specific workload, compiler, and accelerator.

The leverage can be high when a hot kernel dominates end-to-end runtime, and
the loop can explore fusion, memory access, tiling, launch geometry, precision,
and compiler-specific choices faster than manual one-shot tuning. The benefit
is bounded by the fraction of application time in that kernel (Amdahl's law),
and naive recursion readily Goodharts the benchmark: it can overfit shapes,
exploit mutable inputs, accept numerical errors, or chase thermal noise. Hidden
correctness trials, paired significance tests, hardware scoping, immutable
harness identity, and append-only lineage are therefore the central feature—not
administrative extras around the optimizer.
