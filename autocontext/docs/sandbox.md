# Sandbox Modes

autocontext supports three shipped execution modes for game scenarios, plus judge-based evaluation for agent tasks:

- `local` executor: runs strategies in a process pool with timeout controls, and applies memory limits in the subprocess path.
- `primeintellect` executor: runs strategies remotely via PrimeIntellect sandbox lifecycle (create/wait/execute/delete).
- `monty` executor: runs strategies in a pydantic-monty interpreter sandbox with external function callbacks and configurable timeout/call limits.
- **Agent task evaluation**: Agent task scenarios bypass match execution entirely. `JudgeExecutor` delegates to `AgentTaskInterface.evaluate_output()`, which may use `LLMJudge` for LLM-based scoring against a rubric.

## Gondolin Boundary

Gondolin is reserved as an optional microVM sandbox backend for deployments that need stronger isolation, secret policy, and egress policy than the local/Monty paths provide. It is not a hosted scheduler or background-worker control plane by itself.

`AUTOCONTEXT_EXECUTOR_MODE=gondolin` is intentionally fail-closed until a real backend adapter is configured. This prevents a deployment that expected a VM boundary from silently running tasks locally.

Use the current modes this way:

- Use `monty` when you want interpreter-level containment for Python evaluation with low operational overhead.
- Use `local` for trusted local development and fast iteration.
- Use `primeintellect` or `ssh` when you want execution off the current host.
- Use Gondolin only after the adapter is wired for VM lifecycle, mounted artifacts, secret injection, and network/egress policy.

The public OSS contract for a future Gondolin adapter is intentionally narrow:

- Python: implement `GondolinBackend` from `autocontext.execution.executors.gondolin_contract` behind the existing `ExecutionEngine` boundary.
- TypeScript: implement `GondolinBackend` from `autoctx` and start from `createDefaultGondolinSandboxPolicy()`.

The contract carries policy and secret references, not secret values. Hosted fleet orchestration, tenant scheduling, policy UI, billing, and managed audit retention remain deployment concerns outside this OSS boundary.

## Relevant Environment Variables

- `AUTOCONTEXT_EXECUTOR_MODE` (`local`, `primeintellect`, `monty`, `ssh`; `gondolin` is reserved/fail-closed)
- `AUTOCONTEXT_PRIMEINTELLECT_API_BASE`
- `AUTOCONTEXT_PRIMEINTELLECT_API_KEY`
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
