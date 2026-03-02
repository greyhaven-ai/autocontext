# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MTS (MTS Control Plane) is an iterative strategy generation and evaluation system. It runs a multi-agent loop where LLM agents collaboratively evolve strategies for pluggable scenarios, scoring them through tournament matches (game scenarios) or LLM judge evaluation (agent task scenarios) with Elo-based progression gating.

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
  tests/                      # Pytest tests (~1198 tests)
  migrations/                 # SQLite migration SQL files (001-007, applied in filename order)
  dashboard/                  # Single-page HTML dashboard
  knowledge/                  # Runtime-generated: per-scenario playbooks, analysis, tools, hints, snapshots
  skills/                     # Runtime-generated: operational skill notes per scenario
  runs/                       # Runtime-generated: SQLite DB, event stream, generation artifacts
ts/                           # TypeScript port of MTS modules
  src/                        # Source code (types, judge, storage, execution, runtimes, scenarios, knowledge, mcp, cli)
  tests/                      # Vitest tests (119 tests)
  migrations/                 # Shared SQLite migration SQL (cross-compatible with Python)
infra/                        # Docker, Fly.io config, bootstrap script
scripts/                      # Top-level convenience scripts (demo.sh)
.claude/                      # Claude context, implementation plans, synced skill symlinks
```

## Commands

All commands run from the `mts/` directory:

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
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 3 --run-id my_run

# Run (live Anthropic mode)
MTS_AGENT_PROVIDER=anthropic MTS_ANTHROPIC_API_KEY=... uv run mts run --scenario grid_ctf --gens 1

# Run (Agent SDK mode — agents use native tool loops)
MTS_AGENT_PROVIDER=agent_sdk MTS_ANTHROPIC_API_KEY=... uv run mts run --scenario grid_ctf --gens 3

# Run (RLM mode — REPL-loop agents for analyst/architect)
MTS_AGENT_PROVIDER=deterministic MTS_RLM_ENABLED=true uv run mts run --scenario grid_ctf --gens 3 --run-id rlm_run

# Run (Monty sandbox executor — pydantic-monty interpreter)
MTS_AGENT_PROVIDER=deterministic MTS_EXECUTOR_MODE=monty uv run mts run --scenario grid_ctf --gens 3

# Run (RLM with Monty backend — sandboxed REPL)
MTS_AGENT_PROVIDER=deterministic MTS_RLM_ENABLED=true MTS_RLM_BACKEND=monty uv run mts run --scenario grid_ctf --gens 3

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

- **Competitor** — Produces a JSON strategy dict matching the scenario's strategy interface. Runs first (sequentially) since its output feeds into tournament scoring. In code strategy mode (`MTS_CODE_STRATEGIES_ENABLED=true`), produces executable Python code instead of JSON.
- **Translator** (`agents/translator.py`) — Extracts structured strategy from competitor output. In standard mode, uses an LLM call to parse JSON. In code strategy mode, `translate_code()` extracts Python from markdown fences via regex (no LLM call), producing `{"__code__": "<source>"}`.
- **Analyst** — Produces markdown analysis (Findings, Root Causes, Recommendations)
- **Coach** — Updates the accumulated playbook (Strategy Updates, Prompt Optimizations, Next Gen Checklist). Output parsed via `<!-- PLAYBOOK_START/END -->`, `<!-- LESSONS_START/END -->`, and `<!-- COMPETITOR_HINTS_START/END -->` delimiters.
- **Architect** — Proposes tooling improvements + emits a `{"tools": [...]}` JSON block that gets persisted as Python files in `knowledge/<scenario>/tools/`. Can update existing tools (old versions archived to `tools/_archive/`).
- **Curator** (`agents/curator.py`) — Opus-level quality gate for playbook updates and lesson consolidation. Runs after tournament on `advance` decisions. Uses `<!-- CURATOR_DECISION: accept|reject|merge -->` and `<!-- CONSOLIDATED_LESSONS_START/END -->` output markers.

The architect only intervenes fully every N generations (`MTS_ARCHITECT_EVERY_N_GENS`, default 3). The curator runs its quality gate on every advance and consolidates lessons every N generations (`MTS_CURATOR_CONSOLIDATE_EVERY_N_GENS`, default 3). The `LanguageModelClient` base class provides both single-turn `generate()` and multi-turn `generate_multiturn()` methods, both accepting an optional `role` parameter used by the Agent SDK client.

### Agent SDK Provider (`agents/agent_sdk_client.py`)

Optional provider (`MTS_AGENT_PROVIDER=agent_sdk`) that uses `claude_agent_sdk.query()` with native tool loops. Each role gets scoped tool permissions via `ROLE_TOOL_CONFIG`: competitor/coach/curator get Read/Glob/Grep, analyst/architect additionally get Bash, translator gets none. The Agent SDK handles multi-turn iteration internally, making RLM mode redundant (automatically skipped when `agent_sdk` provider is active).

### Multi-Model Providers (`providers/`)

Pluggable LLM provider abstraction for the judge and other non-agent LLM calls:

- **`LLMProvider`** (ABC, `providers/base.py`) — Defines `complete(system_prompt, user_prompt, model, temperature, max_tokens) → CompletionResult` and `default_model()`. `CompletionResult` dataclass carries `text`, `model`, and optional `cost_usd`.
- **`AnthropicProvider`** (`providers/anthropic.py`) — Wraps the Anthropic SDK. Uses `hasattr` guard on content blocks for mypy compatibility.
- **`OpenAICompatibleProvider`** (`providers/openai_compat.py`) — Works with any OpenAI-compatible endpoint (vLLM, Ollama, etc.). Optional dependency (`openai` package).
- **`CallableProvider`** (`providers/callable_wrapper.py`) — Wraps any `Callable[[str, str], str]` as an `LLMProvider` for testing and simple integrations.
- **`RetryProvider`** (`providers/retry.py`) — Decorator that wraps any `LLMProvider` with retry logic and exponential backoff. Retries transient errors (rate limits, timeouts, 5xx) automatically; non-transient errors (auth failures) fail immediately. Configurable: `max_retries` (default 3), `base_delay` (default 1.0s), `max_delay` (default 60s), `backoff_factor` (default 2.0), `retry_all` flag. Transient detection via error message substring matching against a `frozenset` of known patterns.
- **`registry.py`** — `create_provider(provider_type, api_key, base_url, model)` factory + `get_provider(settings)` convenience that reads from `AppSettings`.

Provider selection is controlled by `MTS_JUDGE_PROVIDER` (default `anthropic`). For OpenAI-compatible endpoints, set `MTS_JUDGE_BASE_URL` and `MTS_JUDGE_API_KEY`.

### RLM — REPL-Loop Mode (`rlm/`)

Optional mode (`MTS_RLM_ENABLED=true`) that replaces the single-shot analyst and architect with multi-turn REPL sessions where the LLM can iteratively explore data by writing Python code.

- **RlmSession** — Drives a conversation loop: sends messages to the LLM, extracts code from `<code>` tags, executes it in a `ReplWorker`, feeds stdout/errors back as user messages. The loop ends when the model sets `answer["ready"] = True` or hits `max_turns`.
- **ReplWorker** — In-process Python REPL with a restricted namespace (no file I/O, no `os`/`subprocess`/`import`). Pre-populated with safe stdlib modules (`json`, `math`, `statistics`, `collections`, `re`, `time`) and an `answer` dict. Enforces wall-clock timeout via `SIGALRM` (main thread) or daemon thread (worker threads).
- **ContextLoader** — Loads run data (replays, metrics, match scores, playbook, prior analyses, existing tools) into the REPL namespace as Python variables for exploration.
- **`llm_batch()`** — Injected callable that lets REPL code make batched LLM sub-calls (uses `MTS_RLM_SUB_MODEL`).
- **MontyReplWorker** (`harness/repl/monty_worker.py`) — Alternative Monty-backed REPL worker selected via `MTS_RLM_BACKEND=monty`. Each `run_code()` creates a fresh Monty interpreter. Cross-turn state persists via `state["key"]` dict (not bare variables). Stdlib access via `stdlib("module", "func", *args)` dispatch. `print()` is textually rewritten to `_print()` external function. Implements `ReplWorkerProtocol` (`harness/repl/types.py`) for duck-typed interchangeability with `ReplWorker`.

When RLM is enabled, `AgentOrchestrator._run_rlm_roles()` runs analyst and architect as RLM sessions sequentially, while coach still runs via the standard single-shot path. The `MTS_RLM_BACKEND` setting selects which REPL worker to use (`exec` or `monty`).

### Scenarios (`scenarios/`)

Pluggable via `SCENARIO_REGISTRY` dict in `scenarios/__init__.py`. The registry holds two types of scenario:

- **Game scenarios** — Implement `ScenarioInterface` (ABC) with methods: `initial_state`, `get_observation`, `validate_actions`, `step`, `is_terminal`, `get_result`, `execute_match`, `describe_rules`, `describe_strategy_interface`, `describe_evaluation_criteria`. Evaluated via tournament matches.
- **Agent task scenarios** — Implement `AgentTaskInterface` (ABC, `scenarios/agent_task.py`) with methods: `get_task_prompt`, `evaluate_output`, `get_rubric`, `initial_state`, `describe_task`, plus optional `prepare_context` (gather/validate context before generation), `validate_context` (check required context keys are present), and `revise_output` (revise output based on judge feedback for multi-step improvement). Evaluated via LLM judge rather than match execution. Returns `AgentTaskResult` (score, reasoning, dimension_scores).

Both types coexist in `SCENARIO_REGISTRY`. Code that accesses the registry uses `hasattr`/`getattr` guards to handle the dual-interface pattern (e.g., `describe_rules` vs `describe_task`, `execute_match` vs `evaluate_output`).

Built-in game scenarios: `grid_ctf`, `othello`. To add a game scenario manually, implement `ScenarioInterface` and register it in `SCENARIO_REGISTRY`.

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

### Agent Task Creation (`scenarios/custom/agent_task_*.py`)

Agent tasks are a second creation pipeline for scenarios evaluated by LLM judges rather than match execution. The pipeline mirrors custom scenario creation:

1. **Designer** (`custom/agent_task_designer.py`) — LLM generates an `AgentTaskSpec` from a natural-language description
2. **AgentTaskSpec** (`custom/agent_task_spec.py`) — Dataclass defining `task_prompt`, `judge_rubric`, `output_format`, `judge_model`, `difficulty_tiers`, `reference_context` (gold-standard reference for judge), `reference_sources` (URLs/paths), `required_concepts` (must-cover concepts), `context_preparation` (instructions for context gathering), `required_context_keys` (state keys that `prepare_context` must populate), `max_rounds` (improvement loop iterations, default 1), `quality_threshold` (target score 0.0–1.0, default 0.9), and `revision_prompt` (instructions for `revise_output`)
3. **Codegen** (`custom/agent_task_codegen.py`) — `generate_agent_task_class(spec, name)` produces a Python `AgentTaskInterface` subclass. Uses `repr()` for safe string embedding in generated code.
4. **Validation** (`custom/agent_task_validator.py`) — Three stages: `validate_spec()` (structural), `validate_syntax()` (`ast.parse()`), `validate_execution()` (instantiate + call methods)
5. **Creator** (`custom/agent_task_creator.py`) — `AgentTaskCreator` orchestrates the full pipeline: design → validate spec → codegen → validate syntax → validate execution → save to disk → load module → register in `SCENARIO_REGISTRY`
6. **Persistence** — Saved to `knowledge/_custom_scenarios/{name}/agent_task.py` + `agent_task_spec.json` + `scenario_type.txt` (contains `"agent_task"`), auto-loaded on startup via `custom/registry.py`

### Execution (`execution/`)

- **ExecutionSupervisor** — Data-plane boundary wrapping an `ExecutionEngine` protocol
- **LocalExecutor** — Runs strategy in a subprocess (`ProcessPoolExecutor`) with timeout and memory limits; falls back to `ThreadPoolExecutor` if process semaphores are blocked
- **PrimeIntellectExecutor** — Runs remotely via PrimeIntellect sandbox SDK (create/wait/execute/delete lifecycle)
- **MontyExecutor** (`execution/executors/monty.py`) — Sandboxed execution via pydantic-monty interpreter. Scenario classes run on the host; Monty sandboxes a generated eval script that calls back via external functions (`initial_state`, `validate_actions`, `step`, `is_terminal`, `get_result`). Supports both JSON strategies (`_EVAL_SCRIPT`) and agent-authored code strategies (`_CODE_STRATEGY_EVAL_SCRIPT` with `get_observation`). Selected via `MTS_EXECUTOR_MODE=monty`. Enforces timeout and max external call limits.
- **Code strategies** — When `MTS_CODE_STRATEGIES_ENABLED=true`, the competitor emits executable Python code (in `` ```python `` fences) instead of JSON parameters. `StrategyTranslator.translate_code()` extracts the code block without an LLM call, producing `{"__code__": "<source>"}`. `MontyExecutor.execute_code_strategy()` runs the agent code inside the sandbox with access to `get_observation()`, giving agents direct programmatic control over actions.
- **LLMJudge** (`execution/judge.py`) — LLM-based evaluation for agent task scenarios. Calls an LLM N times (configurable via `MTS_JUDGE_SAMPLES`) to evaluate agent output against a rubric. Uses a 4-tier fallback parser to extract scores from LLM responses: (1) `<!-- JUDGE_RESULT_START/END -->` markers (primary), (2) `` ```json `` code blocks, (3) raw JSON objects with `"score"` key, (4) plaintext regex (`Score: 0.85`). Fallback strategies tag reasoning with `[code_block parse]`, `[raw_json parse]`, or `[plaintext parse]` for observability. Retries up to 2 times on parse failure. Returns `JudgeResult` with averaged score, combined reasoning, and per-dimension scores. Score and dimension clamping ensures 0.0–1.0 range; non-numeric dimension values are silently skipped.
- **JudgeExecutor** (`execution/judge_executor.py`) — Executor for agent task scenarios. Runs context preparation (`prepare_context` → `validate_context`) before evaluation — if validation fails, returns score 0.0 with error details. Passes `reference_context`, `required_concepts`, and `calibration_examples` through to `evaluate_output()`.
- **ImprovementLoop** (`execution/improvement_loop.py`) — Multi-step evaluate→revise loop for agent task scenarios. Calls `evaluate_output()`, then `revise_output()` if score is below threshold, repeating up to `max_rounds`. Resilient to judge parse failures: detects failures via `_is_parse_failure()` (checks for known error markers in reasoning when score is 0.0), skips failed rounds for best-score tracking, carries forward last good feedback for revision prompts, and aborts after 3 consecutive judge failures as a safety valve. First-round failures with no prior feedback skip revision and retry the judge. `RoundResult` tracks `judge_failed` flag; `ImprovementResult` tracks `judge_failures` count. The `improved` property filters out failed rounds before comparing first vs last valid scores. Stops early if revision returns unchanged output.
- **TaskRunner** (`execution/task_runner.py`) — Daemon that polls a SQLite-backed task queue and runs `ImprovementLoop` for each queued task. `SimpleAgentTask` builds an `AgentTaskInterface` from queue config (task_prompt, rubric, revision_prompt) without codegen. `TaskConfig.from_json()` deserializes queue config. The runner supports graceful shutdown via `SIGINT`/`SIGTERM`, priority-based dequeue, and optional `Notifier` for event emission on completion/failure. `enqueue_task()` convenience function generates a UUID task ID and stores config JSON. Migration 007 adds the `task_queue` table with columns: id, spec_name, status (pending/running/completed/failed), priority, config_json, best_score, best_output, total_rounds, met_threshold, result_json, error, timestamps.

### Notification System (`notifications/`)

Event-driven notification system for task runner results:

- **`Notifier`** (ABC, `notifications/base.py`) — Abstract `notify(event)` method. Implementations must not raise — failures are logged and swallowed.
- **`NotificationEvent`** — Dataclass with `type` (EventType enum: `threshold_met`, `regression`, `completion`, `failure`), `task_name`, optional `score`, `round_count`, `cost_usd`, `output_preview`, `error`, `metadata`. Has a `summary` property formatting human-readable messages with emoji.
- **`StdoutNotifier`** — Prints to stdout or logger.
- **`HTTPNotifier`** — JSON POST to any webhook URL via `urllib.request`.
- **`SlackWebhookNotifier`** — Formats Slack Block Kit messages with header, summary, field sections, and code preview.
- **`CallbackNotifier`** — Wraps a user-provided `Callable[[NotificationEvent], None]`.
- **`CompositeNotifier`** — Fans out to multiple notifiers with optional `EventType` filtering via `notify_on` set.

The `TaskRunner` accepts an optional `notifier` parameter and emits `THRESHOLD_MET`/`COMPLETION` on success and `FAILURE` on exception.

### Agent Runtimes (`runtimes/`)

Abstraction layer for agent execution, decoupling MTS orchestration from how outputs are generated:

- **`AgentRuntime`** (ABC, `runtimes/base.py`) — Defines `generate(prompt, system, schema) → AgentOutput` and `revise(prompt, previous_output, feedback, system) → AgentOutput`. `AgentOutput` dataclass carries `text`, optional `structured` dict, `cost_usd`, `model`, `session_id`, and `metadata`.
- **`DirectAPIRuntime`** (`runtimes/direct_api.py`) — Wraps an `LLMProvider` for simple single-call generation/revision. Equivalent to what `SimpleAgentTask` does today.
- **`ClaudeCLIRuntime`** (`runtimes/claude_cli.py`) — Invokes `claude -p` (Claude Code print mode) via subprocess. Features: full tool access, structured JSON output via `--json-schema`, cost tracking from JSON output (`total_cost_usd`), session management for multi-round loops, model selection with fallback. `ClaudeCLIConfig` dataclass controls model, tools, permission mode, session persistence, timeout, and system prompt. `create_session_runtime()` factory creates a runtime with a shared UUID session ID for context across rounds.

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
  _agent_tasks/<name>.json  # Persisted agent task specs (created via MCP tools)
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

- **Skill Export** (`knowledge/export.py`) — `SkillPackage` dataclass assembles playbook, cleaned lessons, best strategy JSON, hints, and metadata into a portable bundle. For agent task scenarios, also includes `task_prompt`, `judge_rubric`, `example_outputs`, `output_format`, `reference_context`, `context_preparation`, `max_rounds`, and `quality_threshold` fields with a dedicated `_render_agent_task_markdown()` renderer. `export_skill_package(ctx, scenario_name)` reads from `ArtifactStore` and `SQLiteStore`. `export_agent_task_skill()` is a convenience builder for agent-task packages. `list_solved_scenarios(ctx)` returns metadata for scenarios with completed runs. `_clean_lessons()` strips MTS-internal noise (rollback logs, raw JSON blobs, score parentheticals) from lesson bullets. `_scenario_description()` handles the dual-interface pattern (`describe_rules` vs `describe_task`).
- **Strategy Search** (`knowledge/search.py`) — `search_strategies(ctx, query, top_k)` builds a search index over solved scenarios (both game and agent task) and scores with TF-IDF-style keyword matching across name, description, strategy interface, evaluation criteria, lessons, playbook excerpt, hints, task prompt, and judge rubric. Weighted fields (name ×3, description ×2, task_prompt ×2, lessons ×1.5, judge_rubric ×1.5, playbook ×1) with multi-term coverage boost.
- **Solve-on-Demand** (`knowledge/solver.py`) — `SolveManager` accepts natural-language problem descriptions and runs background threads that: create a scenario via `ScenarioCreator`, run N generations via `GenerationRunner`, and export the resulting `SkillPackage`. Jobs are in-memory with polling via `get_status(job_id)` and `get_result(job_id)`.

Access paths:
- **MCP tools**: `mts_export_skill`, `mts_list_solved`, `mts_search_strategies`, `mts_solve_scenario`, `mts_solve_status`, `mts_solve_result`, `mts_record_feedback`, `mts_get_feedback`, `mts_run_improvement_loop`, `mts_create_agent_task`, `mts_list_agent_tasks`, `mts_get_agent_task`, `mts_delete_agent_task`, `mts_evaluate_output`, `mts_queue_improvement_run`, `mts_get_queue_status`, `mts_get_task_result`, `mts_get_best_output`
- **REST API**: `GET /api/knowledge/scenarios`, `GET /api/knowledge/export/{name}`, `POST /api/knowledge/search`, `POST /api/knowledge/solve`, `GET /api/knowledge/solve/{job_id}`

### Storage

- **SQLiteStore** (`storage/sqlite_store.py`) — Runs, generations, matches, agent outputs, role metrics, recovery markers, knowledge snapshots, human feedback, task queue. Includes `get_best_competitor_output(scenario)` and `count_completed_runs(scenario)` for the knowledge API. Human feedback methods: `insert_human_feedback()`, `get_human_feedback()`, `get_calibration_examples()` (returns scored examples for judge calibration). Task queue methods: `enqueue_task()`, `dequeue_task()` (atomic dequeue with `AND status = 'pending'` guard on UPDATE + `SELECT changes()` check to prevent double-processing under concurrent access; priority DESC, created_at ASC ordering), `complete_task()`, `fail_task()`, `get_task()`, `list_tasks()`, `pending_task_count()`. Migrations applied from `migrations/*.sql` in filename order (001-007; migration 006 adds the `human_feedback` table, migration 007 adds the `task_queue` table with `idx_task_queue_priority` index).
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

- **`mcp/tools.py`** — Pure sync tool implementation functions wrapping `ScenarioInterface`/`AgentTaskInterface`, `ArtifactStore`, `SQLiteStore`. Independently testable without MCP protocol. Includes knowledge API wrappers (`export_skill`, `list_solved`, `search_strategies`), agent task CRUD (`create_agent_task`, `list_agent_tasks`, `get_agent_task`, `delete_agent_task`, `evaluate_output`), and task queue management (`queue_improvement_run`, `get_queue_status`, `get_task_result`, `get_best_output`). Agent task specs are persisted as JSON files under `knowledge/_agent_tasks/`. Uses `hasattr` guards so tools like `validate_strategy`, `run_match`, and `run_tournament` return appropriate messages for agent task scenarios (which use judge evaluation instead of match execution).
- **`mcp/server.py`** — MCP server using `@server.tool()` decorators. Each tool delegates to `tools.py`. Registers scenario tools (list, describe, validate, match, tournament), knowledge tools (playbook, trajectory, hints, skills, analysis, tools), run tools (list, status, replay), sandbox tools (create, run, status, playbook, list, destroy), knowledge API tools (`mts_export_skill`, `mts_list_solved`, `mts_search_strategies`, `mts_solve_scenario`, `mts_solve_status`, `mts_solve_result`), human feedback tools (`mts_record_feedback`, `mts_get_feedback`), improvement loop tool (`mts_run_improvement_loop`), agent task management tools (`mts_create_agent_task`, `mts_list_agent_tasks`, `mts_get_agent_task`, `mts_delete_agent_task`, `mts_evaluate_output`), and task queue tools (`mts_queue_improvement_run`, `mts_get_queue_status`, `mts_get_task_result`, `mts_get_best_output`). Agent task CRUD tools validate task names with a regex (`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$`) to prevent path traversal. JSON parsing at the MCP boundary (`required_concepts`) is wrapped in try/except.
- **`mcp/sandbox.py`** — `SandboxManager` creates isolated environments with their own SQLite DB, knowledge directory (seeded from main), and run storage. Sandbox runs use `GenerationRunner` with sandbox-scoped `AppSettings`. `MTS_SANDBOX_MAX_GENERATIONS` limits generation count.

CLI entry point: `uv run mts mcp-serve` (requires `mcp` optional dependency).

## Configuration

All config via `MTS_*` environment variables, loaded in `config/settings.py` into a Pydantic `AppSettings` model. Key settings:

- `MTS_AGENT_PROVIDER`: `deterministic` (offline/CI), `anthropic` (live), or `agent_sdk` (Agent SDK with native tools)
- `MTS_EXECUTOR_MODE`: `local`, `primeintellect`, or `monty` (Monty sandbox via pydantic-monty)
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
- `MTS_RLM_BACKEND`: RLM REPL backend — `exec` (default) or `monty` (Monty sandbox)
- `MTS_MONTY_MAX_EXECUTION_TIME_SECONDS`: Monty sandbox timeout (default `30.0`)
- `MTS_MONTY_MAX_EXTERNAL_CALLS`: max external function calls per Monty execution (default `100`)
- `MTS_CODE_STRATEGIES_ENABLED`: enable code strategy mode for competitor (default `false`)
- `MTS_AGENT_SDK_CONNECT_MCP`: connect Agent SDK agents to MTS MCP server (default `false`)
- `MTS_SANDBOX_MAX_GENERATIONS`: maximum generations per sandbox run (default `10`)
- `MTS_JUDGE_MODEL`: LLM model for agent task evaluation (default `claude-sonnet-4-20250514`)
- `MTS_JUDGE_SAMPLES`: number of judge calls to average per evaluation (default `1`)
- `MTS_JUDGE_TEMPERATURE`: temperature for judge LLM calls (default `0.0`)
- `MTS_JUDGE_PROVIDER`: LLM provider for judge — `anthropic`, `openai`, `openai-compatible`, `ollama`, `vllm` (default `anthropic`)
- `MTS_JUDGE_BASE_URL`: base URL for OpenAI-compatible judge endpoints (default `None`)
- `MTS_JUDGE_API_KEY`: API key override for judge provider (default `None`, falls back to provider-specific env vars)
- `MTS_NOTIFY_WEBHOOK_URL`: Slack or HTTP webhook URL for notifications (default `None`)
- `MTS_NOTIFY_ON`: comma-separated event types to notify on: `threshold_met`, `regression`, `completion`, `failure` (default `threshold_met,failure`)

## Code Style

- Python 3.11+, managed with `uv` and `hatchling` build backend
- Ruff for linting (rules: E, F, I, B, UP), line length 130
- Mypy with `disallow_untyped_defs`, excludes tests and migrations
- Dataclasses with `slots=True` for value types, Pydantic `BaseModel` for validated models
- CLI via Typer, Rich for terminal output

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs: ruff check, mypy, pytest, deterministic smoke runs for both scenarios (`grid_ctf` 3 gens, `othello` 1 gen), and dashboard API health check. A separate `primeintellect-live` job runs when secrets are available. Monty-specific tests (`test_monty_*.py`) are skipped in CI when pydantic-monty is not installed (`pytest.mark.skipif`).

## TypeScript Port (`ts/`)

A TypeScript port of MTS modules under `ts/`, published as `@greyhaven/mts`. ESM-only, strict TypeScript, Node.js >=18. Provides a `mts` CLI binary.

### Commands

```bash
cd ts
npm install
npm run lint          # tsc --noEmit
npm test              # vitest run (119 tests)
npm run build         # tsc (outputs to dist/)

# CLI (after build, or via npx tsx src/cli/index.ts)
mts judge -p <task-prompt> -o <agent-output> -r <rubric>
mts improve -p <task-prompt> -o <initial-output> -r <rubric> [-n rounds] [-t threshold]
mts queue -s <spec-name> [-p prompt] [-r rubric] [--priority N]
mts status
mts serve             # start MCP server on stdio
mts version
```

Environment variables: `ANTHROPIC_API_KEY` (required for judge/improve/serve), `MTS_MODEL` (default `claude-sonnet-4-20250514`), `MTS_DB_PATH` (default `./mts.db`).

### Modules

- **Types** (`src/types/index.ts`) — Zod schemas mirroring Python dataclasses: `CompletionResult`, `JudgeResult`, `AgentTaskResult`, `TaskRow`, `RoundResult`, `ImprovementResult`, `NotificationEvent`. Also defines `LLMProvider` interface, `AgentTaskInterface`, and `ProviderError` class.
- **Judge Parser** (`src/judge/parse.ts`) — Port of Python's 4-tier fallback parser: markers → code blocks → raw JSON → plaintext regex. `parseJudgeResponse()` returns `{score, reasoning, dimensionScores}` with score clamping to [0, 1].
- **LLM Judge** (`src/judge/index.ts`) — `LLMJudge` class with async `evaluate()`. Supports multi-sample averaging, retry on parse failure, reference context injection, and calibration examples.
- **SQLite Store** (`src/storage/index.ts`) — `SQLiteStore` using `better-sqlite3` with WAL mode and foreign keys. Atomic `dequeueTask()` via transaction with `AND status = 'pending'` guard. Task queue methods: `enqueueTask()`, `dequeueTask()`, `completeTask()`, `failTask()`, `pendingTaskCount()`, `getTask()`, `close()`. Human feedback methods: `insertHumanFeedback()` (validates score range [0, 1]), `getHumanFeedback()` (ordered by created_at DESC with limit), `getCalibrationExamples()` (filters for entries with both score and notes).
- **ImprovementLoop** (`src/execution/improvement-loop.ts`) — Multi-step evaluate→revise loop. Parse-failure resilience: `isParseFailure()` detection, carry-forward of last good feedback, consecutive failure abort (3). `isImproved()` filters failed rounds before comparing first vs last valid scores.
- **JudgeExecutor** (`src/execution/judge-executor.ts`) — Evaluates agent output by delegating to `AgentTaskInterface.evaluateOutput()`. Runs optional context preparation (`prepareContext`) and validation (`validateContext`) before evaluation — if validation fails, returns score 0.0 with error details. Passes reference context, required concepts, and calibration examples through as an opts object.
- **TaskRunner** (`src/execution/task-runner.ts`) — Daemon that polls the SQLite task queue and runs `ImprovementLoop` for each task. `SimpleAgentTask` builds an `AgentTaskInterface` from queue config with `generateOutput()` and `reviseOutput()`. `enqueueTask()` convenience function generates UUID and stores config JSON. `parseTaskConfig()` deserializes snake_case JSON to camelCase `TaskConfig`.
- **Agent Runtimes** (`src/runtimes/`) — `AgentRuntime` interface with `generate()` and `revise()` returning `AgentOutput` (text, structured, costUsd, model, sessionId, metadata). Two implementations:
  - `DirectAPIRuntime` — Wraps any `LLMProvider` for single-call generation/revision.
  - `ClaudeCLIRuntime` — Invokes `claude -p` via `execFileAsync` with JSON output parsing, cost tracking, session management, model/tool/permission configuration. `createSessionRuntime()` factory for shared-session multi-round loops.
- **MCP Server** (`src/mcp/server.ts`) — 5 tools via `@modelcontextprotocol/sdk`: `evaluate_output` (one-shot judge), `run_improvement_loop` (multi-round), `queue_task` (background queue), `get_queue_status` (pending count), `get_task_result` (lookup by ID). `createMcpServer()` returns a configured `McpServer`; `startServer()` connects via stdio.
- **CLI** (`src/cli/index.ts`) — `mts` binary with `judge`, `improve`, `queue`, `status`, `serve` commands. Uses `node:util/parseArgs`. Includes a built-in fetch-based Anthropic provider (no SDK dependency for the CLI). Dynamic imports for fast `--help` response.
- **Agent Task Pipeline** (`src/scenarios/`) — TypeScript port of the agent task creation pipeline:
  - `AgentTaskSpecSchema` (`agent-task-spec.ts`) — Zod schema defining `taskPrompt`, `judgeRubric`, `outputFormat`, `judgeModel`, `maxRounds`, `qualityThreshold`, `referenceContext`, `referenceSources`, `requiredConcepts`, `contextPreparation`, `requiredContextKeys`, `revisionPrompt`, `calibrationExamples`, `exampleOutputs`, `difficultyTiers`. `parseRawSpec()` converts snake_case LLM output to camelCase.
  - `designAgentTask()` (`agent-task-designer.ts`) — LLM-driven spec generation with `AGENT_TASK_SPEC_START/END` delimiter parsing. `parseAgentTaskSpec()` extracts and parses JSON between delimiters.
  - `validateSpec()` (`agent-task-validator.ts`) — Zod `safeParse()` validation with collected error messages.
  - `createAgentTask()` (`agent-task-factory.ts`) — Factory that produces a concrete `AgentTaskInterface` object from a spec. Includes `evaluateOutput` (delegates to `LLMJudge`), `prepareContext`, `validateContext` (null/undefined check for required keys), and `reviseOutput`. No eval or dynamic import needed — returns a plain object.
  - `AgentTaskCreator` (`agent-task-creator.ts`) — Orchestrates the full pipeline: design → validate → derive name → create → save to disk. `deriveName()` extracts 2-3 keywords from description, filtering stop words.
- **Skill Export** (`src/knowledge/skill-package.ts`) — `SkillPackage` class for portable markdown+JSON skill bundles. `toDict()` converts to snake_case for serialization. `toSkillMarkdown()` renders dual-format markdown: game scenarios get strategy JSON blocks, agent tasks get task prompt, evaluation criteria, example outputs (capped at 3) in `<details>` blocks. `exportAgentTaskSkill()` convenience builder. `cleanLessons()` strips MTS-internal noise (rollback logs, raw JSON blobs, score parentheticals) from lesson bullets.

### Dependencies

- **Runtime**: `zod` (schema validation), `better-sqlite3` (SQLite driver), `@modelcontextprotocol/sdk` (MCP server)
- **Dev**: `typescript`, `vitest`, `@types/better-sqlite3`, `tsx` (for CLI tests)

### Migration Compatibility

`ts/migrations/` share schemas with Python migrations for cross-language database compatibility: `007_task_queue.sql` (task queue) and `008_human_feedback.sql` (human feedback table with scenario_name index).
