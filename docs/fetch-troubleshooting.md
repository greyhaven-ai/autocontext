# Fetch Adapter Troubleshooting

Use this guide when a generic Fetch/ESM host wires a generated AutoContext
entrypoint but manifest, invocation, store, or runtime-factory behavior is not
what the host expected. The fix is usually to pass the explicit host capability
through unchanged, then run the conformance case that covers it.

## Runtime Factory Selection Fails

Symptom: `runtimeFactoryName` is set, but invocation fails before the agent
prompt runs.

Check that the handler receives all three named-factory capabilities together:

- `runtimeFactoryName`
- `runtimeFactoryPlan`
- `runtimeFactoryModuleMap`

`runtimeFactoryName` is only a selector. The plan names the allowed factories,
and the module map resolves the selected factory from a static bundler-visible
map. Do not resolve named factories from ambient state or request-time discovery.

## Direct Runtime Capability Wins Unexpectedly

Symptom: a bundled named factory exists, but prompts use a different runtime.

The precedence is intentional:

1. direct `runtime`
2. direct `runtimeFactory`
3. selected `runtimeFactoryName`

Remove the direct `runtime` or `runtimeFactory` capability if the host wants the
named bundled factory to run. Keep this order in wrappers too; it is covered by
`createAgentAppFetchInvocationConformanceCases`.

## Wrapper Drops Host Stores

Symptom: an agent reads or writes a workspace file during one request, but host
store telemetry shows no calls, or session events do not persist.

Forward the exact supplied stores:

- `workspaceStore` for virtual workspace files and directories;
- `sessionEventStore` for runtime-session event append/replay.

Do not replace them with hidden request-local stores in wrapper code. Run
`createAgentAppFetchWorkspaceStoreConformanceCases`,
`createAgentAppFetchSessionEventStoreConformanceCases`, and
`createAgentAppFetchInvocationConformanceCases` against the wrapper. The
invocation suite includes sentinel store checks that fail when a wrapper drops
`workspaceStore`.

## Manifest Or Schema Drift

Symptom: a host accepts a generated package, but its routes or accepted
capability keys differ from the Fetch adapter contract.

Validate the manifest JSON with `agentAppFetchHostCapabilityManifestSchema` and
compare accepted capabilities against the generated artifact. The manifest should
advertise `GET /manifest`, `GET /agents`, and `POST /agents/:agent/invoke`, plus
canonical accepted capability keys including `runtimeFactoryPlan` and
`runtimeFactoryModuleMap`.

If validation fails, regenerate the manifest from the same catalog plan that
produced the entrypoint. Do not hand-edit generated manifest files.

## Named Factory Loads Too Early

Symptom: factory module load counters change during handler creation or manifest
requests.

Named factories should load lazily on first runtime prompt/revise use, not while
creating the handler and not during `GET /manifest` or `GET /agents`. Keep the
runtime factory module map static, but call the selected factory only through the
lazy runtime wrapper. The invocation conformance suite checks this behavior.

## Provider Assumptions Leak Into Generic Fetch

Symptom: a generated Fetch package needs a platform binding, deployment file, or
ambient module lookup before it can answer generic `Request` objects.

Move that code into host-owned adapter code. The generated Fetch entrypoint
should stay provider-neutral: static catalogs and module maps in, explicit host
capabilities in, `Request` to `Response` out. Storage adapters, routing policy,
secrets, and deployment descriptors belong outside the generated generic Fetch
artifact.

## Quick Checks

- Read [`fetch-api-reference.md`](fetch-api-reference.md) for accepted host
  capability keys and route shapes.
- Validate generated manifests with `agentAppFetchHostCapabilityManifestSchema`.
- Run [`fetch-conformance.md`](fetch-conformance.md) suites before exposing a
  wrapper.
- Use `ts/examples/fetch-conformance-host-wrapper.ts` as the smallest executable
  conformance wiring example.
- If runtime factory behavior is confusing, test direct `runtime`, direct
  `runtimeFactory`, then `runtimeFactoryName` separately.
