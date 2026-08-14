# Runtime Component Graph

AC-960 adds a TypeScript-first reactive graph for live runtime components. It
builds on the lifecycle-owned scopes from AC-959 and keeps graph reconciliation
inside the trusted host. It is not a dependency-injection container, service
broker, or cross-process discovery protocol.

## Manifest contract

A component manifest declares:

- a stable logical component `id`;
- an `instanceId` identifying the concrete component/provider generation;
- typed capability keys it `requires`;
- typed capability values it `provides`; and
- an activation callback that receives its lifecycle scope and typed provider
  lookup.

`defineRuntimeCapability<T>(id)` carries the value type through provider and
consumer code. The key's string id is the stable graph and diagnostic identity.
Only one component may provide a capability in a desired graph. Multiplexing,
load balancing, and service brokers are intentionally deferred.

Activation lookup is limited to capabilities listed in that component's
`requires` array. A component cannot consume an ambient provider and omit the
dependency edge. Component, instance, and capability identifiers are audit
fields and must never contain credentials or other secret material.

An `instanceId` must change whenever a provider's activation or provided value
changes. Reusing the same logical component id and instance id means the graph
may preserve the live instance. Distinct instance ids trigger consumer
recomposition even if their provided values compare equal.

## Validation and waiting

Every requested graph is copied and validated before any live component is
changed. Duplicate component ids, duplicate exclusive providers, malformed
manifests, and dependency cycles reject the reconciliation without partial
activation.

A missing requirement is different: the consumer remains inactive with a
`missing_requirement` diagnostic. If its configured provider failed or is
blocked, it waits with `provider_inactive`. Adding a usable provider on a later
reconciliation activates the provider first and then the waiting consumer.

## Replacement order

Provider removal or replacement follows this sequence:

1. Remove the affected provider capabilities from the available lookup and
   emit `provider_unavailable`.
2. Dispose affected consumers in reverse dependency order.
3. Dispose their providers.
4. Activate replacement providers in topological order.
5. Reactivate only affected consumers against the new provider identity.

Unrelated active components retain their existing scope and do not restart.
Component disposal retains AC-959's LIFO and at-most-once guarantees.

If provider cleanup fails, its capabilities remain blocked even though they are
unavailable. A replacement cannot activate until a trusted supervisor repairs
or verifies the external state and calls `acknowledgeProviderCleanup`. This is
fail-closed because activating a second exclusive provider while the first may
still have effects would violate the graph contract.

## Concurrency and failure containment

Reconciliations are serialized in request order. A request may arrive while an
async activation or disposer is running; its validated graph runs next, so the
eventual active state converges to the latest request. AC-962 adds broader
interleaving and fault-injection coverage around this guarantee.

Activation failure automatically unwinds the component scope, records only the
safe `activation_failed` reason, and leaves its consumers waiting. Unrelated
components continue to activate. A failure whose unwind also fails blocks that
component's provided capabilities for supervisor repair.

## Diagnostics

`snapshot()` exposes the applied revision, transition state, component and
provider identities, dependency bindings, waiting/failure reason codes, and
blocked capabilities. It never includes capability values or thrown error
text. `createRuntimeSessionComponentGraphEventSink` records the same safe
identities and transitions as `component_graph` runtime-session events, which
the generic timeline and background-session normalizer can render.

The transaction journal, active candidate pointer, promotion policy, rollback,
and restart recovery remain control-plane responsibilities owned by AC-961.
Python parity is deferred until this experimental contract stabilizes.
