# Transactional Runtime Activation

AC-961 connects candidate promotion metadata to real TypeScript runtime
activation and rollback. The trusted `RuntimeActivationSupervisor` owns the
transaction; candidate components receive neither the supervisor nor the
durable pointer and cannot replace their recovery authority.

## Durable state machine

An active promotion records each boundary in an atomic JSON journal under
`.autocontext/state/runtime-activation-journal/`:

1. `staged`
2. `applying` / `applied`
3. `validating` / `validated`
4. `activating` / `activated`
5. `draining` / `drained`
6. `cutting_over` / `runtime_cutover`
7. `pointer_cutover`
8. `disposing_prior`
9. `committed`

Every entry has a monotonic sequence, timestamp, operation, stage, outcome,
candidate/prior artifact ids, and optional fixed failure code. It deliberately
omits thrown error text, payloads, capability values, callbacks, credentials,
and effect metadata. Writes use a temporary file plus atomic rename.

The active pointer changes only after the candidate graph has completely
activated and the runtime driver reports that exact candidate as live. Prior
components are not disposed until after runtime and pointer cutover. A failure
before commit invokes the staged session's idempotent abort path, disposes new
effects, restores host configuration, resumes the prior graph, and restores the
old pointer.

## Graph-backed activation

`RuntimeComponentGraphActivationDriver` uses a blue/green graph slot. It
validates candidate topology without touching the live graph, activates the
candidate with a host-created effect policy in a private graph, drains the
prior runtime, swaps the live slot, and disposes the prior graph in dependency
order. The private graph is discarded on validation or activation failure, so
the prior runtime remains active.

The manifest resolver receives the host-owned `RuntimeEffectPolicy`; it must
attach that policy to candidate command/tool scopes. All pre-cutover activation
uses candidate, shadow, or canary policy. Even a candidate targeting `active`
does not receive active-mode irreversible authority while it is staging.

Shadow and canary graphs remain side-by-side deployments: they do not replace
the active runtime pointer or dispose the baseline. An explicit rollback names
the shadow/canary candidate and removes its graph while preserving the active
pointer.

## Registry and actuator connection

`createActuatorRuntimeArtifactHooks` connects staged host configuration to the
existing actuator `apply` contract. Its rollback hook invokes the actuator's
declared live `rollback` path and then applies the validated baseline payload;
rollback is therefore not only a registry state change.

`createRegistryRuntimeActivationPointerStore` adapts the existing atomic state
pointer. `RegistryRuntimeActivationController` advances promotion history only
after live activation succeeds. For an active promotion it lets the existing
registry transition demote the old artifact and confirm the same pointer. If a
metadata transition fails, the controller starts a separately journaled
compensating runtime rollback before restoring registry state.

Hosts build the component manifests for an artifact and inject their resolver,
workspace layout, registry, and actuator hooks. The ordinary CLI does not
invent a live component resolver: deployments that want in-process hot
activation must configure this host integration explicitly.

## Restart recovery and idempotency

Transaction ids are idempotency keys. Repeating a completed activation or
rollback returns its stored result and does not repeat effects. A repeated
unfinished operation first recovers it instead of resuming from an ambiguous
instruction boundary.

On startup, `recover()` rolls every unfinished activation or rollback back to
the journaled baseline, verifies the observed runtime, restores the pointer,
and marks the journal `recovered`. It then compares the durable pointer with the
runtime's observed active artifact. A stale runtime is restored to the pointer
target through a separately journaled `repair` operation. `status()` exposes
pointer/observed ids, convergence, unfinished transaction ids, and divergent
transaction ids.

A failed provider disposer is different from an ordinary process interruption:
the external state may still exist. The graph driver records durable divergence
and refuses to instantiate that baseline again until the trusted operator has
repaired or verified the effect and calls `acknowledgeCleanupRepair`. It never
pretends an uncertain inverse succeeded.

Cross-process locking, distributed transactions, arbitrary Git patch
application, and Python parity remain out of scope. A deployment running more
than one activation supervisor for the same pointer must add a process-level
lease around this single-process transaction contract.

The [runtime composition confluence harness](runtime-composition-confluence.md)
exercises every durable precommit boundary, activation/cutover/disposal fault,
and seeded async provider race against the observable clean-boot contract.
