# Provider-neutral remote execution sessions

`RemoteExecutionRequest` is the Python control-plane contract for optional
remote execution. It describes a task without naming a provider:

- image and command/task entrypoint;
- CPU, memory, disk, and optional accelerator requirements;
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
losses. When task/artifact processing and resource cleanup both fail,
`cleanup_error` takes infrastructure precedence while the candidate failure is
retained in the error detail.

## Prime Intellect adapter

The Prime Intellect integration implements this generic request contract and
remains an optional Python extra. It detects provider capabilities before
provisioning and raises a clear unsupported-capability error for accelerator,
secret-grant, reuse, or warm/snapshot requests it cannot honor. GPU resources
are optional; ordinary CPU requests do not assume an accelerator exists.

`ephemeral_per_eval` is the default and always attempts sandbox deletion.
Prime Intellect session reuse is fail-closed by default: the adapter does not
advertise reuse until a provider integration can prove a clean task boundary.
When an operator explicitly enables that capability, `execute_requests()`
provides bounded reuse only for compatible matched-trial cohorts. Requests
must share lifecycle, image, resources, network and secret policies, secret
grants, input artifacts, environment, and snapshot, and all fit within the
declared reuse bound. Per-task metadata may differ because it is not supplied
when the sandbox is provisioned. One sandbox is then used and one cleanup is
recorded for the cohort. `warm_snapshot` requires both an explicit snapshot
reference and advertised Prime Intellect snapshot/warm capabilities from the
AC-784 adapter contract. There is no silent warm-to-cold substitution.

Input artifacts are materialized beneath the task root. Declared output
artifacts are read from the task's final JSON envelope, while JSON event lines
are retained as structured events. The provider API key stays in the host
client. A provider may receive only scoped, expiring grant identifiers when it
advertises secret-grant support.

## Scenario compatibility

The Prime Intellect client contains no game rules or scoring formulas.
`scenario_remote_task` packages a scenario module/class, strategy, seed, and
limits as a generic command; the remote image is responsible for containing
the requested autocontext scenario package. `PrimeIntellectExecutor` consumes
the typed result and preserves the existing `ScenarioInterface` execution
surface. Local execution remains unchanged.

The adapter's bare `python:3.11-slim` default does not contain Autocontext.
Live scenario execution must configure a hermetic image containing the exact
Autocontext/scenario sources and dependencies; otherwise the task fails (or
returns the explicit unavailable result when fallback is explicitly enabled;
fallback is disabled by default). Packaging
custom scenario modules and instance state is not inferred from the host.

Non-game code/research scenarios can construct `RemoteExecutionRequest`
directly, so they do not need provider-specific code or a game-shaped result.
The remote-session adapter currently lives in the Python control plane;
TypeScript retains its existing runtime/sandbox contracts and has no implicit
Prime Intellect fallback.
