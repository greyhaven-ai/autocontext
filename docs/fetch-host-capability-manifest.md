# Fetch Host Capability Manifest Examples

The Fetch host capability manifest is the machine-readable contract that a
generated Fetch/ESM package gives to host code. It lists the generated agents,
the supported routes, accepted host-created capability keys, required capability
keys, and defaults the generic adapter intentionally does not provide.

Use this guide with the [`Fetch adapter API reference`](fetch-api-reference.md),
the [`Generated Fetch packaging guide`](generated-fetch-packaging.md), and the
typed example in `ts/examples/fetch-host-capability-manifest.ts`.

## Generate Manifest And Schema Artifacts

A build step starts from explicit catalog entries and optional runtime factory
entries. These are plain build inputs, not request-time discovery results.

```ts
import {
  planAgentAppFetchCatalog,
  planAgentAppFetchRuntimeFactories,
  renderAgentAppFetchHostCapabilityManifest,
  renderAgentAppFetchHostCapabilityManifestSchema,
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

const manifestJson = renderAgentAppFetchHostCapabilityManifest(catalogPlan);
const schemaJson = renderAgentAppFetchHostCapabilityManifestSchema();
void runtimeFactoryPlan;
```

Typical artifact names are:

- `agent-app-fetch-host-capability-manifest.json`
- `agent-app-fetch-host-capability-manifest.schema.json`

## Example Manifest JSON

A generated manifest is stable JSON. The accepted capability list includes both
runtime factory selection keys and the plan/map keys needed when a wrapper
resolves named factories itself.

```json
{
  "target": "fetch",
  "routes": ["GET /manifest", "GET /agents", "POST /agents/:agent/invoke"],
  "agents": [
    {
      "name": "support",
      "relativePath": ".autoctx/agents/support.mjs",
      "extension": ".mjs",
      "triggers": { "webhook": true }
    }
  ],
  "acceptedHostCapabilities": [
    "env",
    "runtime",
    "runtimeFactory",
    "runtimeFactoryName",
    "runtimeFactoryPlan",
    "runtimeFactoryModuleMap",
    "workspace",
    "workspaceStore",
    "commands",
    "tools",
    "eventStore",
    "sessionEventStore",
    "eventSink",
    "maxBodyBytes"
  ],
  "requiredHostCapabilities": [],
  "unsupportedDefaults": [
    "runtime_filesystem_discovery",
    "ambient_environment_capture",
    "local_shell_execution",
    "provider_deployment_configuration",
    "hosted_orchestration"
  ]
}
```

Hosts should preserve the accepted key vocabulary: `env`, `runtime`,
`runtimeFactory`, `runtimeFactoryName`, `runtimeFactoryPlan`,
`runtimeFactoryModuleMap`, `workspace`, `workspaceStore`, `commands`, `tools`,
`eventStore`, `sessionEventStore`, `eventSink`, and `maxBodyBytes`.

## Validate Before Exposing A Handler

Use the exported `agentAppFetchHostCapabilityManifestSchema` to validate the
manifest emitted by a generated package. The schema is intentionally strict: it
rejects unknown routes, extra capability names, missing sections, and unexpected
properties.

```ts
import Ajv from "ajv";
import { agentAppFetchHostCapabilityManifestSchema } from "autoctx/control-plane/agent-app-fetch";

const validate = new Ajv({ allErrors: true, strict: true }).compile(
  agentAppFetchHostCapabilityManifestSchema,
);
const manifest = JSON.parse(manifestJson);

if (!validate(manifest)) {
  throw new Error(JSON.stringify(validate.errors, null, 2));
}
```

The repository example `ts/examples/fetch-host-capability-manifest.ts` packages
this same generation and validation flow for tests and copy-paste reference.

## Runtime Factory Host Wiring Notes

A generated entrypoint may include runtime factory modules from a static module
map. Host code can still choose the runtime explicitly:

- `runtime` takes precedence over `runtimeFactory`;
- `runtimeFactory` takes precedence over `runtimeFactoryName`;
- `runtimeFactoryName` is resolved from `runtimeFactoryPlan` plus
  `runtimeFactoryModuleMap` when the generated entrypoint or wrapper selects a
  named bundled factory.

Wrappers that validate capability names should allow all four runtime-factory
keys: `runtimeFactory`, `runtimeFactoryName`, `runtimeFactoryPlan`, and
`runtimeFactoryModuleMap`. See
`ts/examples/generated-fetch-runtime-factory-packaging.ts` for a typed example
that emits a generated entrypoint with bundled named runtime factories.

## Boundary Guarantees

These examples stay at the generic Fetch/ESM seam:

- handler and runtime factory entries are explicit build-step inputs;
- generated handlers use static module maps and no request-time file discovery;
- environment values are passed through `env` by host code;
- runtime factories are host-created capabilities or selected from static module
  maps;
- command and tool authority is absent unless host code supplies safe grants;
- deployment descriptors, storage bindings, scheduling policy, and commercial
  orchestration are outside this package.
