# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MTS (MTS Control Plane) is an iterative strategy generation and evaluation system. It runs a multi-agent loop where LLM agents collaboratively evolve strategies for pluggable game scenarios, scoring them through tournament matches with Elo-based progression gating.

## Repository Layout

The Python package lives under `mts/` (not the repo root). All `uv`, `pytest`, and `mts` CLI commands must be run from the `mts/` directory.

```
mts/                          # Python package root (pyproject.toml lives here)
  src/mts/                    # Source code
  tests/                      # Pytest tests
  migrations/                 # SQLite migration SQL files (applied in filename order)
  dashboard/                  # Single-page HTML dashboard
  knowledge/                  # Runtime-generated: per-scenario playbooks, analysis, tools
  skills/                     # Runtime-generated: operational skill notes per scenario
  runs/                       # Runtime-generated: SQLite DB, event stream, generation artifacts
infra/                        # Docker, Fly.io config, bootstrap script
scripts/                      # Top-level convenience scripts (demo.sh)
.claude/                      # Claude context, implementation plans, synced skill symlinks
```

## Commands

All commands run from the `mts/` directory:

```bash
# Setup
uv venv && source .venv/bin/activate && uv sync --group dev

# Lint and type check
uv run ruff check src tests
uv run mypy src

# Tests
uv run pytest                              # all tests
uv run pytest tests/test_elo.py            # single file
uv run pytest tests/test_elo.py -k "test_name"  # single test

# Run (deterministic/offline mode)
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 3 --run-id my_run

# Run (live Anthropic mode)
MTS_AGENT_PROVIDER=anthropic MTS_ANTHROPIC_API_KEY=... uv run mts run --scenario grid_ctf --gens 1

# Run (RLM mode — REPL-loop agents for analyst/architect)
MTS_AGENT_PROVIDER=deterministic MTS_RLM_ENABLED=true uv run mts run --scenario grid_ctf --gens 3 --run-id rlm_run

# Other CLI commands
uv run mts list                            # list recent runs
uv run mts status <run_id>                 # generation-level status
uv run mts replay <run_id> --generation 1  # print replay JSON
uv run mts benchmark --scenario grid_ctf --runs 5
uv run mts serve --host 127.0.0.1 --port 8000  # dashboard + API

# Bootstrap + demo from repo root
bash infra/scripts/bootstrap.sh
bash scripts/demo.sh
```

## Architecture

### Generation Loop (`loop/generation_runner.py`)

The core loop drives everything. For each generation within a run:

1. **Scenario setup** — Load scenario, create initial state, read accumulated playbook and tool context from the knowledge directory
2. **Agent orchestration** — `AgentOrchestrator` runs four LLM roles (competitor runs first, then analyst/coach/architect in parallel via `ThreadPoolExecutor`)
3. **Tournament** — `TournamentRunner` executes N matches (default 3) through `ExecutionSupervisor`, scoring with Elo updates
4. **Backpressure gate** — `BackpressureGate` decides `advance`/`retry`/`rollback` based on score delta vs threshold (`MTS_BACKPRESSURE_MIN_DELTA`)
5. **Persistence** — Results, metrics, replays, agent outputs, and recovery markers are saved to SQLite and the filesystem artifact store

Runs are idempotent — `generation_exists()` check skips already-completed generations on resume. Playbook updates only persist on `advance` gate decisions.

### Agent Roles (`agents/`)

All roles use `SubagentRuntime` wrapping a `LanguageModelClient` (Anthropic API or `DeterministicDevClient` for offline/CI):

- **Competitor** — Produces a JSON strategy dict matching the scenario's strategy interface. Runs first (sequentially) since its output feeds into tournament scoring.
- **Analyst** — Produces markdown analysis (Findings, Root Causes, Recommendations)
- **Coach** — Updates the accumulated playbook (Strategy Updates, Prompt Optimizations, Next Gen Checklist). Output parsed via `<!-- PLAYBOOK_START/END -->` and `<!-- LESSONS_START/END -->` delimiters.
- **Architect** — Proposes tooling improvements + emits a `{"tools": [...]}` JSON block that gets persisted as Python files in `knowledge/<scenario>/tools/`

The architect only intervenes fully every N generations (`MTS_ARCHITECT_EVERY_N_GENS`, default 3). The `LanguageModelClient` base class provides both single-turn `generate()` and multi-turn `generate_multiturn()` methods.

### RLM — REPL-Loop Mode (`rlm/`)

Optional mode (`MTS_RLM_ENABLED=true`) that replaces the single-shot analyst and architect with multi-turn REPL sessions where the LLM can iteratively explore data by writing Python code.

- **RlmSession** — Drives a conversation loop: sends messages to the LLM, extracts code from `<code>` tags, executes it in a `ReplWorker`, feeds stdout/errors back as user messages. The loop ends when the model sets `answer["ready"] = True` or hits `max_turns`.
- **ReplWorker** — In-process Python REPL with a restricted namespace (no file I/O, no `os`/`subprocess`/`import`). Pre-populated with safe stdlib modules (`json`, `math`, `statistics`, `collections`, `re`, `time`) and an `answer` dict. Enforces wall-clock timeout via `SIGALRM` (main thread) or daemon thread (worker threads).
- **ContextLoader** — Loads run data (replays, metrics, match scores, playbook, prior analyses, existing tools) into the REPL namespace as Python variables for exploration.
- **`llm_batch()`** — Injected callable that lets REPL code make batched LLM sub-calls (uses `MTS_RLM_SUB_MODEL`).

When RLM is enabled, `AgentOrchestrator._run_rlm_roles()` runs analyst and architect as RLM sessions sequentially, while coach still runs via the standard single-shot path.

### Scenarios (`scenarios/`)

Pluggable via `SCENARIO_REGISTRY` dict in `scenarios/__init__.py`. Each scenario implements `ScenarioInterface` (ABC) with methods: `initial_state`, `get_observation`, `validate_actions`, `step`, `is_terminal`, `get_result`, `execute_match`, etc.

Current scenarios: `grid_ctf`, `othello`. To add a new scenario, implement `ScenarioInterface` and register it in `SCENARIO_REGISTRY`.

### Execution (`execution/`)

- **ExecutionSupervisor** — Data-plane boundary wrapping an `ExecutionEngine` protocol
- **LocalExecutor** — Runs strategy in a subprocess (`ProcessPoolExecutor`) with timeout and memory limits; falls back to `ThreadPoolExecutor` if process semaphores are blocked
- **PrimeIntellectExecutor** — Runs remotely via PrimeIntellect sandbox SDK (create/wait/execute/delete lifecycle)

### Storage

- **SQLiteStore** (`storage/sqlite_store.py`) — Runs, generations, matches, agent outputs, role metrics, recovery markers. Migrations applied from `migrations/*.sql` in filename order.
- **ArtifactStore** (`storage/artifacts.py`) — Filesystem persistence: generation metrics/replays under `runs/<run_id>/generations/`, playbooks/analysis/tools under `knowledge/<scenario>/`, skill notes under `skills/`. Syncs skill notes to `.claude/skills/` via symlinks.

### Dashboard & Events

- **FastAPI server** (`server/app.py`) — REST endpoints (`/api/runs`, `/api/runs/{id}/status`, `/api/runs/{id}/replay/{gen}`) + WebSocket (`/ws/events`) streaming from ndjson event file + `/health` endpoint
- **EventStreamEmitter** (`loop/events.py`) — Appends ndjson events to `runs/events.ndjson`

## Configuration

All config via `MTS_*` environment variables, loaded in `config/settings.py` into a Pydantic `AppSettings` model. Key settings:

- `MTS_AGENT_PROVIDER`: `deterministic` (offline/CI) or `anthropic` (live)
- `MTS_EXECUTOR_MODE`: `local` or `primeintellect`
- `MTS_MODEL_*`: per-role model selection (competitor, analyst, coach, architect)
- `MTS_MATCHES_PER_GENERATION`, `MTS_BACKPRESSURE_MIN_DELTA`, `MTS_MAX_RETRIES`, `MTS_ARCHITECT_EVERY_N_GENS`: loop tuning
- `MTS_RLM_ENABLED`: enable REPL-loop mode for analyst/architect (default `false`)
- `MTS_RLM_MAX_TURNS`, `MTS_RLM_MAX_STDOUT_CHARS`, `MTS_RLM_SUB_MODEL`, `MTS_RLM_CODE_TIMEOUT_SECONDS`: RLM tuning

## Code Style

- Python 3.11+, managed with `uv` and `hatchling` build backend
- Ruff for linting (rules: E, F, I, B, UP), line length 130
- Mypy with `disallow_untyped_defs`, excludes tests and migrations
- Dataclasses with `slots=True` for value types, Pydantic `BaseModel` for validated models
- CLI via Typer, Rich for terminal output

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs: ruff check, mypy, pytest, deterministic smoke runs for both scenarios (`grid_ctf` 3 gens, `othello` 1 gen), and dashboard API health check. A separate `primeintellect-live` job runs when secrets are available.
