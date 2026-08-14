# Runtime Effect Policy

AC-958 adds a TypeScript-first host policy for effects invoked by live runtime
components. The policy sits beside the component lifecycle from AC-959 and is
an invocation gate, not an operating-system sandbox or a distributed
transaction manager.

## Effect classes

Every command or tool used under a `RuntimeEffectPolicy` must declare exactly
one class:

| Class | Required metadata | Recovery contract | Typical examples |
| --- | --- | --- | --- |
| `reversible` | A synchronous or asynchronous disposer | The component scope owns the disposer and invokes it at most once while unwinding or unloading. | Hook registration, temporary listener, mounted in-memory adapter |
| `compensatable` | A compensation callback, stable idempotency key, stated observational equivalence, and confirmation that the intent was journaled before invocation | Recovery may run the compensation at most once from the component scope. The caller remains responsible for durable journal recovery after a process crash. | Reservation with cancellation, staged remote resource with cleanup |
| `irreversible` | A stable commit-boundary identifier | Candidate, shadow, and canary execution is denied until the trusted supervisor explicitly authorizes and commits the exact boundary. | External publication, destructive remote mutation, non-retractable notification |

Missing or malformed declarations fail closed before the command or tool
handler runs. Reversible and compensatable effects also fail closed without an
active `RuntimeComponentScope`, because there would be no lifecycle owner for
their inverse.

## Candidate and shadow policy

Candidate, shadow, and canary modes deny irreversible effects by default. A
trusted supervisor may allow one only by setting all three policy facts:

- irreversible effects are allowed for this operation;
- the transaction is committed; and
- the policy commit-boundary id exactly matches the effect declaration.

Those facts are immutable after the host constructs the policy. The policy is
stored in private workspace state and is not exposed as a candidate mutation
surface. Direct ambient shell fallback is disabled whenever a workspace has an
effect policy, so an undeclared command cannot bypass a scoped grant.

## Crash recovery

The in-process component scope guarantees deterministic LIFO cleanup and
at-most-once invocation within the current process. It does not make a remote
operation transactional across a crash. A compensatable integration must
persist its intent before invoking the effect, use the declared idempotency key
when recovering, and define the externally observable state that counts as an
equivalent rollback. The AC-961 transaction journal owns restart recovery and
durable candidate activation records.

AC-961 now supplies that journal and always stages active promotions under
candidate policy. The trusted supervisor performs the commit/cutover outside
the candidate graph; staging code never receives active-mode irreversible
authority.

An irreversible effect has no automatic recovery path. Its commit boundary is
therefore a supervisor decision outside the mutable candidate graph.

## Sandbox and trust boundary

An untrusted component is rejected unless the host confirms an available
external `process`, `interpreter`, or `microvm` boundary. `in_process` is never
accepted as isolation for untrusted code. The policy object cannot prove that
a configured sandbox actually contains filesystem, process, network, egress,
or secret access; the deployment adapter must enforce those controls.

This initial enforcement slice covers scoped runtime command and tool
invocations. Direct workspace filesystem methods remain a trusted host API,
and a directly held MCP client can still make calls outside the scoped
workspace wrapper. Do not provide either ambient surface to untrusted
candidate code. Hosted and multi-tenant deployments still require the controls
listed in [Background execution trust boundaries](../background-execution-trust-boundaries.md).

## Audit contract

Classified command and tool events record only the stable command/tool name,
working directory, effect class, phase, and safe outcome (`allowed`, `denied`,
`completed`, or `failed`) plus structural redaction metadata. They omit input
arguments, output, provenance, candidate-owned error text, callbacks,
idempotency keys, and commit-boundary ids. Policy denials use fixed reason
codes rather than candidate-controlled messages.

Python parity is deferred until this TypeScript control-plane experiment has a
stable public behavior. Fleet orchestration and arbitrary user-defined
reversibility remain out of scope.
