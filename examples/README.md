# Examples

These are copy-paste starting points for people evaluating the repo, integrating external agents, or embedding the packages directly.

## Which Example To Start With

- Want the full control plane from a source checkout? Use the Python CLI example.
- Want to wire Claude Code or another MCP client? Use the MCP config snippet.
- Want a typed Python integration? Use the Python SDK example.
- Want a Node/TypeScript integration? Use the TypeScript library example.

## Python CLI From Source

Run this from the repo root. It uses the deterministic provider, so it does not require external API keys.

```bash
cd autocontext
export AUTOCONTEXT_AGENT_PROVIDER=deterministic

RUN_ID="example_$(date +%s)"

uv run autoctx run \
  --scenario grid_ctf \
  --gens 3 \
  --run-id "$RUN_ID" \
  --json | jq .

uv run autoctx status "$RUN_ID" --json | jq .

mkdir -p exports
uv run autoctx export \
  --scenario grid_ctf \
  --output "exports/${RUN_ID}.json" \
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
        "AUTOCONTEXT_ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

For a fuller comparison of CLI, MCP, and SDK integrations, see [autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md).

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
uv run autoctx run --scenario grid_ctf --gens 3 --run-id "$RUN_ID" --json >"logs/${RUN_ID}.json" 2>"logs/${RUN_ID}.err" &
RUN_PID=$!
while kill -0 "$RUN_PID" 2>/dev/null; do
  uv run autoctx status "$RUN_ID" --json | jq '.generations[-1]'
  sleep 5
done
wait "$RUN_PID"
cat "logs/${RUN_ID}.json" | jq .
uv run autoctx export --scenario grid_ctf --output "exports/${RUN_ID}.json" --json | jq .
uv run autoctx solve --description "Design a safe, adaptive grid capture-the-flag strategy." --gens 2 --json | jq .
```

For the full walkthrough including polling, timeouts, and integration path comparison, see [autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md#hermes-cli-first-starter-workflow).

## Read Next

- Repo overview: [README.md](../README.md)
- Python package guide: [autocontext/README.md](../autocontext/README.md)
- TypeScript package guide: [ts/README.md](../ts/README.md)
- External agent integration guide: [autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md)
- Change history: [CHANGELOG.md](../CHANGELOG.md)
