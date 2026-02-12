# MTS Control Plane

Infrastructure-first control plane for iterative strategy generation and evaluation.

## Quick start

```bash
uv venv
source .venv/bin/activate
uv sync --group dev
# For real Agent SDK-backed runs:
# export MTS_AGENT_PROVIDER=anthropic
# export MTS_ANTHROPIC_API_KEY=...
# For local deterministic CI-style runs:
export MTS_AGENT_PROVIDER=deterministic
mts --help
```

## Live PrimeIntellect mode

```bash
export MTS_EXECUTOR_MODE=primeintellect
export MTS_PRIMEINTELLECT_API_BASE="https://api.primeintellect.ai"
export MTS_PRIMEINTELLECT_API_KEY="your_api_key"
export MTS_PRIMEINTELLECT_DOCKER_IMAGE="python:3.11-slim"
export MTS_AGENT_PROVIDER=anthropic
export MTS_ANTHROPIC_API_KEY="your_anthropic_key"
uv run mts run --scenario grid_ctf --gens 1 --run-id live_prime_smoke
```

Prime mode now uses the documented sandbox lifecycle (create, wait, execute
command, delete) through the official `prime-sandboxes` SDK. You can tune
resource limits with the `MTS_PRIMEINTELLECT_*` sandbox environment variables.

## Dashboard and replay stream

```bash
export MTS_AGENT_PROVIDER=deterministic
uv run mts run --scenario grid_ctf --gens 3 --run-id dashboard_seed
uv run mts serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` for the dashboard UI.

## One-command demo

From repository root:

```bash
bash infra/scripts/bootstrap.sh
bash scripts/demo.sh
```
