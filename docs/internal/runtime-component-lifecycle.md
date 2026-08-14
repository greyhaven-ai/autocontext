# Runtime Component Lifecycle

AC-959 introduces a TypeScript-first lifecycle primitive for runtime-owned,
reversible effects. It is deliberately smaller than a dependency-injection
container or hot-module-replacement system.

## Ownership and import direction

- `ts/src/runtimes/component-lifecycle.ts` owns `RuntimeComponentScope`, its
  states, disposer contract, and lifecycle event sink port.
- Runtime consumers such as `ts/src/extensions/` may depend on that contract.
- `ts/src/session/runtime-component-lifecycle-events.ts` adapts the sink port
  to durable runtime-session events. The runtime module does not import the
  session or control-plane layers.
- Promotion, evaluation, rollback supervision, and candidate pointers remain
  control-plane responsibilities. A candidate component cannot replace its
  supervisor by owning this scope.

This direction keeps the reusable lifecycle contract in the existing
umbrella-owned runtime/core surface and respects the deferred package split in
[`core-control-package-split.md`](./core-control-package-split.md).

## Contract

A component begins `inactive`, transitions through `loading` to `active`, and
disposes through `unloading` to `inactive`. Activation or disposal failure ends
in `failed` after every registered inverse has been attempted.

Effects are registered next to their inverse with `scope.defer(disposer)`.
Disposers may be synchronous or asynchronous, run in exact reverse order, and
are marked invoked before execution. Repeated or concurrent disposal therefore
cannot invoke an inverse twice. If activation fails after partial setup, the
scope automatically unwinds everything registered so far.

Lifecycle audit events contain only the component identifier, state
transition, operation, and outcome. They intentionally omit thrown errors,
configuration, disposer details, and captured values. Component identifiers
must be stable non-secret identifiers.

## Extension proof slice

`ExtensionAPI` accepts an optional component scope and registers each hook
unsubscribe operation with it. `loadExtensionComponents` creates one scope per
extension and returns an unload handle. The compatibility `loadExtensions`
function uses the same managed activation path but retains its existing
`Promise<string[]>` return value.

Python parity, a general reactive dependency graph, and transactional candidate
activation are intentionally deferred to AC-960 and AC-961 or later slices.
