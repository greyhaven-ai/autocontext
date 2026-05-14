# Flue-Inspired Runtime Decisions

Short design note recording what AutoContext borrowed from a [Flue](https://github.com/withastro/flue)
review and what it explicitly did not borrow. This is internal reference
material so future contributors do not copy Flue terms, APIs, or product
positioning by accident.

The canonical AutoContext concept model remains [concept-model.md](./concept-model.md);
this doc is positioning, not new vocabulary.

## What we borrowed (and where it landed)

- **Runtime workspace / session contract** as a first-class boundary.
  Landed in `ts/src/runtimes/` (`RuntimeWorkspaceEnv`, `RuntimeSessionAgentRuntime`)
  and `autocontext/src/autocontext/runtimes/` with parity in `runtime-session-*`
  modules and recorded session logs.
- **Scoped command and tool grants.** Landed as `RuntimeCommandGrant`,
  `RuntimeToolGrant`, `RuntimeGrantScopePolicy` in the runtime contracts.
  Grant events surface lifecycle, redaction, and provenance metadata.
- **Child-agent task execution with isolated history.** Landed as the
  child-task inheritance model on runtime grants and the
  `runtime-session-run-trace` adapter that maps lineage into `RunTrace`.
- **Runtime context layering and `cwd` discovery.** Landed in the
  session runtime-context modules: `ts/src/session/runtime-context.ts`
  and `autocontext/src/autocontext/session/runtime_context.py` own the
  canonical layer order, repo instruction discovery
  (`AGENTS.md`/`CLAUDE.md`), skill discovery, and the
  `assembleRuntimeContext` / `assemble_runtime_context` helpers.
  Workspace adapters (`createLocalWorkspaceEnv`,
  `createInMemoryWorkspaceEnv`) own virtual `cwd` / path resolution
  beneath that layer, not the layering itself.
- **Programmable agent app runner and deploy targets** (later, post-spike).
  In flight as the agent-app build-target work (AC-724 parent, AC-762
  Node MVP, AC-763 Cloudflare spike).

## What we explicitly did not borrow

- **The Flue dependency itself.** AutoContext does not import or wrap
  Flue at runtime. The borrowed ideas are reimplemented against
  AutoContext's own contracts and pass our own test suites.
- **Flue API names.** AutoContext keeps its own surface
  (`createLocalWorkspaceEnv`, `defineRuntimeCommand`, etc.). Code review
  should flag any drift toward Flue-shaped names.
- **Flue's provider stack** (Astro / Vite assumptions, etc.). Out of
  scope.
- **Flue vocabulary as a replacement for AutoContext nouns.** AutoContext
  keeps its own product model: `Scenario`, `Task`, `Mission`,
  `Campaign`, `Run`, `Step`, `Verifier`, `Artifact`, `Knowledge`,
  `Budget`, `Policy`. See [concept-model.md](./concept-model.md) for the
  full table.

## Naming guardrails for public docs

- `sandbox` is **runtime isolation / policy**, not a peer top-level
  product noun. Sandbox backends (local subprocess, Monty, Gondolin
  microVM, PrimeIntellect) live under `Run` execution, not at the same
  level as `Scenario` or `Mission`.
- `workspace` and `session` describe the runtime boundary, not a
  user-facing concept distinct from `Run`. A `Run` may use a
  `RuntimeWorkspaceEnv` for filesystem and command access; the workspace
  is the _how_, not the _what_.

## Core vs control-plane ownership

The split is documented separately in
[core-control-package-split.md](./core-control-package-split.md). For
the borrowed ideas above:

- **Core** owns: `RuntimeWorkspaceEnv` adapters, grant types, session
  recording, child-task lineage. These are runtime contracts shared
  across packages.
- **Control plane** owns: promotion gates, eval-run integrity,
  candidate/quarantine semantics, harness change proposals. Flue did
  not influence this layer.

The agent-app build targets (Node MVP, Cloudflare spike) live in the
control-plane / packaging layer, not the runtime contracts; the runtime
contracts are reused as-is by whichever build target embeds the agent.

## Status

Borrowed ideas above are either shipped (workspace contract, grants,
child-task lineage, cwd discovery, session recording) or in-flight via
explicit issues (agent-app build targets). No new public commands are
introduced by this note; the relevant CLI commands (`autoctx agent run`,
`autoctx agent dev`) ship under AC-723 with their own help text and
docs.

If a future change wants to surface a Flue-shaped command or vocabulary
publicly, that change should explicitly update this note first.
