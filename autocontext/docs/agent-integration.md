# External Agent Integration Guide

autocontext provides three integration surfaces for external agents: the `autoctx` CLI, an MCP server, and a Python SDK. This guide covers them in order of recommended usage.

## Why CLI-First

The `autoctx` CLI is the default integration surface for external agents. Unix-style CLI interfaces are a natural fit for LLM agents:

- **Everything is text.** Commands accept text arguments and return text output. No serialization protocol to negotiate.
- **Commands compose cleanly.** Pipe, redirect, chain with `&&` — standard shell patterns that agents already handle well.
- **Success and failure are explicit.** Exit code 0 means success; non-zero means failure. No ambiguous status fields to parse.
- **stdout/stderr separation is a proven machine-usable contract.** Data goes to stdout, diagnostics and errors go to stderr.
- **Agents already perform well with shell-style interaction patterns.** Most LLM agents have extensive training on CLI usage.

In practice, users have reported better experiences integrating via the CLI than via MCP. The CLI is simpler to set up, easier to debug, and more predictable.

## CLI Integration Patterns

### Machine-Readable Output (`--json`)

Most `autoctx` commands accept a `--json` flag that switches output to structured JSON:

```bash
# Structured JSON to stdout
autoctx list --json
autoctx status <run_id> --json
autoctx run --scenario grid_ctf --gens 3 --json
autoctx export --scenario grid_ctf --json
autoctx train --scenario grid_ctf --data data.jsonl --json
```

**Contract:**
- **stdout** receives the JSON payload (one JSON object per line).
- **stderr** receives errors in the format `{"error": "description"}`.
- **Exit code 0** means the command succeeded. The JSON payload is on stdout.
- **Exit code 1** means the command failed. An error JSON is on stderr.

### Command Reference

#### `autoctx run` — Execute a scenario

```bash
# Game scenario (tournament-based)
autoctx run --scenario grid_ctf --gens 5 --run-id my_run --json

# Agent task scenario (judge-based evaluation)
autoctx run --scenario my_agent_task --gens 3 --json
```

JSON output shape:
```json
{
  "run_id": "my_run",
  "scenario": "grid_ctf",
  "best_score": 0.85,
  "generations_executed": 5,
  "current_elo": 1523.4
}
```

#### `autoctx status` — Check run progress

```bash
autoctx status <run_id> --json
```

JSON output shape:
```json
{
  "run_id": "abc123",
  "generations": [
    {
      "generation": 1,
      "mean_score": 0.72,
      "best_score": 0.85,
      "elo": 1523.4,
      "wins": 3,
      "losses": 2,
      "gate_decision": "advance",
      "status": "completed"
    }
  ]
}
```

#### `autoctx list` — List recent runs

```bash
autoctx list --json
```

Returns an array of run summaries:
```json
[
  {
    "run_id": "abc123",
    "scenario": "grid_ctf",
    "target_generations": 5,
    "executor_mode": "local",
    "status": "completed",
    "created_at": "2026-03-13T10:00:00"
  }
]
```

#### Monitoring long-running work

For run completion, external agents should still poll `autoctx status --json` (and related read surfaces such as `list --json`) until the desired condition is visible.

Simple polling pattern:

```bash
while true; do
  current=$(autoctx status "$RUN_ID" --json)
  state=$(echo "$current" | jq -r '.generations[-1].status // "unknown"')
  if [ "$state" = "completed" ] || [ "$state" = "failed" ]; then
    break
  fi
  sleep 5
done
```

If you are waiting on a monitor condition instead of a run status transition, the Python CLI also exposes `autoctx wait`:

```bash
autoctx wait <condition_id> --timeout 30 --json
```

JSON output shape on success:

```json
{
  "fired": true,
  "condition_id": "cond_123",
  "alert": {
    "detail": "score dropped below threshold"
  }
}
```

JSON output shape on timeout:

```json
{
  "fired": false,
  "condition_id": "cond_123",
  "timeout_seconds": 30
}
```

#### `autoctx export` — Export a strategy package

```bash
autoctx export --scenario grid_ctf --output pkg.json --json
```

JSON output shape:
```json
{
  "scenario": "grid_ctf",
  "output_path": "pkg.json",
  "best_score": 0.92,
  "lessons_count": 12,
  "harness_count": 3
}
```

#### `autoctx train` — Run a training loop

```bash
autoctx train --scenario grid_ctf --data training.jsonl --time-budget 300 --json
```

JSON output shape:
```json
{
  "scenario": "grid_ctf",
  "total_experiments": 8,
  "kept_count": 5,
  "discarded_count": 3,
  "best_score": 0.89,
  "checkpoint_path": "workspace/checkpoint.pt"
}
```

#### `autoctx import-package` — Import a strategy package

```bash
autoctx import-package --file grid_ctf_package.json --json
```

JSON output shape:
```json
{
  "scenario_name": "grid_ctf",
  "playbook_written": true,
  "hints_written": true,
  "skill_written": true,
  "harness_written": 2,
  "harness_skipped": 0,
  "conflict_policy": "merge"
}
```

### Error Handling

All commands follow the same error contract when `--json` is passed:

```bash
# On error, stderr receives:
{"error": "Run 'xyz' not found"}
# And the exit code is 1
```

Without `--json`, errors appear as formatted Rich console output on stderr.

### Provider Configuration

Configure which LLM provider autocontext uses via environment variables:

```bash
# Anthropic (default)
AUTOCONTEXT_AGENT_PROVIDER=anthropic \
AUTOCONTEXT_ANTHROPIC_API_KEY=sk-ant-... \
autoctx run --scenario my_task --json

# OpenAI-compatible
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_JUDGE_PROVIDER=openai-compatible \
AUTOCONTEXT_JUDGE_API_KEY=sk-... \
AUTOCONTEXT_JUDGE_BASE_URL=https://api.openai.com/v1 \
autoctx run --scenario my_task --json

# Ollama (local, no API key needed)
AUTOCONTEXT_AGENT_PROVIDER=ollama \
AUTOCONTEXT_JUDGE_PROVIDER=ollama \
autoctx run --scenario my_task --json

# Pi CLI (local Pi agent runtime)
AUTOCONTEXT_AGENT_PROVIDER=pi \
AUTOCONTEXT_PI_COMMAND=pi \
AUTOCONTEXT_PI_TIMEOUT=120 \
autoctx run --scenario my_task --json

# Pi RPC (Pi agent via HTTP RPC — supports session persistence)
AUTOCONTEXT_AGENT_PROVIDER=pi-rpc \
AUTOCONTEXT_PI_RPC_ENDPOINT=http://localhost:3284 \
AUTOCONTEXT_PI_RPC_API_KEY=your-key \
autoctx run --scenario my_task --json
```

Key environment variables:

| Variable | Purpose |
|---|---|
| `AUTOCONTEXT_AGENT_PROVIDER` | Agent provider: `anthropic`, `openai-compatible`, `ollama`, `vllm`, `pi`, `pi-rpc`, `deterministic` |
| `AUTOCONTEXT_JUDGE_PROVIDER` | Judge provider (defaults to `anthropic`) |
| `AUTOCONTEXT_JUDGE_API_KEY` | API key for the judge provider |
| `AUTOCONTEXT_JUDGE_BASE_URL` | Base URL for OpenAI-compatible judge endpoints |
| `AUTOCONTEXT_JUDGE_MODEL` | Override judge model name |
| `AUTOCONTEXT_MODEL_COMPETITOR` | Override competitor agent model |
| `AUTOCONTEXT_DB_PATH` | SQLite database path |
| `AUTOCONTEXT_PI_COMMAND` | Path to Pi CLI binary (default: `pi`) |
| `AUTOCONTEXT_PI_TIMEOUT` | Pi CLI execution timeout in seconds (default: 120) |
| `AUTOCONTEXT_PI_WORKSPACE` | Pi CLI working directory |
| `AUTOCONTEXT_PI_MODEL` | Manual Pi model override (pins a specific checkpoint/path) |
| `AUTOCONTEXT_PI_RPC_ENDPOINT` | Pi RPC server URL (default: `http://localhost:3284`) |
| `AUTOCONTEXT_PI_RPC_API_KEY` | Pi RPC API key |
| `AUTOCONTEXT_PI_RPC_SESSION_PERSISTENCE` | Persist Pi sessions across turns (default: `true`) |

#### Pi CLI vs Pi RPC

**Pi CLI** (`AUTOCONTEXT_AGENT_PROVIDER=pi`) invokes the `pi` binary in non-interactive `--print` mode for each agent turn. Best for:
- Simple setups where Pi is installed locally
- Stateless, one-shot agent executions
- CI/testing environments

**Pi RPC** (`AUTOCONTEXT_AGENT_PROVIDER=pi-rpc`) communicates with Pi via HTTP RPC. Best for:
- Session persistence across multi-turn improvement loops
- Branch-on-retry strategies (Pi creates branches for each retry)
- Remote Pi instances running as a service

Both support **scenario-aware model handoff** when scenario context is available and no manual Pi model override is set. In that case, autocontext checks the distillation model registry for a scenario-specific checkpoint and routes to it automatically. If `AUTOCONTEXT_PI_MODEL` is set, that value is treated as a manual pin and used directly instead of consulting the registry. This enables the distill→deploy loop where a fine-tuned model is used for specific scenarios while still allowing operators to force a specific checkpoint when needed.

### Concrete CLI-First Integration Example

An external agent integrating with autocontext via CLI:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCENARIO="grid_ctf"
RUN_ID="agent_run_$(date +%s)"

# 1. Start a run and capture structured output
result=$(autoctx run \
  --scenario "$SCENARIO" \
  --gens 3 \
  --run-id "$RUN_ID" \
  --json 2>/dev/null)

best_score=$(echo "$result" | jq -r '.best_score')
echo "Run completed. Best score: $best_score" >&2

# 2. Check detailed status
autoctx status "$RUN_ID" --json | jq '.generations[-1]'

# 3. Export the strategy package
autoctx export --scenario "$SCENARIO" --output "${SCENARIO}_pkg.json" --json

# 4. Training loop (if training data available)
if [ -f "training/${SCENARIO}.jsonl" ]; then
  autoctx train \
    --scenario "$SCENARIO" \
    --data "training/${SCENARIO}.jsonl" \
    --time-budget 120 \
    --json
fi
```

## MCP Integration (Secondary)

Use MCP when your agent framework specifically requires a tool-catalog protocol (e.g., Claude Code with tool discovery). For most agent integrations, the CLI is simpler.

### When to Use MCP

- Your agent runtime expects MCP tool discovery and invocation
- You need interactive, stateful tool sessions (e.g., sandbox create/run/destroy)
- You want to expose autocontext as a tool provider in a multi-tool agent

### When to Prefer CLI

- Your agent can execute shell commands (most can)
- You want simpler setup and debugging
- You need reliable exit codes and stdout/stderr separation
- You're scripting a workflow or pipeline

### Starting the MCP Server

```bash
# Install MCP dependencies
uv sync --group dev --extra mcp

# Start on stdio
uv run autoctx mcp-serve
```

The server uses the stdio transport and exposes tools with the `autocontext_` prefix. Key tool groups:

- **Evaluation**: `autocontext_evaluate_output`, `autocontext_generate_output`
- **Knowledge**: `autocontext_read_playbook`, `autocontext_search_strategies`, `autocontext_export_skill`
- **Runs**: `autocontext_list_runs`, `autocontext_run_status`
- **Scenarios**: `autocontext_list_scenarios`, `autocontext_describe_scenario`
- **Sandbox**: `autocontext_sandbox_create`, `autocontext_sandbox_run`, `autocontext_sandbox_destroy`

### Claude Code Integration

Add to your project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "autocontext": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/autocontext", "autoctx", "mcp-serve"],
      "env": {
        "AUTOCONTEXT_AGENT_PROVIDER": "anthropic",
        "AUTOCONTEXT_ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

### Concrete MCP Example

Once the server is running, invoke tools via the MCP protocol:

```json
{
  "method": "tools/call",
  "params": {
    "name": "autocontext_evaluate_output",
    "arguments": {
      "task_prompt": "Write a haiku about testing",
      "agent_output": "Tests catch the errors\nBefore users ever see\nGreen builds bring me joy",
      "rubric": "Evaluate: (1) valid 5-7-5 haiku format, (2) relevance to testing, (3) creativity"
    }
  }
}
```

Response:
```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"score\": 0.87, \"reasoning\": \"Valid haiku format...\"}"
    }
  ]
}
```

## Python SDK (Programmatic)

For Python agents that want to skip the CLI, the package also exposes a typed SDK:

```python
from autocontext.sdk import AutoContext

ac = AutoContext()

# List available scenarios
scenarios = ac.list_scenarios()

# Evaluate a strategy
result = ac.evaluate(
    scenario="grid_ctf",
    strategy={"type": "aggressive", "target": "flag"},
)
print(f"Best score: {result.best_score}")

# Export a strategy package
package = ac.export_package("grid_ctf")
```

## TypeScript CLI

The TypeScript package also publishes a narrower `autoctx` CLI for Node.js environments. It focuses on judge-based evaluation, improvement loops, task queueing, and MCP serving rather than the full multi-generation control plane:

```bash
npx autoctx judge -p "Write a haiku" -o "output text" -r "evaluate quality"
npx autoctx improve -p "Write a haiku" -o "draft" -r "evaluate quality" -n 3
npx autoctx status
npx autoctx serve  # MCP server on stdio
```

Key entrypoints live in:

- `ts/src/cli/index.ts`
- `ts/src/index.ts`

See [`../../ts/README.md`](../../ts/README.md) for install instructions, provider configuration, and library examples.
