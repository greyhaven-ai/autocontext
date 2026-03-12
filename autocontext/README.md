# MTS Control Plane

Infrastructure-first control plane for iterative strategy generation and evaluation. A multi-agent loop where LLM agents collaboratively evolve strategies for pluggable scenarios — game scenarios scored through tournament matches and agent task scenarios evaluated by LLM judges — with Elo-based progression gating.

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

## Running strategies

```bash
# Deterministic/offline mode (no API key needed)
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 3 --run-id my_run

# Live Anthropic mode
MTS_AGENT_PROVIDER=anthropic MTS_ANTHROPIC_API_KEY=... uv run mts run --scenario grid_ctf --gens 5

# Agent SDK mode (agents use native tool loops with Read/Glob/Grep/Bash)
MTS_AGENT_PROVIDER=agent_sdk MTS_ANTHROPIC_API_KEY=... uv run mts run --scenario grid_ctf --gens 3

# RLM mode (REPL-loop agents for analyst/architect)
MTS_AGENT_PROVIDER=deterministic MTS_RLM_ENABLED=true uv run mts run --scenario grid_ctf --gens 3

# Ecosystem mode (alternate providers across cycles)
MTS_AGENT_PROVIDER=deterministic uv run mts ecosystem \
  --scenario grid_ctf --cycles 3 --gens-per-cycle 2 \
  --provider-a anthropic --provider-b agent_sdk --rlm-a --no-rlm-b

# Other CLI commands
uv run mts list                            # list recent runs
uv run mts status <run_id>                 # generation-level status
uv run mts replay <run_id> --generation 1  # print replay JSON
uv run mts benchmark --scenario grid_ctf --runs 5
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

Prime mode uses the documented sandbox lifecycle (create, wait, execute command, delete) through the official `prime-sandboxes` SDK. Tune resource limits with `MTS_PRIMEINTELLECT_*` environment variables.

## Dashboard and replay stream

```bash
export MTS_AGENT_PROVIDER=deterministic
uv run mts run --scenario grid_ctf --gens 3 --run-id dashboard_seed
uv run mts serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` for the dashboard UI.

## Architecture overview

### Generation loop

For each generation within a run:

1. **Scenario setup** — Load scenario, read playbook/tools/hints/analysis from knowledge directory
2. **Knowledge injection** — Build score trajectory, strategy registry, and latest analysis into prompts
3. **Agent orchestration** — `AgentOrchestrator` runs five LLM roles: competitor (sequential), then analyst/coach/architect (parallel), with optional curator post-processing
4. **Tournament** — Execute N matches, score with Elo updates
5. **Backpressure gate** — Decide `advance`/`retry`/`rollback` based on score delta
6. **Curator quality gate** — (if enabled) Opus-level agent assesses proposed playbook vs current; accepts, rejects, or merges
7. **Persistence** — Results, metrics, replays, versioned playbooks, and knowledge snapshots saved to SQLite + filesystem

### Agent roles

| Role | Model | Purpose |
|------|-------|---------|
| **Competitor** | Sonnet | Produces strategy JSON matching the scenario interface |
| **Analyst** | Sonnet | Markdown analysis (Findings, Root Causes, Recommendations) |
| **Coach** | Opus | Updates playbook, extracts lessons, provides competitor hints |
| **Architect** | Opus | Proposes tooling improvements, emits Python tool code |
| **Curator** | Opus | Quality-gates playbook updates, consolidates lessons |
| **Translator** | Sonnet | Extracts clean JSON from competitor narrative |

### Knowledge feedback loop

The knowledge system ensures agents improve across generations and runs:

```
knowledge/<scenario>/
  playbook.md              # Current consolidated strategy playbook
  playbook_versions/       # Archived prior playbook versions (max N)
  hints.md                 # Coach-generated competitor hints (persist across restarts)
  analysis/gen_N.md        # Per-generation analyst output
  coach_history.md         # Full coach output audit trail
  architect/changelog.md   # Architect decisions log
  tools/                   # Architect-generated Python tools
  tools/_archive/          # Prior tool versions
  snapshots/<run_id>/      # Cross-run knowledge snapshots (playbook + hints + skills)
  _custom_scenarios/<name>/ # Persisted custom scenarios (scenario.py + spec.json)
```

**What agents see each generation:**

- Full score trajectory table (Gen/Mean/Best/Elo/Gate/Delta)
- Strategy-score registry (which strategies produced which scores)
- Current playbook (curator quality-gated)
- Latest advance analysis from prior generation
- Persisted coach hints
- Operational lessons from SKILL.md (curator-consolidated)
- Architect-generated tools (updateable with version archival)

**Cross-run inheritance:** When a new run starts for a scenario with no existing playbook, the system restores the best-scoring prior run's knowledge snapshot (playbook + hints + skills).

### Scenarios

Pluggable via `SCENARIO_REGISTRY`. Two scenario types:

- **Game scenarios** (`ScenarioInterface`) — Evaluated via tournament matches. Built-in: `grid_ctf`, `othello`.
- **Agent task scenarios** (`AgentTaskInterface`) — Evaluated via LLM judge. Created from natural-language descriptions.

Both coexist in `SCENARIO_REGISTRY` with `hasattr` guards for interface differences.

#### Custom game scenario creation

Create game scenarios from natural language via the TUI or WebSocket API:

```bash
# In the TUI:
/scenario create A tower defense game where you balance economy vs firepower

# Review the generated spec preview, then:
#   Enter → confirm    r → revise with feedback    Esc → cancel

# Once confirmed:
/run tower_defense 5
```

The pipeline: LLM generates a `ScenarioSpec` JSON → template codegen produces a `ScenarioInterface` class → 3-stage validation (spec, AST, execution) → persisted to `knowledge/_custom_scenarios/{name}/` → registered in `SCENARIO_REGISTRY`. Custom scenarios are auto-loaded on server startup.

Custom scenarios use local executor only (PrimeIntellect excluded).

#### Agent task creation

Agent tasks are scenarios where an LLM judge evaluates output quality instead of running tournament matches. The pipeline:

1. LLM generates an `AgentTaskSpec` (task prompt, rubric, output format, judge model, difficulty tiers)
2. Codegen produces an `AgentTaskInterface` subclass with `repr()`-safe string embedding
3. Three-stage validation: spec structure, AST parse, execution (instantiate + call)
4. Persisted to `knowledge/_custom_scenarios/{name}/` with `scenario_type.txt` marker
5. Registered in `SCENARIO_REGISTRY`, auto-loaded on startup

Evaluation uses `LLMJudge` (`execution/judge.py`) which calls an LLM N times and averages scores parsed from `<!-- JUDGE_RESULT_START/END -->` markers.

## Configuration

All config via `MTS_*` environment variables (see `src/mts/config/settings.py`).

### Core settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MTS_AGENT_PROVIDER` | `anthropic` | `deterministic` (offline/CI), `anthropic` (live), or `agent_sdk` (Agent SDK with native tools) |
| `MTS_EXECUTOR_MODE` | `local` | `local`, `primeintellect`, or `monty` |
| `MTS_MODEL_COMPETITOR` | `claude-sonnet-4-5-20250929` | Competitor model |
| `MTS_MODEL_ANALYST` | `claude-sonnet-4-5-20250929` | Analyst model |
| `MTS_MODEL_COACH` | `claude-opus-4-6` | Coach model |
| `MTS_MODEL_ARCHITECT` | `claude-opus-4-6` | Architect model |
| `MTS_MODEL_CURATOR` | `claude-opus-4-6` | Curator model |
| `MTS_MATCHES_PER_GENERATION` | `3` | Tournament matches per generation |
| `MTS_BACKPRESSURE_MIN_DELTA` | `0.005` | Minimum score improvement to advance |
| `MTS_MAX_RETRIES` | `2` | Retry attempts before rollback |
| `MTS_ARCHITECT_EVERY_N_GENS` | `3` | Full architect intervention cadence |

### Knowledge feedback settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MTS_CURATOR_ENABLED` | `true` | Enable curator quality gate and lesson consolidation |
| `MTS_CURATOR_CONSOLIDATE_EVERY_N_GENS` | `3` | Lesson consolidation cadence |
| `MTS_SKILL_MAX_LESSONS` | `30` | Trigger consolidation when lessons exceed this |
| `MTS_PLAYBOOK_MAX_VERSIONS` | `5` | Archived playbook versions to keep |
| `MTS_CROSS_RUN_INHERITANCE` | `true` | Inherit best knowledge across runs |
| `MTS_ABLATION_NO_FEEDBACK` | `false` | Suppress all feedback injection (for A/B testing) |

### Agent task judge settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MTS_JUDGE_MODEL` | `claude-sonnet-4-20250514` | LLM model for agent task evaluation |
| `MTS_JUDGE_SAMPLES` | `1` | Judge calls to average per evaluation |
| `MTS_JUDGE_TEMPERATURE` | `0.0` | Temperature for judge LLM calls |

### RLM settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MTS_RLM_ENABLED` | `false` | Enable REPL-loop mode for analyst/architect |
| `MTS_RLM_MAX_TURNS` | `15` | Max REPL conversation turns |
| `MTS_RLM_SUB_MODEL` | `claude-haiku-4-5-20251001` | Model for REPL sub-calls |
| `MTS_RLM_CODE_TIMEOUT_SECONDS` | `10.0` | Per-code-block execution timeout |

## MCP Server — Connect from Claude Code

MTS exposes its scenarios, knowledge, and run management as MCP tools. Any Claude Code user can connect and interact with MTS directly.

### Setup

```bash
uv sync --extra mcp
```

### Connect from Claude Code

Add to your `.claude/mcp_servers.json`:

```json
{
  "mts": {
    "command": "uv",
    "args": ["--directory", "/path/to/MTS/mts", "run", "--extra", "mcp", "mts", "mcp-serve"]
  }
}
```

### Available tools

| Tool | Description |
|------|-------------|
| `mts_list_scenarios` | List available game scenarios |
| `mts_describe_scenario` | Get rules, strategy interface, criteria |
| `mts_validate_strategy` | Check strategy validity |
| `mts_run_match` | Execute a single match |
| `mts_run_tournament` | Run N matches with aggregate stats |
| `mts_read_playbook` | Read current strategy playbook |
| `mts_read_trajectory` | Get score history for a run |
| `mts_read_hints` | Read coach hints |
| `mts_read_skills` | Read operational lessons |
| `mts_sandbox_create` | Create isolated sandbox |
| `mts_sandbox_run` | Run generations in sandbox |
| `mts_sandbox_status` | Get sandbox status |
| `mts_sandbox_playbook` | Read sandbox playbook |
| `mts_sandbox_list` | List active sandboxes |
| `mts_sandbox_destroy` | Clean up sandbox |
| `mts_export_skill` | Export portable skill package for a scenario |
| `mts_list_solved` | List scenarios with completed runs |
| `mts_search_strategies` | Search solved strategies by natural language |
| `mts_solve_scenario` | Submit a problem for on-demand solving |
| `mts_solve_status` | Check solve job status |
| `mts_solve_result` | Get solve job result |

## Agent SDK mode

MTS agents can use the Claude Agent SDK for richer autonomous reasoning with built-in tool access (Read, Glob, Grep, Bash). Each agent role gets scoped tool permissions:

| Role | Tools |
|------|-------|
| Competitor | Read, Glob, Grep |
| Analyst | Read, Glob, Grep, Bash |
| Coach | Read, Glob, Grep |
| Architect | Read, Glob, Grep, Bash |
| Translator | (none) |
| Curator | Read, Glob, Grep |

```bash
uv sync --extra agent-sdk
export MTS_AGENT_PROVIDER=agent_sdk
export MTS_ANTHROPIC_API_KEY=...
uv run mts run --scenario grid_ctf --gens 3
```

When `agent_sdk` is active, RLM mode is automatically bypassed since the Agent SDK provides native multi-turn tool loops.

### Agent SDK settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MTS_AGENT_SDK_CONNECT_MCP` | `false` | Connect agents to the MTS MCP server |

## Ecosystem mode

The ecosystem loop alternates between provider modes across sequential runs, with the shared `knowledge/<scenario>/` directory connecting them. Each cycle runs Phase A then Phase B, creating a feedback loop where different provider capabilities build on each other's knowledge artifacts.

```
mts ecosystem --scenario grid_ctf --cycles 5 --gens-per-cycle 3

Cycle 1:
  Phase A: anthropic + RLM    -> reads/writes knowledge/grid_ctf/
  Phase B: agent_sdk           -> reads/writes knowledge/grid_ctf/
Cycle 2:  (inherits all knowledge from cycle 1)
  Phase A: anthropic + RLM    -> reads/writes knowledge/grid_ctf/
  Phase B: agent_sdk           -> reads/writes knowledge/grid_ctf/
```

No special cross-phase transfer logic is needed — the `knowledge/<scenario>/` directory (playbook, hints, tools, analysis, skills) is the shared mutable state. Provider metadata is tracked in the database for each run and knowledge snapshot.

| Option | Default | Description |
|--------|---------|-------------|
| `--scenario` | `grid_ctf` | Scenario to run |
| `--cycles` | `3` | Number of full A+B cycles |
| `--gens-per-cycle` | `3` | Generations per phase |
| `--provider-a` | `anthropic` | Provider for Phase A |
| `--provider-b` | `agent_sdk` | Provider for Phase B |
| `--rlm-a/--no-rlm-a` | `--rlm-a` | Enable RLM for Phase A |
| `--rlm-b/--no-rlm-b` | `--no-rlm-b` | Enable RLM for Phase B |

## Sandboxed play

External MCP users can create isolated sandbox environments to experiment with strategy evolution without affecting MTS-internal knowledge.

Each sandbox gets:
- Its own SQLite database and runs directory
- Knowledge seeded from the main MTS knowledge base (playbook, hints, tools)
- Isolated knowledge evolution (changes stay within the sandbox)

```
# Via MCP tools:
mts_sandbox_create(scenario_name="grid_ctf", user_id="alice")
mts_sandbox_run(sandbox_id="sbx_alice_abc12345", generations=3)
mts_sandbox_playbook(sandbox_id="sbx_alice_abc12345")
mts_sandbox_destroy(sandbox_id="sbx_alice_abc12345")
```

### Sandbox settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MTS_SANDBOX_MAX_GENERATIONS` | `10` | Maximum generations per sandbox run |

## Future: Open Competition

Sandboxed play currently isolates external users from MTS-internal runs. A future iteration may enable open competition where external Agent SDK agents submit strategies to compete in shared MTS tournaments.

## Development

```bash
# Lint and type check
uv run ruff check src tests
uv run mypy src

# Tests
uv run pytest                              # all tests
uv run pytest tests/test_elo.py            # single file
uv run pytest tests/test_elo.py -k "test_name"  # single test

# Smoke test
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 3 --run-id smoke
```

## One-command demo

From repository root:

```bash
bash infra/scripts/bootstrap.sh
bash scripts/demo.sh
```
