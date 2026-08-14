# Runtime Composition Confluence Harness

AC-962 adds a TypeScript harness for answering a concrete runtime question:
after a bounded dynamic history settles, is the observable runtime equivalent
to a clean boot into the same final configuration? The harness covers the
component graph and the transactional activation boundaries introduced by
AC-959 through AC-961.

## Observable snapshot

`RuntimeCompositionInventory` attaches host-observable resources to their
owning `RuntimeComponentScope`. Disposal removes the registration even when the
resource's inverse fails. `captureRuntimeCompositionSnapshot` combines that
inventory with a component-graph snapshot and active artifact pointer. It
reports:

- active component and provider instance identities;
- hooks, tools, command grants, subscriptions, timers, tasks, temporary
  resources, and other owned resources;
- the active artifact pointer;
- blocked capabilities and unresolved lifecycle errors; and
- the number of live irreversible effects excluded from exact comparison.

Snapshots are sorted and contain no callback bodies, arguments, tool results,
credentials, or thrown error text. Equivalence deliberately ignores graph
revision and the transient `transitioning` flag because those record history,
not the settled runtime. `assertRuntimeCompositionQuiescent` separately
requires that transitions have ended and that no blocked capability or
unresolved lifecycle error remains.

Workspace commands and tools registered with a composition inventory require
an owning component scope. Their inventory entries disappear with that scope,
and retained workspace or tool handles fail after disposal. `ExtensionAPI`
hook registrations use the same ownership path.

## Effect equivalence

Reversible effects compare by their stable resource id. Compensatable effects
must declare `observationalEquivalence`; the harness compares that declared
post-compensation identity instead of attempt-specific ids. This assumes the
host-provided inverse is valid and has completed before quiescence.

Irreversible emissions are not claimed to be exactly equal across dynamic and
clean histories. They are excluded from identity comparison and surfaced only
as an exclusion count. Tests may assert their external history separately, but
must not use this harness to claim that an already-observed emission was
undone.

## Fault and interleaving coverage

The focused suite injects failures at apply, validate, activate, drain,
runtime cutover, durable-pointer cutover, and prior-runtime disposal. It also
constructs interrupted journals at every durable precommit boundary and
requires bounded recovery to the baseline.

`DeterministicRuntimeTransitionScheduler` cooperatively interleaves promise and
async-generator tasks using a reproducible seed. It refuses to run beyond a
caller-supplied step budget, so a non-quiescent history fails instead of
hanging CI. Provider replacement races must converge to the latest requested
configuration.

The ordinary property test uses seed `962`, a bounded history length, and 24
runs by default. Run the longer, separately invokable sweep from `ts/` with:

```bash
npm run test:runtime-confluence:sweep
```

Set `AUTOCTX_CONFLUENCE_RUNS` when invoking the focused Vitest files directly
to choose a different positive run count.

## Assumptions and exclusions

The guarantee applies to resources registered in the host-owned inventory and
to a valid, acyclic component graph with exclusive providers. Untracked
process-global effects, external systems without a declared observation,
cross-process scheduling, distributed transactions, wall-clock ordering, and
Python parity are outside this harness. Cross-process activation still needs a
lease around the single-process transaction contract.
