# AutoContext

AutoContext is a harness for improving agent behavior over repeated runs. It combines scenario execution, staged validation, knowledge accumulation, optional distillation, and OpenClaw-facing APIs so a task can move from frontier-model exploration toward cheaper local execution.

## Before You Start

- The Python application lives in `autocontext/`.
- The main CLI is `autoctx`.
- Most `uv`, `pytest`, `ruff`, and `mypy` commands should be run from `autocontext/`.
- Environment variables use the `AUTOCONTEXT_` prefix.

## Repo Layout

- `autocontext/`: Python package, CLI, API server, dashboard, training loop
- `ts/`: TypeScript package and MCP-compatible tooling
- `tui/`: interactive terminal UI
- `infra/`: Docker, Fly.io, and bootstrap scripts
- `docs/`: design notes and historical implementation plans

## Quick Start

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

That command creates a local run, writes artifacts under `runs/` and `knowledge/`, and works without external API keys.

Start the API server and dashboard:

```bash
cd autocontext
uv run autoctx serve --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000`.

## Main Workflows

- Run the generation loop: `uv run autoctx run --scenario grid_ctf --gens 3`
- Inspect runs: `uv run autoctx list`, `uv run autoctx status <run_id>`
- Export training data: `uv run autoctx export-training-data --scenario grid_ctf --all-runs --output training/grid_ctf.jsonl`
- Launch training: `uv run autoctx train --scenario grid_ctf --data training/grid_ctf.jsonl --time-budget 300`
- Start MCP server: `uv run autoctx mcp-serve`

## Other Packages

TypeScript package:

```bash
cd ts
npm install
npm run lint
npm test
```

TUI:

```bash
cd tui
npm install
npm test
```

Bootstrap script:

```bash
bash infra/scripts/bootstrap.sh
```

## Where To Look

- Package and CLI guide: [autocontext/README.md](autocontext/README.md)
- Contributor setup: [CONTRIBUTING.md](CONTRIBUTING.md)
- Sandbox/executor notes: [autocontext/docs/sandbox.md](autocontext/docs/sandbox.md)
- License: [LICENSE](LICENSE)

## Release Note

This repo has been renamed from `MTS` to `AutoContext`. Historical planning docs may still use the older name or issue identifiers.
