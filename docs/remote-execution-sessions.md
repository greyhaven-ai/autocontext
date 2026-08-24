# Provider-neutral remote execution sessions

`RemoteExecutionRequest` is the Python control-plane contract for optional
remote execution. It describes a task without naming a provider:

- image and command/task entrypoint;
- CPU, memory, disk, and optional accelerator requirements;
- optional region and required hardware/usage telemetry;
- timeout and network policy;
- opaque, expiring secret-grant references (never secret values);
- input artifacts and declared output artifacts; and
- an explicit `ephemeral_per_eval`, `reuse_matched_trials`, or
  `warm_snapshot` lifecycle.

```python
from autocontext.execution.remote_execution import (
    RemoteExecutionRequest,
    RemoteInputArtifact,
    RemoteResourceRequest,
)
from autocontext.integrations.primeintellect import PrimeIntellectClient

request = RemoteExecutionRequest(
    task_id="analyze-dataset",
    image="my-autocontext-research-image:latest",
    command="python analyze.py",
    resources=RemoteResourceRequest(cpu_cores=2, memory_gb=8, disk_gb=20),
    timeout_seconds=120,
    network_policy="deny",
    input_artifacts=(RemoteInputArtifact("analyze.py", b"print('{}')"),),
)

result = PrimeIntellectClient(api_key="...").execute_request(request)
```

`RemoteExecutionResult` returns structured stdout/stderr events, exit status,
typed artifacts, resource usage, session identity, and cleanup outcome. Its
status keeps `timeout`, `provider_error`, `task_error`, `artifact_error`, and
`cleanup_error` distinct. `to_ledger_entry()` produces the external-evaluation
ledger projection so infrastructure failures cannot be counted as candidate
losses. `artifact_error` is an infrastructure outcome, including a declared
scenario-package bootstrap exit; only a successfully bootstrapped scenario
failure is a candidate `task_error`. When task/artifact processing and resource cleanup both fail,
`cleanup_error` takes infrastructure precedence while the candidate failure is
retained in the error detail.

## Prime Intellect adapter

The Prime Intellect integration implements this generic request contract and
remains an optional Python extra. It detects provider capabilities before
provisioning and raises a clear unsupported-capability error for accelerator,
secret-grant, reuse, or warm/snapshot requests it cannot honor. GPU resources
are optional; ordinary CPU requests do not assume an accelerator exists.

Prime accelerator support is explicit rather than inferred from the SDK. The
installed Prime SDK exposes accelerator request and resolved-sandbox fields but
does not expose a capability-discovery endpoint, so an operator configures the
pool allowlist that was verified out of band. A request is accepted only when
its immutable image, optional region, accelerator kind/count, and required
telemetry fit that allowlist. Empty allowlists mean accelerator execution is
disabled; they do not mean “accept anything.”

Example environment for an operator-verified H100 pool (substitute the exact
kind, region, and digest available to your account):

```bash
export AUTOCONTEXT_EXECUTOR_MODE=primeintellect
export AUTOCONTEXT_PRIMEINTELLECT_API_KEY=...
export AUTOCONTEXT_PRIMEINTELLECT_ACCELERATOR_KIND=H100
export AUTOCONTEXT_PRIMEINTELLECT_ACCELERATOR_COUNT=1
export AUTOCONTEXT_PRIMEINTELLECT_REGION=us-central-1
export AUTOCONTEXT_PRIMEINTELLECT_SUPPORTED_ACCELERATOR_KINDS=H100
export AUTOCONTEXT_PRIMEINTELLECT_MAX_ACCELERATOR_COUNT=1
export AUTOCONTEXT_PRIMEINTELLECT_SUPPORTED_REGIONS=us-central-1
export AUTOCONTEXT_PRIMEINTELLECT_SUPPORTED_IMAGES="$AUTOCONTEXT_PRIMEINTELLECT_DOCKER_IMAGE"
export AUTOCONTEXT_PRIMEINTELLECT_REQUIRED_TELEMETRY=hardware_identity
export AUTOCONTEXT_PRIMEINTELLECT_AVAILABLE_TELEMETRY=hardware_identity
```

`AUTOCONTEXT_PRIMEINTELLECT_ACCELERATOR_KIND` and `_COUNT` select the
default accelerator for ordinary Prime-backed runs. Campaign plans declare
their accelerator independently so a plan cannot inherit a hidden GPU
requirement from the process environment. Supported kinds and
`AUTOCONTEXT_PRIMEINTELLECT_MAX_ACCELERATOR_COUNT` must be configured together.
Configured regions and images are exact allowlists. Telemetry names are
`hardware_identity`, `accelerator_usage`, and `accelerator_peak_memory`;
requesting usage telemetry that the provider integration cannot produce fails
closed.

Every provider create call receives the exact validated image, CPU/memory/disk
request, accelerator kind/count, region, and a SHA-256 idempotency key derived
from the complete non-secret remote request. After creation, the adapter checks
the provider-resolved image, region, accelerator kind, and count before running
the command. Drift is an infrastructure failure, the sandbox is cleaned up,
and the command is not dispatched or retried. An accelerator request is never
eligible for the historical local fallback, even when
`AUTOCONTEXT_ALLOW_PRIMEINTELLECT_FALLBACK=true`.

Typed result and ledger provenance include the request digest, requested
placement, required telemetry, resolved image/region/hardware, and Prime SDK
runtime identity. Provider-reported accelerator seconds and peak memory are
copied into `RemoteResourceUsage` when present. Missing required telemetry is
provider drift; optional unavailable metrics remain `null` rather than being
invented from candidate output.

CI retains the ordinary opt-in live Prime CPU smoke. A separate accelerator
smoke runs only when repository variables
`AUTOCONTEXT_PRIMEINTELLECT_LIVE_ACCELERATOR_KIND`, `_COUNT`, `_REGION`, and
`_IMAGE` are all deliberately configured (the kind variable is the step gate).
It is skipped by default so forks and ordinary pull requests never provision a
paid accelerator implicitly.

`ephemeral_per_eval` is the default and always attempts sandbox deletion.
Prime Intellect session reuse is unconditionally disabled, even if a caller
injects a `session_reuse` provider-capability flag. `execute_requests()` fails
closed until the provider exposes a verified task-reset primitive; sequential
commands are never run in one dirty sandbox. `warm_snapshot` requires both an
explicit snapshot reference and advertised Prime Intellect snapshot/warm
capabilities from the AC-784 adapter contract. There is no silent warm-to-cold
substitution.

The adapter keeps a thread-safe task-to-sandbox cancellation registry.
`cancel_request()` accepts either the durable task id or its request and uses
provider deletion to interrupt active work. Execution cleanup and concurrent
cancellation share one exactly-once delete outcome, and a cancellation that
arrives before sandbox creation is honored as soon as the provider returns the
sandbox handle.

Input artifacts are materialized beneath the task root. Declared output
artifacts are read from the task's final JSON envelope, while JSON event lines
are retained as structured events. The provider API key stays in the host
client. A provider may receive only scoped, expiring grant identifiers when it
advertises secret-grant support.

## Scenario compatibility

The Prime Intellect client contains no game rules or scoring formulas.
`scenario_remote_task` builds a deterministic stdlib zipapp containing the
exact built-in or custom scenario module, a minimal `ScenarioInterface` ABI,
JSON instance state, strategy, seed, and recursively discovered local Python
dependencies. Reconstruction bypasses `__init__` and restores the validated
instance state, so scenarios with required constructor arguments remain
executable without guessing constructor inputs. Its manifest records every
file and provenance digest. The remote command verifies the complete package digest, and the
zipapp independently re-verifies its format, runtime, and embedded file
digests before importing or constructing the scenario. Those bootstrap checks
use the request's typed infrastructure exit code. Imports outside the packaged source and standard library fail
preflight, so missing dependencies are infrastructure/configuration failures,
not candidate losses.

The default image is an immutable digest-pinned Python 3.11 slim runtime, and
mutable Prime image settings are rejected while settings are loaded. The
package needs no in-sandbox installation and runs with network denied. Shared
tests execute a built-in and a stateful non-game scenario with `python -I`; an
opt-in Docker CI test executes the same custom artifact in the exact clean,
read-only, network-denied image used by the adapter. Image, package, input,
file, scenario-state, strategy, and seed provenance is copied into every typed
result and external-evaluation ledger entry. Prepared context-bundle
evaluations additionally bind the canonical state-plus-observation fixture
digest and separate hashes of both payloads; all three fields are absent for
ordinary seeded execution and required together for prepared execution. A
zero-exit scenario response is
still an infrastructure `artifact_error` unless its result and replay envelopes
pass structural and provenance validation. `PrimeIntellectExecutor` consumes the typed result and
preserves the existing `ScenarioInterface` execution surface. Local execution
remains unchanged.

Non-game code/research scenarios can construct `RemoteExecutionRequest`
directly, so they do not need provider-specific code or a game-shaped result.
The remote-session adapter currently lives in the Python control plane;
TypeScript retains its existing runtime/sandbox contracts and has no implicit
Prime Intellect fallback.
