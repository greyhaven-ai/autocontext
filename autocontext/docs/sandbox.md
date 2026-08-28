# Sandbox Modes

autocontext supports four shipped execution modes for game scenarios, plus judge-based evaluation for agent tasks:

- `local` executor: runs strategies in a process pool with timeout controls, and applies memory limits in the subprocess path.
- `primeintellect` executor: runs declared tasks through the provider-neutral
  remote-session contract and a Prime Intellect sandbox lifecycle.
- `monty` executor: runs strategies in a pydantic-monty interpreter sandbox with external function callbacks and configurable timeout/call limits.
- `ssh` executor: runs strategies on an explicitly registered, trusted user-owned host and can fall back to the exact prepared local fixture when enabled.
- **Agent task evaluation**: Agent task scenarios bypass match execution entirely. `JudgeExecutor` delegates to `AgentTaskInterface.evaluate_output()`, which may use `LLMJudge` for LLM-based scoring against a rubric.

## Executable policy and harness containment

`PolicyExecutor` policies and `HarnessLoader` validators do not execute in the
long-lived autocontext process on supported local platforms. Each load or call
runs in a fresh, killable child process. The parent enforces the wall
timeout, terminates the child process group, and accepts only bounded JSON over
the result pipe. Pickle is never used across this trust boundary. Before code
runs, the child receives an empty environment, a private working directory,
closed inherited file descriptors, and best-effort CPU, memory, file-size, and
open-file limits. Linux also applies a verified same-UID task
ceiling plus inherited seccomp rules that deny `setsid`/`setpgid` (including
x32 and compatibility-ABI bypasses) and user-namespace creation/entry through
`clone`, `clone3`, `unshare`, or `setns`; macOS sets `RLIMIT_NPROC=1`, forbidding
descendant processes while still permitting helper threads. Results default to
a 1 MiB cap. The memory setting defaults to 256 MiB; on Linux it is a virtual
address-space growth allowance above mappings already inherited at fork,
bounded by any stricter inherited soft or hard `RLIMIT_AS`, while macOS applies
it as best-effort absolute address-space and data limits.

The same boundary is used when `HarnessTester` evaluates synthesized harnesses
and when staged validation loads or invokes a code candidate's
`choose_action`. These paths deliberately serialize local child launches:
forking from a thread is rejected, so `HarnessTester.parallel_workers` does not
cause an unsafe threaded fallback.

Generated simulation and investigation scenarios, agent-task execution
validation, and each command handled by the legacy `rlm_backend=exec` REPL use
the same boundary. The exec REPL sends only bounded, tagged plain built-in data
back to its parent. Candidate-created functions, classes, generators, and
instances become metadata-only placeholders and are not executable in later
commands. A timeout kills the child process group; it never abandons a Python
thread that can continue mutating the parent. Before execution, the REPL uses
the same import, dunder, dynamic-introspection, and exception-handling AST
denials as policy/harness code. Its builtins are a positive allowlist, and
`json`, `math`, `statistics`, `collections`, `re`, and `time` are exposed as
fresh immutable facades containing only named operations—not full module
objects with paths to `__builtins__`.

This boundary is defense in depth around the existing AST checks and restricted
builtins; it is not a host sandbox:

- Resource-limit behavior varies by kernel. In particular, address-space
  and data limits are best effort and are not a portable hard memory guarantee.
- The child runs as the invoking OS user. If Python restrictions are bypassed,
  it may still address host filesystem paths or create network connections.
- Restricted Python and module facades reduce known interpreter escape paths;
  they are not a kernel-enforced filesystem or egress boundary, and future
  runtime gadgets must be treated as a residual risk.
- Wall-time and process-group cleanup are enforced by the live parent process.
  An abrupt host or parent-process termination can leave local execution or
  provider descendants running: Linux parent-death signals do not cover an
  entire descendant tree, and macOS has no equivalent portable primitive.
  Forced cleanup of an interactive runner also cannot guarantee termination of
  provider subprocesses that deliberately created a separate session or gained
  credentials/MAC protection that makes them unsignalable by the parent. Use a
  container, cgroup/service manager with whole-tree cleanup, Windows Job Object,
  or a microVM when the lifecycle boundary must include every detached or
  differently privileged descendant.
- Setting `PolicyExecutor(..., safe_builtins=False)` intentionally weakens the
  interpreter restriction and is suitable only for trusted local policies.
- Local policy/harness execution is supported only for non-root Linux
  x86-64/AArch64 hosts with readable `/proc` task/capability accounting, no
  inheritable, permitted, effective, or ambient Linux capabilities, enforceable
  `RLIMIT_NPROC`, and
  seccomp `prctl`, or non-root macOS hosts with Mach native
  thread accounting and enforceable `RLIMIT_NPROC`. Both require `fork` plus
  `waitid`/`WNOWAIT`, the default `SIGCHLD` disposition, and exactly one OS
  thread. Root, FreeBSD/other POSIX, external child reapers, unverifiable limits,
  unknown native threads, and unsupported ABIs fail closed.
  The interactive run manager launches generation work in a dedicated spawned
  process so supported TUI execution reaches this boundary. It likewise requires
  the default `SIGCHLD` disposition before spawning and rechecks immediately at
  process start. It is incompatible with any library or host component that
  explicitly reaps its child with `waitpid`: in a multithreaded server there is
  no atomic portable operation that both reserves the exited leader's process-
  group ID and signals the group. Deployments that require protection from an
  independent child reaper need external cgroup or Job Object ownership. No
  in-process fallback is used.

Other executable-code features retain separate trust classifications. Loading
a persisted custom scenario through `scenarios.custom.loader`, the custom
registry, or the verbatim-solve registration path returns a live Python class
and therefore imports that module in the control-plane process. Those are
**trusted local plugin activation** paths, not validation sandboxes. Their
source generators perform structural validation and literal-safe emission, but
operators must not point their knowledge root at tenant-writable content or
load an artifact whose provenance they do not trust. Moving those loaders to a
child requires a scenario RPC/proxy design rather than returning a Python class
across the JSON boundary. User extension modules and autoresearch
`register_import` hooks are also explicitly trusted operator code and run
in-process.

Use Monty for compatible interpreter-sandboxed strategy code, or a configured
remote/container/microVM adapter with read-only mounts and denied egress when
code authors are mutually untrusted. A Gondolin adapter remains the intended
boundary for deployments that require enforceable filesystem, network, secret,
and memory isolation.

## Gondolin Boundary

Gondolin is reserved as an optional microVM sandbox backend for deployments that need stronger isolation, secret policy, and egress policy than the local/Monty paths provide. It is not a hosted scheduler or background-worker control plane by itself.

`AUTOCONTEXT_EXECUTOR_MODE=gondolin` is intentionally fail-closed until a real backend adapter is configured. This prevents a deployment that expected a VM boundary from silently running tasks locally. Separately, capable code/research workspaces can use the shipped `DockerResearchSandboxBackend`; it implements the `ResearchSandboxBackend` boundary and is not selected by the game executor mode.

Use the current modes this way:

- Use `monty` when you want interpreter-level containment for Python evaluation with low operational overhead.
- Use `local` for trusted local development and fast iteration.
- Use `primeintellect` or `ssh` when you want execution off the current host; PrimeIntellect requires `pip install 'autocontext[primeintellect]'`.
- Use Gondolin only after the adapter is wired for VM lifecycle, mounted artifacts, secret injection, and network/egress policy.

The public OSS contract for a future Gondolin adapter is intentionally narrow:

- Python: implement `GondolinBackend` from `autocontext.execution.executors.gondolin_contract` behind the existing `ExecutionEngine` boundary.
- TypeScript: implement `GondolinBackend` from `autoctx` and start from `createDefaultGondolinSandboxPolicy()`.

Remote sandbox snapshot and warming support is similarly adapter-neutral. Adapters may advertise `snapshot`, `restore`, `prebuild_repo_image`, `warm`, and `resolve_tunnel_ports` capabilities through the sandbox adapter contract. The core runner does not require those methods: local and persistent-host deployments can advertise no capabilities and boot fresh. Restore startup requires both an advertised restore capability and a non-empty snapshot reference. When a requested capability or required reference is missing, startup planning either fails closed or explicitly degrades to fresh according to policy, and sanitized capability events with safe reason codes can be shown in the background-session timeline.

Prime Intellect requests use `RemoteExecutionRequest` rather than embedding
scenario rules in the provider client. Ephemeral execution is the default;
bounded matched-trial reuse and warm/snapshot startup are explicit,
capability-checked lifecycle policies. Results distinguish task, provider,
timeout, artifact, and cleanup failures and can be projected directly into an
external-evaluation ledger. See
[provider-neutral remote execution sessions](../../docs/remote-execution-sessions.md).

`ResearchWorkspace(profile="isolated_sandbox")` also fails closed unless its
caller supplies a backend with every mandatory security control. The Docker
implementation provides read-only-root, workspace-only container isolation
with deny-network policy, bounded processes/resources, scrubbed environment,
transactional result commit, terminable execution, opaque secret resolution,
and verified labeled-container cleanup. It deliberately rejects network grants;
an allowlist requires a lower-layer egress-policy backend. See
[capability-scoped research workspaces](../../docs/research-workspaces.md).

The contract carries policy and secret references, not secret values. Hosted fleet orchestration, tenant scheduling, policy UI, billing, proactive warm-pool management, image-cache economics, and managed audit retention remain deployment concerns outside this OSS boundary. A deployment is not multi-tenant safe merely because it uses a remote sandbox; it also needs tenant-aware credential brokering, per-tenant filesystem/network isolation, egress policy, audit, retention, and abuse controls. See [Background execution trust boundaries and credential model](../../docs/background-execution-trust-boundaries.md).

## Live Component Effects

TypeScript live components may run scoped commands and tools through a host-owned
effect policy. Each invocation must be declared `reversible`, `compensatable`,
or `irreversible`; candidate, shadow, and canary runtimes cannot perform an
irreversible effect before an explicit matching commit boundary. This policy is
an invocation gate, not a sandbox. Untrusted components require an available
external process, interpreter, or microVM boundary and must not receive ambient
filesystem, shell, network, or direct MCP access. See the
[runtime effect policy](../../docs/internal/runtime-effect-policy.md) for the
recovery and audit contracts.

## Relevant Environment Variables

- `AUTOCONTEXT_EXECUTOR_MODE` (`local`, `primeintellect`, `monty`, `ssh`; `gondolin` is reserved/fail-closed)
- `AUTOCONTEXT_PRIMEINTELLECT_API_BASE` (deprecated compatibility field; only the provider default is accepted)
- `AUTOCONTEXT_PRIMEINTELLECT_API_KEY` (deployment secret; never store in prompts, traces, runtime-session events, background-session summaries, lifecycle hook payloads, or artifact metadata)
- `AUTOCONTEXT_PRIMEINTELLECT_DOCKER_IMAGE`
- `AUTOCONTEXT_PRIMEINTELLECT_CPU_CORES`
- `AUTOCONTEXT_PRIMEINTELLECT_MEMORY_GB`
- `AUTOCONTEXT_PRIMEINTELLECT_DISK_SIZE_GB`
- `AUTOCONTEXT_PRIMEINTELLECT_TIMEOUT_MINUTES`
- `AUTOCONTEXT_PRIMEINTELLECT_WAIT_ATTEMPTS`
- `AUTOCONTEXT_PRIMEINTELLECT_MAX_RETRIES`
- `AUTOCONTEXT_PRIMEINTELLECT_BACKOFF_SECONDS`
- `AUTOCONTEXT_ALLOW_PRIMEINTELLECT_FALLBACK`
- `AUTOCONTEXT_LOCAL_SANDBOX_HARDENED`
- `AUTOCONTEXT_MONTY_MAX_EXECUTION_TIME_SECONDS`
- `AUTOCONTEXT_MONTY_MAX_EXTERNAL_CALLS`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_ENABLED`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_BACKEND` (`interpreter` or `docker`)
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_EXECUTE_CANDIDATES`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_CAPABILITIES_APPROVED`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_ALLOWED_IMPORTS` (JSON array)
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_ALLOWED_COMMANDS` (JSON array)
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_DOCKER_IMAGE` (must include `@sha256:`)
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_MEMORY_MB`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_CPU_COUNT`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_PIDS_LIMIT`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_MAX_FILE_BYTES`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_MAX_WORKSPACE_BYTES`
- `AUTOCONTEXT_WORKSPACE_INTERPRETER_MAX_WORKSPACE_INODES`
- `AUTOCONTEXT_JUDGE_MODEL`
- `AUTOCONTEXT_JUDGE_SAMPLES`
- `AUTOCONTEXT_JUDGE_TEMPERATURE`

## Recovery Behavior

- PrimeIntellect preflight probe retries according to control-plane backoff.
- PrimeIntellect remote-session execution retries provider failures with
  backoff around full sandbox lifecycle operations; candidate/task failures are
  returned without infrastructure retry.
- Remote failures remain typed and fail closed by default. The legacy strategy
  compatibility facade may generate a local fallback only when
  `AUTOCONTEXT_ALLOW_PRIMEINTELLECT_FALLBACK=true`; prepared-fixture and campaign
  execution never substitute an unattested local result.
