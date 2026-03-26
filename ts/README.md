# autoctx — autocontext TypeScript Package

`autoctx` is the Node/TypeScript package for autocontext. It provides the same major operator workflows as the Python package:

- **Scenario execution**: run generation loops with tournament scoring and Elo progression
- **Knowledge system**: versioned playbooks, score trajectories, session reports, dead-end tracking
- **Interactive server**: HTTP dashboard + API, WebSocket control plane, TUI
- **MCP control plane**: 40+ tools covering scenarios, runs, knowledge, evaluation, feedback, solve, sandbox, and export
- **Provider routing**: Anthropic, OpenAI-compatible, Ollama, vLLM, Hermes, Pi, Pi-RPC, deterministic
- **Evaluation**: one-shot judging, multi-round improvement loops, REPL-loop sessions
- **Package management**: strategy package export/import, training data export

## Install

```bash
npm install autoctx
```

Important: use `autoctx`, not `autocontext`.
`autocontext` on npm is a different package and not this project.

From source:

```bash
cd ts
npm install
npm run build
```

## CLI Commands

The package ships a full `autoctx` CLI with 22 commands:

```bash
# Project setup and discovery
autoctx init
autoctx capabilities
autoctx login
autoctx whoami
autoctx logout

# Scenario execution
autoctx run --scenario grid_ctf --gens 3 --json
autoctx list --json
autoctx replay --run-id <id> --generation 1
autoctx benchmark --scenario grid_ctf --runs 5

# Package management
autoctx export --scenario grid_ctf --output pkg.json
autoctx export-training-data --run-id <id> --output data.jsonl
autoctx import-package --file pkg.json
autoctx new-scenario --description "Test summarization quality"

# Interactive
autoctx tui [--port 8000]
autoctx serve [--port 8000] [--json] # HTTP dashboard + API
autoctx mcp-serve                     # MCP server on stdio

# Evaluation
autoctx judge -p <prompt> -o <output> -r <rubric>
autoctx judge --scenario my_saved_task -o <output>
autoctx improve -p <prompt> -o <output> -r <rubric> [-n rounds]
autoctx improve --scenario my_saved_task [-o <output>]
autoctx repl --scenario my_saved_task

# Task queue
autoctx queue -s <spec> [--priority N]
autoctx status
```

## Provider Configuration

Configure the agent provider via environment variables:

```bash
# Anthropic (default)
ANTHROPIC_API_KEY=sk-ant-... autoctx run --scenario grid_ctf --json

# OpenAI-compatible
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_AGENT_API_KEY=sk-... \
AUTOCONTEXT_AGENT_BASE_URL=https://api.openai.com/v1 \
autoctx run --scenario grid_ctf --json

# Ollama (local)
AUTOCONTEXT_AGENT_PROVIDER=ollama autoctx run --scenario grid_ctf --json

# Hermes (via OpenAI-compatible gateway)
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1 \
AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b \
autoctx run --scenario grid_ctf --json

# Hermes shortcut provider (same gateway path, Hermes defaults)
AUTOCONTEXT_AGENT_PROVIDER=hermes \
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1 \
autoctx run --scenario grid_ctf --json

# Pi CLI
AUTOCONTEXT_AGENT_PROVIDER=pi autoctx run --scenario grid_ctf --json

# Deterministic (CI/testing)
AUTOCONTEXT_AGENT_PROVIDER=deterministic autoctx run --scenario grid_ctf --json
```

Supported providers: `anthropic`, `openai`, `openai-compatible`, `ollama`, `vllm`, `hermes`, `pi`, `pi-rpc`, `deterministic`.

Key environment variables:

| Variable | Purpose |
|----------|---------|
| `AUTOCONTEXT_AGENT_PROVIDER` | Agent provider selection |
| `AUTOCONTEXT_AGENT_API_KEY` | API key (or use provider-specific env vars) |
| `AUTOCONTEXT_AGENT_BASE_URL` | Base URL for compatible providers |
| `AUTOCONTEXT_AGENT_DEFAULT_MODEL` | Override default model |
| `AUTOCONTEXT_CONFIG_DIR` | Override where `login` / `whoami` read saved credentials |
| `AUTOCONTEXT_DB_PATH` | SQLite database path |

Credential resolution order is:

1. Environment variables
2. CLI flags
3. Project config (`.autoctx.json`)
4. Credential store (`~/.config/autoctx/credentials.json`)

## Project Defaults

`autoctx init` scaffolds a `.autoctx.json` file in your project. When present, the CLI uses it for:

- Default provider selection
- Default model preference
- Default scenario for `run`, `benchmark`, and `export`
- Project `runs/` and `knowledge/` roots
- The default SQLite database location under the configured `runs_dir`

`autoctx init` also writes an `AGENTS.md` block with the recommended local AutoContext workflow.

`autoctx capabilities` returns structured JSON describing commands, providers, scenarios, and project-specific state such as the current project config, active runs, and knowledge directory summary.

`autoctx login` can prompt interactively for provider credentials. `autoctx login --provider ollama` validates that a local Ollama server is reachable before persisting the connection details, and `autoctx logout` clears the stored credentials.

`autoctx replay` writes the selected generation and available generations to `stderr` before printing the replay JSON payload. `autoctx export-training-data` writes progress updates to `stderr` while keeping JSONL records on `stdout`.

Saved custom agent-task scenarios under `knowledge/_custom_scenarios/` can be referenced by name in `judge`, `improve`, `repl`, and `queue`. That lets a saved scenario spec become directly usable from the TS CLI without retyping its prompt and rubric.

## MCP Tools (40+)

`mcp-serve` starts the MCP server on stdio with tools across these families:

| Family | Tools |
|--------|-------|
| Scenarios | list_scenarios, get_scenario, validate_strategy, run_match, run_tournament, run_scenario |
| Runs | list_runs, get_run_status, get_generation_detail, run_replay |
| Knowledge | get_playbook, read_trajectory, read_hints, read_analysis, read_tools, read_skills |
| Evaluation | evaluate_output, run_improvement_loop, run_repl_session, generate_output |
| Task queue | queue_task, get_queue_status, get_task_result |
| Export/Search | export_skill, export_package, import_package, list_solved, search_strategies |
| Feedback | record_feedback, get_feedback |
| Solve | solve_scenario, solve_status, solve_result |
| Sandbox | sandbox_create, sandbox_run, sandbox_status, sandbox_playbook, sandbox_list, sandbox_destroy |
| Agent tasks | create_agent_task, list_agent_tasks, get_agent_task |
| Discovery | capabilities |

### Claude Code integration

```json
{
  "mcpServers": {
    "autocontext": {
      "command": "npx",
      "args": ["autoctx", "mcp-serve"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

## Library Usage

```ts
import {
  createProvider,
  LLMJudge,
  ImprovementLoop,
  SimpleAgentTask,
  GenerationRunner,
  GridCtfScenario,
  SQLiteStore,
} from "autoctx";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const autoctxRoot = dirname(require.resolve("autoctx/package.json"));

// One-shot evaluation
const provider = createProvider({ providerType: "anthropic", apiKey: "sk-ant-..." });
const judge = new LLMJudge({ provider, rubric: "Score clarity and correctness." });
const result = await judge.evaluate({
  taskPrompt: "Explain binary search.",
  agentOutput: "Binary search halves the search space each step.",
});

// Multi-round improvement
const task = new SimpleAgentTask("Explain binary search.", "Score clarity.", provider);
const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
const improved = await loop.run({ initialOutput: "Binary search is fast.", state: {} });

// Generation loop
const store = new SQLiteStore("autocontext.db");
store.migrate(join(autoctxRoot, "migrations"));
const runner = new GenerationRunner({
  provider,
  scenario: new GridCtfScenario(),
  store,
  runsRoot: "runs",
  knowledgeRoot: "knowledge",
});
const run = await runner.run("my-run", 3);
```

## Python-Only Commands

These workflows require infrastructure not available in the npm package:

- `train` — Requires MLX/CUDA training backends
- `ecosystem` — Multi-provider cycling
- `ab-test` — Requires ecosystem runner
- `resume` / `wait` — Run recovery infrastructure
- `trigger-distillation` — Training pipeline
- Monitor conditions — Monitoring engine

Use the Python package (`pip install autocontext`) for these workflows.

## Development

```bash
cd ts
npm install
npm test              # vitest
npm run lint          # tsc --noEmit
npm run build         # tsc (outputs to dist/)
```
