# autoctx — TypeScript package

`autoctx` is the Node/TypeScript package for autocontext. It ships the operator-facing CLI, TUI, simulations, investigations, analysis, missions, MCP server, runtime/session primitives, production-trace SDK, and experimental agent-handler surface.

Use the Python package when you need the full Python control plane or local MLX/CUDA training implementation. Use this package when you need Node, npm, the TUI, Fetch/agent adapters, or TypeScript library APIs.

## Install

```bash
bun add -g autoctx
# or
npm install -g autoctx
```

From a checkout:

```bash
cd ts
npm install
npm run build
```

## Quick start

```bash
AUTOCONTEXT_AGENT_PROVIDER=deterministic autoctx solve "improve customer-support replies" --iterations 3
```

Use a real provider by setting `AUTOCONTEXT_AGENT_PROVIDER` and its credential:

```bash
AUTOCONTEXT_AGENT_PROVIDER=anthropic ANTHROPIC_API_KEY=... autoctx solve "..." --iterations 3
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8000/v1 AUTOCONTEXT_AGENT_API_KEY=... autoctx solve "..." --iterations 3
AUTOCONTEXT_AGENT_PROVIDER=pi AUTOCONTEXT_PI_COMMAND=pi autoctx solve "..." --iterations 3
```

Provider routing details live in [../autocontext/docs/agent-integration.md](../autocontext/docs/agent-integration.md).

## CLI surfaces

| Command                                                | Purpose                                                      |
| ------------------------------------------------------ | ------------------------------------------------------------ |
| `autoctx solve "..." --iterations 3`                   | Generate and run a scenario from a plain-language goal       |
| `autoctx run <scenario> --iterations 3`                | Run a saved scenario                                         |
| `autoctx simulate -d "..."`                            | Build/replay/compare simulations                             |
| `autoctx investigate -d "..."`                         | Evidence-driven diagnosis                                    |
| `autoctx analyze --id <id> --type <kind>`              | Inspect runs, simulations, investigations, or missions       |
| `autoctx mission create --name "..." --goal "..."`     | Create verifier-driven goals                                 |
| `autoctx mission run --id <id> --max-iterations 3`     | Execute a mission                                            |
| `autoctx queue add --task-prompt "..." --rubric "..."` | Add evaluation/improvement work                              |
| `autoctx runtime-sessions timeline --run-id <run_id>`  | Inspect provider/tool/child-task timelines                   |
| `autoctx mcp-serve`                                    | Expose MCP tools                                             |
| `autoctx tui`                                          | Start the terminal UI                                        |
| `autoctx train --scenario <name> --dataset <jsonl>`    | Validate training input and call an injected training runner |
| `autoctx agent run <name> --payload '{...}'`           | Invoke experimental `.autoctx/agents` handlers               |

`train` is a validation/executor-hook surface in TypeScript; end-to-end MLX/CUDA training lives in the Python package unless your application injects a real `TrainingRunner`.

## MCP and control plane

```bash
autoctx mcp-serve
```

The MCP server exposes scenario/run/knowledge/evaluation/feedback/solve/sandbox/export tools. Python and TypeScript share the same high-level vocabulary; parity details are tracked in [../docs/scenario-parity-matrix.md](../docs/scenario-parity-matrix.md).

## Library usage

```ts
import { createProvider, LLMJudge } from "autoctx";

const provider = createProvider({
  providerType: "anthropic",
  apiKey: process.env.ANTHROPIC_API_KEY ?? "",
});

const judge = new LLMJudge({
  provider,
  model: provider.defaultModel(),
  rubric: "Score clarity and correctness.",
});

const result = await judge.evaluate({
  taskPrompt: "Explain binary search.",
  agentOutput: "Binary search halves the search space each step.",
});
```

Prefer package subpath exports for specialized surfaces:

```ts
import { buildTrace } from "autoctx/production-traces";
import { instrumentClient } from "autoctx/integrations/anthropic";
import type { AutoctxAgentContext } from "autoctx/agent-runtime";
import { connectMcpRuntimeTools } from "autoctx/runtimes/mcp";
```

## Production traces

```ts
import Anthropic from "@anthropic-ai/sdk";
import { FileSink, instrumentClient } from "autoctx/integrations/anthropic";

const sink = new FileSink("./traces/anthropic.jsonl");
const client = instrumentClient(new Anthropic(), {
  sink,
  appId: "billing-bot",
  environmentTag: "prod",
});
```

The SDK captures provider-native content blocks, cache-aware usage, outcome
taxonomy, and dataset/retention helpers. Deeper notes live in
[../docs/analytics.md](../docs/analytics.md),
[../docs/opentelemetry-bridge.md](../docs/opentelemetry-bridge.md), and the
source under `src/production-traces/`.

## Agent handlers

The experimental `autoctx/agent-runtime` subpath discovers handlers only from
`.autoctx/agents` and invokes them with explicit `payload`, `env`, `workspace`,
and `AgentRuntime` capabilities.

```ts
// .autoctx/agents/support.ts
import type { AutoctxAgentContext } from "autoctx/agent-runtime";

export default async function ({ init, payload }: AutoctxAgentContext<{ message: string }>) {
  const runtime = await init();
  const session = await runtime.session("support");
  return session.prompt(payload.message, { role: "support-triager" });
}
```

```bash
autoctx agent run support --payload '{"message":"triage this ticket"}' --json
autoctx agent dev --port 3583
autoctx agent build --target node --out .autoctx/build/node
```

Examples:
[../examples/README.md](../examples/README.md#experimental-typescript-agent-handler).

## Fetch/edge adapters

Generic Fetch/ESM hosts can use static catalogs and explicit host capabilities;
the package does not imply Cloudflare/Vercel/Deno deployment wrappers.

Reference docs moved out of this README:

- [../docs/fetch-api-reference.md](../docs/fetch-api-reference.md)
- [../docs/fetch-host-capability-manifest.md](../docs/fetch-host-capability-manifest.md)
- [../docs/generated-fetch-packaging.md](../docs/generated-fetch-packaging.md)
- [../docs/fetch-conformance.md](../docs/fetch-conformance.md)
- [../docs/fetch-troubleshooting.md](../docs/fetch-troubleshooting.md)
- [../docs/edge-runtime-compatibility.md](../docs/edge-runtime-compatibility.md)

## Contract probes

```bash
autoctx probes check --suite contract-probes.json
autoctx probes check --suite contract-probes.json --json
autoctx probes extract --trace harness-trace.json --output contract-probes.json
```

Use probes to turn observed harness behavior into strict executable checks.

## Project defaults

`autoctx` searches upward for `.autoctxrc.json`, `.autoctxrc`, or
`autoctx.config.json`. Explicit CLI flags win over config files. Env file
loading for agent handlers is explicit: pass `--env FILE` or set
`AUTOCTX_ENV_FILE` for generated Node servers.

## Development

```bash
npm run build
npm run lint
npm test
```

Keep this README as the package entry point. Put long reference material in
`../docs/`, `examples/`, or source-level API docs.
