# Sandbox Modes

MTS supports two execution modes:

- `local` executor: runs strategies in a process pool with timeout controls, and applies memory limits in the subprocess path.
- `primeintellect` executor: runs strategies remotely via PrimeIntellect sandbox lifecycle (create/wait/execute/delete).

## Relevant Environment Variables

- `MTS_EXECUTOR_MODE` (`local` or `primeintellect`)
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

## Recovery Behavior

- PrimeIntellect preflight probe retries according to control-plane backoff.
- PrimeIntellect match execution retries with backoff around full sandbox lifecycle operations.
- If remote execution remains unavailable, fallback replay/result payloads are generated and captured through normal recovery markers.
