# Generic Edge Runtime Compatibility Spike

AC-763 records the portability questions for generated agent apps after the
Node target MVP in AC-762. The goal is to identify generic contracts that make
agent apps portable across constrained Fetch/ESM runtimes. It is not approval to
ship a provider-specific target in the OSS repo.

Cloudflare Workers/Durable Objects may be reference environments, alongside
Deno Deploy, Vercel Edge, and other Fetch-compatible runtimes, but provider
names are used only to expose portability constraints. Provider deployment
manifests, hosted tenant routing, managed secrets, fleet scheduling, billing,
and hosted observability remain outside this Apache-2.0 repository.

Boundary reference: [Agent App Build Targets](./core-control-package-split.md#agent-app-build-targets).

## Result

The AC-762 manifest/invoke wire shape can be reused in an edge runtime if the
Node-specific server shell is replaced by a standards-based Fetch adapter and
handler discovery is moved to a build-time manifest or explicit module map.
The current Node target should remain the only emitted build target until those
generic adapter seams are proven.

The first OSS adapter seam is the TypeScript control-plane Fetch helper at
`autoctx/control-plane/agent-app-fetch`. It handles `Request` -> `Response` for
the existing `GET /manifest` / `GET /agents` manifest aliases and
`POST /agents/:agent/invoke` shape using a static handler catalog or module map. It also includes a build-time catalog
planner that turns explicit `.autoctx/agents` entries into bundler-visible
module maps, so generated bundles do not need runtime filesystem discovery.
The same subpath now exposes a generated Fetch entrypoint template, a host
capability manifest, and provider-neutral workspace-store/runtime-session
event-store contracts for explicit host-created capabilities. These helpers are
not deployment targets and do not add provider-specific build output.

Recommended follow-up after the generic adapter seams:

1. Add durable workspace/session storage implementations only in host-owned or
   separately approved provider packages that depend on the generic contracts.
2. Keep provider-specific deployment templates and hosted orchestration in a
   separate product/repository unless they are deliberately opened later.

## Reusable Invocation Shape

The Node agent app currently exposes:

- `GET /manifest` or `GET /agents`
- `POST /agents/<agent>/invoke`

The invoke body is a JSON object with optional `id` and optional `payload`; the
success envelope is:

```json
{
  "ok": true,
  "agent": "support",
  "id": "ticket-123",
  "result": {}
}
```

That shape is transport-neutral. A Fetch adapter can preserve it by parsing the
URL path and JSON body from `Request`, then returning a JSON `Response`. The
handler API does not need to change if the adapter can still supply the same
AutoContext invocation context: `id`, `payload`, explicit `env`, workspace,
runtime, event store/sink, command grants, and tool grants.

## Node Assumptions That Do Not Hold At The Edge

| Area                | AC-762 Node target assumption                                                                     | Edge compatibility concern                                                                                                |
| ------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| HTTP                | Uses `node:http` `IncomingMessage`/`ServerResponse` and a long-lived `Server`.                    | Edge runtimes expose `fetch(request, env, ctx)` or equivalent `Request`/`Response` APIs.                                  |
| Handler discovery   | Reads `.autoctx/agents` with `node:fs` at runtime.                                                | Edge bundles usually cannot scan a deployment filesystem; discovery needs a manifest produced at build time.              |
| Handler loading     | Dynamically imports file paths and bare modules from local disk.                                  | Edge bundlers require static or bundler-known dynamic imports; native Node resolution is unavailable.                     |
| Workspace           | Defaults to `createLocalWorkspaceEnv()`, which can read/write files and run local command grants. | Edge workspaces must be in-memory, remote object-backed, or explicitly unavailable; shell execution must not be emulated. |
| Session persistence | Can lazy-import the local SQLite runtime-session store when `AUTOCTX_SESSION_DB` is set.          | Edge persistence needs an append/replay event-log adapter backed by runtime storage, not native SQLite.                   |
| Environment         | Reads selected process env values and optional `.env` files from the source project root.         | Edge env must be explicit bindings/config passed by the host; the adapter must not capture deployment-wide ambient env.   |
| Runtime factory     | Loads `AUTOCTX_RUNTIME_MODULE` as URL, file path, or bare package from `node_modules`.            | Runtime factories must be bundled or passed as explicit host-created capabilities; provider SDKs must be edge-compatible. |
| Dependencies        | The generated package can install `autoctx` and Node dependencies.                                | Edge bundles must avoid native addons, Node-only modules, and optional dependencies that bundlers cannot tree-shake.      |
| Lifecycle           | A Node process owns server startup/shutdown and resource cleanup.                                 | Edge invocations may be short-lived; cleanup must be scoped to requests or runtime-provided wait hooks.                   |

## Minimum Generic Edge Adapter Contract

A generic edge adapter should stay in the TypeScript control-plane boundary until
its reusable contracts are extracted into `@autocontext/core`.

### Fetch Transport

The adapter should accept a standards-based request and context:

```ts
type EdgeAgentAppFetch = (
  request: Request,
  context: EdgeAgentAppContext,
) => Promise<Response> | Response;
```

The transport layer owns:

- route matching for `GET /manifest`, `GET /agents`, and
  `POST /agents/:agent/invoke`;
- JSON parsing with an explicit body-size limit;
- JSON response serialization and stable error envelopes;
- request cancellation through `AbortSignal` where the runtime supplies one;
- no dependency on `node:http`, `Buffer`, process globals, or local ports.

### Handler Catalog And Loader

Edge runtimes should not discover handlers from the filesystem at request time.
The build step should provide a catalog similar to:

```ts
interface EdgeAgentCatalogEntry {
  name: string;
  relativePath: string;
  extension: string;
  triggers?: Record<string, unknown>;
  load(): Promise<AutoctxLoadedAgent>;
}
```

The catalog can be generated from `.autoctx/agents` by the control-plane build
workflow, but the runtime adapter receives a static list or module map. The
provider-neutral `planAgentAppFetchCatalog()` and
`createAgentAppFetchCatalogFromModuleMap()` helpers keep source-project
scanning out of the edge request path and let bundlers see which handler modules
must be included. Runtime factory bundling follows the same explicit pattern:
`planAgentAppFetchRuntimeFactories()` accepts build-discovered
`.autoctx/runtimes` entries, and `createAgentAppFetchRuntimeFactoryFromModuleMap()`
resolves a named factory from a static module map without ambient module lookup.
`renderAgentAppFetchEntrypointTemplate()` can then emit a generic ESM entrypoint
with static handler/runtime module maps, an embedded host capability manifest,
and a `createAgentAppFetchEntrypoint()` factory that accepts explicit host
capabilities. `renderAgentAppFetchHostCapabilityManifest()` can also emit that
manifest as standalone JSON for external/provider hosts, while
`agentAppFetchHostCapabilityManifestSchema` and
`renderAgentAppFetchHostCapabilityManifestSchema()` expose the matching
provider-neutral validation schema. See
[`generated-fetch-packaging.md`](generated-fetch-packaging.md) for a generic
Fetch/ESM packaging walkthrough. Provider wrappers remain external to that
generated source.

### Explicit Environment And Runtime Capabilities

The adapter should receive env as a plain object from trusted host code. It must
not read `process.env`, parse `.env` files, or bake secrets into generated
artifacts. Runtime factories should be host-created capabilities, not ambient
module lookups, unless a bundler-safe module map is supplied explicitly. When a
generated entrypoint includes a runtime factory plan, the host can either pass a
`runtimeFactory` capability directly or pass `runtimeFactoryName` to select one
of the statically bundled factories.

The Fetch host capability manifest is machine-readable and lists the supported
routes (`GET /manifest`, `GET /agents`, and `POST /agents/:agent/invoke`), the
accepted host capability keys (`env`, `runtime`, `runtimeFactory`,
`runtimeFactoryName`, `workspace`, `workspaceStore`, `commands`, `tools`,
`eventStore`, `sessionEventStore`, `eventSink`, and `maxBodyBytes`) plus
unsupported defaults: runtime filesystem discovery, ambient environment capture,
local shell execution, provider deployment config, and hosted orchestration.
Provider hosts can use the manifest plus its JSON Schema to validate their
wrapper wiring without adding provider code to the generic adapter.

### Workspace And Grants

The existing `RuntimeWorkspaceEnv`, `RuntimeCommandGrant`, and
`RuntimeToolGrant` concepts can carry over, but edge adapters need stricter
capability choices:

- in-memory workspace for pure handlers;
- remote/object-backed workspace for explicit persisted artifacts;
- no-op or denied filesystem methods when persistence was not granted;
- no local shell command grants;
- remote tool grants only when the host explicitly supplies them;
- grant lifecycle events should keep the existing redaction and truncation
  semantics.

The adapter must fail closed or return an explicit unsupported-capability error
when a handler asks for local filesystem or shell authority that does not exist.

### Fetch Workspace Store

The TypeScript Fetch adapter exposes a provider-neutral
`AgentAppFetchWorkspaceStore` contract and an in-memory reference
implementation. `createAgentAppFetchWorkspaceEnv()` adapts that store to the
existing `RuntimeWorkspaceEnv` surface, preserving virtual absolute paths,
file/directory collision semantics, byte cloning on read/write, lexicographic
listings, recursive `mkdir`/`rm`, and fail-closed shell execution.

Fetch hosts can pass `workspaceStore` to `createAgentAppFetchHandler()` when
handler artifacts should survive beyond one request. Without an explicit store,
the adapter keeps the existing request-local in-memory workspace behavior for
pure handlers. Host-created stores must provide read-your-writes behavior after
a write resolves and should serialize writes that target the same virtual path
or document stronger host-specific atomicity. Remote object stores, key/value
stores, and other durable backends are implementation choices outside this
provider-neutral package.

### Runtime-Session Event Log

The TypeScript Fetch adapter exposes a provider-neutral
`AgentAppFetchSessionEventStore` contract for runtime-backed generated agent
apps. The contract accepts append batches keyed by runtime-session id and replays
a JSON snapshot with session metadata plus a per-session ordered timeline. The
in-memory reference implementation is useful for tests and pure handlers; host
code owns any durable storage binding.

The generic contract requires:

- append batches by runtime-session id;
- read session metadata and timeline by id;
- optional list/query methods where the runtime supports them;
- deterministic child-session linkage fields;
- idempotent writes by `eventId`;
- replay ordered by per-session `sequence`;
- read-your-writes behavior after an append resolves.

Concurrency remains a storage-adapter responsibility. Generic Fetch hosts should
prefer one writer per runtime-session id or a storage primitive that serializes
append batches. If an implementation accepts concurrent appends, it must either
preserve supplied sequence numbers without collision or deterministically assign
the next open per-session sequence while keeping `eventId` idempotency.

Durable Objects, Deno KV, Vercel KV, S3/R2-like object stores, and hosted event
logs are possible storage implementations, but OSS defines only the generic
adapter contract plus an in-memory reference. Provider bindings and deployment
manifests remain outside this OSS adapter.

### Fetch Store Conformance

The `autoctx/control-plane/agent-app-fetch` subpath also exports
framework-agnostic conformance helpers for host-owned workspace/session store
adapters. `createAgentAppFetchWorkspaceStoreConformanceCases()` and
`createAgentAppFetchSessionEventStoreConformanceCases()` return named async
cases that Vitest, Jest, or another runner can execute. Each case calls
`createStore` for a fresh isolated store; the workspace root-removal case is
destructive for that store instance.

The suite covers read-your-writes behavior, byte cloning, deterministic
workspace listings, recursive root removal, fail-closed shell execution,
session append idempotency by `eventId`, replay ordering by per-session
`sequence`, metadata/payload cloning, and child-session linkage visibility.

### Fetch Invocation Conformance

`createAgentAppFetchInvocationConformanceCases()` returns named async cases for
host-owned Fetch wrappers that accept the generic handler options shape. The
suite validates `GET /manifest`, `GET /agents`, `POST /agents/:agent/invoke`,
stable success/error envelopes, missing-agent behavior, invalid JSON, body-size
limits, no handler loading during manifest reads, and explicit env/workspace /
runtime capability wiring. The helper is runner-agnostic and does not add any
provider deployment or storage binding.

## Reference Runtime Findings

| Runtime family                       | Useful reference                                                   | Main constraints                                                                                                              |
| ------------------------------------ | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Cloudflare Workers + Durable Objects | Fetch routing and per-object session/event coordination.           | No Node server, no local filesystem, bundler restrictions, Durable Object namespace provisioning is provider deployment code. |
| Deno Deploy                          | Standards-first Fetch/ESM runtime.                                 | Different module resolution, explicit permissions model, no Node native addons.                                               |
| Vercel Edge                          | Fetch-like function surface integrated with a deployment platform. | Limited Node API support, platform-specific env and storage adapters.                                                         |
| Generic Fetch/ESM                    | Lowest common denominator for an OSS adapter contract.             | Requires static/bundler-known handler modules and explicit capabilities.                                                      |

Provider-specific details should feed constraints back into the generic adapter;
they should not create provider-specific OSS deployment workflows by default.

## Follow-Up Split

### OSS Core Contract Changes Only If Needed

- Extract public handler/catalog types when `autoctx/agent-runtime` moves into
  the planned `@autocontext/core` surface.
- Keep runtime-session event contracts provider-neutral and storage-agnostic.
- Add capability vocabulary only when multiple runtimes need the same concept.

### TypeScript Control-Plane / OSS Adapter Work

- Maintain the generic Fetch request adapter that reuses the Node
  manifest/invoke envelope without advertising a provider deployment target.
- Maintain the build-time handler manifest/module-map planner and generic
  generated Fetch entrypoint template.
- Maintain the provider-neutral workspace-store contract for explicit
  host-supplied Fetch artifact capabilities.
- Maintain the provider-neutral session event-store contract for explicit
  host-supplied Fetch runtime capabilities.
- Add tests proving pure local handlers can run through generated catalogs with
  in-memory workspace/env and without Node-only server globals.
- Document unsupported shell/filesystem capability behavior.

### Provider-Specific Or Proprietary Work

- Cloudflare `wrangler` config, Durable Object bindings, namespace provisioning,
  queues, alarms, and deployment scripts.
- Vercel/Deno deployment manifests and platform-specific secret wiring.
- Hosted tenant scheduling, hosted warm pools, provider image/cache economics,
  billing, organization policy rollout, hosted observability cockpit, or remote
  secret brokering.

## Decision

Do not add `autoctx agent build --target cloudflare` or any provider-specific
edge target from this spike. The OSS implementation surface is a generic Fetch
adapter and static handler catalog in the TypeScript control-plane boundary.
Provider-specific deployment can depend on that adapter from outside the OSS
repo or from a future explicitly approved open provider package.
