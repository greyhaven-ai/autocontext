# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

autocontext is an iterative strategy generation and evaluation system. It runs a multi-agent loop where LLM agents collaboratively evolve strategies for pluggable scenarios, scoring them through tournament matches (game scenarios) or LLM judge evaluation (agent task scenarios) with Elo-based progression gating.

## Repository Layout

The Python package lives under `autocontext/` (not the repo root). All `uv`, `pytest`, and `autoctx` CLI commands must be run from the `autocontext/` directory.

```
autocontext/                  # Python package root (pyproject.toml lives here)
  src/autocontext/            # Source code
    agents/                   # LLM agent roles (competitor, analyst, coach, architect, curator)
    knowledge/                # Knowledge processing (trajectory builder, skill export, search, solve-on-demand)
    loop/                     # Generation runner, event emitter
    prompts/                  # Prompt template assembly
    config/                   # Pydantic settings from AUTOCONTEXT_* env vars
    storage/                  # SQLiteStore, ArtifactStore
    scenarios/                # Pluggable scenarios (grid_ctf, othello, custom/, agent tasks)
      custom/               # Natural-language → generated scenario pipeline (spec, codegen, validation, loading)
                            # Also: agent task pipeline (agent_task_designer, agent_task_codegen, agent_task_validator, agent_task_creator)
    execution/                # Execution supervisor, local/remote executors, LLM judge, task runner daemon
    providers/                # Multi-model LLM provider abstraction (Anthropic, OpenAI-compat, callable wrapper)
    notifications/            # Notification webhooks (Slack, HTTP, stdout, callback, composite)
    runtimes/                 # Agent runtime abstraction (Claude CLI, direct API)
    rlm/                      # REPL-loop mode (optional analyst/architect)
    mcp/                      # MCP server, tool implementations, sandbox manager
    server/                   # FastAPI dashboard + WebSocket events
  tests/                      # Pytest tests (~2800 tests)
  migrations/                 # SQLite migration SQL files (001-007, applied in filename order)
  dashboard/                  # Single-page HTML dashboard
  knowledge/                  # Runtime-generated: per-scenario playbooks, analysis, tools, hints, snapshots
  skills/                     # Runtime-generated: operational skill notes per scenario
  runs/                       # Runtime-generated: SQLite DB, event stream, generation artifacts
ts/                           # TypeScript package (autoctx on npm)
  src/                        # Source code
    scenarios/                # Scenario families, codegen, templates, materialization
      codegen/                # V8 isolate code generation for all 11 families (AC-436)
      templates/              # Pre-built scenario templates (AC-443)
    simulation/               # SimulationEngine: run, replay, compare, export, sweep DSL (AC-446)
    investigation/            # InvestigationEngine: evidence-driven diagnosis (AC-447)
    analysis/                 # AnalysisEngine: interpret and compare artifacts (AC-448)
    mission/                  # MissionManager, planner, adaptive executor, campaigns (AC-410, AC-435, AC-428)
    traces/                   # Public trace schema, redaction, export, publishers, data plane (AC-462–466)
    training/                 # Model strategy, backends (MLX/CUDA), prompt alignment, promotion (AC-456–460)
    mcp/                      # MCP server with tool implementations
    cli/                      # CLI entry point with all commands
  tests/                      # Vitest tests (1600+ tests)
  migrations/                 # Shared SQLite migration SQL (cross-compatible with Python)
pi/                           # Pi coding agent extension (@autocontext/pi)
  src/                        # Extension with 5 tools (judge, improve, status, scenarios, queue)
  skills/                     # Autocontext skill for Pi
  prompts/                    # Prompt templates for Pi
infra/                        # Docker, Fly.io config, bootstrap script
scripts/                      # Top-level convenience scripts (demo.sh)
.claude/                      # Claude context, implementation plans, synced skill symlinks
```

## Commands

All commands run from the `autocontext/` directory:

```bash
# Setup
uv venv && source .venv/bin/activate && uv sync --group dev

# Setup with Monty sandbox support (optional)
uv sync --group dev --extra monty

# Lint and type check
uv run ruff check src tests
uv run mypy src

# Tests
uv run pytest                              # all tests
uv run pytest tests/test_elo.py            # single file
uv run pytest tests/test_elo.py -k "test_name"  # single test

# Run (deterministic/offline mode)
AUTOCONTEXT_AGENT_PROVIDER=deterministic uv run autoctx run --scenario grid_ctf --gens 3 --run-id my_run

# Run (live Anthropic mode)
AUTOCONTEXT_AGENT_PROVIDER=anthropic AUTOCONTEXT_ANTHROPIC_API_KEY=... uv run autoctx run --scenario grid_ctf --gens 1

# Run (Agent SDK mode — agents use native tool loops)
AUTOCONTEXT_AGENT_PROVIDER=agent_sdk AUTOCONTEXT_ANTHROPIC_API_KEY=... uv run autoctx run --scenario grid_ctf --gens 3

# Run (RLM mode — REPL-loop agents for analyst/architect)
AUTOCONTEXT_AGENT_PROVIDER=deterministic AUTOCONTEXT_RLM_ENABLED=true uv run autoctx run --scenario grid_ctf --gens 3 --run-id rlm_run

# Run (Monty sandbox executor — pydantic-monty interpreter)
AUTOCONTEXT_AGENT_PROVIDER=deterministic AUTOCONTEXT_EXECUTOR_MODE=monty uv run autoctx run --scenario grid_ctf --gens 3

# Run (RLM with Monty backend — sandboxed REPL)
AUTOCONTEXT_AGENT_PROVIDER=deterministic AUTOCONTEXT_RLM_ENABLED=true AUTOCONTEXT_RLM_BACKEND=monty uv run autoctx run --scenario grid_ctf --gens 3

# Run (Pi CLI — local Pi agent runtime)
AUTOCONTEXT_AGENT_PROVIDER=pi AUTOCONTEXT_PI_COMMAND=pi uv run autoctx run --scenario grid_ctf --gens 3

# Run (Pi RPC — remote Pi agent via HTTP)
AUTOCONTEXT_AGENT_PROVIDER=pi-rpc AUTOCONTEXT_PI_RPC_ENDPOINT=http://localhost:3284 uv run autoctx run --scenario grid_ctf --gens 3

# Ecosystem mode (alternate providers across cycles, shared knowledge directory)
uv run autoctx ecosystem --scenario grid_ctf --cycles 3 --gens-per-cycle 2 \
  --provider-a anthropic --provider-b agent_sdk --rlm-a --no-rlm-b

# Other CLI commands
uv run autoctx list                            # list recent runs
uv run autoctx status <run_id>                 # generation-level status
uv run autoctx replay <run_id> --generation 1  # print replay JSON
uv run autoctx benchmark --scenario grid_ctf --runs 5
uv run autoctx serve --host 127.0.0.1 --port 8000  # dashboard + API

# MCP server (stdio, for Claude Code integration)
uv run autoctx mcp-serve

# Bootstrap + demo from repo root
bash infra/scripts/bootstrap.sh
bash scripts/demo.sh
```

## Architecture

### Generation Loop (`loop/generation_runner.py`)

Each generation: load scenario + knowledge → build score trajectory → orchestrate agents (competitor first, analyst/coach/architect in parallel, optional curator) → tournament matches with Elo → backpressure gate (`advance`/`retry`/`rollback`) → curator quality gate (`accept`/`reject`/`merge`) → persist to SQLite + artifacts → periodic lesson consolidation → cross-run snapshot on completion. Runs are idempotent; playbook updates only persist on `advance`.

### Agent Roles (`agents/`)

- **Competitor** — Produces JSON strategy (or executable Python code when `AUTOCONTEXT_CODE_STRATEGIES_ENABLED=true`)
- **Translator** — Extracts structured strategy from competitor output
- **Analyst** — Produces markdown analysis (Findings, Root Causes, Recommendations)
- **Coach** — Updates the accumulated playbook; output delimited by `<!-- PLAYBOOK_START/END -->`, `<!-- LESSONS_START/END -->`, `<!-- COMPETITOR_HINTS_START/END -->`
- **Architect** — Proposes tooling improvements, persists generated tools to `knowledge/<scenario>/tools/`
- **Curator** — Quality gate for playbook updates + lesson consolidation; uses `<!-- CURATOR_DECISION: accept|reject|merge -->` markers

Agent SDK provider (`AUTOCONTEXT_AGENT_PROVIDER=agent_sdk`) uses `claude_agent_sdk.query()` with native tool loops and per-role tool permissions.

### Providers (`providers/`)

Pluggable LLM providers: `AnthropicProvider`, `OpenAICompatibleProvider` (vLLM, Ollama), `CallableProvider` (testing), `RetryProvider` (decorator with exponential backoff). Factory: `create_provider()` / `get_provider(settings)`. Controlled by `AUTOCONTEXT_JUDGE_PROVIDER`.

### RLM — REPL-Loop Mode (`rlm/`)

Optional (`AUTOCONTEXT_RLM_ENABLED=true`): replaces single-shot analyst/architect with multi-turn REPL sessions. `RlmSession` drives conversation loops, `ReplWorker` provides a sandboxed Python REPL, `MontyReplWorker` is an alternative backend (`AUTOCONTEXT_RLM_BACKEND=monty`).

### Scenarios (`scenarios/`)

Dual-interface registry (`SCENARIO_REGISTRY` in `scenarios/__init__.py`):
- **Game scenarios** — `ScenarioInterface` ABC (`execute_match`, `describe_rules`, etc.). Built-in: `grid_ctf`, `othello`.
- **Agent task scenarios** — `AgentTaskInterface` ABC (`evaluate_output`, `get_task_prompt`, `revise_output`, etc.). Evaluated by LLM judge.

Code accessing the registry uses `hasattr`/`getattr` guards for the dual-interface pattern.

**Custom creation** (`scenarios/custom/`): natural-language → LLM designer → spec → codegen → validation → dynamic loading → registration. Both game scenarios and agent tasks have parallel pipelines. Persisted to `knowledge/_custom_scenarios/`.

### Execution (`execution/`)

- **LocalExecutor** — Subprocess execution with timeout/memory limits
- **PrimeIntellectExecutor** — Remote sandbox via PrimeIntellect SDK
- **MontyExecutor** — Sandboxed via pydantic-monty (`AUTOCONTEXT_EXECUTOR_MODE=monty`); supports JSON and code strategies
- **LLMJudge** — Multi-sample LLM evaluation with 4-tier fallback parser for score extraction
- **JudgeExecutor** — Runs context preparation + validation before judge evaluation
- **ImprovementLoop** — Multi-step evaluate→revise loop with parse-failure resilience
- **TaskRunner** — Daemon polling SQLite task queue, runs `ImprovementLoop` per task

### Knowledge System (`knowledge/`)

Per-scenario directory (`knowledge/<scenario>/`) stores: `playbook.md` (versioned, with rollback), `hints.md` (coach hints, persist across restarts), `analysis/gen_N.md`, `tools/` (architect-generated, old versions in `_archive/`), `snapshots/<run_id>/` (cross-run inheritance), `_custom_scenarios/`, `_agent_tasks/`. Score trajectory is injected into all agent prompts. Curator periodically consolidates lessons.

**Knowledge API** (`knowledge/export.py`, `search.py`, `solver.py`): skill export as portable markdown+JSON packages, TF-IDF strategy search, solve-on-demand. Exposed via MCP tools (`autocontext_*` prefix — see `mcp/server.py`) and REST under `/api/knowledge/`.

### Storage, Server, MCP

- **SQLiteStore** / **ArtifactStore** — SQLite for structured data (runs, generations, matches, feedback, task queue; migrations 001-007), filesystem for artifacts (playbooks, tools, snapshots). Skill notes synced to `.claude/skills/` via symlinks.
- **FastAPI** (`server/app.py`) — REST + WebSocket for runs, knowledge API, scenario creation, event streaming.
- **MCP server** (`mcp/`) — Stdio-based; `tools.py` (pure sync) + `server.py` (`@server.tool()` wrappers). CLI: `uv run autoctx mcp-serve`.
- **Ecosystem** (`loop/ecosystem_runner.py`) — Alternates provider modes across cycles sharing the knowledge directory.
- **Notifications** (`notifications/`) — Stdout, HTTP, Slack, callback, composite notifiers for task runner events.

## Configuration

All config via `AUTOCONTEXT_*` env vars, loaded in `config/settings.py` as Pydantic `AppSettings`. See that file for the full list. Key groups:

- **Provider**: `AUTOCONTEXT_AGENT_PROVIDER` (`deterministic`/`anthropic`/`agent_sdk`/`pi`/`pi-rpc`/`openai`/`ollama`/`vllm`), `AUTOCONTEXT_MODEL_*` (per-role model selection)
- **Execution**: `AUTOCONTEXT_EXECUTOR_MODE` (`local`/`primeintellect`/`monty`), `AUTOCONTEXT_MATCHES_PER_GENERATION`, `AUTOCONTEXT_CODE_STRATEGIES_ENABLED`
- **Loop tuning**: `AUTOCONTEXT_BACKPRESSURE_MIN_DELTA`, `AUTOCONTEXT_MAX_RETRIES`, `AUTOCONTEXT_ARCHITECT_EVERY_N_GENS`
- **Curator**: `AUTOCONTEXT_CURATOR_ENABLED`, `AUTOCONTEXT_CURATOR_CONSOLIDATE_EVERY_N_GENS`, `AUTOCONTEXT_SKILL_MAX_LESSONS`
- **Knowledge**: `AUTOCONTEXT_CROSS_RUN_INHERITANCE`, `AUTOCONTEXT_PLAYBOOK_MAX_VERSIONS`, `AUTOCONTEXT_ABLATION_NO_FEEDBACK`
- **RLM**: `AUTOCONTEXT_RLM_ENABLED`, `AUTOCONTEXT_RLM_BACKEND`, `AUTOCONTEXT_RLM_MAX_TURNS`, `AUTOCONTEXT_RLM_SUB_MODEL`
- **Judge**: `AUTOCONTEXT_JUDGE_PROVIDER`, `AUTOCONTEXT_JUDGE_MODEL`, `AUTOCONTEXT_JUDGE_SAMPLES`, `AUTOCONTEXT_JUDGE_TEMPERATURE`, `AUTOCONTEXT_JUDGE_BASE_URL`, `AUTOCONTEXT_JUDGE_API_KEY`
- **Pi**: `AUTOCONTEXT_PI_COMMAND`, `AUTOCONTEXT_PI_TIMEOUT`, `AUTOCONTEXT_PI_WORKSPACE`, `AUTOCONTEXT_PI_MODEL`
- **Pi RPC**: `AUTOCONTEXT_PI_RPC_ENDPOINT`, `AUTOCONTEXT_PI_RPC_API_KEY`, `AUTOCONTEXT_PI_RPC_SESSION_PERSISTENCE`
- **Notifications**: `AUTOCONTEXT_NOTIFY_WEBHOOK_URL`, `AUTOCONTEXT_NOTIFY_ON`

## Code Style

- Python 3.11+, managed with `uv` and `hatchling` build backend
- Ruff for linting (rules: E, F, I, B, UP), line length 130
- Mypy with `disallow_untyped_defs`, excludes tests and migrations
- Dataclasses with `slots=True` for value types, Pydantic `BaseModel` for validated models
- CLI via Typer, Rich for terminal output

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs: ruff check, mypy, pytest, deterministic smoke runs for both scenarios (`grid_ctf` 3 gens, `othello` 1 gen), and dashboard API health check. A separate `primeintellect-live` job runs when secrets are available. Monty-specific tests (`test_monty_*.py`) are skipped in CI when pydantic-monty is not installed (`pytest.mark.skipif`).

## TypeScript Package (`ts/`)

Published as `autoctx` on npm. ESM-only, strict TypeScript, Node.js >=18.

```bash
cd ts
npm install
npm run lint          # tsc --noEmit
npm test              # vitest run (1600+ tests)
npm run build         # tsc (outputs to dist/)

# Core commands
autoctx run --scenario grid_ctf --gens 3
autoctx judge -p <task-prompt> -o <agent-output> -r <rubric>
autoctx improve -p <task-prompt> -o <initial-output> -r <rubric>

# Execution surfaces
autoctx simulate -d "simulate a deployment pipeline with rollback"
autoctx simulate --replay <id> --variables threshold=0.9
autoctx simulate --compare-left sim_a --compare-right sim_b
autoctx investigate -d "why did conversion drop after the release"
autoctx analyze --id <artifact-id> --type simulation
autoctx analyze --left <id> --right <id> --type simulation

# Missions
autoctx mission create --name "Ship OAuth" --goal "Implement login"
autoctx mission run --id <mission-id>

# Training
autoctx train --scenario grid_ctf --dataset train.jsonl --backend cuda

# Scenario management
autoctx new-scenario --description "test error handling in APIs"
autoctx new-scenario --template content-generation --name my_task

# Infrastructure
autoctx serve              # HTTP API server (REST + WebSocket)
autoctx tui                # Interactive terminal UI
autoctx mcp-serve          # MCP server on stdio
```

Environment variables: `ANTHROPIC_API_KEY` (required for LLM features), `AUTOCONTEXT_MODEL` (default `claude-sonnet-4-20250514`), `AUTOCONTEXT_DB_PATH` (default `./autocontext.db`).

Mirrors and extends the Python architecture. Migrations in `ts/migrations/` are cross-compatible with Python.
