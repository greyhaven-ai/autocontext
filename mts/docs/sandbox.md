# Sandbox Modes

MTS supports three execution modes for game scenarios, plus judge-based evaluation for agent tasks:

- `local` executor: runs strategies in a process pool with timeout controls, and applies memory limits in the subprocess path.
- `primeintellect` executor: runs strategies remotely via PrimeIntellect sandbox lifecycle (create/wait/execute/delete).
- `monty` executor: runs strategies in a pydantic-monty interpreter sandbox with external function callbacks and configurable timeout/call limits.
- **Agent task evaluation**: Agent task scenarios bypass match execution entirely. `JudgeExecutor` delegates to `AgentTaskInterface.evaluate_output()`, which may use `LLMJudge` for LLM-based scoring against a rubric.

## Relevant Environment Variables

- `MTS_EXECUTOR_MODE` (`local`, `primeintellect`, or `monty`)
- `MTS_PRIMEINTELLECT_API_BASE`
- `MTS_PRIMEINTELLECT_API_KEY`
- `MTS_PRIMEINTELLECT_DOCKER_IMAGE`
- `MTS_PRIMEINTELLECT_CPU_CORES`
- `MTS_PRIMEINTELLECT_MEMORY_GB`
- `MTS_PRIMEINTELLECT_DISK_SIZE_GB`
- `MTS_PRIMEINTELLECT_TIMEOUT_MINUTES`
- `MTS_PRIMEINTELLECT_WAIT_ATTEMPTS`
- `MTS_PRIMEINTELLECT_MAX_RETRIES`
- `MTS_PRIMEINTELLECT_BACKOFF_SECONDS`
- `MTS_ALLOW_PRIMEINTELLECT_FALLBACK`
- `MTS_LOCAL_SANDBOX_HARDENED`
- `MTS_MONTY_MAX_EXECUTION_TIME_SECONDS`
- `MTS_MONTY_MAX_EXTERNAL_CALLS`
- `MTS_JUDGE_MODEL`
- `MTS_JUDGE_SAMPLES`
- `MTS_JUDGE_TEMPERATURE`

## Recovery Behavior

- PrimeIntellect preflight probe retries according to control-plane backoff.
- PrimeIntellect match execution retries with backoff around full sandbox lifecycle operations.
- If remote execution remains unavailable, fallback replay/result payloads are generated and captured through normal recovery markers.
