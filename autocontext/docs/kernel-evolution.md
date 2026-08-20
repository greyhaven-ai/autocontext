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
| Recompute benchmark statistics | Run host-owned correctness and hidden holdout trials |
| Decide promotion independently of scalar prompt score | Record paired/interleaved timing blocks |
| Require an optional fresh-protocol confirmation before promotion | Re-run provisional winners with new host-owned seeds and measurement order |
| Persist source, raw reports, decisions, and lineage | Report hardware, runtime, driver, toolchain, and workload identity |

The external worker may be local, SSH-backed, or a container/job scheduler. A
subprocess is lifecycle isolation, not a security boundary; generated Python,
CUDA, or Triton must run on a dedicated worker with no secrets, no network,
resource limits, read-only harness mounts, and an operator-owned GPU allocation.

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
)
evaluator = KernelBenchmarkEvaluator(
    external,
    KernelBenchmarkEvaluatorConfig(problem_id="kernelbench-level1-problem1"),
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
)
confirmation_evaluator = KernelBenchmarkEvaluator(
    confirmation_external,
    KernelBenchmarkEvaluatorConfig(problem_id="kernelbench-level1-problem1"),
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

These controls fail closed and limit ordinary adapter mistakes, but they are
not a hostile-code sandbox. The report caps use a polling watchdog and can
briefly overshoot. Process-tree cleanup does not prevent generated code from
reading available credentials, using the network, writing elsewhere, asking a
privileged service to launch work, or exploiting the host. AC-991 tracks that
separate security boundary: a dedicated container/VM or equivalent isolated
worker, cgroup or Job resource quotas, read-only mounts, restricted networking,
and a worker account with no secrets.

`confirmation_fn` is optional and backward compatible. When present, it
receives the exact provisional candidate and incumbent and must return a
consumer-validated `KernelBenchmarkObservation`. AutoContext rejects missing,
ineligible, identity-mismatched, same-protocol, protocol-incompatible, or
different-environment confirmation. Successful confirmation may have a
different workload scope because fresh seeds are part of the workload
fingerprint, but its static workload-family ID must match. That family binds
the shape, dtype, reference, and input contract while excluding only randomized
seed/order material. The backend, device, runtime, driver, toolchain, hardware
metadata (for example device UUID or MIG/topology identity), problem, and
reference baseline must also match. Its protocol ID must differ while its
compatibility ID must match, so only the seed/order commitment can change;
tolerances, correctness/hidden trials, warmups, timing blocks, and calls per
block remain fixed.
The confirmation observation, report digest, and decision are persisted with
the attempt.

## Benchmark report contract

The adapter writes `autocontext.kernelbench-eval/v2`. Pydantic validates it
with unknown fields forbidden and NaN, infinity, zero, or negative timings
rejected. Important fields are:

```json
{
  "schema_version": "autocontext.kernelbench-eval/v2",
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
    "correctness_trials": 5,
    "hidden_trials": 5,
    "warmup_runs": 3,
    "timing_blocks": 30,
    "calls_per_block": 20,
    "atol": 0.01,
    "rtol": 0.01,
    "seed_commitment": "sha256:...",
    "compatibility_version": "autocontext.kernel-protocol-compatibility/v1"
  },
  "compile": {
    "candidate_passed": true,
    "incumbent_passed": true,
    "candidate_compile_ms": 4271.0,
    "diagnostics": ""
  },
  "correctness": {
    "passed": true,
    "tests_run": 5,
    "tests_passed": 5,
    "hidden_tests_run": 5,
    "hidden_tests_passed": 5,
    "parameter_state_match": true,
    "input_mutation_detected": false,
    "failures": []
  },
  "performance": {
    "blocks": [
      {"block": 0, "candidate_ms": 0.0183, "incumbent_ms": 0.0194, "reference_ms": 0.0271}
    ]
  },
  "resources": {
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

AutoContext does not trust supplied summary numbers. It recomputes geometric
paired speedups, medians, p95 latency, reference drift, and a deterministic
paired-bootstrap 95% lower confidence bound from raw blocks. It also recomputes
source digests and the canonical hardware scope ID. A different GPU,
driver/runtime/toolchain, workload fingerprint, or reference baseline is a hard
scope mismatch. It also hashes and pins the complete protocol from the baseline,
so later proposals cannot weaken tolerances, hidden-trial counts, seeds,
warmups, timing blocks, or calls per block.

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
2. Candidate and incumbent compile, and every correctness/hidden trial passes.
3. The report contains at least the configured number of paired blocks.
4. Relative latency improvement meets the margin:

   ```text
   paired_speedup = geometric_mean(incumbent_ms / candidate_ms)
   relative_improvement = 1 - (1 / paired_speedup)
   ```

   The boundary is inclusive: a configured 5% margin accepts exactly 5%.
5. The paired-bootstrap 95% lower bound supports the configured margin (enabled by default). For a 5% latency margin,
   it must reach `1 / (1 - 0.05) = 1.05263x`; an exact boundary is accepted.
6. Candidate p95, reference drift, and peak-memory use remain within configured limits.
7. When confirmation is configured, a distinct but compatibility-matched
   protocol must pass the same correctness, improvement, confidence, tail,
   drift, and resource gates.

Compile failure, malformed JSON, nonzero command exit, timeout, correctness
failure, insufficient gain, or measurement instability rejects only that
proposal and preserves the incumbent. An invalid baseline is terminal because
there is no trustworthy comparison scope, but its failed attempt remains
auditable on disk.

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

For a long or open-ended search, repeated hypothesis testing makes a lucky
false positive increasingly likely. Configure `confirmation_fn` so provisional
winners face a fresh process, correctness seeds, and measurement order before
promotion. Confirmation reduces selection bias but does not provide a complete
sequential-testing guarantee; large campaigns should additionally use a fixed
attempt budget or an explicit alpha-spending policy.

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
