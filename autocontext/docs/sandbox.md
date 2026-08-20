# Sandbox Modes

autocontext supports four shipped execution modes for game scenarios, plus judge-based evaluation for agent tasks:

- `local` executor: runs strategies in a process pool with timeout controls, and applies memory limits in the subprocess path.
- `primeintellect` executor: runs declared tasks through the provider-neutral
  remote-session contract and a Prime Intellect sandbox lifecycle.
- `monty` executor: runs strategies in a pydantic-monty interpreter sandbox with external function callbacks and configurable timeout/call limits.
- `ssh` executor: runs strategies on an explicitly registered, trusted user-owned host and can fall back to the exact prepared local fixture when enabled.
- **Agent task evaluation**: Agent task scenarios bypass match execution entirely. `JudgeExecutor` delegates to `AgentTaskInterface.evaluate_output()`, which may use `LLMJudge` for LLM-based scoring against a rubric.

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
