# Explicit executable skill candidates (Python)

The AC-1028 pilot converts an explicitly enrolled helper into a callable,
isolated **proposal** for one non-game transformation: profile schema v1 to v2.
It has no external side effects and requires no LLM completion interface.

Ordinary context-bundle `TOOL_SPEC` components still render as reference helpers.
Neither rendering nor source discovery makes a helper executable. Call
`propose_schema_migration()` with a `SkillReference`, an existing baseline,
source example/counterexample artifacts, and resource limits to create an
inactive candidate. It stores an explicit `executable_skill_candidate`
eligibility record in the existing `ContextBundleStore`. It does not create
another registry or promote the candidate.

## Run the end-to-end fixture

Docker must be running. Prepare the repository's exact pinned image before
invocation; the executor never downloads images or falls back to local Python.

```bash
docker pull python:3.11.10-slim-bookworm@sha256:840e180ebcc6e5c8efab209c43f5e40fd2af98cb49db5c7103c90539c56bb30e
cd autocontext
uv sync --group dev
uv run python ../examples/executable_skills/run.py --output /tmp/autocontext-skill-demo
```

Use a new output directory each time. The example records inputs, immutable
candidate/evidence artifacts, verified transformed proposals, an unsupported
input that abstains, and a refused attempt to serve the inactive candidate.
Its active pointer remains unchanged. This is deterministic engineering test
data, not synthesis evidence, a held-out benchmark, or a promotion decision.
The fixture also replays these inputs through the opt-in
[skill/model router](skill-routing.md), retaining per-request decisions without
enabling model calls.

The task contract accepts exactly `schema_version`, `name`, and `enabled`.
Version 1 maps to version 2 by retaining the name as `display_name` and mapping
the boolean to `enabled`/`disabled` status. Other versions, unknown fields,
coerced types, duplicate keys, excessive inputs and invalid JSON abstain.
The host independently checks the complete output against this contract;
valid JSON with the wrong name or status is a verification failure.

## Runtime seam and identity

```python
result = invoke_executable_skill(
    store, bundle_digest, input_json,
    mode="evaluation",  # explicit inactive candidate/incumbent replay
    caller_limits=limits,
    cancel=cancellation_event,  # optional threading.Event
    request_budget=shared_budget,  # optional RuntimeBudget across routed attempts
)
```

Use `mode="serving"` for live requests. It requires that this exact bundle is
active under the current evaluator and has a matching durable promotion
artifact. Bootstrapping an active bundle is insufficient. The gate and artifact
identity are checked again before returning an accepted proposal, followed by a
final cancellation check. Revocation, tampering or cancellation during execution
or final verification discards the result. This is a request boundary,
not a lock on later application of a returned artifact. Applying a proposal is
the caller's responsibility and is outside this pilot.

Both modes use the same immutable manifest, `SkillReference` assembly, pinned
runtime, applicability checks and verifier. Candidate/incumbent evaluators pin
their respective bundle digests and invoke this same function. AC-1020's
[opt-in router](skill-routing.md) composes this seam with verified model fallback;
AC-1021 owns complete route promotion decisions and atomic activation.
This seam is opt-in and is not wired into automatic production routing.
When a `RuntimeBudget` is supplied, its absolute deadline is checked after
preflight and carried into Docker execution. It caps runtime timeouts without
changing the immutable manifest, and expired requests never start a container.

The manifest shares AC-1019's base contract without changing its GridCTF v1 wire
fields or canonical digest. It binds the source/entrypoint, input/output schemas,
applicability, evidence contents/digests, empty dependency/capability grants,
pinned image, limits, and evaluator implementation identity (including the host
Python and Pydantic versions used by the verifier). Replaying under a
changed implementation requires a new candidate/evaluator epoch. Existing
GridCTF candidates retain their existing behavior and execution boundary.

Results distinguish `success`, `abstention`, `execution_failure`, and
`verification_failure`, with the bundle, manifest, source, raw input, environment
and evaluator identities. A container run also binds its resolved image
ID/architecture into the environment digest. Keep the original input JSON to replay its raw-byte digest;
the example retains the input values and uses `json.dumps(value)` for invocation.
Input size is checked before encoding or hashing; UTF-8 byte length is also
bounded. `input_sha256` is null when preflight rejects oversized or unencodable
input, or cancellation occurs before input validation. Bounded UTF-8 input
retains its raw-byte digest even when later schema validation rejects it.
Successful `output_json` is canonical JSON. Only verified success includes an
output proposal.

## Boundary and resource accounting

The adapter uses the shared Docker isolation command builder and bounded-output
process primitives. Code runs in a digest-pinned Python 3.11 container with a
read-only root and input mount, no writable host mount, no network, scrubbed
environment, dropped capabilities, and memory/swap, CPU, process and file
descriptor limits. Responses cross as bounded UTF-8 JSON, never as host-loaded
Python, pickle or candidate-written host files. Python standard-library access
inside the container remains available. No external dependencies or host
capability grants are supported.

The default budget is 10 seconds, 128 MiB memory and 64 KiB per output stream;
source/input are each limited to 64 KiB and scratch tmpfs to 16 MiB. CPU quota is
one core, process count is capped at 16, and CPU time is capped per process.
The wall deadline includes image inspection, container startup, Docker OOM-state
inspection and a bounded check of the container's daemon OOM and exit events.
An OOM kill, including a killed child whose parent returns valid JSON and exits
zero, returns `oom`. A Python `MemoryError` without a kernel OOM event can return
`candidate_error`, but cannot authorize output. Missing or malformed resource
state or an unverified container exit event returns `resource_status_unverified`;
inspection errors also fail closed.
Cancellation, timeout and output overflow terminate the invocation; cleanup force-removes the
container and verifies removal with a separate bounded cleanup allowance.
Cleanup failure returns `cleanup_unverified` and never accepts an output. Host
process death and wider legacy execution isolation remain AC-1009 work; this
bridge does not complete that remediation.

Missing Docker/image/daemon, unsupported runtime/capabilities/dependencies,
excessive resource requests, artifact drift or cancellation fail closed. The
real Docker regression is explicitly selected by CI's `sandbox-integration`
job; normal unit tests alone do not establish OS isolation.

`model_calls` is zero for invocation. `execution_seconds`, total elapsed time,
declared resource limits, and resolved image identity are reported separately.
CPU-seconds, peak memory and monetary cost are not measured by this pilot; zero
model calls must not be presented as zero total cost or proven savings.

TypeScript parity is deferred: this pilot uses the Python control plane's
manifest, bundle lifecycle and Docker boundary. No TypeScript callable-skill or
automatic-routing support is claimed.
