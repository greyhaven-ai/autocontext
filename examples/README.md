# Examples

These are copy-paste starting points for people evaluating the repo, integrating external agents, or embedding the packages directly.

## Which Example To Start With

- Want the full control plane from a source checkout? Use the Python CLI example.
- Want Hermes Agent to understand autocontext? Use the Hermes CLI-first workflow.
- Want to wire Claude Code or another MCP client? Use the MCP config snippet.
- Want a typed Python integration? Use the Python SDK example.
- Want a Node/TypeScript integration? Use the TypeScript library example.
- Want to prototype a reusable TypeScript agent handler? Use the experimental agent-runtime example.
- Want always-on queued work? Use the persistent host worker recipe.

## Python CLI From Source

Run this from the repo root. It uses the deterministic provider, so it does not require external API keys.

```bash
cd autocontext
export AUTOCONTEXT_AGENT_PROVIDER=deterministic

RUN_ID="example_$(date +%s)"

uv run autoctx run \
  grid_ctf \
  --iterations 3 \
  --run-id "$RUN_ID" \
  --json | jq .

uv run autoctx status "$RUN_ID" --json | jq .

mkdir -p exports
uv run autoctx export \
  "$RUN_ID" \
  --output "exports/${RUN_ID}.json" \
  --json | jq .

uv run autoctx export \
  "$RUN_ID" \
  --format pi-package \
  --output "exports/${RUN_ID}-pi-package" \
  --json | jq .
```

## Claude Code MCP Config

Add this to your project-level `.claude/settings.json` and replace `/ABSOLUTE/PATH/TO/REPO/autocontext` with the real path to this repo's Python package directory.

```json
{
  "mcpServers": {
    "autocontext": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/ABSOLUTE/PATH/TO/REPO/autocontext",
        "autoctx",
        "mcp-serve"
      ],
      "env": {
        "AUTOCONTEXT_AGENT_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

For a fuller comparison of CLI, MCP, and SDK integrations, see [autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md).

## Persistent Host Worker

Run the API server and worker from the same durable workspace when queued tasks should continue in the background:

```bash
cd autocontext
export AUTOCONTEXT_DB_PATH=/srv/autoctx/runs/autocontext.sqlite3
export AUTOCONTEXT_RUNS_ROOT=/srv/autoctx/runs
export AUTOCONTEXT_KNOWLEDGE_ROOT=/srv/autoctx/knowledge

uv run autoctx serve --host 0.0.0.0 --port 8000
uv run autoctx worker --poll-interval 5 --concurrency 2
```

When using a stateful persistent provider such as persistent Pi RPC, the worker keeps effective concurrency at `1` for that provider so task streams cannot overlap.

For bounded smoke tests, use `uv run autoctx worker --once --json`. See [autocontext/docs/persistent-host.md](../autocontext/docs/persistent-host.md) for deployment notes.

## Hermes Agent Skill And Curator Inspection

Hermes agents can use autocontext through the CLI without MCP. Export the Hermes skill into a Hermes profile, then inspect Hermes v0.12 skill usage and Curator reports read-only.

```bash
cd autocontext

uv run autoctx hermes export-skill \
  --output ~/.hermes/skills/autocontext/SKILL.md \
  --json | jq .

uv run autoctx hermes inspect --json | jq .
```

For a fuller walkthrough, see [autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md#hermes-cli-first-starter-workflow).

## Python SDK

Run this after setting up the Python package in `autocontext/`.

```python
from autocontext import AutoContext

client = AutoContext(db_path="runs/autocontext.sqlite3")

scenario = "grid_ctf"
strategy = {
    "aggression": 0.65,
    "defense": 0.45,
    "path_bias": 0.55,
}

description = client.describe_scenario(scenario)
print(description["strategy_interface"])

validation = client.validate(scenario, strategy)
if not validation.valid:
    raise SystemExit(validation.reason)

result = client.evaluate(scenario, strategy, matches=3)
print(result.model_dump_json(indent=2))
```

## TypeScript Library

Install the package in your own project with `npm install autoctx`, then set the provider env vars before running this example.

```ts
import {
  ImprovementLoop,
  LLMJudge,
  SimpleAgentTask,
  createProvider,
  resolveProviderConfig,
} from "autoctx";

const provider = createProvider(resolveProviderConfig());
const model = provider.defaultModel();

const taskPrompt = "Explain binary search to a new engineer in 4-6 sentences.";
const rubric = "Score correctness, clarity, and usefulness on a 0-1 scale.";
const initialOutput = "Binary search is a fast way to find things in a sorted list.";

const judge = new LLMJudge({ provider, model, rubric });
const baseline = await judge.evaluate({ taskPrompt, agentOutput: initialOutput });

const task = new SimpleAgentTask(taskPrompt, rubric, provider, model);
const loop = new ImprovementLoop({ task, maxRounds: 3, qualityThreshold: 0.9 });
const result = await loop.run({ initialOutput, state: {} });

console.log(JSON.stringify({
  baselineScore: baseline.score,
  bestScore: result.bestScore,
  bestOutput: result.bestOutput,
}, null, 2));
```

Example provider setup:

```bash
export AUTOCONTEXT_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

## Experimental TypeScript Agent Handler

The TypeScript package exposes an experimental `autoctx/agent-runtime` subpath
for local programmable handlers in `.autoctx/agents/*.ts`. It uses the bundled
`tsx` loader for `.ts`, `.tsx`, and `.mts` files on Node 18+. This is an
open-source local authoring surface, not the hosted deployment/orchestration
layer.

See [`examples/agent-runtime/.autoctx/agents/support.ts`](agent-runtime/.autoctx/agents/support.ts)
for a minimal handler:

```ts
import type { AutoctxAgentContext } from "autoctx/agent-runtime";

type SupportPayload = {
  threadId?: string;
  message: string;
};

export const triggers = { webhook: true };

export default async function supportAgent(
  { init, payload }: AutoctxAgentContext<SupportPayload>,
) {
  const runtime = await init();
  const session = await runtime.session(payload.threadId ?? "default");
  return session.prompt(payload.message, { role: "support-triager" });
}
```

## Hermes CLI-First Workflow

A Hermes agent can drive autocontext entirely through CLI commands. Set the gateway env vars and use `--json` for machine-readable output.

```bash
cd autocontext

# Configure Hermes gateway
export AUTOCONTEXT_AGENT_PROVIDER=openai-compatible
export AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1
export AUTOCONTEXT_AGENT_API_KEY=no-key
export AUTOCONTEXT_AGENT_DEFAULT_MODEL=hermes-3-llama-3.1-8b

# Run → status → export loop
RUN_ID="hermes_$(date +%s)"
mkdir -p logs
uv run autoctx run grid_ctf --iterations 3 --run-id "$RUN_ID" --json >"logs/${RUN_ID}.json" 2>"logs/${RUN_ID}.err" &
RUN_PID=$!
while kill -0 "$RUN_PID" 2>/dev/null; do
  uv run autoctx status "$RUN_ID" --json | jq '.generations[-1]'
  sleep 5
done
wait "$RUN_PID"
cat "logs/${RUN_ID}.json" | jq .
uv run autoctx export "$RUN_ID" --output "exports/${RUN_ID}.json" --json | jq .
uv run autoctx solve "Design a safe, adaptive grid capture-the-flag strategy." --iterations 2 --json | jq .
```

For the full walkthrough including polling, timeouts, and integration path comparison, see [autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md#hermes-cli-first-starter-workflow).

## Read Next

- Repo overview: [README.md](../README.md)
- Python package guide: [autocontext/README.md](../autocontext/README.md)
- TypeScript package guide: [ts/README.md](../ts/README.md)
- External agent integration guide: [autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md)
- Change history: [CHANGELOG.md](../CHANGELOG.md)
