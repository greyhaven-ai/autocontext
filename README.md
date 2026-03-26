<!-- autocontext-readme-hero:start -->
<p align="center">
  <img src="autocontext/assets/banner.svg" alt="autocontext ASCII banner" />
</p>

<p align="center"><strong>closed-loop control plane for agent improvement</strong></p>
<!-- autocontext-readme-hero:end -->

autocontext is a closed-loop control plane for improving agent behavior over repeated runs.

It executes tasks, evaluates outcomes, updates persistent knowledge, and can distill successful behavior into cheaper local runtimes. The goal is to move from frontier-model exploration toward validated, reusable, lower-cost execution.

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

Strategies are then evaluated through scenario execution, staged validation, and gating. Weak changes are rolled back. Successful changes accumulate into reusable knowledge.

## Choose An Entry Point

- Want the full control plane, dashboard, scenario runner, and training loop? Start with the Python package in `autocontext/`.
- Want a lighter Node/TypeScript toolkit for judging outputs, running improvement loops, queueing work, or exposing MCP tools? Start with `ts/`.
- Want to wire another agent into autocontext? Start with the CLI-first guide in `autocontext/docs/agent-integration.md`.
- Want to contribute or point a coding agent at the repo? Read `CONTRIBUTING.md` and `AGENTS.md`.

<!-- autocontext-whats-new:start -->
## What's New

- GEPA-inspired ASI/Pareto optimizer wired into improvement loop
- Component sensitivity profiling and credit assignment
- Pluggable scoring backends with Elo and Glicko support
- Novelty exploration and multi-basin playbook branching
- Cost-aware loop control and long-run presets
<!-- autocontext-whats-new:end -->

## Core Capabilities

- Persistent playbooks, hints, tools, reports, and progress snapshots across runs
- Staged validation, harness synthesis, and harness-aware execution
- Scenario families for simulation, investigation, workflow, coordination, negotiation, artifact editing, operator-in-the-loop, tool-fragility, and schema-evolution tasks
- Frontier-to-local distillation with MLX on Apple Silicon
- Runtime routing across Anthropic, OpenAI-compatible backends, Ollama, vLLM, MLX, and Pi-based runtimes
- OpenClaw-facing APIs and agent integration surfaces
- CLI, API server, dashboard, and TypeScript/TUI surfaces for operators and external agents

## Quick Start From Source

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

Use the repo-level `.env.example` as the reference for available `AUTOCONTEXT_*` settings.

## Installable Packages

The repo publishes two installable packages with different scopes:

- Python package: `pip install autoctx`
- TypeScript package: `npm install autoctx`

Important: the npm package for this project is `autoctx`.
`autocontext` on npm is a different package.

The Python package exposes the full `autoctx` control-plane CLI (`run`, `serve`, `mcp-serve`, `train`, `new-scenario`, `export`, `wait`, and more). The TypeScript package exposes a narrower `autoctx` CLI focused on evaluation, improvement loops, queueing, and MCP serving for Node runtimes.

## Which Package Should You Use?

| If you want to... | Start here | Why |
|---|---|---|
| Run the full multi-generation control plane | [autocontext/README.md](autocontext/README.md) | Python has the dashboard, API server, training loop, scenario scaffolding, export/import, and full CLI surface. |
| Embed judging or improvement loops in a Node app | [ts/README.md](ts/README.md) | The TypeScript package is smaller and focused on judge-based workflows, queueing, and MCP serving. |
| Point an external agent at autocontext | [autocontext/docs/agent-integration.md](autocontext/docs/agent-integration.md) | It documents the CLI-first contract, JSON output, MCP usage, and SDK options. |
| Grab copy-paste integration snippets | [examples/README.md](examples/README.md) | The examples cover Python CLI, Claude Code MCP, Python SDK, and TypeScript library usage. |
| Catch up on recent repo evolution | [CHANGELOG.md](CHANGELOG.md) | It summarizes the `v0.2.0` release and current unreleased work. |

## Common Workflows

- Run the generation loop: `uv run autoctx run --scenario grid_ctf --gens 3`
- Inspect runs: `uv run autoctx list`, `uv run autoctx status <run_id>`
- Scaffold a custom scenario: `uv run autoctx new-scenario --template prompt-optimization --name my-task`
- Export training data: `uv run autoctx export-training-data --scenario grid_ctf --all-runs --output training/grid_ctf.jsonl`
- Train a local model: `uv run autoctx train --scenario grid_ctf --data training/grid_ctf.jsonl --time-budget 300`
- Start the API server: `uv run autoctx serve --host 127.0.0.1 --port 8000`
- Start the MCP server: `uv run autoctx mcp-serve`
- Wait on a monitor condition: `uv run autoctx wait <condition_id> --json`

`operator-in-the-loop` remains a typed scenario family for capability discovery and experimentation, but autocontext does not scaffold executable operator-loop runtimes. Use datasets, tools, or live-agent experiments instead of harness-owned escalation scripts.

MLX training is host-only on Apple Silicon macOS. If you want a sandboxed OpenClaw agent to trigger training, use the file-based host watcher flow documented in [autocontext/docs/mlx-training.md](autocontext/docs/mlx-training.md).

## Repository Layout

- `autocontext/`: Python package, CLI, API server, dashboard, training loop
- `ts/`: published TypeScript package, CLI, and MCP-compatible tooling
- `tui/`: interactive terminal UI
- `docs/`: docs landing page and maintainer checklists
- `examples/`: copy-paste integration snippets for package users and external agents
- `infra/`: Docker, Fly.io, and bootstrap scripts
- `protocol/`: shared protocol artifacts
- `scripts/`: repo maintenance and generation scripts

## Where To Look Next

- Docs overview: [docs/README.md](docs/README.md)
- Analytics and adoption: [docs/analytics.md](docs/analytics.md)
- Python package guide: [autocontext/README.md](autocontext/README.md)
- TypeScript package guide: [ts/README.md](ts/README.md)
- Copy-paste examples: [examples/README.md](examples/README.md)
- External agent integration: [autocontext/docs/agent-integration.md](autocontext/docs/agent-integration.md)
- Recent changes: [CHANGELOG.md](CHANGELOG.md)
- Contributor setup: [CONTRIBUTING.md](CONTRIBUTING.md)
- Repo agent guide: [AGENTS.md](AGENTS.md)
- MLX host training and OpenClaw bridge: [autocontext/docs/mlx-training.md](autocontext/docs/mlx-training.md)
- Sandbox and executor notes: [autocontext/docs/sandbox.md](autocontext/docs/sandbox.md)
- License: [LICENSE](LICENSE)


## Project Signals

[![npm downloads](https://img.shields.io/npm/dm/autoctx?logo=npm&label=npm%20downloads)](https://www.npmjs.com/package/autoctx)
[![PyPI downloads](https://img.shields.io/pypi/dm/autoctx?logo=pypi&label=PyPI%20downloads)](https://pypi.org/project/autoctx/)

[![Star History Chart](https://api.star-history.com/svg?repos=greyhaven-ai/autocontext&type=Date)](https://www.star-history.com/#greyhaven-ai/autocontext&Date)
