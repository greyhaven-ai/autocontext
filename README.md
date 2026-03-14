# autocontext

autocontext is a closed-loop system for improving agent behavior over repeated runs.

It executes tasks, evaluates outcomes, updates persistent knowledge, and optionally distills successful behavior into cheaper local runtimes. The goal is to move from frontier-model exploration toward validated, reusable, lower-cost execution.

## Why It Exists

Most agent systems start every run cold. They do not reliably carry forward what worked, what failed, and what should change next.

autocontext adds that missing feedback loop:

- run the task
- analyze what happened
- persist validated lessons
- use those lessons in the next run
- optionally train and route to local models when the task is stable enough

## How It Works

Each generation runs through a structured multi-agent loop:

- `competitor` proposes a strategy or artifact for the task
- `analyst` explains what happened and why
- `coach` turns that analysis into playbook updates and future hints
- `architect` proposes tools, harness improvements, or structural changes
- `curator` gates what knowledge is allowed to persist
- `librarian` (per-book) reviews strategies against ingested literature, advises, and flags violations
- `archivist` arbitrates librarian escalations — spot-pulls original passages and decides: dismiss, soft-flag, or hard-gate

Strategies are then evaluated through scenario execution, staged validation, and gating. Librarian violations can trigger hard gates that force retries before tournament matches. Weak changes are rolled back. Successful changes accumulate into reusable knowledge.

## Core Capabilities

- Persistent playbooks, hints, tools, reports, and progress snapshots across runs
- Staged validation, harness synthesis, and harness-aware execution
- Literature-aware advisory and gating via librarian/archivist agents bound to ingested books
- Frontier-to-local distillation with MLX on Apple Silicon
- Runtime routing across Anthropic, OpenAI-compatible backends, Ollama, vLLM, MLX, and Pi-based runtimes
- OpenClaw-facing APIs and agent integration surfaces
- CLI, API server, dashboard, and TypeScript/TUI surfaces for operators and external agents

## Quick Start

The Python application lives in `autocontext/`, and most `uv`, `pytest`, `ruff`, and `mypy` commands should be run from there.

```bash
cd autocontext
uv venv
source .venv/bin/activate
uv sync --group dev

AUTOCONTEXT_AGENT_PROVIDER=deterministic uv run autoctx run \
  --scenario grid_ctf \
  --gens 3 \
  --run-id quickstart
```

That creates a local run, writes artifacts under `runs/` and `knowledge/`, and works without external API keys.

Run with Anthropic:

```bash
cd autocontext
AUTOCONTEXT_AGENT_PROVIDER=anthropic \
AUTOCONTEXT_ANTHROPIC_API_KEY=your-key \
uv run autoctx run --scenario grid_ctf --gens 3
```

Start the API server and dashboard:

```bash
cd autocontext
uv run autoctx serve --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000`.

## Common Workflows

- Run the generation loop: `uv run autoctx run --scenario grid_ctf --gens 3`
- Inspect runs: `uv run autoctx list`, `uv run autoctx status <run_id>`
- Ingest a book: `uv run autoctx add-book path/to/book.md --title "Clean Architecture" --tag architecture`
- List library: `uv run autoctx list-books`
- Remove a book: `uv run autoctx remove-book clean-architecture`
- Export training data: `uv run autoctx export-training-data --scenario grid_ctf --all-runs --output training/grid_ctf.jsonl`
- Train a local model: `uv run autoctx train --scenario grid_ctf --data training/grid_ctf.jsonl --time-budget 300`
- Start the API server: `uv run autoctx serve --host 127.0.0.1 --port 8000`
- Start the MCP server: `uv run autoctx mcp-serve`

MLX training is host-only on Apple Silicon macOS. If you want a sandboxed OpenClaw agent to trigger training, use the file-based host watcher flow documented in [autocontext/docs/mlx-training.md](autocontext/docs/mlx-training.md).

## Repository Layout

- `autocontext/`: Python package, CLI, API server, dashboard, training loop
- `ts/`: TypeScript package and CLI/MCP-compatible tooling
- `tui/`: interactive terminal UI
- `infra/`: Docker, Fly.io, and bootstrap scripts
- `docs/`: higher-level repo notes

## Where To Look Next

- Package and CLI guide: [autocontext/README.md](autocontext/README.md)
- Contributor setup: [CONTRIBUTING.md](CONTRIBUTING.md)
- MLX host training and OpenClaw bridge: [autocontext/docs/mlx-training.md](autocontext/docs/mlx-training.md)
- Sandbox and executor notes: [autocontext/docs/sandbox.md](autocontext/docs/sandbox.md)
- License: [LICENSE](LICENSE)

## Note

This repo was previously named `MTS`. Some historical references may still use the older name or issue prefixes.
