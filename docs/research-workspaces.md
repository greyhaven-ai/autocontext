# Capability-scoped research workspaces

Python exposes two distinct persistent execution surfaces:

- `InterpreterWorkspace` is the default `restricted_scratch` profile. It is an
  in-process convenience for plain data exploration and does not provide file,
  import, subprocess, network, or credential access.
- `ResearchWorkspace` is an opt-in surface for `trusted_local` or
  `isolated_sandbox` profiles. Every elevated capability must appear in the
  request and be approved before the workspace opens.

```python
import sys

from autocontext.execution.research_workspace import (
    ResearchWorkspace,
    WorkspaceCapabilityRequest,
)

request = WorkspaceCapabilityRequest(
    workspace_id="paper-analysis",
    profile="trusted_local",
    requested_capabilities=frozenset({
        "workspace_read",
        "workspace_write",
        "package_import",
        "subprocess",
    }),
    allowed_imports=frozenset({"statistics"}),
    allowed_commands=frozenset({sys.executable}),
    lifecycle="delete_on_close",
)

workspace = ResearchWorkspace(request, approver=lambda request: True)
workspace.runtime_env.write_file("samples.txt", "1\n3\n8\n")
workspace.run("""
import statistics
def summarize(text):
    return statistics.mean(int(line) for line in text.splitlines())
mean = summarize(workspace_read_text("samples.txt"))
""")
workspace.run("workspace_write_text('result.txt', str(mean))")
snapshot = workspace.snapshot()
cleanup = workspace.close()
```

## Grant and isolation model

The request records the profile, requested grants, package and command
allowlists, network-host allowlist, limits, approval context, and lifecycle
policy. Elevated profiles default to deny when no approval hook is supplied.
Network is a separate grant and only permits HTTPS hosts listed in the request.
Redirects are denied instead of inheriting trust from the original URL.
Credentialed or authoritative operations stay in the host control plane and
use the typed `host_call()` bridge; credentials are never installed in the
candidate namespace or child-process environment.

Capable executions run in killable child processes. Each call works against a
staged copy of the workspace and commits variables and files only after a
successful result. A timeout terminates the child and discards that stage, so
it cannot keep mutating persistent state. Imports, subprocesses, output, files,
and network responses are bounded by explicit request limits. Path traversal,
symbolic-link snapshots, unapproved imports/commands, and ungranted network or
host calls fail closed.

`package_import` and `subprocess` remain host-powerful in the explicitly
approved `trusted_local` profile. `isolated_sandbox` never uses that local
child-process path: construction requires a `ResearchSandboxBackend` attesting
OS isolation, workspace mounts, network policy, process limits, environment
scrubbing, transactional files, terminable execution, and verified cleanup.
Missing controls fail before candidate code runs.

An isolated request cannot combine `package_import` and `subprocess`. Python
packages can retain process-capable objects in function globals, which would
bypass a command-name allowlist even when their public module attributes are
wrapped. Deployments needing both must provide a lower-layer process broker;
the shipped backend fails closed instead of treating an object facade as a
security boundary.

The shipped `DockerResearchSandboxBackend` is a concrete deny-network backend.
It uses the same pinned clean Python image as hermetic remote scenarios, a
read-only root filesystem, only the generated runtime-input, workspace, and
result-output bind mounts (no repository or unrelated host mount),
dropped Linux capabilities, `no-new-privileges`, a non-root UID, PID/CPU/memory
limits, a bounded tmpfs, and an explicit environment. A timeout forcibly
removes the named container. Every container carries its execution deadline;
restart reconciliation removes only containers whose deadline has expired.
Live containers owned by another worker, and legacy containers without a
verifiable deadline, are never guessed to be orphans. The task-runner factory
shares one backend across concurrent workspaces, while the deadline contract
also makes independent worker processes safe. It does not pretend a Docker
bridge plus Python hostname check is an
egress firewall: any request for network access fails. Deployments needing
allowlisted egress must supply a backend that enforces DNS, redirect, private
range, and subprocess policy below the container.

Opaque `WorkspaceSecretGrant` references remain in the host control plane.
Docker rejects callbacks that resolve a credential value for candidate use;
an optional broker can perform only the operations allowlisted on the grant
through `credential_call()`. Neither references nor credential values enter
the container environment, payload, workspace, or result artifact.

Aggregate workspace byte and inode quotas apply in addition to the per-file
limit. Docker reconciles expired labeled orphan containers before every
execution, and removal or verification failures propagate through the typed
cleanup result instead of being logged as successful cleanup.

Snapshots contain plain built-in variables, helper-function source, and
workspace-rooted files. `snapshot()`, `restore()`, and `close()` are explicit;
owned roots can be deterministically deleted with `delete_on_close`. Audit
events record the profile, grants, resource action, outcome, limits/usage
detail, host calls, and cleanup result.

The standard-library `trusted_local` process boundary is not a hostile-code VM.
Docker supplies a real mount/network/process boundary but still shares the host
kernel; deployments whose threat model requires a separate kernel should use a
microVM implementation of `ResearchSandboxBackend`. Both are explicit choices,
and neither can silently become `trusted_local`.

## Runtime responsibilities

The Python control plane owns the research interpreter and its process
lifecycle. Files are exposed through the existing `RuntimeWorkspaceEnv`
boundary, so a TypeScript session may coordinate the same declared workspace,
commands, and artifacts without embedding Python execution. TypeScript does
not currently implement a parallel research interpreter; this is an explicit
runtime asymmetry, not an implicit fallback.

`benchmark_research_workspace()` provides a deterministic three-generation
acceptance task. It records task quality, prompt-size samples, wall time, and
cleanup outcomes against the restricted baseline.

The live `TaskRunner` can select this path without application-specific
injection. The following fail-closed settings make multi-generation queued
candidates executable, preserve the accepted workspace state across
generations, and judge stdout plus the structured `answer` mapping:

```bash
export AUTOCONTEXT_WORKSPACE_INTERPRETER_ENABLED=true
export AUTOCONTEXT_WORKSPACE_INTERPRETER_BACKEND=docker
export AUTOCONTEXT_WORKSPACE_INTERPRETER_EXECUTE_CANDIDATES=true
export AUTOCONTEXT_WORKSPACE_INTERPRETER_CAPABILITIES_APPROVED=true
export AUTOCONTEXT_WORKSPACE_INTERPRETER_ALLOWED_IMPORTS='["statistics"]'
export AUTOCONTEXT_WORKSPACE_INTERPRETER_ALLOWED_COMMANDS='[]'
```

Candidate execution is accepted only with the Docker backend and explicit
operator approval; selecting the legacy interpreter for this mode raises a
configuration error. The image is pinned by digest by default, and memory,
CPU, PID, timeout, per-file bytes, aggregate workspace bytes/inodes, import,
and command settings are independently configurable.
No network setting exists for the shipped Docker backend because its only
supported policy is deny. Workspace execution and cleanup audit events are
included in the persisted evolution trajectory. Applications may still inject
an `EvolutionWorkspace` factory and evaluation callback for a different
lower-layer sandbox implementation.
