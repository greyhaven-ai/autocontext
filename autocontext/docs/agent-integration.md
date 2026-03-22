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

# Hermes (via OpenAI-compatible gateway)
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1 \
AUTOCONTEXT_AGENT_API_KEY=hermes-key \
AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b \
autoctx run --scenario my_task --json

# Hermes for both agent and judge
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible \
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1 \
AUTOCONTEXT_AGENT_API_KEY=hermes-key \
AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b \
AUTOCONTEXT_JUDGE_PROVIDER=openai-compatible \
AUTOCONTEXT_JUDGE_BASE_URL=http://localhost:8080/v1 \
AUTOCONTEXT_JUDGE_API_KEY=hermes-key \
AUTOCONTEXT_JUDGE_MODEL=hermes-3-llama-3.1-70b \
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

#### Hermes via OpenAI-Compatible Gateway

Hermes exposes an OpenAI-compatible API server, so the fastest way to connect autocontext to Hermes is through the existing `openai-compatible` provider — no native runtime needed.

**When to use the gateway path:**
- You have a Hermes instance already running (local or remote)
- You want to get started immediately without waiting for native Hermes runtime support
- The OpenAI chat completions API surface is sufficient for your use case

**Caveats:**
- **Model naming**: Use the exact model name your Hermes server reports (e.g. `hermes-3-llama-3.1-8b`). Check `GET /v1/models` on your Hermes endpoint.
- **Determinism**: Hermes temperature behavior may differ from OpenAI. Set `AUTOCONTEXT_JUDGE_TEMPERATURE=0.0` explicitly for reproducible evaluations.
- **Memory/sessions**: The gateway path is stateless per-request. Hermes memory and tool configuration are server-side concerns, not managed by autocontext.
- **Tool access**: Hermes tool/function-calling support depends on your Hermes server configuration. autocontext sends standard chat completion requests.
- **API key**: Local Hermes servers often don't require authentication. Set `AUTOCONTEXT_AGENT_API_KEY=""` or `AUTOCONTEXT_AGENT_API_KEY=no-key` for keyless servers.

**Long-term**: A native Hermes runtime (similar to the Pi CLI/RPC runtimes) would enable deeper integration — session persistence, memory management, tool routing. The gateway path is the recommended approach today.

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

### Hermes CLI-First Starter Workflow

A Hermes agent can drive autocontext entirely through CLI commands. This workflow requires no custom glue code — it uses `autoctx` commands with `--json` output and standard shell primitives.

#### Prerequisites

```bash
# Install autocontext (from repo root)
cd autocontext && uv venv && source .venv/bin/activate && uv sync --group dev

# Set the Hermes gateway env vars once
export AUTOCONTEXT_AGENT_PROVIDER=openai-compatible
export AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1
export AUTOCONTEXT_AGENT_API_KEY=no-key
export AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b

# Optional: use Hermes as the judge too (or keep Anthropic default)
export AUTOCONTEXT_JUDGE_PROVIDER=openai-compatible
export AUTOCONTEXT_JUDGE_BASE_URL=http://localhost:8080/v1
export AUTOCONTEXT_JUDGE_API_KEY=no-key
export AUTOCONTEXT_JUDGE_MODEL=hermes-3-llama-3.1-70b
```

#### Step 1: Discover scenarios

```bash
autoctx list --json | jq '.[].run_id'        # list past runs
# Or: autoctx run --help                      # see available scenarios
```

#### Step 2: Start a run

```bash
RUN_ID="hermes_$(date +%s)"
mkdir -p logs

autoctx run \
  --scenario grid_ctf \
  --gens 5 \
  --run-id "$RUN_ID" \
  --json \
  >"logs/${RUN_ID}.json" \
  2>"logs/${RUN_ID}.err" &
RUN_PID=$!
```

The `--json` flag makes stdout fully machine-readable. `stderr` receives diagnostics. Because `autoctx run` is synchronous, background it when you want to poll progress from another shell loop.

#### Step 3: Poll for completion (long-running jobs)

For runs with many generations, poll `autoctx status` while the backgrounded `run` process is still active:

```bash
while kill -0 "$RUN_PID" 2>/dev/null; do
  status=$(autoctx status "$RUN_ID" --json 2>/dev/null)
  last_gate=$(echo "$status" | jq -r '.generations[-1].gate_decision // "pending"')
  last_gen=$(echo "$status" | jq -r '.generations | length')
  echo "Generation $last_gen: gate=$last_gate" >&2
  sleep 10
done

wait "$RUN_PID"
jq . "logs/${RUN_ID}.json"
```

**Timeouts**: Each `autoctx` command has its own timeout. For runs with many generations, the CLI may take minutes, so run it in the background and poll `status` from the foreground shell.

**Idempotency**: `autoctx run` with the same `--run-id` is idempotent (INSERT OR IGNORE). Re-running is safe.

#### Step 4: Export knowledge

```bash
autoctx export \
  --scenario grid_ctf \
  --output "hermes_knowledge.json" \
  --json | jq .
```

#### Step 5: Solve on demand

```bash
autoctx solve \
  --description "Design a grid capture-the-flag strategy that prioritizes safe flag captures, defends home base when behind, and adapts pathing when lanes are contested." \
  --gens 3 \
  --output "logs/${RUN_ID}_solve_package.json" \
  --json | jq .
```

`autoctx solve` is a synchronous CLI wrapper around the solve-on-demand pipeline. Use the server or MCP solve APIs if you need background job submission and later result retrieval from a long-lived process.

#### When to use which integration path

| Path | Best for | Complexity |
|------|----------|-----------|
| **CLI-first** (this section) | Hermes agents driving `autoctx` via shell commands | Lowest |
| **OpenAI-compatible provider** | autocontext calling Hermes for agent/judge completions | Low |
| **MCP server** | Tool-catalog agents (Claude Code, MCP clients) | Medium |
| **Native Hermes runtime** (future) | Session persistence, memory, tool routing | Highest |

The CLI-first path is recommended for getting started. Move to the provider path when you want autocontext to call Hermes instead of Hermes calling autocontext.

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

### Hermes MCP Integration

Hermes supports MCP servers natively. Add the autocontext MCP server to your Hermes `mcp_servers` configuration to give Hermes agents access to scenario discovery, evaluation, run management, and knowledge export.

#### Configuration

Add to your Hermes `mcp_servers` config (typically `~/.hermes/config.json` or the workspace config):

```json
{
  "mcp_servers": {
    "autocontext": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/autocontext", "autoctx", "mcp-serve"],
      "env": {
        "AUTOCONTEXT_AGENT_PROVIDER": "openai-compatible",
        "AUTOCONTEXT_AGENT_BASE_URL": "http://localhost:8080/v1",
        "AUTOCONTEXT_AGENT_API_KEY": "no-key",
        "AUTOCONTEXT_AGENT_DEFAULT_MODEL": "hermes-3-llama-3.1-8b"
      }
    }
  }
}
```

This starts the autocontext MCP server on stdio when Hermes connects. The server exposes all `autocontext_*` tools.

#### Recommended Tool Allowlists

For safe Hermes exposure, consider allowing tools by category:

**Read-only (safe for any operator):**
- `autocontext_list_scenarios` — Browse available scenarios
- `autocontext_describe_scenario` — Get scenario details, rules, strategy interface
- `autocontext_read_playbook` — Read accumulated strategy playbook
- `autocontext_read_hints` — Read competitor hints
- `autocontext_read_tools` — Read architect-generated tools
- `autocontext_list_runs` — List past runs
- `autocontext_run_status` — Check run progress
- `autocontext_read_trajectory` — Score trajectory for a run
- `autocontext_search_strategies` — Search past strategies by keyword
- `autocontext_list_solved` — List scenarios with exported knowledge

**Evaluation (stateless, safe):**
- `autocontext_evaluate_output` — One-shot judge evaluation
- `autocontext_validate_strategy` — Validate strategy JSON against scenario constraints
- `autocontext_run_match` — Run a single match (deterministic)
- `autocontext_run_tournament` — Run N matches with Elo scoring

**Write operations (require operator trust):**
- `autocontext_run_replay` — Replay a generation
- `autocontext_export_skill` — Export strategy package
- `autocontext_solve_scenario` — Launch a solve job (long-running, creates artifacts)
- `autocontext_sandbox_create` / `autocontext_sandbox_run` / `autocontext_sandbox_destroy` — Sandboxed execution

#### End-to-End Walkthrough

Once configured, a Hermes agent can drive the full autocontext loop:

**1. Discover scenarios:**
```
Use autocontext_list_scenarios to see what's available.
```
→ Returns JSON array of scenario names with descriptions.

**2. Inspect a scenario:**
```
Use autocontext_describe_scenario with scenario_name="grid_ctf".
```
→ Returns rules, strategy interface, evaluation criteria, and scoring dimensions.

**3. Validate a strategy:**
```
Use autocontext_validate_strategy with scenario_name="grid_ctf" and
strategy='{"aggression": 0.6, "defense": 0.4, "path_bias": 0.5}'.
```
→ Returns `{"valid": true, "reason": "ok"}` or validation errors.

**4. Run a tournament:**
```
Use autocontext_run_tournament with scenario_name="grid_ctf",
strategy='{"aggression": 0.6, "defense": 0.4, "path_bias": 0.5}',
matches=5.
```
→ Returns mean/best scores, Elo, wins/losses.

**5. Read the playbook:**
```
Use autocontext_read_playbook with scenario_name="grid_ctf".
```
→ Returns the accumulated playbook markdown (or sentinel if none exists).

**6. Export knowledge:**
```
Use autocontext_export_skill with scenario_name="grid_ctf".
```
→ Returns a portable skill package with playbook, lessons, best strategy.

**7. Install the exported skill into Hermes:**
```
Take the result from autocontext_export_skill, read result.skill_markdown and
result.suggested_filename, and write the markdown into your Hermes skill directory.
```

For raw MCP clients, `autocontext_export_skill` returns structured JSON that now includes:
- `skill_markdown` — the rendered `SKILL.md` contents
- `suggested_filename` — the recommended install filename, such as `grid-ctf-knowledge.md`

Example shell flow once you have the tool result available as JSON:

```bash
mkdir -p "$HERMES_SKILLS_DIR"
printf '%s\n' "$EXPORT_RESULT_JSON" \
  | jq -r '.skill_markdown' \
  > "$HERMES_SKILLS_DIR/$(printf '%s\n' "$EXPORT_RESULT_JSON" | jq -r '.suggested_filename')"
```

After writing the file, restart Hermes or reload its skills so the new knowledge file is picked up.

#### Tool Naming and Ergonomics

All tools use the `autocontext_` prefix (e.g., `autocontext_list_scenarios`). This is deliberate — it prevents collisions in multi-MCP-server setups. In Hermes, the prefix is visible in tool discovery and helps distinguish autocontext tools from other MCP servers.

**Known rough edges:**
- Tool names are verbose — Hermes agents may need explicit instruction to use the `autocontext_` prefix
- `autocontext_solve_scenario` is long-running and returns a `job_id`; poll with `autocontext_solve_status`
- Sandbox tools require explicit create/destroy lifecycle management

#### MCP vs CLI-First for Hermes

| Aspect | MCP | CLI-first |
|--------|-----|-----------|
| **Setup** | Config in `mcp_servers` | Set env vars |
| **Tool discovery** | Automatic (Hermes sees all tools) | Manual (`autoctx --help`) |
| **Output format** | Structured MCP responses | `--json` stdout |
| **Long-running jobs** | Poll via `autocontext_solve_status` | Poll via `autoctx status` |
| **Best for** | Hermes agents with MCP support | Hermes agents with shell access |

Use MCP when Hermes has native MCP client support and you want automatic tool discovery. Use CLI-first when you want simpler debugging or are scripting a workflow.

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
