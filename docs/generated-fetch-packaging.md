# Generated Fetch Packaging

This guide shows how to package an AutoContext agent app for a generic
Fetch/ESM host. The output is a portable entrypoint plus manifest artifacts; the
host still creates and passes runtime, workspace, storage, and grant
capabilities explicitly.

The same pattern is implemented as a typed repository example in
`ts/examples/generated-fetch-packaging.ts`.

## Inputs

A build step supplies explicit handler entries from `.autoctx/agents` and,
optionally, runtime factory entries from `.autoctx/runtimes`. These entries are
plain data. They are not discovered by the request handler.

```ts
import {
  planAgentAppFetchCatalog,
  planAgentAppFetchRuntimeFactories,
} from "autoctx/control-plane/agent-app-fetch";

const catalogPlan = planAgentAppFetchCatalog({
  entries: [
    {
      name: "support",
      relativePath: ".autoctx/agents/support.mjs",
      extension: ".mjs",
      triggers: { webhook: true },
    },
  ],
});

const runtimeFactoryPlan = planAgentAppFetchRuntimeFactories({
  entries: [
    {
      name: "standard",
      relativePath: ".autoctx/runtimes/standard.mjs",
      extension: ".mjs",
    },
  ],
});
```

## Generated Artifacts

Use the provider-neutral render helpers to emit the Fetch entrypoint, host
capability manifest, and manifest JSON Schema:

```ts
import {
  renderAgentAppFetchEntrypointTemplate,
  renderAgentAppFetchHostCapabilityManifest,
  renderAgentAppFetchHostCapabilityManifestSchema,
} from "autoctx/control-plane/agent-app-fetch";

const entrypointSource = renderAgentAppFetchEntrypointTemplate(catalogPlan, {
  runtimeFactoryPlan,
});
const manifestJson = renderAgentAppFetchHostCapabilityManifest(catalogPlan);
const manifestSchemaJson = renderAgentAppFetchHostCapabilityManifestSchema();
```

Typical file names are:

- `agent-app-fetch-entrypoint.mjs`
- `agent-app-fetch-host-capability-manifest.json`
- `agent-app-fetch-host-capability-manifest.schema.json`

## Host Wiring

The generated entrypoint exports `createAgentAppFetchEntrypoint()` and a default
`fetch`. Host code can call the factory with host-created capabilities:

```ts
import { createAgentAppFetchEntrypoint } from "./agent-app-fetch-entrypoint.mjs";

export default {
  fetch: createAgentAppFetchEntrypoint({
    env: { AUTOCONTEXT_MODE: "example" },
    runtimeFactoryName: "standard",
    workspaceStore,
    sessionEventStore,
  }),
};
```

Accepted host-created capabilities include `env`, `runtime`, `runtimeFactory`,
`runtimeFactoryName`, `workspace`, `workspaceStore`, `commands`, `tools`,
`eventStore`, `sessionEventStore`, `eventSink`, and `maxBodyBytes`. Direct
`runtime` and `runtimeFactory` capabilities take precedence over bundled runtime
factory selection.

## Boundaries

- No request-time filesystem discovery: handler and runtime module maps are
  static in the generated entrypoint.
- No ambient environment capture: hosts pass `env` explicitly.
- No provider deployment configuration: deployment descriptors, storage binding
  setup, routing rules, and platform policy live outside this OSS package.
- No local shell authority by default: workspace stores fail closed unless a host
  explicitly supplies safe command grants.

Use the manifest plus `agentAppFetchHostCapabilityManifestSchema` to validate
host wiring before exposing the generated `fetch` handler.
