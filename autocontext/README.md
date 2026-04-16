# autocontext

autocontext is the Python control-plane package for running scenarios, carrying forward validated knowledge, exporting artifacts, and distilling stable behavior into cheaper runtimes over time.

The intended use is to hand the harness a real task in plain language, let it solve or simulate the problem mostly hands-off, and then inspect the resulting traces, reports, playbooks, datasets, and optional distilled model.

## Install

```bash
pip install autocontext
```

The current PyPI release line is `autocontext==0.4.1`.
The PyPI package name is now `autocontext`. The CLI entrypoint remains `autoctx`.

## Working Directory

Run the commands in this README from the `autocontext/` directory. The Python package, CLI entrypoint, tests, and migrations all live here.

## What It Does

- Runs iterative generation loops against game scenarios and agent-task scenarios
- Adds a first-class `simulate` surface for modeled-world exploration, replay, compare, and export
- Persists playbooks, hints, tools, reports, and snapshots across runs
- Supports staged validation, harness synthesis, and harness-aware routing
- Exports training data and runs autoresearch-style local training loops
- Exposes evaluation, validation, artifact, and discovery operations over MCP and HTTP

## Surface Summary

The Python package is the full control-plane surface in this repo. It currently includes:

- generation-loop execution via `autoctx run`
- plain-language simulation via `autoctx simulate`
- plain-language investigation via `autoctx investigate`
- local training workflows via `autoctx export-training-data` and `autoctx train`
- scenario creation and materialization via `autoctx new-scenario`
- HTTP API and MCP server surfaces via `autoctx serve` and `autoctx mcp-serve`

Some newer operator-facing surfaces are currently TypeScript-first:

- `autoctx analyze`
- the interactive terminal UI via `npx autoctx tui`

`campaign` currently lives in that same bucket: it has partial TypeScript CLI/API/MCP support, but the Python package does not expose a campaign control-plane workflow yet.

## Quick Start

From the repo root:

```bash
cd autocontext
uv venv
source .venv/bin/activate
uv sync --group dev
```

Use the repo-level `.env.example` as the reference for available `AUTOCONTEXT_*` settings and supported provider-native credential aliases such as `ANTHROPIC_API_KEY`.

`operator-in-the-loop` is a runnable scenario family for escalation and clarification experiments. Use it when you want executable operator-loop simulations, judgment evaluation, and live-agent escalation workflow testing.

Run a deterministic local scenario:

```bash
AUTOCONTEXT_AGENT_PROVIDER=deterministic \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
```

Run with Anthropic:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic \
ANTHROPIC_API_KEY=... \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
```

`ANTHROPIC_API_KEY` is the preferred Anthropic credential env var. `AUTOCONTEXT_ANTHROPIC_API_KEY` remains supported as a compatibility alias.

Run with Claude CLI (`claude -p` via a local authenticated Claude Code runtime):

```bash
AUTOCONTEXT_AGENT_PROVIDER=claude-cli \
AUTOCONTEXT_CLAUDE_MODEL=sonnet \
AUTOCONTEXT_CLAUDE_TIMEOUT=300 \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
```

For longer `autoctx judge` / `autoctx improve` prompts on `claude-cli`, use `--timeout <seconds>` or set `AUTOCONTEXT_CLAUDE_TIMEOUT`.

Run with Codex CLI (`codex exec` via a local authenticated Codex runtime):

```bash
AUTOCONTEXT_AGENT_PROVIDER=codex \
AUTOCONTEXT_CODEX_MODEL=o4-mini \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
```

Run with Pi CLI (local Pi agent runtime):

```bash
AUTOCONTEXT_AGENT_PROVIDER=pi \
AUTOCONTEXT_PI_COMMAND=pi \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
```

`autoctx simulate` now follows the effective architect-role runtime surface, so `AUTOCONTEXT_ARCHITECT_PROVIDER`, other role-routing overrides, and per-call `--provider <name>` overrides all apply to live simulation generation.

`autoctx investigate` now ships as a first-class Python CLI surface as well. It uses the architect runtime for investigation-spec synthesis and the analyst runtime for hypothesis generation, so role-routing overrides apply there too.

Run with Pi RPC (remote Pi agent via HTTP):

```bash
AUTOCONTEXT_AGENT_PROVIDER=pi-rpc \
AUTOCONTEXT_PI_RPC_ENDPOINT=http://localhost:3284 \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
```

Run with Hermes (via OpenAI-compatible gateway):

```bash
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1 \
AUTOCONTEXT_AGENT_API_KEY=no-key \
AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
```

Start the API server:

```bash
uv run autoctx serve --host 127.0.0.1 --port 8000
```

Inspect `http://127.0.0.1:8000/` for the API index after the server starts. For an interactive terminal UI, use the TypeScript package: `npx autoctx tui`.

Start the MCP server:

```bash
uv sync --group dev --extra mcp
uv run autoctx mcp-serve
```

## Main CLI Commands

```bash
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
uv run autoctx simulate --description "simulate deploying a web service with rollback"
uv run autoctx simulate --description "simulate deploying a web service with rollback" --provider claude-cli
uv run autoctx investigate --description "why did conversion drop after Tuesday's release"
uv run autoctx queue add --task-prompt "Write a 1-line fact about primes" --rubric "correct" --threshold 0.8 --rounds 2
uv run autoctx simulate --replay deploy_sim --variables threshold=0.9
uv run autoctx list
uv run autoctx status <run_id>
uv run autoctx replay <run_id> --generation 1
uv run autoctx run --scenario support_triage --gens 3
uv run autoctx benchmark --scenario support_triage --runs 5
uv run autoctx new-scenario --template prompt-optimization --name support_triage
uv run autoctx export-training-data --scenario support_triage --all-runs --output training/support_triage.jsonl
uv run autoctx train --scenario support_triage --data training/support_triage.jsonl --time-budget 300
uv run autoctx serve --host 127.0.0.1 --port 8000
uv run autoctx mcp-serve
uv run autoctx wait <condition_id> --json
```

Saved custom scenarios under `knowledge/_custom_scenarios/` can be rerun and benchmarked by name once their `spec.json` has been persisted, so the `new-scenario` / `solve` workflow lines up with the named `run` and `benchmark` surfaces.

Useful variants:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic ANTHROPIC_API_KEY=... \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3

AUTOCONTEXT_AGENT_PROVIDER=anthropic \
ANTHROPIC_API_KEY=sk-ant-primary \
AUTOCONTEXT_COMPETITOR_PROVIDER=openai-compatible \
AUTOCONTEXT_COMPETITOR_API_KEY=sk-role \
AUTOCONTEXT_COMPETITOR_BASE_URL=http://localhost:8000/v1 \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3

AUTOCONTEXT_AGENT_PROVIDER=deterministic AUTOCONTEXT_RLM_ENABLED=true \
uv run autoctx solve --description "improve customer-support replies for billing disputes" --gens 3
```

## Training Workflow

Export JSONL training data from completed runs:

```bash
uv run autoctx export-training-data \
  --scenario support_triage \
  --all-runs \
  --output training/support_triage.jsonl
```

Launch the autoresearch-style training loop:

```bash
uv sync --group dev --extra mlx
uv run autoctx train \
  --scenario support_triage \
  --data training/support_triage.jsonl \
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
- `AUTOCONTEXT_PI_TIMEOUT` (defaults to 300 seconds for Pi-backed live runs)
- `AUTOCONTEXT_RLM_ENABLED`
- `AUTOCONTEXT_HARNESS_PREFLIGHT_ENABLED`
- `AUTOCONTEXT_STAGED_VALIDATION_ENABLED`

See the repo-level [.env.example](../.env.example) for a working starting point.

## Repository Structure

```text
autocontext/
  src/autocontext/   Python package
  tests/             Pytest suite
  docs/              Package-specific documentation
  migrations/        SQLite migrations
ts/                  TypeScript package
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

- [Canonical concept model](../docs/concept-model.md)
- [Agent integration guide](docs/agent-integration.md) — CLI-first integration for external agents, MCP fallback, JSON output reference
- [Sandbox modes](docs/sandbox.md)
- [MLX host training](docs/mlx-training.md)
- [TypeScript package guide](../ts/README.md) — `analyze`, mission control, and interactive TUI surfaces
- [Demo data notes](demo_data/README.md)
- [Copy-paste examples](../examples/README.md)
- [Change history](../CHANGELOG.md)
- [Repository overview](../README.md)
