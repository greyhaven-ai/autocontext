# autocontext

autocontext is a control plane for improving agent behavior over repeated runs. It combines multi-agent candidate generation, staged validation, scenario execution, knowledge accumulation, optional local distillation, and OpenClaw-facing APIs.

## Working Directory

Run the commands in this README from the `autocontext/` directory. The Python package, CLI entrypoint, tests, migrations, and dashboard assets all live here.

## What It Does

- Runs iterative generation loops against game scenarios and agent-task scenarios
- Persists playbooks, hints, tools, reports, and snapshots across runs
- Supports staged validation, harness synthesis, and harness-aware routing
- Exports training data and runs autoresearch-style local training loops
- Exposes evaluation, validation, artifact, and discovery operations over MCP and HTTP

## Quick Start

From the repo root:

```bash
cd autocontext
uv venv
source .venv/bin/activate
uv sync --group dev
```

Use the repo-level `.env.example` as the reference for available `AUTOCONTEXT_*` settings.

Run a deterministic local scenario:

```bash
AUTOCONTEXT_AGENT_PROVIDER=deterministic \
uv run autoctx run --scenario grid_ctf --gens 3 --run-id quickstart
```

Run with Anthropic:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic \
AUTOCONTEXT_ANTHROPIC_API_KEY=... \
uv run autoctx run --scenario grid_ctf --gens 3
```

Start the API server and dashboard:

```bash
uv run autoctx serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` after the server starts.

Start the MCP server:

```bash
uv sync --group dev --extra mcp
uv run autoctx mcp-serve
```

## Main CLI Commands

```bash
uv run autoctx run --scenario grid_ctf --gens 3
uv run autoctx list
uv run autoctx status <run_id>
uv run autoctx replay <run_id> --generation 1
uv run autoctx benchmark --scenario grid_ctf --runs 5
uv run autoctx serve --host 127.0.0.1 --port 8000
uv run autoctx mcp-serve
```

Useful variants:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic AUTOCONTEXT_ANTHROPIC_API_KEY=... \
uv run autoctx run --scenario grid_ctf --gens 3

AUTOCONTEXT_AGENT_PROVIDER=deterministic AUTOCONTEXT_RLM_ENABLED=true \
uv run autoctx run --scenario grid_ctf --gens 3
```

## Training Workflow

Export JSONL training data from completed runs:

```bash
uv run autoctx export-training-data \
  --scenario grid_ctf \
  --all-runs \
  --output training/grid_ctf.jsonl
```

Launch the autoresearch-style training loop:

```bash
uv sync --group dev --extra mlx
uv run autoctx train \
  --scenario grid_ctf \
  --data training/grid_ctf.jsonl \
  --time-budget 300
```

MLX training is host-only. It must run on an Apple Silicon macOS machine with Metal access. It will not run correctly inside a Docker sandbox on macOS.

If you only want to inspect generated training data first, export without training and open the JSONL directly.

For host setup details and OpenClaw automation via a file-based watcher bridge, see [docs/mlx-training.md](docs/mlx-training.md).

## Configuration

Configuration is loaded from `AUTOCONTEXT_*` environment variables in `src/autocontext/config/settings.py`.

Common settings:

- `AUTOCONTEXT_AGENT_PROVIDER`
- `AUTOCONTEXT_EXECUTOR_MODE`
- `AUTOCONTEXT_MODEL_COMPETITOR`
- `AUTOCONTEXT_MATCHES_PER_GENERATION`
- `AUTOCONTEXT_MAX_RETRIES`
- `AUTOCONTEXT_JUDGE_PROVIDER`
- `AUTOCONTEXT_RLM_ENABLED`
- `AUTOCONTEXT_HARNESS_PREFLIGHT_ENABLED`
- `AUTOCONTEXT_STAGED_VALIDATION_ENABLED`

See the repo-level [.env.example](../.env.example) for a working starting point.

## Repository Structure

```text
autocontext/
  src/autocontext/   Python package
  tests/             Pytest suite
  dashboard/         Static dashboard assets
  docs/              Package-specific documentation
  migrations/        SQLite migrations
ts/                  TypeScript package
tui/                 Interactive terminal UI
infra/               Docker, Fly.io, bootstrap scripts
```

## Validation and Development

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest
```

If you change protocol messages, regenerate the derived protocol artifacts from the repo root:

```bash
cd ..
uv run --directory autocontext python scripts/generate_protocol.py
```

## OpenClaw / ClawHub

autocontext exposes:

- artifact contracts for harnesses, policies, and distilled models
- REST and MCP operations for evaluate, validate, publish, import, and discover
- ClawHub skill manifests and scenario discovery metadata
- an adapter layer for running OpenClaw agents inside the harness

## Additional Docs

- [Agent integration guide](docs/agent-integration.md) — CLI-first integration for external agents, MCP fallback, JSON output reference
- [Sandbox modes](docs/sandbox.md)
- [MLX host training](docs/mlx-training.md)
- [Demo data notes](demo_data/README.md)
- [Repository overview](../README.md)
