# Fetch Conformance

AutoContext's generic Fetch adapter exposes conformance helpers so a host can
prove its wrapper and storage adapters preserve the OSS contract before exposing
a generated `fetch` handler. See the
[`Fetch adapter API reference`](fetch-api-reference.md) for the exported helper
surface. The helpers are framework-agnostic: each case is an async function with
a stable name, and each one-shot runner executes the same cases without assuming
a test framework. See `ts/examples/fetch-conformance-host-wrapper.ts` for a
minimal executable host-wrapper example and
[`fetch-troubleshooting.md`](fetch-troubleshooting.md) for common host wiring
failures.

## Runner-Agnostic Usage

Use the case factories when you want your test runner to report each case
individually:

```ts
import {
  createAgentAppFetchInvocationConformanceCases,
  createAgentAppFetchSessionEventStoreConformanceCases,
  createAgentAppFetchWorkspaceStoreConformanceCases,
} from "autoctx/control-plane/agent-app-fetch";
import { describe, it } from "vitest";

describe("host Fetch workspace store", () => {
  for (const testCase of createAgentAppFetchWorkspaceStoreConformanceCases({
    createStore: createHostWorkspaceStore,
  })) {
    it(testCase.name, testCase.run);
  }
});

describe("host Fetch session event store", () => {
  for (const testCase of createAgentAppFetchSessionEventStoreConformanceCases({
    createStore: createHostSessionEventStore,
  })) {
    it(testCase.name, testCase.run);
  }
});

describe("host Fetch invocation wrapper", () => {
  for (const testCase of createAgentAppFetchInvocationConformanceCases({
    createHandler: createHostFetchHandler,
  })) {
    it(testCase.name, testCase.run);
  }
});
```

Use one-shot runners when your harness has a single async setup step:

```ts
import {
  runAgentAppFetchInvocationConformance,
  runAgentAppFetchSessionEventStoreConformance,
  runAgentAppFetchWorkspaceStoreConformance,
} from "autoctx/control-plane/agent-app-fetch";

await runAgentAppFetchWorkspaceStoreConformance({
  createStore: createHostWorkspaceStore,
});
await runAgentAppFetchSessionEventStoreConformance({
  createStore: createHostSessionEventStore,
});
await runAgentAppFetchInvocationConformance({
  createHandler: createHostFetchHandler,
});
```

## Workspace Store Conformance

`createAgentAppFetchWorkspaceStoreConformanceCases()` and
`runAgentAppFetchWorkspaceStoreConformance()` validate a host-owned
`AgentAppFetchWorkspaceStore` implementation. Each case calls `createStore()`
for a fresh isolated instance. The root-removal case recursively removes `/`, so
that store instance must not be shared with other tests.

The workspace cases verify:

- read-your-writes behavior after each write resolves;
- byte cloning on write and read boundaries;
- lexicographic directory listings;
- recursive root removal that preserves `/` while clearing entries;
- fail-closed shell execution through `createAgentAppFetchWorkspaceEnv()`.

## Session Event-Store Conformance

`createAgentAppFetchSessionEventStoreConformanceCases()` and
`runAgentAppFetchSessionEventStoreConformance()` validate a host-owned
`AgentAppFetchSessionEventStore` implementation. The store should close any
request-scoped resources through its optional `close()` method when a case
finishes.

The session event-store cases verify:

- append idempotency by `eventId`;
- replay ordering by per-session `sequence`;
- cloning of session metadata and event payloads at append and load boundaries;
- preservation of parent and child runtime-session links.

## Invocation Conformance

`createAgentAppFetchInvocationConformanceCases()` and
`runAgentAppFetchInvocationConformance()` validate a host-owned Fetch wrapper
that accepts the generic `AgentAppFetchHandlerOptions` shape. The wrapper may
call `createAgentAppFetchHandler()` internally or adapt the options to an
equivalent `Request` to `Response` handler.

The invocation cases verify:

- `GET /manifest` and `GET /agents` return the static catalog without loading
  handler modules;
- `POST /agents/:agent/invoke` preserves the success envelope and request id;
- explicit `env`, `workspaceStore`, `runtime`, `runtimeFactory`, and
  `runtimeFactoryName` capabilities are wired into the agent context;
- `runtime` takes precedence over `runtimeFactory`;
- `runtimeFactory` takes precedence over `runtimeFactoryName`;
- named runtime factories load lazily from the explicit static module map;
- missing agents, invalid JSON, and over-limit bodies return stable error
  envelopes;
- the supplied workspace store is used rather than a hidden request-local store.

## Host Guarantees

A conforming host should provide only explicit capabilities to the generated
entrypoint or wrapper:

- create fresh stores for conformance cases and isolate destructive cases;
- pass plain `env` data from trusted host code instead of reading ambient
  deployment state inside the generated handler;
- pass `runtime`, `runtimeFactory`, or `runtimeFactoryName` as host-created
  capabilities, preserving that precedence order;
- when a wrapper resolves `runtimeFactoryName` itself, pair it with
  `runtimeFactoryPlan` and `runtimeFactoryModuleMap` so named factories resolve
  from an explicit static module map; generated entrypoints embed that map;
- pass `workspaceStore` and `sessionEventStore` when persistence is required;
- keep handler and runtime catalogs static and bundler-visible;
- keep command and tool grants absent unless the host deliberately supplies safe
  grants.

## Failure Modes

Common conformance failures usually indicate a contract mismatch. For a shorter
symptom-to-fix checklist, see
[`fetch-troubleshooting.md`](fetch-troubleshooting.md).

| Failure                                            | Likely cause                                            | Expected fix                                                        |
| -------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------- |
| Mutating a written byte array changes later reads. | Store retained caller-owned buffers.                    | Clone bytes on write and read.                                      |
| Directory listing order changes between runs.      | Backend iteration order leaked into the contract.       | Sort path names lexicographically before returning them.            |
| Removing `/` deletes the root entry itself.        | Recursive cleanup treated root like a normal directory. | Preserve `/` and remove only its children.                          |
| Manifest requests load agent modules.              | Wrapper discovers or imports handlers at request time.  | Use a static catalog or module map supplied by the build step.      |
| Invocation ignores sentinel workspace calls.       | Wrapper replaced the supplied `workspaceStore`.         | Thread host-created stores through to the agent context.            |
| Session replay duplicates events.                  | Store appends without `eventId` idempotency.            | Treat duplicate event ids as already persisted.                     |
| Runtime prompts fail in invocation cases.          | Wrapper did not pass the explicit runtime capability.   | Forward the provided runtime or selected runtime factory.           |
| Named factory loads before invocation.             | Wrapper eagerly called the factory module map.          | Wrap factories with lazy runtime creation.                          |
| Named factory overrides direct capabilities.       | Wrapper ignored runtime precedence.                     | Prefer `runtime`, then `runtimeFactory`, then `runtimeFactoryName`. |

These helpers intentionally stop at the generic Fetch/ESM seam. Durable storage
adapters, deployment descriptors, scheduling policy, secrets handling, and
commercial orchestration stay outside this OSS conformance package.
