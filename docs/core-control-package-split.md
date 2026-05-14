# Core/Control Package Split

This document is the source of truth for the autocontext core/control package
boundary. It turns the Linear strategy in AC-642, AC-643, AC-644, AC-648,
AC-649, and AC-650 into a concrete implementation guardrail before moving
behavior or changing public install paths.

## Strategy

autocontext is keeping the existing public repository and already-written code
Apache-2.0. The boundary work continues as architecture and package hygiene, not
as a historical relicensing project.

The package split should make these domains clear:

1. Apache-2.0 core: foundational runtime, SDK, scenario contracts, providers,
   execution primitives, local state, and extension points.
2. Apache-2.0 control plane: operator workflows, management UX, orchestration,
   advanced trace management, knowledge packaging/export, and other higher-level
   control surfaces that still live in this repo.
3. Future proprietary products: hosted infrastructure, enterprise deployment,
   service-only features, and other net-new proprietary work in a separate repo
   under its own license.

The goal is not a repo-wide source-available license flip. The goal is a clean
Apache public foundation with stable contracts that a future proprietary repo can
depend on without copying or relicensing historical code.

## Hard Guardrails

- Keep the existing public repository and already-written code Apache-2.0.
- Do not add dual-license metadata, per-package non-Apache license files, or a
  root `LICENSING.md` for the existing repo.
- Treat AC-645 as superseded unless it is re-scoped to Apache metadata hygiene.
- Treat AC-646 as provenance context, not as a blocker for boundary wrap-up.
- Preserve `pip install autocontext`, `npm install autoctx`, and the `autoctx`
  CLI as the default compatibility path while the split is in progress.
- Keep `autocontext/` and `ts/` as umbrella compatibility packages until the
  new artifacts are buildable and downstream migration is documented.
- Treat `knowledge` and production traces as dedicated split projects, not
  incidental fallout from package extraction.
- Prefer compatibility shims and re-exports over breaking old import paths
  during the first migration phases.

The boundary-enforcement contract also encodes the Apache-only publication rule:
no root `LICENSING.md`, no per-package non-Apache `LICENSE` files, and no
dual-license metadata for the existing repo. The AC-646 engineering audit is
preserved as historical provenance context in
[`contributor-rights-audit.md`](./contributor-rights-audit.md).

## Package Topology

The machine-readable topology map lives in
[`packages/package-topology.json`](../packages/package-topology.json). The
machine-readable boundary-enforcement contract lives in
[`packages/package-boundaries.json`](../packages/package-boundaries.json) and is
checked in CI.

| Ecosystem  | Umbrella package                                | Apache core artifact | Control-plane artifact       |
| ---------- | ----------------------------------------------- | -------------------- | ---------------------------- |
| Python     | `autocontext`                                   | `autocontext-core`   | `autocontext-control`        |
| TypeScript | `autoctx`                                       | `@autocontext/core`  | `@autocontext/control-plane` |
| Pi         | `pi-autocontext` initially depends on `autoctx` | Deferred             | Deferred                     |

The umbrella packages preserve the default install and CLI experience. The new
core/control artifacts make the dependency boundary explicit at the artifact
level while remaining Apache-2.0 in this repo.

## Agent App Build Targets

autocontext should treat `build --target node|cloudflare` deployment targets as
control-plane packaging around stable runtime contracts, not as new runtime
contracts themselves. The machine-readable target boundary lives in
[`packages/package-topology.json`](../packages/package-topology.json) under
`agentApps`.

Ownership:

- Runtime contracts are still umbrella-owned until the extraction work adds
  those exports to `@autocontext/core`. Today, the Node build target must use
  the public `autoctx/agent-runtime` surface, plus the runtime-session exports
  already available from `autoctx`, instead of importing missing core package
  contracts.
- The planned home for reusable runtime contracts remains the Apache core
  artifact once the boundary explicitly exports handler loading contracts,
  runtime workspace/session environment contracts, scoped command/tool grants,
  child-task contracts, context-layer discovery contracts, provider/runtime
  interfaces, runtime-session event contracts, and the dependencies needed by
  those contracts.
- Build and deploy workflows belong in the Apache control-plane artifact. This
  includes target selection, bundle planning, generated server/worker templates,
  target-specific adapters, packaging checks, and operator-facing CLI/API
  commands.
- The umbrella `autoctx` CLI may dispatch build commands while package splitting
  is in progress, but it should delegate to the control-plane implementation.
- Hosted fleet orchestration is out of scope for this Apache repo. Multi-tenant
  worker scheduling, hosted secret brokering, billing, organization policy
  rollout, remote execution fleets, and production deployment control rooms are
  separate proprietary product work.

### Node Target MVP

The first target should be a local or self-hosted Node server generator. It
should load handlers through the public `autoctx/agent-runtime` surface already
used by `.autoctx/agents`, expose a small manifest/invoke HTTP shape, and wire
runtime-session recording through the same umbrella-owned event contracts used
by local execution until those contracts are extracted into `@autocontext/core`.
It may generate a minimal server entrypoint and package manifest, but should not
invent a second handler API or bypass the runtime workspace/session contracts.

The MVP is approved only as a packaging/control-plane layer around existing
contracts:

- discover handlers from `.autoctx/agents` using the public loader;
- bind request ids, payload, explicit env, runtime, workspace, commands, and
  tools through the existing invocation context;
- persist local sessions with the current runtime-session store contract;
- keep `ts/src/agent-runtime/index.ts`, runtime-session storage/notification
  contracts, and TypeScript handler-loading support umbrella-owned until the
  core package boundary grows matching exports and dependencies;
- treat shell command grants as host-created capabilities, not app-provided
  ambient authority;
- keep deployment, service hosting, process supervision, and remote secret
  management out of scope.

### Cloudflare Target Spike

The Cloudflare target should stay a spike until the Node target proves the
handler/server boundary. The spike can explore Workers for request routing and
Durable Objects for session/event persistence, but it should report contract
gaps before adding a production build path.

The spike should answer:

- how a Worker bundle loads or embeds agent handlers without depending on
  Node-only dynamic import behavior;
- how Durable Objects map onto runtime-session ids, append/replay semantics, and
  child-session links;
- how tool grants are represented when local process execution and filesystem
  access are unavailable;
- which runtime workspace adapters are valid in an edge environment;
- which pieces must remain target adapters in the control-plane package instead
  of leaking into core.

### Risks

- Bundling: TypeScript handler loading, ESM/CJS interop, native dependencies,
  optional provider SDKs, source maps, and dynamic imports can diverge between
  Node and edge runtimes.
- Environment variables: build targets must preserve explicit env loading and
  redaction semantics. They must not capture the full host environment or bake
  secrets into generated artifacts.
- Session persistence: Node can start with local SQLite/file stores, while
  Cloudflare needs a Durable Object or other edge-native event store. Replay
  semantics must stay compatible before sessions move between targets.
- Sandbox providers: local shell grants, filesystem adapters, and subprocess
  runtimes do not automatically exist in Workers. Target adapters must degrade
  explicitly or require remote tools rather than silently broadening authority.
- Product boundary: hosted scheduling, policy rollout, tenant isolation,
  observability cockpit features, and managed deployment are not open-source
  build-target responsibilities.

## Path Map

This map is the starting point for implementation. It should be updated if code
review discovers a boundary mistake.

### Python Core Candidates

- `autocontext/src/autocontext/agents/`
- `autocontext/src/autocontext/analytics/`
- `autocontext/src/autocontext/agentos/`
- `autocontext/src/autocontext/blobstore/`
- `autocontext/src/autocontext/config/`
- `autocontext/src/autocontext/evaluation/`
- `autocontext/src/autocontext/evidence/`
- `autocontext/src/autocontext/execution/`
- `autocontext/src/autocontext/harness/`
- `autocontext/src/autocontext/investigation/`
- `autocontext/src/autocontext/loop/`
- `autocontext/src/autocontext/notifications/`
- `autocontext/src/autocontext/prompts/`
- `autocontext/src/autocontext/providers/`
- `autocontext/src/autocontext/runtimes/`
- `autocontext/src/autocontext/scenarios/`
- `autocontext/src/autocontext/security/`
- `autocontext/src/autocontext/session/`
- `autocontext/src/autocontext/simulation/`
- `autocontext/src/autocontext/storage/`
- `autocontext/src/autocontext/util/`

`autocontext/src/autocontext/runtimes/workspace_env.py` is the Python
counterpart to the TypeScript runtime workspace contract. It defines the
Apache-core `RuntimeWorkspaceEnv` protocol for shell execution, file
reads/writes, stat/listing, virtual cwd resolution, scoped child environments,
and cleanup, with local filesystem and in-memory adapters. The contract is
runtime isolation plumbing for sessions and sandbox-backed execution; it is not
a deployment provider integration or a top-level product noun.

### Python Control-Plane Candidates

- `autocontext/src/autocontext/server/`
- `autocontext/src/autocontext/mcp/`
- `autocontext/src/autocontext/monitor/`
- `autocontext/src/autocontext/notebook/`
- `autocontext/src/autocontext/openclaw/`
- `autocontext/src/autocontext/sharing/`
- `autocontext/src/autocontext/research/`
- `autocontext/src/autocontext/training/`
- control-plane portions of `autocontext/src/autocontext/production_traces/`
- control-plane portions of `autocontext/src/autocontext/knowledge/`
- likely `autocontext/src/autocontext/consultation/`

### TypeScript Core Candidates

- `ts/src/agents/`
- `ts/src/analytics/`
- `ts/src/agentos/`
- `ts/src/blobstore/`
- `ts/src/config/`
- `ts/src/execution/`
- `ts/src/investigation/`
- `ts/src/judge/`
- `ts/src/loop/`
- `ts/src/prompts/`
- `ts/src/providers/`
- `ts/src/runtimes/`
- `ts/src/scenarios/`
- `ts/src/session/`
- `ts/src/simulation/`
- `ts/src/storage/`
- `ts/src/types/`
- open/shared pieces of `ts/src/traces/` and `ts/src/production-traces/`

`ts/src/runtimes/workspace-env.ts` is the first explicit runtime carve-out in
the TypeScript core artifact: it is a pure workspace/session environment
contract plus local/in-memory adapters and scoped command grants. Provider
wrappers such as Claude CLI, Codex CLI, Pi, and direct API runtimes remain
outside the core package boundary unless they are split into pure contracts and
provider-specific implementations.

Runtime workspace adapters in both languages use virtual absolute paths. A
relative path resolves against the environment `cwd`; an absolute path resolves
inside the adapter's virtual root. Local adapters map that virtual root onto a
caller-owned host directory and must never allow `..` traversal to escape it.
Scoped environments share the same backing workspace while narrowing cwd and
adding or overriding command grants for one operation branch. `cleanup()` marks
owned in-memory workspaces closed and is a no-op for caller-owned local
workspaces.

TypeScript and Python scoped command grants are host-created capability handles.
Grant env values stay in trusted host code and are never rendered into prompt
text. Local grant wrappers do not inherit the host environment by default;
callers must opt in with an explicit `inheritEnv`/`inherit_env` allowlist.
Runtime-session recording translates grant lifecycle notifications into
structured `SHELL_COMMAND` events with grant name, phase, args summary, exit
code, and redaction metadata; stdout, stderr, args, and error previews are
truncated and redacted against the exact env supplied to the grant before they
enter the log. Tool grant events use the same `TOOL_CALL` payload vocabulary
where a runtime surface emits tool lifecycle notifications.
`createLocalRuntimeCommandGrant()` and `create_local_runtime_command_grant()`
run the allowed executable directly with shell execution disabled, so wrapper
invocations do not depend on shell history or shell interpolation.
Prompt-scoped grants are not inherited by later prompts or child tasks; child
tasks receive grants only when the caller passes grants to
`runChildTask()`/`run_child_task()` or when an already-granted workspace contains
grants whose policy allows child-task inheritance.

### TypeScript Control-Plane Candidates

- `ts/src/control-plane/`
- `ts/src/server/`
- `ts/src/mcp/`
- `ts/src/mission/`
- `ts/src/tui/`
- `ts/src/training/`
- `ts/src/research/`
- control-plane portions of `ts/src/production-traces/`
- control-plane portions of `ts/src/knowledge/`

## Mixed Domains

The detailed planning map for knowledge and trace ownership lives in
[`knowledge-production-trace-boundary-map.md`](./knowledge-production-trace-boundary-map.md).

### Knowledge

Do not move `knowledge` as one unit.

Python core-leaning files:

- `coherence.py`
- `compaction.py`
- `dead_end_manager.py`
- `evidence_freshness.py`
- `fresh_start.py`
- `harness_quality.py`
- `hint_volume.py`
- `lessons.py`
- `mutation_log.py`
- `normalized_metrics.py`
- `progress.py`
- `protocol.py`
- `rapid_gate.py`
- `report.py`
- `stagnation.py`
- `trajectory.py`
- `tuning.py`
- `weakness.py`

Python control-leaning files:

- `export.py`
- `package.py`
- `search.py`
- `solver.py`
- `research_hub.py`

TypeScript core-leaning files:

- `artifact-store.ts`
- `dead-end.ts`
- `playbook.ts`
- `session-report.ts`
- `trajectory.ts`
- minimal runtime persistence helpers needed by loop/execution

TypeScript control-leaning files:

- `package.ts`
- package/export workflow helpers
- `solver.ts`
- `solve-*` workflows
- skill/package workflows intended for operator-facing export/import flows

### Production Traces

Keep open where possible:

- public schemas and contracts
- taxonomy and validation contracts
- SDK surfaces intended for ecosystem use

Move to the control plane:

- ingestion workflows
- retention workflows
- dataset/build/promotion pipelines
- operator registry and emit management surfaces

## Sequencing

1. PR0: land this guardrail document and topology map.
2. PR1: introduce package skeletons without moving source-of-truth behavior.
3. Create compatibility facades in domain batches, not one-symbol PRs unless a
   contract drift needs isolated review.
4. Begin real TypeScript and Python core extraction with exact file/package
   build scopes.
5. Move obvious control-plane directories.
6. Split `knowledge` deliberately.
7. Split production trace contracts/SDK from management workflows.
8. Rewire umbrella packages and CLI ownership.
9. Remove or reword any user-facing dual-license migration language before
   publishing the package split.
10. Revisit Pi dependency ownership after the TypeScript split stabilizes.

## Review Checks

- Core package builds must not compile or ship control-plane-only code.
- Core packages must not depend on control-plane artifacts or umbrella
  compatibility packages.
- Control-plane package builds may depend on core, but core must not depend on
  control-plane artifacts.
- Control-plane package facades must update the boundary manifest when they add
  source imports or TypeScript build includes.
- Broad package globs should be treated suspiciously during the split; prefer
  exact includes until ownership is settled.
- Any PR that changes existing protocol or payload semantics should say so
  explicitly instead of presenting itself as facade-only work.
- Public docs should not advertise a dual-license migration for the existing
  repo. They should describe Apache package boundaries and any future
  proprietary work as separate-repo work.
