# @autocontext/core skeleton

Internal Apache-2.0 package skeleton for the TypeScript core boundary.

The core facade exposes pure contracts and dependency-light primitives that can
be shared by local tools, packaged runtimes, and future control-plane surfaces.

## Runtime Workspace Contract

`RuntimeWorkspaceEnv` is the core boundary for workspace-scoped filesystem and
shell behavior. It models runtime isolation as plumbing around a run, task, or
mission step rather than as a top-level product concept.

Current core-owned exports include:

- `RuntimeWorkspaceEnv`
- `createInMemoryWorkspaceEnv`
- `createLocalWorkspaceEnv`
- `defineRuntimeCommand`

Provider-specific runtimes such as Claude, Codex, Pi, and direct API adapters
remain outside the core package boundary.
