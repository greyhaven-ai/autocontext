# Sandbox Modes

AutoContext supports three execution modes for game scenarios, plus judge-based evaluation for agent tasks:

- `local` executor: runs strategies in a process pool with timeout controls, and applies memory limits in the subprocess path.
- `primeintellect` executor: runs strategies remotely via PrimeIntellect sandbox lifecycle (create/wait/execute/delete).
- `monty` executor: runs strategies in a pydantic-monty interpreter sandbox with external function callbacks and configurable timeout/call limits.
- **Agent task evaluation**: Agent task scenarios bypass match execution entirely. `JudgeExecutor` delegates to `AgentTaskInterface.evaluate_output()`, which may use `LLMJudge` for LLM-based scoring against a rubric.

## Relevant Environment Variables

- `AUTOCONTEXT_EXECUTOR_MODE` (`local`, `primeintellect`, or `monty`)
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
