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
- **Provider routing**: Anthropic, OpenAI-compatible, Gemini, Mistral, Groq, OpenRouter, Azure OpenAI, Ollama, vLLM, Hermes, Pi, Pi-RPC, deterministic
- **Evaluation**: one-shot judging, multi-round improvement loops, REPL-loop sessions
- **Package management**: strategy package export/import, training data export
- **Training hook surface**: dataset validation and executor-backed `train` entry point
- **Production-traces emit SDK** at `autoctx/production-traces` — customer-facing emit APIs mirroring the Python SDK (A2-II-a)

## Install

```bash
npm install autoctx
```

The current npm release line is `autoctx@0.4.4`.
Important: use `autoctx`, not `autocontext`.
`autocontext` on npm is a different package and not this project.

From source:

```bash
cd ts
npm install
npm run build
```

## Emit SDK: `autoctx/production-traces`

Customer applications can emit production traces directly from their
TypeScript code using the `autoctx/production-traces` subpath. This is the
TS mirror of the Python `autocontext.production_traces` emit module;
customers using both languages get one mental model, enforced at the byte
level by cross-runtime property tests.

```ts
import {
  buildTrace,
  writeJsonl,
  TraceBatch,
  hashUserId,
  loadInstallSalt,
} from "autoctx/production-traces";

// 1) Hash personally-identifying identifiers with the install salt.
const salt = (await loadInstallSalt(process.cwd())) ?? "";
const userIdHash = hashUserId(session.user.id, salt);

// 2) Build and validate a ProductionTrace. Throws ValidationError on
//    invalid input with per-field detail.
const trace = buildTrace({
  provider: "openai",
  model: "gpt-4o-mini",
  messages: [
    { role: "user", content: prompt, timestamp: new Date().toISOString() },
  ],
  timing: {
    startedAt: "2026-04-17T12:00:00.000Z",
    endedAt: "2026-04-17T12:00:01.250Z",
    latencyMs: 1250,
  },
  usage: { tokensIn: 42, tokensOut: 88 },
  env: { environmentTag: "production", appId: "my-app" },
  session: { userIdHash },
});

// 3) Persist: one file per call, or batch across many calls.
writeJsonl(trace);

const batch = new TraceBatch();
for (const event of stream) batch.add(buildTrace(/* ... */));
batch.flush();  // writes accumulated traces as one file
```

Both ESM and CommonJS consumers are supported via the `"exports"` map:

```ts
// ESM
import { buildTrace } from "autoctx/production-traces";

// CJS
const { buildTrace } = require("autoctx/production-traces");
```

### Zero telemetry

**Traces go where you put them.** The SDK itself emits zero telemetry
about its own usage. No analytics, no phone-home, no opt-out toggle
needed. CI script `check:no-telemetry` greps the SDK source plus every
transitive dep for suspicious network patterns on every PR.

### Enterprise-discipline guarantees

- **Bundle size**: ~48 kB gzipped at ship, enforced in CI at a
  100 kB ceiling. Tree-shakable via `"sideEffects"` discipline.
- **License compatibility**: every dep in the SDK's transitive closure
  carries an MIT / Apache-2.0 / BSD / ISC / 0BSD license, enforced by
  `check:license-compatibility`.
- **No install scripts**: `autoctx` declares no `preinstall`,
  `install`, or `postinstall` lifecycle hooks. Safe to deploy with
  `npm install --ignore-scripts`.
- **Dual ESM + CJS**: both `import` and `require()` work via the
  `package.json` `"exports"` map.
- **Cross-runtime parity**: TS `buildTrace` and Python `build_trace`
  produce byte-identical canonical JSON, enforced at 50 property runs
  plus 7 committed fixtures.

See [`src/production-traces/sdk/STABILITY.md`](src/production-traces/sdk/STABILITY.md)
for the API-stability commitment and
[`src/production-traces/sdk/BUDGET.md`](src/production-traces/sdk/BUDGET.md)
for the bundle-size budget details.

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
autoctx investigate -d "checkout is failing" --browser-url https://status.example.com
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
autoctx improve -p <prompt> -r <rubric> [-n rounds]
autoctx improve -p <prompt> -o <output> -r <rubric> [-n rounds]
autoctx improve --scenario my_saved_task [-o <output>]
autoctx repl --scenario my_saved_task

# Task queue
autoctx queue -s <spec> [--priority N] [--browser-url https://status.example.com]
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

# Role-scoped override: competitor uses a separate gateway/key
AUTOCONTEXT_AGENT_PROVIDER=anthropic \
ANTHROPIC_API_KEY=sk-ant-primary \
AUTOCONTEXT_COMPETITOR_PROVIDER=openai-compatible \
AUTOCONTEXT_COMPETITOR_API_KEY=sk-role \
AUTOCONTEXT_COMPETITOR_BASE_URL=http://localhost:8000/v1 \
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

# Claude CLI (local authenticated Claude Code runtime)
AUTOCONTEXT_AGENT_PROVIDER=claude-cli \
AUTOCONTEXT_CLAUDE_MODEL=sonnet \
autoctx run --scenario support_triage --json

# Codex CLI (local authenticated Codex runtime)
AUTOCONTEXT_AGENT_PROVIDER=codex \
AUTOCONTEXT_CODEX_MODEL=o4-mini \
autoctx run --scenario support_triage --json

# Pi CLI
AUTOCONTEXT_AGENT_PROVIDER=pi autoctx run --scenario support_triage --json

# Deterministic (CI/testing)
AUTOCONTEXT_AGENT_PROVIDER=deterministic autoctx run --scenario support_triage --json
```

`ANTHROPIC_API_KEY` is the preferred Anthropic credential env var. `AUTOCONTEXT_ANTHROPIC_API_KEY` remains supported as a compatibility alias.

Supported providers: `anthropic`, `openai`, `openai-compatible`, `gemini`, `mistral`, `groq`, `openrouter`, `azure-openai`, `ollama`, `vllm`, `hermes`, `claude-cli`, `codex`, `pi`, `pi-rpc`, `deterministic`.

`autoctx simulate` and `autoctx investigate` require a configured provider for spec generation. If you want synthetic placeholder behavior for CI/testing, select the deterministic provider explicitly instead of relying on implicit fallback.

Key environment variables:

| Variable | Purpose |
|----------|---------|
| `AUTOCONTEXT_AGENT_PROVIDER` | Agent provider selection |
| `AUTOCONTEXT_AGENT_API_KEY` | Global API key override (or use provider-specific env vars) |
| `AUTOCONTEXT_AGENT_BASE_URL` | Global base URL override for compatible providers |
| `AUTOCONTEXT_AGENT_DEFAULT_MODEL` | Override default model |
| `AUTOCONTEXT_COMPETITOR_API_KEY` / `AUTOCONTEXT_COMPETITOR_BASE_URL` | Optional competitor-specific credential/endpoint override |
| `AUTOCONTEXT_ANALYST_API_KEY` / `AUTOCONTEXT_ANALYST_BASE_URL` | Optional analyst-specific credential/endpoint override |
| `AUTOCONTEXT_COACH_API_KEY` / `AUTOCONTEXT_COACH_BASE_URL` | Optional coach-specific credential/endpoint override |
| `AUTOCONTEXT_ARCHITECT_API_KEY` / `AUTOCONTEXT_ARCHITECT_BASE_URL` | Optional architect-specific credential/endpoint override |
| `AUTOCONTEXT_CLAUDE_MODEL` | Claude CLI model alias override |
| `AUTOCONTEXT_CODEX_MODEL` | Codex CLI model override |
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

Saved custom scenarios under `knowledge/_custom_scenarios/` can be reused directly from the TS CLI. Saved parametric scenarios can now be targeted by name in `run` and `benchmark`, while saved agent-task scenarios remain directly usable in `judge`, `improve`, `repl`, and `queue` without retyping their prompt and rubric.

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

The TypeScript package includes the current 0.4.x operator-facing surfaces:

- `simulate`
- `investigate`
- `analyze`
- `mission`
- `train` as a validation plus executor-hook surface

`campaign` now ships as a first-class TypeScript CLI/API/MCP workflow for multi-mission coordination.

For end-to-end local MLX/CUDA training, the Python package is still the canonical out-of-the-box runtime.

## Browser Exploration Contract

The TypeScript package exposes the shared browser exploration contract and policy helpers from the package root. Browser exploration is disabled by default and configured through `AUTOCONTEXT_BROWSER_*` settings such as `AUTOCONTEXT_BROWSER_ENABLED`, `AUTOCONTEXT_BROWSER_ALLOWED_DOMAINS`, and `AUTOCONTEXT_BROWSER_PROFILE_MODE`.

Use `resolveBrowserSessionConfig(...)`, `evaluateBrowserActionPolicy(...)`, and the `validateBrowser*` helpers when integrating a browser backend or agent harness.

When browser exploration is enabled, the TS CLI can capture a policy-gated Chrome DevTools Protocol snapshot and attach it as evidence for `autoctx investigate --browser-url <url>`. Queued agent tasks can also store `--browser-url`; the runner resolves it through an injected browser-context service so enterprise deployments can keep browser access disabled by default, domain-scoped, and audit-artifact backed.

## Python-Only Commands

These workflows require infrastructure not available in the npm package:

- `ecosystem` — Multi-provider cycling
- `ab-test` — Requires ecosystem runner
- `resume` / `wait` — Run recovery infrastructure
- `trigger-distillation` — Training pipeline
- Monitor conditions — Monitoring engine

`train` is exposed in the TS CLI as a validation plus executor-hook surface, but the npm package does not bundle a real MLX/CUDA trainer. For end-to-end local training, use the Python package (`pip install autocontext`) or inject a real `TrainingRunner` executor from code.

## OpenAI integration

Autocontext ships a zero-configuration OpenAI instrumentation path that
automatically wraps your existing `new OpenAI(...)` calls and emits structured
traces to a sink of your choice.

### 1. Register detectors

Create `.autoctx.instrument.config.mjs` at the root of your repo:

```js
// .autoctx.instrument.config.mjs
import { registerDetectorPlugin } from "autoctx/control-plane/instrument";
import { plugin as openaiTsPlugin } from "autoctx/detectors/openai-ts";

registerDetectorPlugin(openaiTsPlugin);
```

### 2. Run instrument

Preview changes without touching any files:

```bash
autoctx instrument --dry-run
```

Apply changes on a new branch for review:

```bash
autoctx instrument --apply --branch autoctx/instrument
```

### 3. Review the PR

The instrument command opens a branch. Open the PR and review the diff — you
will see your `new OpenAI(...)` calls wrapped with `instrumentClient(...)`.
Edit the generated TODO comment to point at your `FileSink`:

```ts
// Before (generated):
const client = instrumentClient(new OpenAI(), { sink: /* TODO: pass your TraceSink here */ });

// After (your edit):
import { FileSink } from "autoctx/integrations/openai";
const sink = new FileSink("./traces/openai.jsonl");
const client = instrumentClient(new OpenAI(), { sink });
```

Merge the PR.

### 4. Customer code emits traces

Your code is unchanged beyond the wrap. Every `chat.completions.create` call
now emits a JSONL trace line to your sink:

```ts
import OpenAI from "openai";
import { instrumentClient, FileSink, autocontextSession } from "autoctx/integrations/openai";

const sink = new FileSink("./traces/openai.jsonl");
const client = instrumentClient(new OpenAI(), { sink });

await autocontextSession({ userId: "u_123" }, async () => {
  const res = await client.chat.completions.create({
    model: "gpt-4o",
    messages: [{ role: "user", content: "Hello!" }],
  });
  console.log(res.choices[0].message.content);
});

await sink.close();
```

Emitted trace line (pretty-printed for readability):

```jsonl
{"schemaVersion":"1.0","traceId":"...","sessionContext":{"userId":"u_123"},"request":{"model":"gpt-4o","messages":[{"role":"user","content":"Hello!"}]},"response":{"id":"...","choices":[{"message":{"role":"assistant","content":"Hi! How can I help?"},"finish_reason":"stop"}],"usage":{"prompt_tokens":9,"completion_tokens":7,"total_tokens":16}},"durationMs":342,"errorReason":null}
```

For the Python equivalent, see
`autocontext/src/autocontext/integrations/openai/STABILITY.md`.

## Anthropic integration

Autocontext ships a zero-configuration Anthropic instrumentation path that
automatically wraps your existing `new Anthropic(...)` calls and emits structured
traces to a sink of your choice.

### 1. Register detectors

Create `.autoctx.instrument.config.mjs` at the root of your repo:

```js
// .autoctx.instrument.config.mjs
import { registerDetectorPlugin } from "autoctx/control-plane/instrument";
import { plugin as anthropicTsPlugin } from "autoctx/detectors/anthropic-ts";

registerDetectorPlugin(anthropicTsPlugin);
```

### 2. Run instrument

Preview changes without touching any files:

```bash
autoctx instrument --dry-run
```

Apply changes on a new branch for review:

```bash
autoctx instrument --apply --branch autoctx/instrument
```

### 3. Review the PR

The instrument command opens a branch. Open the PR and review the diff — you
will see your `new Anthropic(...)` calls wrapped with `instrumentClient(...)`.
Edit the generated TODO comment to point at your `FileSink`:

```ts
// Before (generated):
const client = instrumentClient(new Anthropic(), { sink: /* TODO: pass your TraceSink here */ });

// After (your edit):
import { FileSink } from "autoctx/integrations/anthropic";
const sink = new FileSink("./traces/anthropic.jsonl");
const client = instrumentClient(new Anthropic(), { sink });
```

Merge the PR.

### 4. Customer code emits traces

Your code is unchanged beyond the wrap. Every `messages.create` call now emits
a JSONL trace line to your sink:

```ts
import Anthropic from "@anthropic-ai/sdk";
import { instrumentClient, FileSink, autocontextSession } from "autoctx/integrations/anthropic";

const sink = new FileSink("./traces/anthropic.jsonl");
const client = instrumentClient(new Anthropic(), { sink });

await autocontextSession({ userId: "u_123" }, async () => {
  const res = await client.messages.create({
    model: "claude-opus-4-7-20251101",
    max_tokens: 256,
    messages: [{ role: "user", content: "Hello!" }],
  });
  console.log(res.content[0].type === "text" ? res.content[0].text : "");
});

await sink.close();
```

Emitted trace line (pretty-printed for readability):

```jsonl
{"schemaVersion":"1.0","traceId":"...","sessionContext":{"userId":"u_123"},"request":{"model":"claude-opus-4-7-20251101","messages":[{"role":"user","content":"Hello!"}]},"response":{"id":"...","content":[{"type":"text","text":"Hi! How can I help?"}],"stop_reason":"end_turn","usage":{"input_tokens":9,"output_tokens":7}},"durationMs":342,"errorReason":null}
```

For the Python equivalent, see
`autocontext/src/autocontext/integrations/anthropic/STABILITY.md`.

## Development

```bash
cd ts
npm install
npm test              # vitest
npm run lint          # tsc --noEmit
npm run build         # tsc (outputs to dist/)
npm run check:a2-ii-a-all  # enterprise discipline checks for the SDK subpath
```
