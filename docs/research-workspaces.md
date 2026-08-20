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

`package_import` and `subprocess` are available only to the explicitly approved
`trusted_local` profile. `isolated_sandbox` rejects both until it is backed by a
real OS sandbox: an allowed module can expose transitive file, network, or
process primitives, and an executable allowlist cannot constrain what an
interpreter or shell reads, writes, or connects to.

Snapshots contain plain built-in variables, helper-function source, and
workspace-rooted files. `snapshot()`, `restore()`, and `close()` are explicit;
owned roots can be deterministically deleted with `delete_on_close`. Audit
events record the profile, grants, resource action, outcome, limits/usage
detail, host calls, and cleanup result.

The standard library process boundary is not a hostile-code VM. Package code
and the Python runtime still share the host kernel, so deployments that accept
adversarial programs must supply a real sandbox adapter. The
`isolated_sandbox` profile fails closed for package imports and subprocesses in
the meantime.

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
