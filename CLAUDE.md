# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MTS (MTS Control Plane) is an iterative strategy generation and evaluation system. It runs a multi-agent loop where LLM agents collaboratively evolve strategies for pluggable game scenarios, scoring them through tournament matches with Elo-based progression gating.

## Repository Layout

The Python package lives under `mts/` (not the repo root). All `uv`, `pytest`, and `mts` CLI commands must be run from the `mts/` directory.

```
mts/                          # Python package root (pyproject.toml lives here)
  src/mts/                    # Source code
    agents/                   # LLM agent roles (competitor, analyst, coach, architect, curator)
    knowledge/                # Knowledge processing (trajectory builder, skill export, search, solve-on-demand)
    loop/                     # Generation runner, event emitter
    prompts/                  # Prompt template assembly
    config/                   # Pydantic settings from MTS_* env vars
    storage/                  # SQLiteStore, ArtifactStore
    scenarios/                # Pluggable game scenarios (grid_ctf, othello, custom/)
      custom/               # Natural-language → generated scenario pipeline (spec, codegen, validation, loading)
    execution/                # Execution supervisor, local/remote executors
    rlm/                      # REPL-loop mode (optional analyst/architect)
    mcp/                      # MCP server, tool implementations, sandbox manager
    server/                   # FastAPI dashboard + WebSocket events
  tests/                      # Pytest tests (~330 tests)
  migrations/                 # SQLite migration SQL files (001-004, applied in filename order)
  dashboard/                  # Single-page HTML dashboard
  knowledge/                  # Runtime-generated: per-scenario playbooks, analysis, tools, hints, snapshots
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

# Run (Agent SDK mode — agents use native tool loops)
MTS_AGENT_PROVIDER=agent_sdk MTS_ANTHROPIC_API_KEY=... uv run mts run --scenario grid_ctf --gens 3

# Run (RLM mode — REPL-loop agents for analyst/architect)
MTS_AGENT_PROVIDER=deterministic MTS_RLM_ENABLED=true uv run mts run --scenario grid_ctf --gens 3 --run-id rlm_run

# Ecosystem mode (alternate providers across cycles, shared knowledge directory)
uv run mts ecosystem --scenario grid_ctf --cycles 3 --gens-per-cycle 2 \
  --provider-a anthropic --provider-b agent_sdk --rlm-a --no-rlm-b

# Other CLI commands
uv run mts list                            # list recent runs
uv run mts status <run_id>                 # generation-level status
uv run mts replay <run_id> --generation 1  # print replay JSON
uv run mts benchmark --scenario grid_ctf --runs 5
uv run mts serve --host 127.0.0.1 --port 8000  # dashboard + API

# MCP server (stdio, for Claude Code integration)
uv run mts mcp-serve

# Bootstrap + demo from repo root
bash infra/scripts/bootstrap.sh
bash scripts/demo.sh
```

## Architecture

### Generation Loop (`loop/generation_runner.py`)

The core loop drives everything. For each generation within a run:

1. **Scenario setup** — Load scenario, create initial state, read accumulated playbook, tool context, hints, and latest analysis from the knowledge directory
2. **Knowledge injection** — `ScoreTrajectoryBuilder` builds score trajectory and strategy registry tables. Latest advance analysis and persisted hints are loaded. All suppressed when `ablation_no_feedback` is set.
3. **Agent orchestration** — `AgentOrchestrator` runs five LLM roles (competitor runs first, then analyst/coach/architect in parallel via `ThreadPoolExecutor`, then optional curator post-processing)
4. **Tournament** — `TournamentRunner` executes N matches (default 3) through `ExecutionSupervisor`, scoring with Elo updates
5. **Backpressure gate** — `BackpressureGate` decides `advance`/`retry`/`rollback` based on score delta vs threshold (`MTS_BACKPRESSURE_MIN_DELTA`)
6. **Curator quality gate** — If enabled and gate is `advance`, `KnowledgeCurator` compares current vs proposed playbook and decides `accept`/`reject`/`merge`. Rejected playbooks are not persisted; merged playbooks replace the coach output.
7. **Persistence** — Results, metrics, replays, versioned playbooks, agent outputs, and recovery markers are saved to SQLite and the filesystem artifact store
8. **Curator lesson consolidation** — Every N generations (default 3), if lessons exceed `skill_max_lessons`, the curator deduplicates and prunes SKILL.md lessons
9. **Cross-run snapshot** — On run completion, playbook + hints + skills are snapshotted for inheritance by future runs

Runs are idempotent — `generation_exists()` check skips already-completed generations on resume. Playbook updates only persist on `advance` gate decisions. Coach hints persist to `hints.md` and survive restarts.

### Agent Roles (`agents/`)

All roles use `SubagentRuntime` wrapping a `LanguageModelClient` (Anthropic API, `DeterministicDevClient` for offline/CI, or `AgentSdkClient` for Agent SDK mode):

- **Competitor** — Produces a JSON strategy dict matching the scenario's strategy interface. Runs first (sequentially) since its output feeds into tournament scoring.
- **Analyst** — Produces markdown analysis (Findings, Root Causes, Recommendations)
- **Coach** — Updates the accumulated playbook (Strategy Updates, Prompt Optimizations, Next Gen Checklist). Output parsed via `<!-- PLAYBOOK_START/END -->`, `<!-- LESSONS_START/END -->`, and `<!-- COMPETITOR_HINTS_START/END -->` delimiters.
- **Architect** — Proposes tooling improvements + emits a `{"tools": [...]}` JSON block that gets persisted as Python files in `knowledge/<scenario>/tools/`. Can update existing tools (old versions archived to `tools/_archive/`).
- **Curator** (`agents/curator.py`) — Opus-level quality gate for playbook updates and lesson consolidation. Runs after tournament on `advance` decisions. Uses `<!-- CURATOR_DECISION: accept|reject|merge -->` and `<!-- CONSOLIDATED_LESSONS_START/END -->` output markers.

The architect only intervenes fully every N generations (`MTS_ARCHITECT_EVERY_N_GENS`, default 3). The curator runs its quality gate on every advance and consolidates lessons every N generations (`MTS_CURATOR_CONSOLIDATE_EVERY_N_GENS`, default 3). The `LanguageModelClient` base class provides both single-turn `generate()` and multi-turn `generate_multiturn()` methods, both accepting an optional `role` parameter used by the Agent SDK client.

### Agent SDK Provider (`agents/agent_sdk_client.py`)

Optional provider (`MTS_AGENT_PROVIDER=agent_sdk`) that uses `claude_agent_sdk.query()` with native tool loops. Each role gets scoped tool permissions via `ROLE_TOOL_CONFIG`: competitor/coach/curator get Read/Glob/Grep, analyst/architect additionally get Bash, translator gets none. The Agent SDK handles multi-turn iteration internally, making RLM mode redundant (automatically skipped when `agent_sdk` provider is active).

### RLM — REPL-Loop Mode (`rlm/`)

Optional mode (`MTS_RLM_ENABLED=true`) that replaces the single-shot analyst and architect with multi-turn REPL sessions where the LLM can iteratively explore data by writing Python code.

- **RlmSession** — Drives a conversation loop: sends messages to the LLM, extracts code from `<code>` tags, executes it in a `ReplWorker`, feeds stdout/errors back as user messages. The loop ends when the model sets `answer["ready"] = True` or hits `max_turns`.
- **ReplWorker** — In-process Python REPL with a restricted namespace (no file I/O, no `os`/`subprocess`/`import`). Pre-populated with safe stdlib modules (`json`, `math`, `statistics`, `collections`, `re`, `time`) and an `answer` dict. Enforces wall-clock timeout via `SIGALRM` (main thread) or daemon thread (worker threads).
- **ContextLoader** — Loads run data (replays, metrics, match scores, playbook, prior analyses, existing tools) into the REPL namespace as Python variables for exploration.
- **`llm_batch()`** — Injected callable that lets REPL code make batched LLM sub-calls (uses `MTS_RLM_SUB_MODEL`).

When RLM is enabled, `AgentOrchestrator._run_rlm_roles()` runs analyst and architect as RLM sessions sequentially, while coach still runs via the standard single-shot path.

### Scenarios (`scenarios/`)

Pluggable via `SCENARIO_REGISTRY` dict in `scenarios/__init__.py`. Each scenario implements `ScenarioInterface` (ABC) with methods: `initial_state`, `get_observation`, `validate_actions`, `step`, `is_terminal`, `get_result`, `execute_match`, etc.

Built-in scenarios: `grid_ctf`, `othello`. To add a scenario manually, implement `ScenarioInterface` and register it in `SCENARIO_REGISTRY`.

### Custom Scenario Creation (`scenarios/custom/`)

Users can create scenarios from natural language via the TUI (`/scenario create <description>`) or WebSocket API (`create_scenario` message). The pipeline:

1. **Designer prompt** (`custom/designer.py`) — LLM generates a `ScenarioSpec` JSON between `<!-- SCENARIO_SPEC_START/END -->` delimiters
2. **ScenarioSpec** (`custom/spec.py`) — Dataclass defining strategy params, constraints, environment variables, scoring components, weights, and win threshold
3. **Codegen** (`custom/codegen.py`) — `generate_scenario_class(spec)` produces a full Python `ScenarioInterface` module with deterministic weighted scoring
4. **Validation** (`custom/validator.py`) — Three stages: spec structural validation, `ast.parse()`, and execution validation (3 test matches with default params)
5. **Loading** (`custom/loader.py`) — Dynamic import via `importlib.util`, registered in `sys.modules` as `mts.scenarios.custom.generated.{name}` for `LocalExecutor` subprocess compatibility
6. **Persistence** — Saved to `knowledge/_custom_scenarios/{name}/scenario.py` + `spec.json`, auto-loaded on startup via `custom/registry.py`
7. **Registration** — Added to `SCENARIO_REGISTRY`, immediately available for `/run`

The `ScenarioCreator` (`custom/creator.py`) orchestrates the full pipeline and supports iterative revision via LLM feedback loops. `DeterministicDevClient` includes a fixed scenario designer response for offline/CI testing.

WebSocket protocol for interactive creation: `create_scenario` → `scenario_generating` → `scenario_preview` → `confirm_scenario` → `scenario_ready`. Supports `revise_scenario` (with feedback) and `cancel_scenario`.

### Execution (`execution/`)

- **ExecutionSupervisor** — Data-plane boundary wrapping an `ExecutionEngine` protocol
- **LocalExecutor** — Runs strategy in a subprocess (`ProcessPoolExecutor`) with timeout and memory limits; falls back to `ThreadPoolExecutor` if process semaphores are blocked
- **PrimeIntellectExecutor** — Runs remotely via PrimeIntellect sandbox SDK (create/wait/execute/delete lifecycle)

### Knowledge System (`knowledge/`, `storage/artifacts.py`)

The knowledge feedback loop ensures agents improve across generations and runs:

```
knowledge/<scenario>/
  playbook.md              # Current consolidated strategy playbook
  playbook_versions/       # Archived prior versions (max N, configurable)
  hints.md                 # Coach competitor hints (persist across restarts)
  analysis/gen_N.md        # Per-generation analyst output
  coach_history.md         # Full coach output audit trail
  architect/changelog.md   # Architect decisions log
  tools/                   # Architect-generated Python tools
  tools/_archive/          # Prior tool versions (archived on update)
  snapshots/<run_id>/      # Cross-run knowledge snapshots
  _custom_scenarios/<name>/  # Persisted custom scenarios (scenario.py + spec.json)
```

Key behaviors:
- **Score trajectory**: `ScoreTrajectoryBuilder` (`knowledge/trajectory.py`) queries SQLite to build markdown tables showing Gen/Mean/Best/Elo/Gate/Delta history. Injected into all agent prompts.
- **Playbook versioning**: Each overwrite archives the previous version. Rollback support via `rollback_playbook()`. Pruning keeps last N versions.
- **Hint persistence**: Coach hints saved to `hints.md`, loaded on run start instead of being lost as ephemeral state.
- **Analysis injection**: Most recent advance-generation analysis injected into all agent prompts.
- **Tool updates**: Architect can update existing tools by name; old versions archived to `_archive/`.
- **Lesson consolidation**: Curator periodically deduplicates and prunes SKILL.md lessons to prevent unbounded growth.
- **Cross-run inheritance**: On run completion, playbook + hints + skills are snapshotted. New runs for the same scenario restore from the best-scoring snapshot if no playbook exists.

### Strategy Knowledge API (`knowledge/export.py`, `knowledge/search.py`, `knowledge/solver.py`)

Framework-agnostic knowledge service that lets any autonomous agent query MTS for solved strategies, search for relevant tactics, and submit new problems for on-demand solving. Consumers receive portable markdown+JSON skill packages they can drop into any agent skill directory.

- **Skill Export** (`knowledge/export.py`) — `SkillPackage` dataclass assembles playbook, cleaned lessons, best strategy JSON, hints, and metadata into a portable bundle. `export_skill_package(ctx, scenario_name)` reads from `ArtifactStore` and `SQLiteStore`. `list_solved_scenarios(ctx)` returns metadata for scenarios with completed runs. `_clean_lessons()` strips MTS-internal noise (rollback logs, raw JSON blobs, score parentheticals) from lesson bullets.
- **Strategy Search** (`knowledge/search.py`) — `search_strategies(ctx, query, top_k)` builds a search index over solved scenarios and scores with TF-IDF-style keyword matching across name, description, strategy interface, evaluation criteria, lessons, playbook excerpt, and hints. Weighted fields (name ×3, description ×2, lessons ×1.5, playbook ×1) with multi-term coverage boost.
- **Solve-on-Demand** (`knowledge/solver.py`) — `SolveManager` accepts natural-language problem descriptions and runs background threads that: create a scenario via `ScenarioCreator`, run N generations via `GenerationRunner`, and export the resulting `SkillPackage`. Jobs are in-memory with polling via `get_status(job_id)` and `get_result(job_id)`.

Access paths:
- **MCP tools**: `mts_export_skill`, `mts_list_solved`, `mts_search_strategies`, `mts_solve_scenario`, `mts_solve_status`, `mts_solve_result`
- **REST API**: `GET /api/knowledge/scenarios`, `GET /api/knowledge/export/{name}`, `POST /api/knowledge/search`, `POST /api/knowledge/solve`, `GET /api/knowledge/solve/{job_id}`

### Storage

- **SQLiteStore** (`storage/sqlite_store.py`) — Runs, generations, matches, agent outputs, role metrics, recovery markers, knowledge snapshots. Includes `get_best_competitor_output(scenario)` and `count_completed_runs(scenario)` for the knowledge API. Migrations applied from `migrations/*.sql` in filename order.
- **ArtifactStore** (`storage/artifacts.py`) — Filesystem persistence: generation metrics/replays under `runs/<run_id>/generations/`, playbooks/analysis/tools/hints/snapshots under `knowledge/<scenario>/`, skill notes under `skills/`. Syncs skill notes to `.claude/skills/` via symlinks.

### Dashboard & Events

- **FastAPI server** (`server/app.py`) — REST endpoints (`/api/runs`, `/api/runs/{id}/status`, `/api/runs/{id}/replay/{gen}`) + Knowledge API router (`/api/knowledge/scenarios`, `/api/knowledge/export/{name}`, `/api/knowledge/search`, `/api/knowledge/solve`, `/api/knowledge/solve/{job_id}`) + WebSocket (`/ws/events`) streaming from ndjson event file + `/health` endpoint. The `/ws/interactive` WebSocket also handles custom scenario creation (`create_scenario`, `confirm_scenario`, `revise_scenario`, `cancel_scenario`).
- **EventStreamEmitter** (`loop/events.py`) — Appends ndjson events to `runs/events.ndjson`

### Ecosystem Loop (`loop/ecosystem_runner.py`)

The ecosystem loop alternates between provider modes across sequential runs, with the shared `knowledge/<scenario>/` directory as the connection point. Each cycle runs Phase A (default: anthropic + RLM) then Phase B (default: agent_sdk), creating a feedback loop.

- **`EcosystemPhase`** — Dataclass defining a phase's provider, rlm_enabled, and generation count
- **`EcosystemConfig`** — Scenario, cycles, gens_per_cycle, and phase list (defaults to anthropic+RLM / agent_sdk two-phase)
- **`EcosystemRunner`** — Creates a fresh `GenerationRunner` per phase with `model_copy(update={...})` to swap provider/rlm settings while preserving shared storage roots. Emits lifecycle events on `channel="ecosystem"`.
- **`EcosystemSummary`** — Collects `RunSummary` from each phase; `score_trajectory()` returns `(run_id, best_score)` pairs.
- **Run ID pattern** — `eco_{scenario}_c{cycle}_p{phase}_{uuid8}` for traceability
- **Provider tracking** — `agent_provider` column on `runs` and `knowledge_snapshots` tables (migration 005)

### MCP Server (`mcp/`)

Stdio-based MCP server exposing MTS functionality as tools for external Claude Code users:

- **`mcp/tools.py`** — Pure sync tool implementation functions wrapping `ScenarioInterface`, `ArtifactStore`, `SQLiteStore`. Independently testable without MCP protocol. Includes knowledge API wrappers (`export_skill`, `list_solved`, `search_strategies`).
- **`mcp/server.py`** — MCP server using `@server.tool()` decorators. Each tool delegates to `tools.py`. Registers scenario tools (list, describe, validate, match, tournament), knowledge tools (playbook, trajectory, hints, skills, analysis, tools), run tools (list, status, replay), sandbox tools (create, run, status, playbook, list, destroy), and knowledge API tools (`mts_export_skill`, `mts_list_solved`, `mts_search_strategies`, `mts_solve_scenario`, `mts_solve_status`, `mts_solve_result`).
- **`mcp/sandbox.py`** — `SandboxManager` creates isolated environments with their own SQLite DB, knowledge directory (seeded from main), and run storage. Sandbox runs use `GenerationRunner` with sandbox-scoped `AppSettings`. `MTS_SANDBOX_MAX_GENERATIONS` limits generation count.

CLI entry point: `uv run mts mcp-serve` (requires `mcp` optional dependency).

## Configuration

All config via `MTS_*` environment variables, loaded in `config/settings.py` into a Pydantic `AppSettings` model. Key settings:

- `MTS_AGENT_PROVIDER`: `deterministic` (offline/CI), `anthropic` (live), or `agent_sdk` (Agent SDK with native tools)
- `MTS_EXECUTOR_MODE`: `local` or `primeintellect`
- `MTS_MODEL_*`: per-role model selection (competitor, analyst, coach, architect, curator, translator)
- `MTS_MATCHES_PER_GENERATION`, `MTS_BACKPRESSURE_MIN_DELTA`, `MTS_MAX_RETRIES`, `MTS_ARCHITECT_EVERY_N_GENS`: loop tuning
- `MTS_CURATOR_ENABLED`: enable curator quality gate and lesson consolidation (default `true`)
- `MTS_CURATOR_CONSOLIDATE_EVERY_N_GENS`: lesson consolidation cadence (default `3`)
- `MTS_SKILL_MAX_LESSONS`: consolidation threshold (default `30`)
- `MTS_PLAYBOOK_MAX_VERSIONS`: archived playbook versions to keep (default `5`)
- `MTS_CROSS_RUN_INHERITANCE`: inherit best knowledge across runs (default `true`)
- `MTS_ABLATION_NO_FEEDBACK`: suppress all feedback injection for A/B testing (default `false`)
- `MTS_RLM_ENABLED`: enable REPL-loop mode for analyst/architect (default `false`)
- `MTS_RLM_MAX_TURNS`, `MTS_RLM_MAX_STDOUT_CHARS`, `MTS_RLM_SUB_MODEL`, `MTS_RLM_CODE_TIMEOUT_SECONDS`: RLM tuning
- `MTS_AGENT_SDK_CONNECT_MCP`: connect Agent SDK agents to MTS MCP server (default `false`)
- `MTS_SANDBOX_MAX_GENERATIONS`: maximum generations per sandbox run (default `10`)

## Code Style

- Python 3.11+, managed with `uv` and `hatchling` build backend
- Ruff for linting (rules: E, F, I, B, UP), line length 130
- Mypy with `disallow_untyped_defs`, excludes tests and migrations
- Dataclasses with `slots=True` for value types, Pydantic `BaseModel` for validated models
- CLI via Typer, Rich for terminal output

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs: ruff check, mypy, pytest, deterministic smoke runs for both scenarios (`grid_ctf` 3 gens, `othello` 1 gen), and dashboard API health check. A separate `primeintellect-live` job runs when secrets are available.
