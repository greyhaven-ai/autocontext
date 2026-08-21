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

For authoritative evaluation of hostile generated code, use
`DockerProtectedKernelBenchmarkRunner`. Its core protocol is accelerator
neutral: attestations name a backend, vendor, architecture, device/partition,
runtime, driver, and capacity without encoding CUDA, NVIDIA, MIG, or H100 into
the schema. The H100/MIG example is the first conformance profile, not a limit
on future ROCm, other accelerator, or VM-backed implementations.

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

## Protected evaluator authority boundary

`DockerProtectedKernelBenchmarkRunner` creates three separately named
containers: a trusted evaluator, an isolated candidate authority, and an
isolated incumbent authority. The evaluator mounts private plans, references,
its immutable code, two Unix-socket directories, and report storage, but no
generated source. Each artifact container mounts only its own read-only source,
the public authority worker/support files, and one read-only socket directory.
It receives no private-plan path, report path, evaluator build identity, other
role channel, ambient credential, network, or durable writable mount.

The socket contract uses strict bounded JSON headers (including duplicate-key
rejection) and canonical safetensors
payloads; pickle and candidate-authored timing/resource/report fields are not
accepted. The evaluator creates fresh randomized timed inputs, independently
checks each returned tensor against its trusted reference, measures wall time
around the authority exchange, and observes accelerator-partition memory from
the trusted side. This prevents cached correctness outputs, forged clocks, or a
candidate-local `torch` patch from becoming authoritative evidence.

Every report can carry an
`autocontext.accelerator-authority-receipt/v1` receipt binding:

- candidate and incumbent artifact identities;
- private-plan commitment and input/output commitments;
- a constant-size evaluator-owned transcript summary: ordered transcript and
  request/response set roots, unique exchange counts, outcome counts, and
  role-specific resource peaks;
- evaluator build and public boundary-manifest digests;
- the generic accelerator attestation and the report content itself.

Receipts are authenticated with HMAC-SHA256 under an operator-pinned key id and
owner-only 0400/0600 secret file. The secret is mounted only into the evaluator
and is never serialized in a manifest, report, observation, or profile export.
`KernelBenchmarkEvaluatorConfig(require_authority_receipt=True)` requires that
external trust configuration and rejects missing, self-issued, tampered, or
wrong-build receipts. Replay also reconciles transcript outcomes and resource
peaks with the authenticated report. The protected Docker runner requires the
authenticated evaluator/boundary digests, device, partition kind/capacity,
attestor, and underlying host grant digest to match host-computed values.
Protocol corruption, evaluator crash,
candidate crash, OOM, timeout, and teardown failure remain separate outcomes.

Portable campaign profile evidence is separately wrapped in
`autocontext.kernel-profile-evidence-envelope/v1`. Its canonical content digest
and HMAC authenticate the complete profile payload—champion and report wrapper
identities, decision policy, holdout summaries, resource claims, and campaign
counts—so a valid evaluator receipt cannot be transplanted into a doctored
profile artifact. Consumers must verify the outer envelope with the pinned key
before using any profile field.

Normal teardown kills and removal-verifies every created container. Crash-safe
ownership during synchronous container creation is not implemented, so the
protected runner remains unavailable; expiry labels alone do not close that
pre-watchdog coordinator-loss window. Manifests expose this as the unavailable
`crash_safe_container_creation` requirement.

Protected adaptive campaigns should also set
`adaptive_feedback_policy="aggregate-gates"`. Promotion and persisted audit
evidence still use the exact trusted statistics, but later candidate prompts
receive only documented passed/failed/not-evaluated gates and a disposition—no
candidate-modulatable latency digits or exact derived metrics. Confirmation
feedback remains quarantined as before.

The H100 example's protected factory is constructible and manifest-inspectable,
but neither it nor the normal recursive campaign can execute authoritative
work. The runner returns `resource_policy_unsupported` before Docker while
independently attested role grants, trusted out-of-process mutation observation,
comparable candidate/incumbent/reference timing boundaries, and crash-safe
container creation are unavailable. The release guard must remain until all
four requirements are implemented and
the exact digest-pinned path passes real H100 adversarial validation.

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
        min_timing_blocks=8,
        bootstrap_samples=None,
        statistics_method="paired-sign-eprocess/v1",
        finite_sample_improvement_margin=0.05,
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
        min_timing_blocks=8,
        bootstrap_samples=None,
        statistics_method="paired-sign-eprocess/v1",
        finite_sample_improvement_margin=0.05,
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
        min_relative_improvement=0.05,
        precision_profile="strict-fp32-v1",
        proposal_cap=10,
        familywise_alpha=0.05,
    ),
    generate_fn=my_kernel_generator,
    evaluator=evaluator,
    lineage_root=Path("runs/kernel-evolution"),
    confirmation_fn=confirm,
    sealed_audit_root=Path("/operator-private/kernel-confirmation-audit"),
)
result = runner.run(proposals=10)
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
For finite-sample campaigns, the complete confirmation observation, raw report,
case names, and detailed veto are first written to `sealed_audit_root`, which
must be outside the mailbox and public lineage root. The adaptive run directory
contains only report/audit digests and aggregate gate states. After generation
has stopped, terminal completion, failure, or interruption publishes the sealed
records under `audit/confirmation`; they cannot influence a later proposal.

## Benchmark report contract

New finite-sample adapters write `autocontext.kernelbench-eval/v4`. Pydantic validates it
with unknown fields forbidden and NaN, infinity, zero, or negative timings
rejected. The v4 report includes a measurement-design receipt; the v4 derived
statistics receipt binds the raw-report and raw-block digests, method, block
definition, sample count, deterministic schedule-seed derivation, policy ID,
and every promotion-affecting metric. V4 lineage, result, and H100 profile
receipts reproduce the same complete decision-policy digest and replay every
gate and champion transition. Result, attempt, and report versions cannot be
mixed. Important fields are:

```json
{
  "schema_version": "autocontext.kernelbench-eval/v4",
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
    "timing_blocks": 8,
    "calls_per_block": 10,
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
    "sequential_testing": {"method": "bonferroni", "proposal_cap": 10, "familywise_alpha": 0.05}
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
  "metadata": {
    "measurement_design": {
      "schema_version": "autocontext.kernel-measurement-design/v1",
      "block_definition": "balanced-interleaved-paired-block/v1",
      "schedule_seed_derivation": "sha256-plan-commitment-block-schedule/v1",
      "dependence_assumption": "conditional-threshold-win-probability-lte-half/v1",
      "fixed_block_count": 8,
      "early_stopping_allowed": false,
      "order_balanced": true
    }
  }
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
paired speedups, medians, p95 latency, reference drift, threshold-win signs,
the terminal e-value, and its finite-sample p-value bound from the raw blocks
and policy. It also recomputes
source digests and the canonical hardware scope ID. A different GPU,
driver/runtime/toolchain, workload fingerprint, or reference baseline is a hard
scope mismatch. It also hashes and pins the complete protocol from the baseline,
so later proposals cannot weaken precision semantics, tolerances, holdout
commitments, slice floors, search budget, warmups, timing blocks, or calls per
block.

The production statistic is the pre-registered paired sign e-process. A block
is a win only when `incumbent_ms / candidate_ms >= 1 / (1 - margin)`. With the
fixed all-in bet and null conditional win probability at most one half, each
win multiplies the e-value by two and any non-win zeros it. Therefore eight
pre-registered blocks have exact per-look bound `2^-8 = 0.00390625`; ten
Bonferroni looks have familywise bound at most `10 / 256 = 0.0390625`, below
the configured 0.05 budget. The argument needs no Gaussian timing model and no
independence assumption on timing magnitudes. It does require the documented
conditional sign assumption at the block boundary, fixed block count, balanced
order, no early stopping, and a schedule fixed by the private-plan commitment.

`calibrate_kernel_promotion()` deterministically stress-tests the exact eight-
block, ten-proposal configuration under null, heavy-tail, paired shared drift,
across-block AR(1) magnitudes with conditionally symmetric signs, and
heteroskedastic noise. Simulation is a
diagnostic of the implementation and operating design; the theorem above, not
a Monte Carlo percentile, supplies the advertised error bound. Historical v2
and v3 evidence remains readable as explicitly unverified policy replay. The
v1 empirical percentile bootstrap remains available for legacy/non-production
reports and is never described as distribution-free coverage.

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
6. The finite-sample receipt shows every pre-registered paired block clears the
   configured margin and its sign e-process p-value is at most
   `familywise_alpha / proposal_cap` (Bonferroni alpha spending). V4 evidence
   contains no bootstrap field labeled as a confidence bound.
7. Candidate p95, reference drift, and peak-memory use remain within configured limits.
8. When confirmation is configured, a distinct but compatibility-matched
   protocol must pass the same correctness, improvement, confidence, tail,
   drift, and resource gates. Each attempted confirmation burns its protocol
   identity; later adaptive proposals must use a new independently committed
   confirmation plan. Detailed confirmation evidence remains in sealed audit
   storage until the adaptive campaign is terminal; prompts receive only
   aggregate passed/failed/not-evaluated gates and disposition.

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
finite-sample bound for every proposal, and persists proposal index, cap,
per-proposal alpha, cumulative spend, and confidence level with each attempt. Configure
`confirmation_fn` as well so provisional winners face a fresh process,
disjoint committed inputs, and a different measurement order. Supply enough
fresh plans for every possible confirmation attempt; a fixed confirmation plan
cannot be reused after its result has influenced champion selection.

`read_kernel_evolution_result()` makes compatibility explicit:

- v2: readable as `legacy-v2-unverified-policy-replay`;
- v3: readable as `legacy-v3-empirical-unverified-policy-replay`;
- v4: accepted only after finite-sample policy/raw-block/gate/lineage replay;
- a reader capped at v2 or v3 rejects v4 with a clear newer-reader error.

Duplicate JSON keys, non-finite constants, mixed nesting, missing policy IDs,
and canonical-digest mismatches fail closed. There is no ambiguous automatic
downgrade from v4 to a legacy evidence family.

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
