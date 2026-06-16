# Fetch Adapter API Reference

AutoContext's public Fetch/ESM adapter lives at
`autoctx/control-plane/agent-app-fetch`. It exposes the same agent-app manifest
and invocation wire shape as the TypeScript control-plane Node target while
remaining a generic `Request` to `Response` seam. Host code owns runtime,
workspace, storage, command, tool, and environment capabilities and passes them
explicitly.

## Import Path

```ts
import {
  createAgentAppFetchHandler,
  planAgentAppFetchCatalog,
  renderAgentAppFetchEntrypointTemplate,
} from "autoctx/control-plane/agent-app-fetch";
```

The subpath is the supported package boundary for Fetch helpers. Importing from
source files is not required for generated handlers, conformance cases, or host
capability manifests.

## Handler Surface

Use `createAgentAppFetchHandler(options)` when host code already has a static
catalog and wants a `Request` to `Response` handler. The lower-level
`handleAgentAppFetchRequest()` is exported for wrappers that need to prepare the
resolved environment/workspace before dispatching a single request.

Core handler types and helpers:

| API                            | Purpose                                                                       |
| ------------------------------ | ----------------------------------------------------------------------------- |
| `AgentAppFetchHandlerOptions`  | Options accepted by the handler and wrapper conformance helpers.              |
| `AgentAppFetchCatalogEntry`    | Static or lazily loaded agent handler catalog entry.                          |
| `createStaticAgentAppCatalog`  | Clones already-loaded handler entries into a Fetch catalog.                   |
| `createAgentAppFetchHandler`   | Builds the generic Fetch handler from a catalog plus explicit capabilities.   |
| `handleAgentAppFetchRequest`   | Dispatches one request when a wrapper has already normalized options.         |
| `AgentAppFetchManifest`        | Manifest envelope returned by `GET /manifest` and `GET /agents`.              |
| `AgentAppFetchSuccessEnvelope` | Successful invocation envelope returned by `POST /agents/:agent/invoke`.      |
| `AgentAppFetchErrorEnvelope`   | Stable error envelope for missing agents, invalid bodies, and handler errors. |

Supported routes are:

- `GET /manifest`
- `GET /agents`
- `POST /agents/:agent/invoke`

Accepted host-created capability keys are `env`, `runtime`, `runtimeFactory`,
`runtimeFactoryName`, `runtimeFactoryPlan`, `runtimeFactoryModuleMap`,
`workspace`, `workspaceStore`, `commands`, `tools`, `eventStore`,
`sessionEventStore`, `eventSink`, and `maxBodyBytes`.

## Catalog And Entrypoint Planning

Build tooling should provide explicit catalog entries. The runtime handler does
not discover files.

| API                                       | Purpose                                                                                |
| ----------------------------------------- | -------------------------------------------------------------------------------------- |
| `planAgentAppFetchCatalog`                | Validates and normalizes explicit `.autoctx/agents` entries into a deterministic plan. |
| `createAgentAppFetchCatalogFromModuleMap` | Turns a plan plus a static module map into lazy catalog entries.                       |
| `renderAgentAppFetchModuleMapEntrypoint`  | Emits a small ESM module-map entrypoint that directly creates `fetch`.                 |
| `renderAgentAppFetchEntrypointTemplate`   | Emits the generated Fetch entrypoint template with manifest and factory exports.       |
| `AGENT_APP_FETCH_ROUTES`                  | Canonical route list used by catalog plans and manifests.                              |

`renderAgentAppFetchEntrypointTemplate()` is the preferred packaging helper for
generated Fetch bundles because it emits the catalog plan, static module map,
host capability manifest, and `createAgentAppFetchEntrypoint()` factory in one
ESM source string.

## Runtime Factory Helpers

Runtime factories let generated entrypoints bundle a static set of runtime
factory modules while preserving explicit host capability precedence.

| API                                              | Purpose                                                                                  |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `planAgentAppFetchRuntimeFactories`              | Validates and normalizes explicit `.autoctx/runtimes` entries into a deterministic plan. |
| `createAgentAppFetchRuntimeFactoryFromModuleMap` | Resolves a named runtime factory from a static module map.                               |
| `createAgentAppFetchLazyRuntime`                 | Wraps a runtime factory so the runtime is created only on first prompt/revise use.       |

Precedence is fixed: direct `runtime` wins over `runtimeFactory`, and direct
`runtimeFactory` wins over `runtimeFactoryName`. Named factories should be
selected from `runtimeFactoryPlan` plus `runtimeFactoryModuleMap`, never from an
ambient module lookup. See
`ts/examples/generated-fetch-runtime-factory-packaging.ts` for a typed generated
Fetch packaging example with bundled named runtime factories.

## Host Capability Manifest

Generated packages can emit a manifest and schema so host wrappers can validate
which capabilities the generic Fetch handler accepts.

| API                                               | Purpose                                                                       |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| `createAgentAppFetchHostCapabilityManifest`       | Builds the machine-readable manifest from a catalog plan.                     |
| `renderAgentAppFetchHostCapabilityManifest`       | Serializes the manifest as pretty JSON with a trailing newline.               |
| `agentAppFetchHostCapabilityManifestSchema`       | JSON Schema object for the manifest contract.                                 |
| `renderAgentAppFetchHostCapabilityManifestSchema` | Serializes the schema as pretty JSON with a trailing newline.                 |
| `AGENT_APP_FETCH_ACCEPTED_HOST_CAPABILITIES`      | Canonical accepted capability key list.                                       |
| `AGENT_APP_FETCH_UNSUPPORTED_DEFAULTS`            | Canonical list of defaults intentionally not provided by the generic adapter. |

The unsupported-default list documents that the generated handler does not add
runtime filesystem discovery, ambient environment capture, local shell
execution, host deployment configuration, or commercial orchestration behavior.
See [`fetch-host-capability-manifest.md`](fetch-host-capability-manifest.md) for
manifest JSON and schema validation examples.

## Workspace And Session Stores

The Fetch adapter exports provider-neutral store contracts and in-memory
references for tests, examples, and pure handlers.

| API                                            | Purpose                                                                    |
| ---------------------------------------------- | -------------------------------------------------------------------------- |
| `AgentAppFetchWorkspaceStore`                  | Virtual workspace store contract for files, directories, and metadata.     |
| `createAgentAppFetchWorkspaceEnv`              | Adapts a workspace store to the shared runtime workspace surface.          |
| `createInMemoryAgentAppFetchWorkspaceStore`    | In-memory reference workspace store.                                       |
| `createEdgeInMemoryWorkspaceEnv`               | Convenience in-memory workspace environment for Fetch-compatible runtimes. |
| `AgentAppFetchSessionEventStore`               | Runtime-session event-store contract with idempotent append and replay.    |
| `createAgentAppFetchSessionEventStoreBridge`   | Bridges a session event store into the runtime event-store adapter shape.  |
| `createInMemoryAgentAppFetchSessionEventStore` | In-memory reference session event store.                                   |

The in-memory references are useful defaults but do not imply persistence across
requests. Hosts that need persistence should pass `workspaceStore` and
`sessionEventStore` explicitly.

## Conformance Helpers

Conformance helpers are framework-agnostic. Case factories return named async
cases; one-shot runners execute the same cases without assuming a test runner.

| API                                                    | Purpose                              |
| ------------------------------------------------------ | ------------------------------------ |
| `createAgentAppFetchWorkspaceStoreConformanceCases`    | Workspace store case factory.        |
| `runAgentAppFetchWorkspaceStoreConformance`            | One-shot workspace store runner.     |
| `createAgentAppFetchSessionEventStoreConformanceCases` | Session event-store case factory.    |
| `runAgentAppFetchSessionEventStoreConformance`         | One-shot session event-store runner. |
| `createAgentAppFetchInvocationConformanceCases`        | Invocation wrapper case factory.     |
| `runAgentAppFetchInvocationConformance`                | One-shot invocation wrapper runner.  |

See [`fetch-conformance.md`](fetch-conformance.md) for case details, failure
modes, and runner examples,
[`fetch-troubleshooting.md`](fetch-troubleshooting.md) for common host wiring
failures, and `ts/examples/fetch-conformance-host-wrapper.ts` for a typed
executable wrapper example.

## Generated Entrypoint Contract

A source string emitted by `renderAgentAppFetchEntrypointTemplate()` exports:

- `agentAppFetchCatalogPlan`
- `agentAppFetchModuleMap`
- `agentAppFetchCatalog`
- `agentAppFetchRuntimeFactoryPlan` when runtime factories are planned
- `agentAppFetchRuntimeFactoryModuleMap` when runtime factories are planned
- `agentAppFetchHostCapabilityManifest`
- `createAgentAppFetchEntrypoint(hostCapabilities?)`
- `fetch`
- a default object containing `fetch`

`createAgentAppFetchEntrypoint()` forwards host-created capabilities to
`createAgentAppFetchHandler()`. When the generated source includes runtime
factory entries, it can resolve `runtimeFactoryName` through the static runtime
factory module map lazily. Direct `runtime` and `runtimeFactory` capabilities
still take precedence over named factory selection.

## Boundary Guarantees

The Fetch adapter remains a generic OSS seam:

- handler and runtime catalogs are explicit build-step inputs;
- request handling uses static catalogs/module maps and no request-time file
  discovery;
- environment data is passed through `env` rather than captured ambiently;
- runtime factories are host-created capabilities or selected from static module
  maps;
- shell execution is unavailable unless a host deliberately supplies safe
  command grants;
- host deployment descriptors, fleet policy, storage bindings, and commercial
  orchestration stay outside this package.

For packaging guidance, see
[`generated-fetch-packaging.md`](generated-fetch-packaging.md). For wrapper and
store verification, see [`fetch-conformance.md`](fetch-conformance.md). For
common host wiring failures, see
[`fetch-troubleshooting.md`](fetch-troubleshooting.md).
