# Examples

These are copy-paste starting points for people evaluating the repo, integrating external agents, or embedding the packages directly.

## Which Example To Start With

- Want the full control plane from a source checkout? Use the Python CLI example.
- Want Hermes Agent to understand autocontext? Use the Hermes CLI-first workflow.
- Want to wire Claude Code or another MCP client? Use the MCP config snippet.
- Want a typed Python integration? Use the Python SDK example.
- Want a Node/TypeScript integration? Use the TypeScript library example.
- Want to operate local or remote runs in a terminal? Use the TypeScript TUI example.
- Want to compose trusted live runtime components? Use the runtime composition example.
- Want to package generic Fetch/ESM agent app artifacts? Use the generated Fetch packaging example.
- Want to prototype a reusable TypeScript agent handler? Use the experimental agent-runtime example.
- Want always-on queued work? Use the persistent host worker recipe.
- Want to build a Node evaluator for context promotion? Import the
  `autoctx/context-bundles` subpath and follow the
  [matched-trial and campaign error-control contract](../docs/context-bundles.md).
- Want OS-isolated multi-generation Python research? Enable the TaskRunner's
  Docker workspace backend, candidate execution, and explicit capability
  approval; custom runtimes may still inject a `ResearchWorkspace` factory.
  Start with the settings and lifecycle requirements in
  [research workspaces](../docs/research-workspaces.md).
- Want correctness-first recursive GPU-kernel search? Start with the kernel evolution MVP.
- Want to compare transfer and specialist behavior across kernel families? Run
  the synthetic [multi-workload kernel study](kernel_evolution/multi_workload/README.md).
- Want a scenario campaign on a managed accelerator? Use the
  [capability-validated Prime plan](../docs/campaign-scheduler.md#prime-accelerator-plans);
  accelerator type/count, image, region, and telemetry must be declared and
  are never silently downgraded to CPU.

## Kernel Evolution MVP

The Python kernel adapter composes AutoContext's multi-generation loop with a
strict external benchmark contract, hardware-scoped promotion gates, and
append-only candidate lineage. Its deterministic single-problem example runs
without a GPU:

```bash
cd autocontext
uv run --frozen python ../examples/kernel_evolution/run.py
```

See [the example](kernel_evolution/README.md) and
[kernel evolution guide](../autocontext/docs/kernel-evolution.md). The bundled
orchestration adapter is synthetic. The companion
[H100 contract smoke](kernel_evolution/kernelbench_h100/README.md) preserves a
real KernelBench/AutoKernel comparison and now separates strict-FP32 from
relaxed-precision campaigns, binds private holdout commitments, and applies a
bounded sequential promotion gate. The historical one-shot smoke accepts only
trusted source. The accelerator-neutral protected runner now separates
generated execution from its authoritative evaluator and authenticates compact
receipts against an operator-pinned host key. New v4 evidence uses a
finite-sample paired sign e-process, canonical policy/calibration receipts, and
sealed confirmation audit storage. Model-backed campaigns additionally persist
exact generation provenance and deterministic retry/token/cost/wall budgets,
support safe stop/status/resume, and retain mailbox generation as a fallback.
The production H100 campaign
remains fail-closed until role-separated telemetry, trusted mutation
observation, comparable reference timing, and crash-safe container creation are
implemented and the exact path passes the opt-in real MIG adversarial gate.
The companion multi-workload study uses the same runner/report lineage across
variable-shape matmul, fused elementwise/reduction, and causal attention. It
keeps every family's primary and fresh-confirmation result visible, records
cross-hardware and cross-family trials, and cannot label a champion portable
when any required correctness slice or case floor fails.

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

uv run autoctx status "$RUN_ID" --json | jq '.latest_generation'

mkdir -p exports
uv run autoctx export \
  "$RUN_ID" \
  --format json \
  --output "exports/${RUN_ID}.json" \
  --json | jq .

uv run autoctx export \
  "$RUN_ID" \
  --format pi-package \
  --output "exports/${RUN_ID}-pi-package" \
  --json | jq .
```

Omit `--output` for JSON to print the portable strategy package directly to
stdout. `--format strategy` remains a compatibility alias for `--format json`.

## Create A Scenario Or Start MCP

Use the canonical nested commands in scripts and agent instructions. Python
selects a family pipeline explicitly; TypeScript can classify a plain-language
description and materialize the resulting scenario:

```bash
uv run autoctx scenario create \
  --family workflow \
  --name billing_support_workflow \
  --description "evaluate and route concise billing-support replies"

bunx autoctx scenario create --description "evaluate concise support replies"

uv run autoctx serve mcp
```

The `scenario create` and `serve mcp` paths are shared by the Python and npm
CLIs; their scenario-creation flag sets are documented in the
[cross-runtime CLI contract](../docs/cli-contract.md).

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
        "serve",
        "mcp"
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

## TypeScript TUI

The npm CLI includes the Node.js 22.19+ pi-tui operator client. Start a local
interactive server and client together:

```bash
autoctx tui
```

Or attach the client to an existing TypeScript server:

```bash
autoctx tui --connect https://host.example
```

Non-loopback endpoints must use HTTPS/WSS. Python exposes its existing CLI and
API surfaces but does not yet ship this TUI. See the [operator TUI
guide](../ts/README.md#operator-tui) for commands, key bindings, replay, and
security behavior.

## Trusted-Host Runtime Composition

The package root exposes a typed, host-owned component graph. A consumer waits
until its declared provider is present, and reconciling to an empty graph
drains both components:

```ts
import {
  RuntimeComponentGraph,
  defineRuntimeCapability,
  provideRuntimeCapability,
  type RuntimeComponentManifest,
} from "autoctx";

const endpoint = defineRuntimeCapability<string>("service.endpoint");

const provider: RuntimeComponentManifest = {
  id: "provider",
  instanceId: "provider@1",
  provides: [provideRuntimeCapability(endpoint, "https://api.example")],
  activate: () => undefined,
};

const consumer: RuntimeComponentManifest = {
  id: "consumer",
  instanceId: "consumer@1",
  requires: [endpoint],
  activate: ({ get }) => {
    console.log(get(endpoint));
  },
};

const graph = new RuntimeComponentGraph();
await graph.reconcile([consumer]);
await graph.reconcile([consumer, provider]);
await graph.reconcile([]);
```

Keep graph construction and artifact-to-manifest resolution in trusted host
code. Before granting generated components live capabilities, read the
[component graph](../docs/internal/runtime-component-graph.md), [effect
policy](../docs/internal/runtime-effect-policy.md), and [transactional
activation](../docs/internal/runtime-transactional-activation.md) contracts.

## Generated Fetch Packaging

The TypeScript package exposes a generic `autoctx/control-plane/agent-app-fetch`
subpath for generated Fetch/ESM entrypoints. The example in
[`../ts/examples/generated-fetch-packaging.ts`](../ts/examples/generated-fetch-packaging.ts)
shows how a build step can emit an entrypoint, host capability manifest, and
manifest schema from explicit `.autoctx/agents` plus optional `.autoctx/runtimes`
entries. It does not include deployment descriptors or platform policy.

For the full walkthrough, see
[`../docs/generated-fetch-packaging.md`](../docs/generated-fetch-packaging.md).

## Experimental TypeScript Agent Handler

The TypeScript package exposes an experimental `autoctx/agent-runtime` subpath
for local programmable handlers in `.autoctx/agents/*.ts`. It uses the bundled
`tsx` loader for `.ts`, `.tsx`, and `.mts` files on Node 22.19+. This is an
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
  uv run autoctx status "$RUN_ID" --json | jq '.latest_generation'
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
