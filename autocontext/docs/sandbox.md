# Sandbox Modes

autocontext supports three shipped execution modes for game scenarios, plus judge-based evaluation for agent tasks:

- `local` executor: runs strategies in a process pool with timeout controls, and applies memory limits in the subprocess path.
- `primeintellect` executor: runs strategies remotely via PrimeIntellect sandbox lifecycle (create/wait/execute/delete).
- `monty` executor: runs strategies in a pydantic-monty interpreter sandbox with external function callbacks and configurable timeout/call limits.
- **Agent task evaluation**: Agent task scenarios bypass match execution entirely. `JudgeExecutor` delegates to `AgentTaskInterface.evaluate_output()`, which may use `LLMJudge` for LLM-based scoring against a rubric.

## Executable policy and harness containment

`PolicyExecutor` policies and `HarnessLoader` validators do not execute in the
long-lived autocontext process on supported local platforms. Each load or call
runs in a fresh, killable POSIX child process. The parent enforces the wall
timeout, terminates the child process group, and accepts only bounded JSON over
the result pipe. Pickle is never used across this trust boundary. Before code
runs, the child receives an empty environment, a private working directory,
closed inherited file descriptors, and best-effort CPU, address-space, data,
file-size, open-file, and process-count limits. Results default to a 1 MiB cap
and child memory defaults to 256 MiB.

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

- POSIX resource-limit behavior varies by kernel. In particular, address-space
  and data limits are best effort and are not a portable hard memory guarantee.
- The child runs as the invoking OS user. If Python restrictions are bypassed,
  it may still address host filesystem paths or create network connections.
- Restricted Python and module facades reduce known interpreter escape paths;
  they are not a kernel-enforced filesystem or egress boundary, and future
  runtime gadgets must be treated as a residual risk.
- Setting `PolicyExecutor(..., safe_builtins=False)` intentionally weakens the
  interpreter restriction and is suitable only for trusted local policies.
- Local policy/harness execution fails closed on non-POSIX platforms, POSIX
  platforms without `waitid`/`WNOWAIT`, and when invoked from a Python worker
  thread, where the current fork-based boundary is unavailable. No in-process
  fallback is used.

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

`AUTOCONTEXT_EXECUTOR_MODE=gondolin` is intentionally fail-closed until a real backend adapter is configured. This prevents a deployment that expected a VM boundary from silently running tasks locally.

Use the current modes this way:

- Use `monty` when you want interpreter-level containment for Python evaluation with low operational overhead.
- Use `local` for trusted local development and fast iteration.
- Use `primeintellect` or `ssh` when you want execution off the current host; PrimeIntellect requires `pip install 'autocontext[primeintellect]'`.
- Use Gondolin only after the adapter is wired for VM lifecycle, mounted artifacts, secret injection, and network/egress policy.

The public OSS contract for a future Gondolin adapter is intentionally narrow:

- Python: implement `GondolinBackend` from `autocontext.execution.executors.gondolin_contract` behind the existing `ExecutionEngine` boundary.
- TypeScript: implement `GondolinBackend` from `autoctx` and start from `createDefaultGondolinSandboxPolicy()`.

Remote sandbox snapshot and warming support is similarly adapter-neutral. Adapters may advertise `snapshot`, `restore`, `prebuild_repo_image`, `warm`, and `resolve_tunnel_ports` capabilities through the sandbox adapter contract. The core runner does not require those methods: local and persistent-host deployments can advertise no capabilities and boot fresh. Restore startup requires both an advertised restore capability and a non-empty snapshot reference. When a requested capability or required reference is missing, startup planning either fails closed or explicitly degrades to fresh according to policy, and sanitized capability events with safe reason codes can be shown in the background-session timeline.

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
- `AUTOCONTEXT_PRIMEINTELLECT_API_BASE`
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
- `AUTOCONTEXT_JUDGE_MODEL`
- `AUTOCONTEXT_JUDGE_SAMPLES`
- `AUTOCONTEXT_JUDGE_TEMPERATURE`

## Recovery Behavior

- PrimeIntellect preflight probe retries according to control-plane backoff.
- PrimeIntellect match execution retries with backoff around full sandbox lifecycle operations.
- If remote execution remains unavailable, fallback replay/result payloads are generated and captured through normal recovery markers.
