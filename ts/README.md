# autoctx — autocontext TypeScript Package

`autoctx` is the Node/TypeScript package for autocontext. It provides the operator-facing CLI, simulation, investigation, analysis, mission, and trace surfaces for Node environments:

The intended use is to point the harness at a real task, simulation, investigation, or mission, let it produce a rich execution history, and then use the returned traces, reports, datasets, packages, and artifacts to improve or operationalize that workflow.

Need the canonical product/runtime vocabulary first? Start with [docs/concept-model.md](../docs/concept-model.md).

- **Scenario execution**: run generation loops with tournament scoring and Elo progression
- **Simulation surface**: plain-language simulations with sweeps, replay, compare, and export
- **Investigation surface**: evidence-driven diagnosis with hypotheses and confidence scoring
- **Analysis surface**: interpret and compare runs, simulations, investigations, and missions
- **Mission surface**: adaptive execution, mission artifacts, and verifier-driven control plane
- **Knowledge system**: versioned playbooks, score trajectories, session reports, dead-end tracking
- **Interactive server**: HTTP API, WebSocket control plane, bundled Ink TUI
- **MCP control plane**: 40+ tools covering scenarios, runs, knowledge, evaluation, feedback, solve, sandbox, and export
- **Provider routing**: Anthropic, OpenAI-compatible, Ollama, vLLM, Hermes, Pi, Pi-RPC, deterministic
- **Evaluation**: one-shot judging, multi-round improvement loops, REPL-loop sessions
- **Package management**: strategy package export/import, training data export
- **Training hook surface**: dataset validation and executor-backed `train` entry point

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

The package ships a full `autoctx` CLI with commands including:

```bash
# Project setup and discovery
autoctx init
autoctx capabilities
autoctx login
autoctx whoami
autoctx logout
autoctx providers
autoctx models

# Scenario execution
autoctx run --scenario support_triage --gens 3 --json
autoctx list --json
autoctx replay --run-id <id> --generation 1
autoctx benchmark --scenario support_triage --runs 5

# Package management
autoctx export --scenario support_triage --output pkg.json
autoctx export-training-data --run-id <id> --output data.jsonl
autoctx import-package --file pkg.json
autoctx new-scenario --description "Test summarization quality"
autoctx new-scenario --template prompt-optimization --name support_triage

# Interactive, simulations, and missions
autoctx tui [--port 8000]
autoctx serve [--port 8000] [--json] # HTTP API
autoctx mcp-serve                     # MCP server on stdio
autoctx simulate -d "simulate deploying a web service with rollback"
autoctx simulate -d "simulate escalation thresholds" --sweep max_escalations=1:5:1
autoctx investigate -d "why did conversion drop after Tuesday's release"
autoctx analyze --id deploy_sim --type simulation
autoctx analyze --left sim_a --right sim_b --type simulation
autoctx mission create --name "Ship login" --goal "Implement OAuth"
autoctx mission create --type code --name "Fix login" --goal "Tests pass" --repo-path . --test-command "npm test"
autoctx mission run --id <mission-id> --max-iterations 3
autoctx mission status --id <mission-id>
autoctx mission artifacts --id <mission-id>
autoctx train --scenario support_triage --dataset data.jsonl --backend cuda

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
ANTHROPIC_API_KEY=sk-ant-... autoctx run --scenario support_triage --json

# OpenAI-compatible
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_AGENT_API_KEY=sk-... \
AUTOCONTEXT_AGENT_BASE_URL=https://api.openai.com/v1 \
autoctx run --scenario support_triage --json

# Ollama (local)
AUTOCONTEXT_AGENT_PROVIDER=ollama autoctx run --scenario support_triage --json

# Hermes (via OpenAI-compatible gateway)
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1 \
AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b \
autoctx run --scenario support_triage --json

# Hermes shortcut provider (same gateway path, Hermes defaults)
AUTOCONTEXT_AGENT_PROVIDER=hermes \
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1 \
autoctx run --scenario support_triage --json

# Pi CLI
AUTOCONTEXT_AGENT_PROVIDER=pi autoctx run --scenario support_triage --json

# Deterministic (CI/testing)
AUTOCONTEXT_AGENT_PROVIDER=deterministic autoctx run --scenario support_triage --json
```

Supported providers: `anthropic`, `openai`, `openai-compatible`, `ollama`, `vllm`, `hermes`, `pi`, `pi-rpc`, `deterministic`.

`autoctx simulate` and `autoctx investigate` require a configured provider for spec generation. If you want synthetic placeholder behavior for CI/testing, select the deterministic provider explicitly instead of relying on implicit fallback.

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

`autoctx capabilities` returns structured JSON describing commands, providers, scenarios, the canonical concept model, and project-specific state such as the current project config, active runs, and knowledge directory summary.

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
| Missions | create_mission, mission_status, mission_result, mission_artifacts, pause_mission, resume_mission, cancel_mission |
| Discovery | capabilities |

`create_mission` and `autoctx mission create` both support a code-mission variant with `type=code` plus `repo_path` / `test_command` (and optional `lint_command` / `build_command`) so mission success is tied to external checks instead of model self-report.

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
} from "autoctx";

// One-shot evaluation
const provider = createProvider({ providerType: "anthropic", apiKey: "sk-ant-..." });
const judge = new LLMJudge({ provider, rubric: "Score clarity and correctness." });
const result = await judge.evaluate({
  taskPrompt: "Explain binary search.",
  agentOutput: "Binary search halves the search space each step.",
});

// Multi-round improvement
const task = new SimpleAgentTask("Draft a support reply for a billing dispute.", "Score accuracy, policy compliance, and tone.", provider);
const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
const improved = await loop.run({ initialOutput: "We can help with that billing issue.", state: {} });
```

## TS / Python Scope

The TypeScript package includes the operator-facing 0.3.0 surfaces:

- `simulate`
- `investigate`
- `analyze`
- `mission`
- `train` as a validation plus executor-hook surface

`campaign` is still part of the concept model, but it is not yet a shipped first-class workflow.

For end-to-end local MLX/CUDA training, the Python package is still the canonical out-of-the-box runtime.

## Python-Only Commands

These workflows require infrastructure not available in the npm package:

- `ecosystem` — Multi-provider cycling
- `ab-test` — Requires ecosystem runner
- `resume` / `wait` — Run recovery infrastructure
- `trigger-distillation` — Training pipeline
- Monitor conditions — Monitoring engine

`train` is exposed in the TS CLI as a validation plus executor-hook surface, but the npm package does not bundle a real MLX/CUDA trainer. For end-to-end local training, use the Python package (`pip install autoctx`) or inject a real `TrainingRunner` executor from code.

## Development

```bash
cd ts
npm install
npm test              # vitest
npm run lint          # tsc --noEmit
npm run build         # tsc (outputs to dist/)
```
